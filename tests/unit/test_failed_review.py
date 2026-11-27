"""Failed captures can be reviewed without weakening the mask/depth geometry contract."""
import csv
import io
import json
import unittest
from backend.dataset import Dataset
from backend.core import Image, np
from tests.unit import test_input_modes as fixtures


class FailedReviewTests(unittest.TestCase):
    setUp=fixtures.InputModesTests.setUp
    tearDown=fixtures.InputModesTests.tearDown
    dataset=fixtures.InputModesTests.dataset
    payload=fixtures.InputModesTests.payload

    def test_rgb_profile_mismatch_uses_raw_preview_and_keeps_inference_strict(self):
        Image.fromarray(self.rgb.transpose(1,0,2)).save(self.folder/'color.png')
        d=self.dataset('rgb');row=d.rows[0];d.sam=self.model
        with self.assertRaisesRegex(ValueError,'尺寸'):d.detail(row['id'])  # No general geometry bypass.
        d.record_failure(row,'原始 RGB 尺寸与实际彩色流 profile 不符')
        before={p:p.read_bytes() for p in self.folder.iterdir()}
        detail=d.detail(row['id'])
        self.assertTrue(detail['review_only']);self.assertTrue(detail['image_available'])
        self.assertFalse(detail['depth_available']);self.assertFalse(detail['draft_available'])
        self.assertFalse(detail['geometry']['pointcloud_ready'])
        self.assertEqual((detail['width'],detail['height']),(120,180))
        with Image.open(io.BytesIO(d.media(row['id'],'base'))) as im:
            np.testing.assert_array_equal(np.asarray(im),self.rgb.transpose(1,0,2))
        with Image.open(io.BytesIO(d.media(row['id'],'mask'))) as im:
            self.assertEqual(im.size,(120,180));self.assertFalse(np.asarray(im).any())
        meta=d.decide({'id':row['id'],'revision':0,'decision':'reject','notes':'尺寸异常，需核查原图','head_side':'up'})
        self.assertEqual(meta['status'],'failed');self.assertFalse(meta['reviewed'])
        self.assertIn('尺寸',meta['segmentation_error'])
        self.assertTrue(d.detail(row['id'])['review_only'])
        with self.assertRaisesRegex(ValueError,'重新载入'):
            d.decide({'id':row['id'],'revision':0,'decision':'keep'})
        with self.assertRaisesRegex(ValueError,'尺寸'):d.predict_sam({'id':row['id']})
        with self.assertRaisesRegex(ValueError,'尺寸'):
            d.save(self.payload(d,np.ones((180,120),np.uint8)*255,revision=1,reviewed=True))
        self.assertEqual(self.model.calls,[])
        self.assertEqual(before,{p:p.read_bytes() for p in self.folder.iterdir()})
        self.assertEqual(list(d.dest(row).glob('*mask*.png')),[])

    def test_inference_failure_can_be_screened_then_recovered_with_sam(self):
        d=self.dataset('rgb');row=d.rows[0];d.sam=self.model
        d.record_failure(row,'未找到猪体')
        detail=d.detail(row['id'])
        self.assertFalse(detail['review_only']);self.assertTrue(detail['depth_available'])
        self.assertFalse(detail['draft_available'])
        screened=d.decide({'id':row['id'],'revision':0,'decision':'keep','head_side':'left','notes':'人工补做'})
        self.assertEqual(screened['status'],'failed');self.assertFalse(screened['reviewed'])
        self.assertEqual(d.failure_records()[0]['failure_status'],'failed')
        predicted=d.predict_sam({'id':row['id'],'prompts':{'points':[[90,60]],'labels':[1]}})
        saved=d.save(self.payload(d,self.model.last_raw_mask,revision=1,reviewed=True,
                                  sam_prediction_id=predicted['prediction_id'],notes='人工补做'))
        self.assertEqual(saved['status'],'reviewed');self.assertEqual(saved['decision'],'keep')
        self.assertEqual(saved['segmentation_error'],'')
        self.assertFalse(d.detail(row['id'])['draft_available'])
        self.assertEqual(d.failure_records()[0]['failure_status'],'resolved')
        self.assertEqual(d.failure_records()[0]['error'],'未找到猪体')
        self.assertTrue((d.dest(row)/'trunk_mask.png').is_file())

    def test_unreadable_raw_and_ir_depth_failures_still_allow_review_records(self):
        d=self.dataset('rgb');row=d.rows[0]
        (self.folder/'color.png').write_bytes(b'broken PNG')
        d.record_failure(row,'无法解码 RGB')
        detail=d.detail(row['id'])
        self.assertTrue(detail['review_only']);self.assertFalse(detail['image_available'])
        self.assertIn('原图也无法读取',detail['review_error'])
        d.decide({'id':row['id'],'revision':0,'decision':'unsure','notes':'重新采集'})
        self.assertEqual(Dataset(d.source,d.output).metadata(row)['notes'],'重新采集')
        ir=self.dataset('infrared');ir_row=ir.rows[0]
        (self.folder/'depth_mm.npy').write_bytes(b'')
        ir.record_failure(ir_row,'深度文件不完整')
        detail=ir.detail(ir_row['id'])
        self.assertTrue(detail['review_only']);self.assertTrue(detail['image_available'])
        self.assertEqual((detail['width'],detail['height']),(100,80))

    def test_failed_review_survives_csv_export_and_reopening_exported_history(self):
        d=self.dataset('rgb');row=d.rows[0]
        d.record_failure(row,'模型未找到猪体')
        with self.assertRaisesRegex(ValueError,'淘汰原因'):
            d.decide({'id':row['id'],'revision':0,'decision':'reject','notes':''})
        with self.assertRaisesRegex(ValueError,'头侧'):
            d.decide({'id':row['id'],'revision':0,'decision':'unsure','head_side':'invalid'})
        d.decide({'id':row['id'],'revision':0,'decision':'reject','notes':'背景遮挡','head_side':'right'})
        self.workspace.open_results(str(d.output));active=self.workspace.dataset
        download=list(csv.DictReader(io.StringIO(self.workspace.csv(active.dataset_id).decode('utf-8-sig'))))
        self.assertEqual(download[0]['筛选'],'淘汰');self.assertEqual(download[0]['头侧方向'],'右侧')
        self.workspace.start_export({'dataset_id':active.dataset_id,'parent':str(self.parent),'name':'report','scope':'reject'})
        self.workspace.export_worker.join(timeout=10)
        self.assertEqual(self.workspace.export_job['status'],'completed',self.workspace.export_job)
        export=self.parent/'report'
        manifest=json.loads((export/'dataset.json').read_text())
        self.assertEqual(manifest['frames'],[])
        for filename in ['筛选记录.csv','分割失败记录.csv']:
            report=list(csv.DictReader(io.StringIO((export/filename).read_text(encoding='utf-8-sig'))))
            self.assertEqual(report[0]['筛选'],'淘汰');self.assertEqual(report[0]['备注'],'背景遮挡')
            self.assertEqual(report[0]['头侧方向'],'右侧')
        history=json.loads((export/'segmentation_failures.json').read_text())['records'][0]
        self.assertEqual(history['decision'],'reject');self.assertEqual(history['failure_status'],'failed')
        # Mixed exports reopen normally; failure-only exports stay reports. Exercise the
        # same out-of-manifest history path when a failure report is later downloaded.
        historical=Dataset(d.source,export,rows=[])
        self.workspace.dataset=historical
        again=list(csv.DictReader(io.StringIO(self.workspace.csv(historical.dataset_id).decode('utf-8-sig'))))
        self.assertEqual(again[0]['筛选'],'淘汰');self.assertEqual(again[0]['备注'],'背景遮挡')
        self.assertEqual(again[0]['头侧方向'],'右侧')


if __name__=='__main__':unittest.main()
