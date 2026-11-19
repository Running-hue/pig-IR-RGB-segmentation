"""Regression coverage for native RGB/IR coordinates, persistence and inference."""
import base64
from contextlib import nullcontext
import io
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app import Dataset
from core import Image, np, atomic_json, discover, input_fingerprint, mask_stats, png_bytes, read_input
from sam_backend import SAMPredictor
from sam_batch import segment_frame
from workspace import Workspace
from geometry import RGB_VERSION
from geometry_fixture import calibration


class RecordingModel:
    def __init__(self):
        self.calls=[]

    def predict(self,image,prompts,key):
        self.calls.append((image.copy(),key))
        h,w=image.shape[:2]
        self.last_raw_mask=np.zeros((h,w),np.uint8)
        self.last_raw_mask[h//4:3*h//4,w//4:3*w//4]=255
        return self.last_raw_mask.copy(),{'model':'test','seconds':0,'prompts':prompts}


class InputModesTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.root=Path(self.tmp.name)
        self.source=self.root/'raw'
        self.folder=self.source/'pig_1'/'capture'
        self.folder.mkdir(parents=True)
        y,x=np.indices((80,100))
        ir=np.where(((x-55)/32)**2+((y-40)/23)**2<1,1000,20).astype(np.uint16)
        np.save(self.folder/'infrared.npy',ir)
        np.save(self.folder/'depth_mm.npy',np.full(ir.shape,900,np.uint16))
        Image.fromarray(ir).save(self.folder/'infrared.png')
        self.rgb=np.zeros((120,180,3),np.uint8)
        self.rgb[:]=[25,70,140]
        self.rgb[20:100,30:150]=[215,165,120]
        Image.fromarray(self.rgb).save(self.folder/'color.png')
        np.save(self.folder/'depth_aligned_to_color_mm.npy',np.full(self.rgb.shape[:2],1500,np.uint16))
        calibration(self.folder)
        self.parent=self.root/'results';self.parent.mkdir()
        self.model=RecordingModel()
        self.workspace=Workspace(self.root/'state',model_factory=lambda:self.model)

    def tearDown(self):
        if self.workspace.worker:self.workspace.worker.join(timeout=10)
        if self.workspace.export_worker:self.workspace.export_worker.join(timeout=10)
        self.tmp.cleanup()

    def dataset(self,mode):
        output=self.parent/mode;output.mkdir()
        atomic_json(output/'dataset.json',{'source':str(self.source),'input_mode':mode,
                                         'geometry_version':RGB_VERSION if mode=='rgb' else None,
                                         'backend':'sam2','frames':discover(self.source,mode)})
        return Dataset(self.source,output)

    def payload(self,d,mask,**extra):
        return {'id':d.rows[0]['id'],'revision':0,'head_side':'left',
                'mask':'data:image/png;base64,'+base64.b64encode(png_bytes(mask)).decode(),**extra}

    def test_rgb_color_channels_and_native_dimensions_reach_model_and_saved_mask(self):
        d=self.dataset('rgb');d.sam=self.model
        result=d.predict_sam({'id':d.rows[0]['id'],'prompts':{'points':[[90,60]],'labels':[1]}})
        image,key=self.model.calls[-1]
        self.assertTrue(np.array_equal(image,self.rgb));self.assertTrue(key.startswith('rgb:'))
        self.assertEqual(d.detail(d.rows[0]['id'])['width'],180)
        self.assertFalse((d.dest(d.rows[0])/'segmentation.json').exists())
        meta=d.save(self.payload(d,self.model.last_raw_mask,sam_prediction_id=result['prediction_id'],reviewed=True))
        self.assertEqual(meta['input_mode'],'rgb');self.assertEqual(meta['shape_hw'],[120,180])
        self.assertEqual(meta['coordinate_space'],'aligned_color')
        self.assertEqual(meta['depth_file'],'depth_aligned_to_color_mm.npy')
        self.assertEqual(set(meta['source_sha256']),{'color.png','depth_aligned_to_color_mm.npy','reconstruction.json'})
        self.assertEqual(meta['stats']['valid_depth_ratio'],1.)
        with Image.open(d.dest(d.rows[0])/'overlay.png') as overlay:
            self.assertEqual(overlay.size,(180,120))
            self.assertEqual(overlay.getpixel((0,0)),tuple(self.rgb[0,0]))
        with self.assertRaisesRegex(ValueError,'尺寸'):
            d.save(self.payload(d,np.full((80,100),255,np.uint8),revision=1))
        self.assertEqual(Dataset(self.source,d.output).input_mode,'rgb')

    def test_ir_and_rgb_inference_cache_keys_are_distinct_for_same_capture(self):
        for mode,shape in [('infrared',(80,100)),('rgb',(120,180,3))]:
            d=self.dataset(mode);d.sam=self.model
            d.predict_sam({'id':d.rows[0]['id'],'prompts':{}})
            self.assertEqual(self.model.calls[-1][0].shape,shape)
        self.assertNotEqual(self.model.calls[0][1],self.model.calls[1][1])

    def test_rgb_only_capture_without_depth_or_ir_can_be_segmented_and_reviewed(self):
        for f in self.folder.iterdir():
            if f.name not in {'color.png','reconstruction.json'}:f.unlink()
        self.assertEqual(discover(self.source),[])
        self.assertEqual(self.workspace.scan(str(self.source),'rgb')['frames'],1)
        d=self.dataset('rgb');row=d.rows[0]
        image,depth=read_input(self.folder,'rgb')
        self.assertIsNone(depth)
        meta=d.save(self.payload(d,np.full(image.shape[:2],255,np.uint8)))
        self.assertIsNone(meta['stats']['valid_depth_ratio'])
        self.assertTrue(any('缺少对齐' in w for w in meta['stats']['warnings']))
        self.assertFalse(d.detail(row['id'])['reference_available'])
        self.assertFalse(d.detail(row['id'])['depth_available'])
        with Image.open(io.BytesIO(d.media(row['id'],'base'))) as im:self.assertEqual(im.size,(180,120))

    def test_wrong_aligned_depth_is_rejected_instead_of_resized_or_replaced(self):
        np.save(self.folder/'depth_aligned_to_color_mm.npy',np.ones((80,100),np.uint16))
        with self.assertRaisesRegex(ValueError,'对齐 RGB'):
            read_input(self.folder,'rgb')

    def test_color_source_changes_block_save_and_missing_mode_keeps_legacy_ir(self):
        d=self.dataset('rgb')
        mask=np.full((120,180),255,np.uint8)
        d.save(self.payload(d,mask))
        Image.fromarray(np.zeros_like(self.rgb)).save(self.folder/'color.png')
        with self.assertRaisesRegex(ValueError,'发生变化'):d.save(self.payload(d,mask,revision=1))
        old=self.parent/'legacy';old.mkdir()
        atomic_json(old/'dataset.json',{'source':str(self.source),'frames':discover(self.source)})
        legacy=Dataset(self.source,old)
        self.assertEqual(legacy.input_mode,'infrared')
        self.assertEqual(legacy.detail(legacy.rows[0]['id'])['width'],100)

    def test_batch_reopen_export_and_csv_retain_rgb_mode(self):
        before={f.name:f.read_bytes() for f in self.folder.iterdir()}
        seed=np.full((120,180),255,np.uint8)
        with patch('sam_backend.ready',return_value=True),patch('sam_batch.rgb_seed',return_value=seed):
            data=self.workspace.start({'source':str(self.source),'parent':str(self.parent),
                                       'name':'rgb_batch','input_mode':'rgb'})
            self.workspace.worker.join(timeout=10)
        self.assertEqual(self.workspace.job['success'],1,self.workspace.job)
        d=self.workspace.dataset;row=d.rows[0]
        self.assertEqual(data['input_mode'],'rgb')
        meta=d.metadata(row)
        self.assertEqual(meta['input_mode'],'rgb')
        self.assertEqual(meta['shape_hw'],[120,180])
        self.assertTrue((d.dest(row)/'color_display.png').is_file())
        self.assertFalse((d.dest(row)/'infrared_display.png').exists())
        d.decide({'id':row['id'],'revision':0,'decision':'keep'})
        self.workspace.start_export({'dataset_id':d.dataset_id,'parent':str(self.parent),'name':'export','scope':'keep'})
        self.workspace.export_worker.join(timeout=10)
        self.assertEqual(self.workspace.export_job['status'],'completed',self.workspace.export_job)
        exported=self.parent/'export'
        self.assertEqual(json.loads((exported/'dataset.json').read_text())['input_mode'],'rgb')
        self.assertIn('RGB',(exported/'筛选记录.csv').read_text())
        self.assertEqual(self.workspace.open_results(str(exported))['input_mode'],'rgb')
        self.assertEqual(Workspace(self.root/'state').dataset.input_mode,'rgb')
        self.assertEqual(before,{f.name:f.read_bytes() for f in self.folder.iterdir()})

    def test_mode_validation_and_mixed_coordinate_metadata_are_rejected(self):
        with self.assertRaises(ValueError):self.workspace.scan(str(self.source),'bad')
        d=self.dataset('rgb')
        atomic_json(d.dest(d.rows[0])/'segmentation.json',{'status':'draft','input_mode':'infrared'})
        with self.assertRaisesRegex(ValueError,'不一致'):d.listing()

    def test_predictor_passes_rgb_channels_unchanged_and_expands_only_grayscale(self):
        # Exercise the real adapter without allocating SAM's neural network.
        model=SAMPredictor.__new__(SAMPredictor)
        class Torch:
            inference_mode=staticmethod(nullcontext)
        class Predictor:
            def set_image(self,image):self.image=image.copy()
            def predict(self,**kw):
                return np.ones((1,*self.image.shape[:2]),bool),np.array([.9]),None
        model.torch=Torch();model.predictor=Predictor();model.current=None
        model.lock=threading.RLock();model.device='cpu';model.checkpoint_sha256='test'
        for image in [self.rgb,read_input(self.folder)[0]]:
            mask,_=model.predict(image,{'points':[[50,40]],'labels':[1]},key=None)
            expected=image if image.ndim==3 else np.repeat(image[...,None],3,axis=2)
            self.assertTrue(np.array_equal(model.predictor.image,expected))
            self.assertEqual(mask.shape,image.shape[:2])


if __name__=='__main__':unittest.main()
