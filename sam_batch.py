#!/usr/bin/env python3
"""Compare official pretrained SAM 2.1 against the earlier traditional baseline."""
import argparse
import json
from pathlib import Path
import time

from core import (Image, np, atomic_bytes, atomic_json, discover, input_fingerprint,
                  mask_stats, overlay, png_bytes, read_frame, stamp, validate_output, auto_segment,
                  read_input, input_mode, rgb_seed, cv2)
from sam_backend import SAMPredictor, default_prompts, REPO_DIR
from geometry import prepare_frame, write_geometry


def infer_with_seed(model, gray, old, key):
    mask, info = model.predict(gray, default_prompts(old), key=key)
    attempts = [{'info':info, 'raw':model.last_raw_mask.copy()}]
    # A point can select a tattoo or spot rather than the animal. An extremely
    # small result triggers a second, box-only model inference, not mask filling.
    if int((mask>0).sum()) < .35*int((old>0).sum()):
        ys,xs=np.nonzero(old);h,w=old.shape
        box=[max(0,int(xs.min())-40),max(0,int(ys.min())-35),
             min(w-1,int(xs.max())+40),min(h-1,int(ys.max())+35)]
        mask,info=model.predict(gray,{'points':[],'labels':[],'box':box},key=key)
        info['retry_reason']='单点结果小于旧初稿面积的35%，可能只选中了纹理；改用宽松框重新推理，面积比不是准确率'
        info['initial_attempt']=attempts[0]['info']
        attempts.append({'info':info,'raw':model.last_raw_mask.copy()})
    return mask,info,attempts


def segment_frame(row, output, model, baseline=None, mode='infrared'):
    """Run one capture directly from raw data; no earlier result folder required."""
    mode=input_mode(mode)
    gray,depth,geometry,valid=prepare_frame(row['source_dir'],mode)
    hashes=input_fingerprint(row['source_dir'],mode)
    if baseline is not None:
        with Image.open(Path(baseline)/row['relative_dir']/'trunk_mask_draft.png') as im:
            old=np.asarray(im)
        if old.shape != gray.shape[:2]:
            raise ValueError('提示初稿与所选图像尺寸不同，不能跨红外／RGB 坐标使用')
        seed_source='指定的旧初稿，仅提供提示，不限制SAM边界'
    elif mode == 'rgb':
        try:
            old=rgb_seed(gray)
            seed_source='从本帧RGB图临时计算GrabCut候选，仅提供提示；没有读取旧成果目录'
        except (ValueError, cv2.error):
            old=np.zeros(gray.shape[:2],np.uint8)
            seed_source='RGB候选不可用，使用RGB坐标内的默认中心点和宽松框提示；没有读取旧成果目录'
    else:
        old=auto_segment(gray,depth)
        seed_source='从本帧红外和深度临时计算GrabCut候选，仅提供提示；没有读取旧成果目录'
    mask,info,attempts=infer_with_seed(model,gray,old,key=mode+':'+str(Path(row['source_dir']).resolve())+':'+geometry.get('geometry_id',''))
    info['input_mode']=mode
    info['geometry_version']=geometry['version']
    mask=mask.copy();mask[~valid]=0
    if not mask.any():
        raise ValueError('模型返回空掩码，请在工作台中手动点选目标')
    if hashes!=input_fingerprint(row['source_dir'],mode):
        raise ValueError('推理期间源文件发生变化，请检查数据源')
    dest=Path(output)/row['relative_dir']
    meta={**row,'status':'draft','reviewed':False,'revision':0,'created_at':stamp(),
          'method':'sam2.1_hiera_base_plus','model_info':info,'source_sha256':hashes,
          'shape_hw':list(gray.shape[:2]),'stats':mask_stats(mask,depth),
          'input_mode':mode,'coordinate_space':geometry['coordinate_space'],'geometry':geometry,
          'depth_file':'depth_aligned_to_color_mm.npy' if mode=='rgb' and depth is not None else ('depth_mm.npy' if mode=='infrared' else None),
          'mask_definition':'可见目标猪体初稿；用于躯干测量前需人工统一头耳尾腿范围',
          'prompt_source':seed_source+'；单点极小结果用宽松框重试，非人工真值'}
    for name,array in [('color_display.png' if mode=='rgb' else 'infrared_display.png',gray),('trunk_mask_draft.png',mask),
                       ('pig_mask_sam2_raw.png',model.last_raw_mask),('overlay_draft.png',overlay(gray,mask))]:
        atomic_bytes(dest/name,png_bytes(array))
    write_geometry(dest,geometry,valid)
    for i,a in enumerate(attempts,1):
        atomic_bytes(dest/'model_attempts'/f'{i:02d}'/'raw.png',png_bytes(a['raw']))
        atomic_json(dest/'model_attempts'/f'{i:02d}'/'model_info.json',a['info'])
    atomic_json(dest/'segmentation.json',meta)
    return meta


