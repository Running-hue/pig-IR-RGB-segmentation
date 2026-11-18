#!/usr/bin/env python3
"""Loopback-only mask editor; source data are opened read-only."""
from __future__ import annotations

import argparse
import base64
import io
import json
from pathlib import Path
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse
import webbrowser

from core import (Image, np, atomic_bytes, atomic_json, discover, input_fingerprint,
                  mask_stats, overlay, png_bytes, read_frame, stamp, validate_output,
                  write_draft)

APP_DIR = Path(__file__).resolve().parent


class Dataset:
    def __init__(self, source, output, rows=None):
        self.source, self.output = validate_output(source, output)
        self.rows = discover(self.source) if rows is None else rows
        self.by_id = {r['id']: r for r in self.rows}
        self.dataset_id = secrets.token_urlsafe(16)
        self.lock = threading.RLock()
        self.sam = None
        self.sam_predictions = {}

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
            return json.loads(p.read_text())
        return {**row, 'status': 'pending', 'reviewed': False, 'revision': 0, 'stats': {}}

    def listing(self):
        with self.lock:
            rows = []
            for r in self.rows:
                meta = self.metadata(r)
                rows.append({**r, **{k:meta.get(k) for k in ['status','reviewed','revision']},
                             'decision':meta.get('decision','pending')})
        from sam_backend import ready
        dataset_file = self.output/'dataset.json'
        backend = json.loads(dataset_file.read_text()).get('backend','grabcut') if dataset_file.exists() else 'grabcut'
        return {'source': str(self.source), 'output': str(self.output), 'frames': rows,
                'dataset_id':self.dataset_id,
                'backend':backend, 'sam_ready':ready(),
                'pigs': len({r['pig_id'] for r in rows})}

    def detail(self, frame_id):
        with self.lock:
            row = self.row(frame_id)
            meta = self.metadata(row)
            gray, _ = read_frame(row['source_dir'])
            return {**meta, 'revision': meta.get('revision', 0),
                    'width': gray.shape[1], 'height': gray.shape[0]}

    def media(self, frame_id, kind):
        row = self.row(frame_id)
        dest = self.dest(row)
        if kind in {'color', 'depth'}:
            path = Path(row['source_dir'])/('color.png' if kind == 'color' else 'depth_preview.png')
            return path.read_bytes()
        if kind == 'infrared':
            path = dest/'infrared_display.png'
            return path.read_bytes() if path.exists() else png_bytes(read_frame(row['source_dir'])[0])
        if kind == 'mask':
            meta = self.metadata(row)
            name = 'trunk_mask_working.png' if meta.get('status') in {'corrected','reviewed'} else 'trunk_mask_draft.png'
            path = dest/name
            if not path.exists():
                gray, _ = read_frame(row['source_dir'])
                return png_bytes(np.zeros(gray.shape, np.uint8))
            return path.read_bytes()
        if kind == 'draft':
            return (dest/'trunk_mask_draft.png').read_bytes()
        raise ValueError('不支持的图片类型')

    def generate(self, frame_id):
        with self.lock:
            row = self.row(frame_id)
            meta = self.metadata(row)
            if meta['status'] in {'corrected', 'reviewed'}:
                raise ValueError('已有人工修正；请使用“恢复自动初稿”在画布中比较，避免覆盖进度')
            return write_draft(row, self.output)

    def predict_sam(self, payload):
        # Inference changes only the preview; saving remains a separate action.
        with self.lock:
            row = self.row(payload['id'])
            gray, _ = read_frame(row['source_dir'])
            if self.sam is None:
                from sam_backend import SAMPredictor
                self.sam = SAMPredictor()
            mask, info = self.sam.predict(gray, payload.get('prompts',{}), key=str(Path(row['source_dir']).resolve()))
            if not mask.any():
                raise ValueError('SAM 返回空掩码，请调整提示点或框')
            prediction_id = secrets.token_urlsafe(24)
            self.sam_predictions[prediction_id] = {'frame_id':row['id'], 'info':info,
                                                  'raw':png_bytes(self.sam.last_raw_mask)}
            while len(self.sam_predictions)>32:
                self.sam_predictions.pop(next(iter(self.sam_predictions)))
            return {'mask':'data:image/png;base64,'+base64.b64encode(png_bytes(mask)).decode(),
                    'prediction_id':prediction_id, 'model_info':info}

    def save(self, payload):
        with self.lock:
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
            gray, depth = read_frame(row['source_dir'])
            encoded = payload.get('mask', '')
            if not encoded.startswith('data:image/png;base64,'):
                raise ValueError('掩码必须是 PNG 图片')
            raw = base64.b64decode(encoded.split(',', 1)[1], validate=True)
            im = Image.open(io.BytesIO(raw))
            if im.size != (gray.shape[1], gray.shape[0]):
                raise ValueError('掩码尺寸必须与原始红外图完全相同')
            mask = np.asarray(im.convert('L'))
            if not np.isin(mask, [0, 255]).all():
                raise ValueError('掩码像素只能是 0 和 255')
            if not mask.any():
                raise ValueError('掩码为空，请先勾画可见躯干')
            reviewed = payload.get('reviewed') is True
            direction = payload.get('head_side', 'unknown')
            if direction not in {'left','right','up','down','unknown'}:
                raise ValueError('头侧方向无效')
            if reviewed and direction == 'unknown':
                raise ValueError('确认合格前，请记录图中的头侧方向')
            hashes = input_fingerprint(row['source_dir'])
            if old.get('source_sha256') and old['source_sha256'] != hashes:
                raise ValueError('原始红外或深度文件发生变化，请先核查数据源')
            dest = self.dest(row)
            mask_png = png_bytes(mask)
            overlay_png = png_bytes(overlay(gray, mask))
            meta = {**old, **row, 'status': 'reviewed' if reviewed else 'corrected',
                    'reviewed': reviewed, 'revision': revision+1, 'saved_at': stamp(),
                    'head_side': direction, 'notes': str(payload.get('notes',''))[:4000],
                    'shape_hw': list(mask.shape), 'source_sha256': hashes,
                    'stats': mask_stats(mask, depth),
                    'initial_method': old.get('initial_method', old.get('method', 'manual')),
                    'method': 'manual_correction'}
            if prediction:
                meta['last_sam_prediction'] = prediction['info']
                meta['method'] = 'sam2_assisted_manual_correction'
            if reviewed:
                meta['stats']['warnings'] = [w for w in meta['stats']['warnings'] if '待人工确认' not in w]
            # Each saved revision is retained independently.
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
            if old['status']=='pending':
                raise ValueError('这张图尚未生成分割，请先完成分割')
            meta={**old,'decision':decision,'notes':note,'revision':old.get('revision',0)+1,
                  'decision_at':stamp()}
            history=list(old.get('decision_history',[]))
            history.append({'at':meta['decision_at'],'decision':decision,'notes':note})
            meta['decision_history']=history
            atomic_json(self.dest(row)/'segmentation.json',meta)
            return meta


