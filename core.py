"""iToF infrared segmentation. All generated files live outside the source tree."""
from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path
import sys
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).resolve().parent / '.deps'))
import cv2
import numpy as np
from PIL import Image

VERSION = 'ir-depth-grabcut-v1'
cv2.setNumThreads(1)


def stamp():
    return datetime.now(timezone.utc).isoformat()


def validate_output(source, output):
    source, output = Path(source).resolve(), Path(output).resolve()
    if source == output or source in output.parents or output in source.parents:
        raise ValueError('输出目录与原始数据必须相互独立，不能相同或互相包含')
    return source, output


def atomic_bytes(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name('.' + path.name + '.tmp')
    tmp.write_bytes(data)
    os.replace(tmp, path)


def atomic_json(path, obj):
    atomic_bytes(path, json.dumps(obj, ensure_ascii=False, indent=2).encode('utf-8'))


def png_bytes(array):
    buf = io.BytesIO()
    Image.fromarray(array).save(buf, format='PNG')
    return buf.getvalue()


def normalize_ir(ir):
    valid = np.isfinite(ir) & (ir > 0)
    if not valid.any():
        raise ValueError('红外数据没有有效像素')
    lo, hi = np.percentile(ir[valid], [1, 99.5])
    if hi <= lo:
        raise ValueError('红外图缺少可分割的亮度变化')
    x = np.clip((ir.astype(np.float32) - lo) / (hi - lo), 0, 1)
    return (np.power(x, .65) * 255).astype(np.uint8)


def read_frame(folder):
    folder = Path(folder)
    ir = np.load(folder / 'infrared.npy', allow_pickle=False)
    depth = np.load(folder / 'depth_mm.npy', allow_pickle=False)
    if ir.ndim != 2 or depth.shape != ir.shape:
        raise ValueError('红外与原始深度数组必须是同尺寸二维数组')
    with Image.open(folder / 'infrared.png') as preview:
        if preview.size != (ir.shape[1], ir.shape[0]):
            raise ValueError('红外 PNG 与 NPY 尺寸不一致')
    return normalize_ir(ir), depth


def kernel(n):
    return cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (n, n))


def central_component(binary):
    n, labels, stats, centers = cv2.connectedComponentsWithStats(binary.astype(np.uint8), 8)
    if n <= 1:
        raise ValueError('没有找到猪体候选区域')
    h, w = binary.shape
    yy, xx = np.indices((h, w))
    central = ((xx / w - .56) / .32)**2 + ((yy / h - .48) / .22)**2 < 1
    best, score = None, -1
    for i in range(1, n):
        area = int(stats[i, cv2.CC_STAT_AREA])
        if area < h*w*.008:
            continue
        overlap = int(((labels == i) & central).sum())
        s = overlap + area * .12
        if s > score:
            best, score = i, s
    if best is None:
        raise ValueError('未找到足够大的猪体候选区域')
    return (labels == best).astype(np.uint8)


def fill_holes(mask):
    # Fill enclosed holes only; retain the outer contour, including concavities.
    inv = (1 - mask).astype(np.uint8)
    padded = np.pad(inv, 1, constant_values=1)
    cv2.floodFill(padded, None, (0, 0), 2)
    return ((mask > 0) | (padded[1:-1, 1:-1] == 1)).astype(np.uint8)


def auto_segment(gray, depth):
    cv2.setRNGSeed(7)
    h, w = gray.shape
    blurred = cv2.GaussianBlur(gray, (7, 7), 0)
    threshold, bright = cv2.threshold(blurred, 0, 1, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    bright = cv2.morphologyEx(bright, cv2.MORPH_OPEN, kernel(9))
    initial = fill_holes(central_component(bright))
    initial = cv2.morphologyEx(initial, cv2.MORPH_CLOSE, kernel(9))
    core = cv2.erode(initial, kernel(23)) > 0
    valid_depth = np.isfinite(depth) & (depth > 0)
    vals = depth[core & valid_depth]
    if vals.size < 20:
        raise ValueError('候选猪体内有效深度太少，需要手工分割')
    zlo, zhi = np.percentile(vals, [3, 97])
    nearby = valid_depth & (depth >= max(1, zlo - 160)) & (depth <= zhi + 230)
    # Depth helps initialize the graph; missing depth does not erase the IR silhouette.
    support = cv2.dilate(initial, kernel(35)) > 0
    seeds = np.zeros((h, w), np.uint8)
    seeds[support] = cv2.GC_PR_BGD
    seeds[(initial > 0) | (support & nearby & (blurred > threshold*.55))] = cv2.GC_PR_FGD
    seeds[core & nearby] = cv2.GC_FGD
    seeds[:3, :] = seeds[-3:, :] = cv2.GC_BGD
    seeds[:, :3] = seeds[:, -3:] = cv2.GC_BGD
    if not (seeds == cv2.GC_FGD).any():
        raise ValueError('无法构建前景提示，请手工勾选目标')
    depth_feature = np.clip((depth.astype(np.float32) - zlo + 250) / max(800, zhi-zlo+500), 0, 1)
    depth_feature[~valid_depth] = .5
    feature = np.dstack([gray, gray, (depth_feature*255).astype(np.uint8)])
    bg, fg = np.zeros((1, 65)), np.zeros((1, 65))
    cv2.grabCut(feature, seeds, None, bg, fg, 4, cv2.GC_INIT_WITH_MASK)
    mask = np.isin(seeds, [cv2.GC_FGD, cv2.GC_PR_FGD]).astype(np.uint8)
    mask = fill_holes(central_component(mask))
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel(9))
    mask = fill_holes(central_component(mask))
    return mask * 255