def run(source, output, baseline, pigs=None):
    source, output = validate_output(source, output)
    rows = discover(source)
    if pigs:
        rows = [next(r for r in rows if r['pig_id']==pig) for pig in pigs]
    output.mkdir(parents=True,exist_ok=True)
    atomic_json(output/'dataset.json',{'source':str(source),'output':str(output),'backend':'sam2',
                                     'frames':rows,'created_at':stamp()})
    print('Loading official SAM 2.1 Base Plus…',flush=True)
    model=SAMPredictor()
    print('Loaded device:',model.device,flush=True)
    results,errors=[],[]
    for i,row in enumerate(rows):
        dest=output/row['relative_dir']
        if (dest/'segmentation.json').exists():
            results.append(json.loads((dest/'segmentation.json').read_text()));continue
        try:
            gray,depth,geometry,valid=prepare_frame(row['source_dir'],'infrared')
            hashes=input_fingerprint(row['source_dir'])
            with Image.open(baseline/row['relative_dir']/'trunk_mask_draft.png') as im:
                old=np.asarray(im)
            mask,info,attempts=infer_with_seed(model,gray,old,row['id'])
            if not mask.any():raise ValueError('模型返回空掩码')
            assert hashes==input_fingerprint(row['source_dir'])
            stats=mask_stats(mask,depth)
            meta={**row,'status':'draft','reviewed':False,'revision':0,'created_at':stamp(),
                  'method':'sam2.1_hiera_base_plus','model_info':info,'source_sha256':hashes,
                  'shape_hw':list(gray.shape),'stats':stats,
                  'input_mode':'infrared','coordinate_space':geometry['coordinate_space'],'geometry':geometry,
                  'mask_definition':'模型提取可见目标猪体；用于躯干测量前需排除头耳尾腿并统一截断位置',
                  'prompt_source':'上一版掩码仅用于建议一个内部前景点；没有框或自动背景点，不限制模型输出边界；非人工真值'}
            atomic_bytes(dest/'infrared_display.png',png_bytes(gray))
            atomic_bytes(dest/'trunk_mask_draft.png',png_bytes(mask))
            atomic_bytes(dest/'pig_mask_sam2_raw.png',png_bytes(model.last_raw_mask))
            atomic_bytes(dest/'overlay_draft.png',png_bytes(overlay(gray,mask)))
            write_geometry(dest,geometry,valid)
            atomic_json(dest/'segmentation.json',meta)
            for attempt,details in enumerate(attempts,1):
                atomic_bytes(dest/'model_attempts'/f'{attempt:02d}'/'raw.png',png_bytes(details['raw']))
                atomic_json(dest/'model_attempts'/f'{attempt:02d}'/'model_info.json',details['info'])
            # Side-by-side comparison always uses the same image and image coordinates.
            panel=Image.new('RGB',(gray.shape[1]*3,gray.shape[0]+36),'#17222a')
            from PIL import ImageDraw
            draw=ImageDraw.Draw(panel)
            for col,(im,title) in enumerate([(np.repeat(gray[...,None],3,axis=2),'Infrared'),
                                            (overlay(gray,old),'Previous GrabCut'),
                                            (overlay(gray,mask),'SAM 2.1 (unreviewed)')]):
                panel.paste(Image.fromarray(im),(col*gray.shape[1],36));draw.text((col*gray.shape[1]+12,10),title,fill='white')
            panel.save(dest/'对比.jpg')
            results.append(meta)
            print(f'{i+1}/{len(rows)} pig_{row["pig_id"]}: {info["seconds"]:.2f}s, internal score {info["predicted_quality"]:.3f}',flush=True)
        except Exception as e:
            errors.append({**row,'error':str(e)});print('FAILED',row['relative_dir'],str(e),flush=True)
    atomic_json(output/'summary.json',{'source':str(source),'model':'SAM 2.1 Hiera Base Plus',
                                      'requested':len(rows),'masks':len(results),'reviewed':0,'errors':errors,
                                      'official_code':json.loads((REPO_DIR/'SOURCE.json').read_text()),
                                      'checkpoint_sha256':model.checkpoint_sha256,'device':model.device})
    if errors:raise RuntimeError(f'{len(errors)} 帧推理失败，见 summary.json')


if __name__=='__main__':
    p=argparse.ArgumentParser()
    p.add_argument('--source',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--baseline',type=Path,required=True)
    p.add_argument('--pigs',nargs='*')
    a=p.parse_args();run(a.source,a.output,a.baseline,a.pigs)
