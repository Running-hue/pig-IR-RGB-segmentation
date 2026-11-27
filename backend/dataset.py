"""Frame data, mask revisions, screening decisions and failure records."""
from __future__ import annotations

import base64
import io
import json
from pathlib import Path
import secrets
import threading

from .core import (Image, np, atomic_bytes, atomic_json, discover, input_fingerprint,
                   mask_stats, overlay, png_bytes, read_frame, stamp, validate_output,
                   write_draft, input_mode)
from .geometry import (prepare_frame, write_geometry, validate_mask_region, RGB_VERSION, RGB_LEGACY,
                       unverified_geometry)
from .records import load_failures, SUCCESS


class Dataset:
    def __init__(self, source, output, rows=None, mode=None):
        self.source, self.output = validate_output(source, output)
        manifest=self.output/'dataset.json'
        saved=json.loads(manifest.read_text()) if manifest.is_file() else {}
        self.input_mode=input_mode(saved.get('input_mode','infrared') if mode is None else mode)
        self.geometry_version=saved.get('geometry_version',RGB_LEGACY if self.input_mode=='rgb' else None)
        if self.input_mode=='rgb' and self.geometry_version not in {RGB_VERSION,RGB_LEGACY}:
            raise ValueError('未知的 RGB 成果几何版本，不能直接打开')
        self.rows = discover(self.source,self.input_mode) if rows is None else rows
        self.by_id = {r['id']: r for r in self.rows}
        self.dataset_id = secrets.token_urlsafe(16)
        self.lock = threading.RLock()
        self.sam = None
        self.sam_predictions = {}
        self._failure_cache = None

    def failures(self):
        paths=[self.output/name for name in ['segmentation_failures.json','batch_job.json','summary.json']]
        signature=tuple((p.stat().st_mtime_ns,p.stat().st_size) if p.is_file() else None for p in paths)
        if self._failure_cache is None or self._failure_cache[0]!=signature:
            self._failure_cache=(signature,load_failures(self.output,self.rows))
        return self._failure_cache[1]

    def failure_records(self):
        records=[]
        for item in self.failures():
            row=next((r for r in self.rows if r['relative_dir']==item['relative_dir']),None)
            meta=self.metadata(row) if row else item
            resolved=meta.get('status') in SUCCESS if row else item.get('failure_status')=='resolved'
            review={k:meta[k] for k in ['decision','notes','head_side','decision_at','revision'] if k in meta}
            records.append({**item,**review,'failure_status':'resolved' if resolved else 'failed'})
        return records

    def record_failure(self,row,error,stage='segmentation'):
        records=self.failure_records()
        records.append({**row,'error':str(error),'stage':stage,'at':stamp(),'failure_status':'failed'})
        atomic_json(self.output/'segmentation_failures.json',{'schema_version':1,'records':records})

    def frame_input(self,row):
        return prepare_frame(row['source_dir'],self.input_mode,self.geometry_version)

    def review_input(self,row):
        """A failed capture remains reviewable even when its geometry cannot be prepared.

        The raw preview is never used by inference, mask saving or depth projection.
        """
        try:
            image,depth,geometry,valid=self.frame_input(row)
            return image,depth,geometry,valid,'',True
        except (ValueError,OSError,KeyError,TypeError,EOFError) as exc:
            if self.metadata(row)['status']!='failed':
                raise
            error=str(exc)
        available=True
        try:
            path=Path(row['source_dir'])/('color.png' if self.input_mode=='rgb' else 'infrared.png')
            with Image.open(path) as im:
                if self.input_mode=='rgb':
                    image=np.asarray(im.convert('RGB')).copy()
                else:
                    from .core import normalize_ir
                    try:image=normalize_ir(np.asarray(im))
                    except ValueError:image=np.asarray(im.convert('L')).copy()
        except (ValueError,OSError,TypeError) as exc:
            image=np.zeros((240,320,3),np.uint8)
            available=False
            error+='；原图也无法读取：'+str(exc)
        geometry=unverified_geometry(self.input_mode,image.shape[:2],error)
        return image,None,geometry,np.zeros(image.shape[:2],bool),error,available

    def editable_geometry(self):
        if self.input_mode=='rgb' and self.geometry_version==RGB_LEGACY:
            raise ValueError('旧 RGB 成果未做几何校正，仅供查看和筛选；请新建 RGB 分割任务，不可在旧掩码上切换去畸变底图')

    def row(self, frame_id):
        if frame_id not in self.by_id:
            raise ValueError('找不到该次采集')
        return self.by_id[frame_id]

    def dest(self, row):
        path = self.output / row['relative_dir']
        if self.output not in path.resolve().parents:
            raise ValueError('输出路径不合法')
        return path

    def metadata(self, row):
        p = self.dest(row)/'segmentation.json'
        if p.exists():
            meta=json.loads(p.read_text())
            if input_mode(meta.get('input_mode','infrared'))!=self.input_mode:
                raise ValueError('帧的分割输入与成果目录模式不一致，请核查成果文件')
            return {**meta,**row,'input_mode':self.input_mode}
        failures=[f for f in self.failures() if f['relative_dir']==row['relative_dir']]
        return {**row, 'input_mode':self.input_mode,'status': 'failed' if failures else 'pending',
                'segmentation_error':failures[-1]['error'] if failures else '',
                'reviewed': False, 'revision': 0, 'stats': {}}

    def listing(self):
        with self.lock:
            rows = []
            for r in self.rows:
                meta = self.metadata(r)
                rows.append({**r, **{k:meta.get(k) for k in ['status','reviewed','revision']},
                             'decision':meta.get('decision','pending'),'segmentation_error':meta.get('segmentation_error','')})
        from .sam_backend import ready
        dataset_file = self.output/'dataset.json'
        backend = json.loads(dataset_file.read_text()).get('backend','grabcut') if dataset_file.exists() else 'grabcut'
        return {'source': str(self.source), 'output': str(self.output), 'frames': rows,
                'dataset_id':self.dataset_id,'input_mode':self.input_mode,'geometry_version':self.geometry_version,
                'geometry_legacy':self.input_mode=='rgb' and self.geometry_version==RGB_LEGACY,
                'backend':backend, 'sam_ready':ready(),
                'pigs': len({r['pig_id'] for r in rows}),'failures':self.failure_records()}

    def detail(self, frame_id):
        with self.lock:
            row = self.row(frame_id)
            meta = self.metadata(row)
            gray, depth, geometry, valid, error, available = self.review_input(row)
            if self.input_mode=='rgb' and self.geometry_version==RGB_LEGACY:
                meta={**meta,'stats':{**meta.get('stats',{}),'valid_depth_ratio':None}}
            return {**meta, 'revision': meta.get('revision', 0),
                    'geometry':geometry,'geometry_legacy':self.input_mode=='rgb' and self.geometry_version==RGB_LEGACY,
                    'review_only':bool(error),'review_error':error,'image_available':available,
                    'draft_available':not error and (self.dest(row)/'trunk_mask_draft.png').is_file(),
                    'width': gray.shape[1], 'height': gray.shape[0],
                    'reference_available':(Path(row['source_dir'])/('infrared.png' if self.input_mode=='rgb' else 'color.png')).is_file(),
                    'depth_available':depth is not None}

    def media(self, frame_id, kind):
        row = self.row(frame_id)
        dest = self.dest(row)
        if kind == 'base':
            return png_bytes(self.review_input(row)[0])
        if kind == 'reference':
            if self.input_mode == 'rgb':
                with Image.open(Path(row['source_dir'])/'infrared.png') as im:
                    array=np.asarray(im)
                from .core import normalize_ir
                return png_bytes(normalize_ir(array))
            return (Path(row['source_dir'])/'color.png').read_bytes()
        if kind == 'depth':
            _,depth,_,_=self.frame_input(row)
            if depth is None:
                raise ValueError('没有对齐 RGB 的深度数组')
            valid=np.isfinite(depth)&(depth>0)
            preview=np.zeros(depth.shape,np.uint8)
            if valid.any():
                lo,hi=np.percentile(depth[valid],[1,99])
                preview[valid]=(255-np.clip((depth[valid]-lo)/max(1,hi-lo),0,1)*254).astype(np.uint8)
            return png_bytes(preview)
        if kind == 'color':
            path = Path(row['source_dir'])/'color.png'
            return path.read_bytes()
        if kind == 'infrared':
            path = dest/'infrared_display.png'
            return path.read_bytes() if path.exists() else png_bytes(read_frame(row['source_dir'])[0])
        if kind == 'mask':
            image,_,_,_,error,_=self.review_input(row)
            if error:
                # No saved mask can be overlaid onto an unverified raw preview.
                return png_bytes(np.zeros(image.shape[:2],np.uint8))
            meta = self.metadata(row)
            name = 'trunk_mask_working.png' if meta.get('status') in {'corrected','reviewed'} else 'trunk_mask_draft.png'
            path = dest/name
            if not path.exists():
                return png_bytes(np.zeros(image.shape[:2], np.uint8))
            with Image.open(path) as im:
                if im.size != (image.shape[1],image.shape[0]):
                    raise ValueError('已保存掩码尺寸与所选输入图像不一致')
            return path.read_bytes()
        if kind == 'draft':
            return (dest/'trunk_mask_draft.png').read_bytes()
        raise ValueError('不支持的图片类型')

    def generate(self, frame_id):
        with self.lock:
            if self.input_mode != 'infrared':
                raise ValueError('传统初稿仅支持红外；RGB 请使用 SAM 或手工勾画')
            row = self.row(frame_id)
            meta = self.metadata(row)
            if meta['status'] in {'corrected', 'reviewed'}:
                raise ValueError('已有人工修正；请使用“恢复自动初稿”在画布中比较，避免覆盖进度')
            return write_draft(row, self.output)

    def predict_sam(self, payload):
        # Inference changes only the preview; saving remains a separate action.
        with self.lock:
            self.editable_geometry()
            row = self.row(payload['id'])
            gray, _, geometry, valid = self.frame_input(row)
            if self.sam is None:
                from .sam_backend import SAMPredictor
                self.sam = SAMPredictor()
            mask, info = self.sam.predict(gray, payload.get('prompts',{}), key=self.input_mode+':'+str(Path(row['source_dir']).resolve())+':'+geometry.get('geometry_id',''))
            info['input_mode']=self.input_mode
            info['geometry_version']=geometry['version']
            mask=mask.copy();mask[~valid]=0
            if not mask.any():
                raise ValueError('SAM 返回空掩码，请调整提示点或框')
            prediction_id = secrets.token_urlsafe(24)
            self.sam_predictions[prediction_id] = {'frame_id':row['id'], 'info':info,
                                                  'geometry_id':geometry.get('geometry_id'),
                                                  'raw':png_bytes(self.sam.last_raw_mask)}
            while len(self.sam_predictions)>32:
                self.sam_predictions.pop(next(iter(self.sam_predictions)))
            return {'mask':'data:image/png;base64,'+base64.b64encode(png_bytes(mask)).decode(),
                    'prediction_id':prediction_id, 'model_info':info}

    def save(self, payload):
        with self.lock:
            self.editable_geometry()
            row = self.row(payload['id'])
            old = self.metadata(row)
            revision = old.get('revision', 0)
            if payload.get('revision', 0) != revision:
                raise ValueError('这张图已在其他页面保存，请重新载入后再修改')
            prediction = None
            if payload.get('sam_prediction_id'):
                prediction = self.sam_predictions.get(payload['sam_prediction_id'])
                if not prediction or prediction['frame_id']!=row['id']:
                    raise ValueError('SAM 预览已失效，请重新运行模型后保存')
            gray, depth, geometry, valid = self.frame_input(row)
            if prediction and prediction.get('geometry_id')!=geometry.get('geometry_id'):
                raise ValueError('SAM 预览之后输入或标定发生变化，请重新运行模型')
            previous_geometry=old.get('geometry',{})
            if previous_geometry.get('geometry_id') and previous_geometry['geometry_id']!=geometry.get('geometry_id'):
                raise ValueError('相机标定或几何来源发生变化，请核查后重新分割')
            encoded = payload.get('mask', '')
            if not encoded.startswith('data:image/png;base64,'):
                raise ValueError('掩码必须是 PNG 图片')
            raw = base64.b64decode(encoded.split(',', 1)[1], validate=True)
            im = Image.open(io.BytesIO(raw))
            if im.size != (gray.shape[1], gray.shape[0]):
                raise ValueError('掩码尺寸必须与所选原始图像完全相同')
            mask = np.asarray(im.convert('L'))
            if not np.isin(mask, [0, 255]).all():
                raise ValueError('掩码像素只能是 0 和 255')
            if not mask.any():
                raise ValueError('掩码为空，请先勾画可见躯干')
            validate_mask_region(mask,valid)
            reviewed = payload.get('reviewed') is True
            direction = payload.get('head_side', 'unknown')
            if direction not in {'left','right','up','down','unknown'}:
                raise ValueError('头侧方向无效')
            if reviewed and direction == 'unknown':
                raise ValueError('确认合格前，请记录图中的头侧方向')
            hashes = input_fingerprint(row['source_dir'],self.input_mode,self.geometry_version)
            if old.get('source_sha256') and old['source_sha256'] != hashes:
                raise ValueError('原始图像或对应深度文件发生变化，请先核查数据源')
            dest = self.dest(row)
            mask_png = png_bytes(mask)
            overlay_png = png_bytes(overlay(gray, mask))
            meta = {**old, **row, 'status': 'reviewed' if reviewed else 'corrected',
                    'reviewed': reviewed, 'revision': revision+1, 'saved_at': stamp(),
                    'head_side': direction, 'notes': str(payload.get('notes',''))[:4000],
                    'shape_hw': list(mask.shape), 'source_sha256': hashes,'input_mode':self.input_mode,
                    'coordinate_space':geometry['coordinate_space'],'geometry':geometry,
                    'segmentation_error':'',
                    'depth_file':'depth_aligned_to_color_mm.npy' if self.input_mode=='rgb' and depth is not None else ('depth_mm.npy' if self.input_mode=='infrared' else None),
                    'stats': mask_stats(mask, depth),
                    'initial_method': old.get('initial_method', old.get('method', 'manual')),
                    'method': 'manual_correction'}
            if prediction:
                meta['last_sam_prediction'] = prediction['info']
                meta['method'] = 'sam2_assisted_manual_correction'
            if reviewed:
                meta['stats']['warnings'] = [w for w in meta['stats']['warnings'] if '待人工确认' not in w]
            # Each saved revision is retained independently.
            write_geometry(dest,geometry,valid)
            if self.input_mode=='rgb':atomic_bytes(dest/'color_display.png',png_bytes(gray))
            rev = dest/'revisions'/f'{revision+1:05d}'
            atomic_bytes(rev/'mask.png', mask_png)
            atomic_json(rev/'segmentation.json', meta)
            if prediction:
                atomic_bytes(rev/'sam_raw_prediction.png',prediction['raw'])
            atomic_bytes(dest/'trunk_mask_working.png', mask_png)
            atomic_bytes(dest/'overlay_working.png', overlay_png)
            if reviewed:
                atomic_bytes(dest/'trunk_mask.png', mask_png)
                atomic_bytes(dest/'overlay.png', overlay_png)
            else:
                # Do not leave a stale approved mask beside a newer unreviewed edit.
                for name in ['trunk_mask.png','overlay.png']:
                    p = dest/name
                    if p.exists():
                        p.unlink()
            atomic_json(dest/'segmentation.json', meta)
            return meta

    def decide(self, payload):
        with self.lock:
            row=self.row(payload['id'])
            old=self.metadata(row)
            if payload.get('revision')!=old.get('revision',0):
                raise ValueError('结果已经更新，请重新载入再筛选')
            decision=payload.get('decision')
            if decision not in {'keep','reject','unsure','pending'}:
                raise ValueError('筛选状态无效')
            note=str(payload.get('notes','')).strip()[:4000]
            if decision=='reject' and not note:
                raise ValueError('请在备注中填写淘汰原因')
            if old['status'] not in SUCCESS|{'failed'}:
                raise ValueError('这张图尚未生成分割，请先完成分割')
            direction=payload.get('head_side',old.get('head_side','unknown'))
            if direction not in {'left','right','up','down','unknown'}:
                raise ValueError('头侧方向无效')
            meta={**old,'decision':decision,'notes':note,'revision':old.get('revision',0)+1,
                  'head_side':direction,'decision_at':stamp()}
            history=list(old.get('decision_history',[]))
            history.append({'at':meta['decision_at'],'decision':decision,'notes':note,'head_side':direction})
            meta['decision_history']=history
            atomic_json(self.dest(row)/'segmentation.json',meta)
            return meta