def make_handler(workspace, token):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            if args and str(args[0]).startswith('POST'):
                super().log_message(fmt, *args)

        def send(self, body, mime='application/json', status=200):
            if not isinstance(body, bytes):
                body = json.dumps(body, ensure_ascii=False).encode('utf-8')
            self.send_response(status)
            self.send_header('Content-Type', mime)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('X-Frame-Options', 'DENY')
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            try:
                url = urlparse(self.path)
                q = parse_qs(url.query)
                if url.path == '/':
                    page = (APP_DIR/'index.html').read_text().replace('__TOKEN__', token)
                    self.send(page.encode(), 'text/html; charset=utf-8')
                elif url.path=='/workflow.js':
                    self.send((APP_DIR/'workflow.js').read_bytes(),'text/javascript; charset=utf-8')
                elif url.path == '/api/captures':
                    self.send(workspace.listing())
                elif url.path == '/api/job':
                    with workspace.lock:
                        self.send({'job':workspace.job,'export_job':workspace.export_job})
                elif url.path in {'/api/frame','/api/image','/api/review.csv'}:
                    with workspace.lock:
                        dataset=workspace.require_dataset(q.get('dataset_id',[''])[0])
                        if url.path=='/api/frame':
                            self.send(dataset.detail(q['id'][0]))
                        elif url.path=='/api/image':
                            self.send(dataset.media(q['id'][0], q['kind'][0]), 'image/png')
                        else:
                            self.send(workspace.csv(dataset.dataset_id),'text/csv; charset=utf-8')
                else:
                    self.send({'error': '页面不存在'}, status=404)
            except (ValueError, KeyError, OSError, TypeError) as e:
                self.send({'error': str(e)}, status=400)

        def do_POST(self):
            try:
                if self.headers.get('X-App-Token') != token:
                    self.send({'error': '请从本机工作台页面操作'}, status=403)
                    return
                origin = self.headers.get('Origin')
                if origin and origin != f'http://127.0.0.1:{self.server.server_port}':
                    self.send({'error': '不支持跨站请求'}, status=403)
                    return
                length = int(self.headers.get('Content-Length', '0'))
                if length <= 0 or length > 3_000_000:
                    raise ValueError('请求大小不合法')
                if not self.headers.get('Content-Type', '').startswith('application/json'):
                    raise ValueError('需要 JSON 请求')
                payload = json.loads(self.rfile.read(length))
                if self.path=='/api/directories':
                    self.send(workspace.directories(payload.get('path')))
                elif self.path=='/api/choose_directory':
                    from native_picker import choose_directory
                    self.send(choose_directory(payload.get('path'),payload.get('title','选择文件夹')))
                elif self.path=='/api/export':
                    self.send(workspace.start_export(payload))
                elif self.path=='/api/scan':
                    self.send(workspace.scan(payload.get('source')))
                elif self.path=='/api/open_results':
                    self.send(workspace.open_results(payload.get('output'),payload.get('source_override')))
                elif self.path=='/api/batch/start':
                    self.send(workspace.start(payload))
                elif self.path=='/api/batch/stop':
                    with workspace.lock:
                        if not workspace.job or payload.get('job_id')!=workspace.job['id']:
                            raise ValueError('任务已经切换，请刷新页面')
                        self.send({'job':workspace.stop()})
                elif self.path in {'/api/save','/api/generate','/api/sam_predict','/api/decide'}:
                    with workspace.lock:
                        workspace._idle()
                        dataset=workspace.require_dataset(payload.get('dataset_id'))
                        if self.path=='/api/save':
                            self.send(dataset.save(payload))
                        elif self.path=='/api/generate':
                            self.send(dataset.generate(payload['id']))
                        elif self.path=='/api/decide':
                            self.send(dataset.decide(payload))
                        else:
                            dataset.sam=workspace.get_model()
                            self.send(dataset.predict_sam(payload))
                else:
                    self.send({'error': '接口不存在'}, status=404)
            except (ValueError, KeyError, OSError, TypeError) as e:
                self.send({'error': str(e)}, status=400)
            except Exception as e:
                self.send({'error':f'操作失败：{e}'},status=500)

    return Handler


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', type=Path)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--state',type=Path,default=APP_DIR/'.state')
    parser.add_argument('--port', type=int, default=8768)
    parser.add_argument('--open', action='store_true')
    args = parser.parse_args()
    from workspace import Workspace
    workspace=Workspace(args.state,initial_output=args.output)
    if args.source and args.output:
        workspace.open_results(str(args.output),str(args.source))
    server = ThreadingHTTPServer(('127.0.0.1', args.port), make_handler(workspace, secrets.token_urlsafe(32)))
    address = f'http://127.0.0.1:{server.server_port}'
    print(f'iToF 躯干分割工作台：{address}\n在浏览器选择原始目录，或打开已有成果目录。', flush=True)
    if args.open:
        webbrowser.open(address)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print('\n分割工作台已停止；已保存进度保留。')
    finally:
        workspace.stop()
        if workspace.worker:
            workspace.worker.join()
        server.server_close()
