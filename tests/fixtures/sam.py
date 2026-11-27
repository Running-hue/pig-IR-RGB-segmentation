"""Deterministic SAM test double shared by unit tests and browser fixtures."""
from backend.core import np


class RecordingModel:
    def __init__(self):
        self.calls=[]

    def predict(self,image,prompts,key):
        self.calls.append((image.copy(),key))
        h,w=image.shape[:2]
        self.last_raw_mask=np.zeros((h,w),np.uint8)
        self.last_raw_mask[h//4:3*h//4,w//4:3*w//4]=255
        return self.last_raw_mask.copy(),{'model':'test','seconds':0,'prompts':prompts}
