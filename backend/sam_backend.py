"""Official SAM 2.1 image inference with editable pixel-coordinate prompts."""
from __future__ import annotations

import hashlib
import sys
import threading
import time

from .paths import MODEL_DIR, PROJECT_ROOT
REPO_DIR = MODEL_DIR/'sam2'
CHECKPOINT = MODEL_DIR/'sam2.1_hiera_base_plus.pt'
CONFIG = 'configs/sam2.1/sam2.1_hiera_b+.yaml'


def ready():
    return CHECKPOINT.is_file() and (REPO_DIR/'sam2'/CONFIG).is_file()


def default_prompts(mask):
    import numpy as np
    from .core import cv2
    h, w = mask.shape
    ys, xs = np.nonzero(mask)
    if not len(xs):
        return {'box': [int(w*.12), int(h*.16), int(w*.99), int(h*.79)],
                'points': [[int(w*.55), int(h*.48)]], 'labels': [1]}
    distance = cv2.distanceTransform((mask>0).astype(np.uint8), cv2.DIST_L2, 5)
    y,x = np.unravel_index(np.argmax(distance),distance.shape)
    # Only suggest an interior foreground point. Do not assume that points just
    # outside an imperfect old mask are background; that can contradict the pig.
    return {'box': None, 'points': [[int(x),int(y)]], 'labels': [1]}


class SAMPredictor:
    def __init__(self, device='auto'):
        if not ready():
            raise ValueError('SAM 2.1 官方代码或模型权重尚未准备好')
        sys.path.insert(0, str(PROJECT_ROOT / '.model-deps'))
        sys.path.insert(0, str(REPO_DIR))
        import torch
        from sam2.build_sam import build_sam2
        from sam2.sam2_image_predictor import SAM2ImagePredictor
        torch.set_num_threads(4)
        self.torch = torch
        self.device = ('mps' if torch.backends.mps.is_available() else 'cpu') if device == 'auto' else device
        self.model = build_sam2(CONFIG, str(CHECKPOINT), device=self.device)
        self.predictor = SAM2ImagePredictor(self.model)
        self.current = None
        self.lock = threading.RLock()
        self.checkpoint_sha256 = hashlib.sha256(CHECKPOINT.read_bytes()).hexdigest()

    def predict(self, gray, prompts, key=None):
        import numpy as np
        points = np.asarray(prompts.get('points',[]),dtype=np.float32)
        labels = np.asarray(prompts.get('labels',[]),dtype=np.int32)
        box = prompts.get('box')
        if gray.dtype != np.uint8 or not (gray.ndim == 2 or (gray.ndim == 3 and gray.shape[2] == 3)):
            raise ValueError('SAM 输入必须是 uint8 灰度图或三通道 RGB 图')
        h,w = gray.shape[:2]
        if len(points):
            if points.ndim != 2 or points.shape[1] != 2 or labels.shape != (len(points),):
                raise ValueError('点提示必须是坐标和对应的前景／背景标签')
            if len(points)>200 or not np.isfinite(points).all() or not np.isin(labels,[0,1]).all():
                raise ValueError('点提示数量或坐标无效')
            if (points<0).any() or (points[:,0]>=w).any() or (points[:,1]>=h).any():
                raise ValueError('提示点超出图像范围')
        if box is not None:
            box = np.asarray(box,dtype=np.float32)
            if box.shape != (4,) or not np.isfinite(box).all() or not (0<=box[0]<box[2]<w and 0<=box[1]<box[3]<h):
                raise ValueError('框选坐标无效')
        if not len(points) and box is None:
            raise ValueError('请至少添加一个前景点，或框选目标猪')
        start = time.perf_counter()
        with self.lock, self.torch.inference_mode():
            if key is None or key != self.current:
                self.predictor.set_image(np.repeat(gray[...,None],3,axis=2) if gray.ndim == 2 else gray)
                self.current = key
            masks,scores,_ = self.predictor.predict(
                point_coords=points if len(points) else None,
                point_labels=labels if len(points) else None,
                box=box, multimask_output=False)
        selected = int(np.argmax(scores))
        mask = (masks[selected] > 0).astype(np.uint8)*255
        self.last_raw_mask = mask.copy()
        # Remove detached predictions without shrinking or smoothing the pig boundary.
        from .core import cv2
        count,components,stats,_ = cv2.connectedComponentsWithStats((mask>0).astype(np.uint8),8)
        if count>1:
            hits = []
            for i in range(1,count):
                n_hits = sum(components[int(p[1]),int(p[0])]==i for p,l in zip(points,labels) if l==1)
                hits.append((n_hits,int(stats[i,cv2.CC_STAT_AREA]),i))
            winner=max(hits)[2]
            mask=(components==winner).astype(np.uint8)*255
        return mask, {'model':'SAM 2.1 Hiera Base Plus', 'device':self.device,
                      'config': CONFIG, 'checkpoint_sha256':self.checkpoint_sha256,
                      'candidate_scores': [float(v) for v in scores],
                      'multimask_output':False,
                      'selected_candidate':selected, 'predicted_quality':float(scores[selected]),
                      'score_note':'模型内部预测质量分数，不是本数据上的实测分割准确率',
                      'postprocessing':'仅保留包含最多前景提示点的连通区域；不腐蚀、不填充遮挡孔洞',
                      'foreground_points_retained':sum(bool(mask[int(p[1]),int(p[0])]) for p,l in zip(points,labels) if l==1),
                      'foreground_points_total':int((labels==1).sum()),
                      'seconds':time.perf_counter()-start,
                      'prompts':{'points':points.tolist(),'labels':labels.tolist(),
                                 'box':box.tolist() if box is not None else None}}