def overlay(gray, mask):
    rgb = np.repeat(gray[..., None], 3, axis=2)
    fg = mask > 0
    rgb[fg] = (rgb[fg]*.60 + np.array([35, 222, 166])*.40).astype(np.uint8)
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    cv2.drawContours(rgb, contours, -1, (58, 255, 193), 2)
    return rgb


def mask_stats(mask, depth):
    area = int((mask > 0).sum())
    valid = (mask > 0) & np.isfinite(depth) & (depth > 0)
    n, _ = cv2.connectedComponents((mask > 0).astype(np.uint8), 8)
    touches = bool(mask[0].any() or mask[-1].any() or mask[:, 0].any() or mask[:, -1].any())
    warnings = ['头颈、尾根及遮挡边界待人工确认']
    if touches:
        warnings.append('候选区域触及图像边界，检查是否出画')
    if n > 2:
        warnings.append('存在多个分离区域，请检查是否混入其他猪或背景')
    if area and valid.sum()/area < .95:
        warnings.append('掩码内存在较多无效深度，后续点云需单独质检')
    return {'area_px': area, 'area_ratio': area/mask.size,
            'valid_depth_ratio': float(valid.sum()/area) if area else 0,
            'components': n-1, 'touches_image_border': touches, 'warnings': warnings}


def discover(source):
    source = Path(source).resolve()
    if not source.is_dir():
        raise ValueError('数据目录不存在')
    frames = []
    for p in sorted(source.rglob('infrared.npy')):
        if p.is_symlink() or any(part.startswith('.') for part in p.relative_to(source).parts):
            continue
        folder = p.parent
        # Do not follow source-directory symlinks or allow source read escapes.
        if folder.resolve() != folder or any(q.is_symlink() for q in folder.iterdir()):
            continue
        if not (folder/'infrared.png').is_file() or not (folder/'depth_mm.npy').is_file():
            continue
        rel = folder.relative_to(source).as_posix()
        pig = next((v[4:] for v in reversed(folder.relative_to(source).parts) if v.startswith('pig_')), '未知')
        frames.append({'id': hashlib.sha256(rel.encode()).hexdigest()[:20], 'relative_dir': rel,
                       'pig_id': pig, 'capture_id': folder.name, 'source_dir': str(folder)})
    frames.sort(key=lambda r: (int(r['pig_id']) if r['pig_id'].isdigit() else 10**9, r['capture_id']))
    return frames


def input_fingerprint(folder):
    return {name: hashlib.sha256((Path(folder)/name).read_bytes()).hexdigest()
            for name in ['infrared.npy', 'infrared.png', 'depth_mm.npy']}


def write_draft(row, output):
    gray, depth = read_frame(row['source_dir'])
    mask = auto_segment(gray, depth)
    dest = Path(output)/row['relative_dir']
    atomic_bytes(dest/'infrared_display.png', png_bytes(gray))
    atomic_bytes(dest/'trunk_mask_draft.png', png_bytes(mask))
    atomic_bytes(dest/'overlay_draft.png', png_bytes(overlay(gray, mask)))
    meta = {**row, 'method': VERSION, 'status': 'draft', 'reviewed': False,
            'created_at': stamp(), 'shape_hw': list(gray.shape),
            'source_sha256': input_fingerprint(row['source_dir']),
            'mask_definition': '可见躯干候选；头颈、耳、尾、腿和遮挡边界须人工修正',
            'stats': mask_stats(mask, depth)}
    atomic_json(dest/'segmentation.json', meta)
    return meta
