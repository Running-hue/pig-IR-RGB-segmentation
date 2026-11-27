"""Camera provenance and durable failure reports across review/export/reopen."""
import csv
import io
import json
import shutil
import unittest
from tests.unit import test_workspace as fixtures
from backend.core import atomic_json,discover,write_draft
from backend.records import camera_info
from backend.workspace import Workspace


class RecordTests(unittest.TestCase):
    setUp=fixtures.BrowserWorkflowTests.setUp
    tearDown=fixtures.BrowserWorkflowTests.tearDown
    start=fixtures.BrowserWorkflowTests.start
    result=fixtures.BrowserWorkflowTests.result
    export=fixtures.BrowserWorkflowTests.export



    def test_camera_device_wins_over_directory_and_unknown_is_explicit(self):
        folder=self.folder/'stereo';folder.mkdir()
        atomic_json(folder/'reconstruction.json',{'runtime':{'device':{'name':'Orbbec Femto Bolt'}}})
        self.assertEqual(camera_info(folder)['camera_type'],'itof')
        self.assertEqual(camera_info(folder)['camera_identified_by'],'device')
        (folder/'reconstruction.json').unlink()
        self.assertEqual(camera_info(folder)['camera_type'],'stereo')
        self.assertEqual(camera_info(self.folder)['camera_type'],'unknown')


    def test_mixed_camera_rows_are_distinct_even_same_pig_and_capture(self):
        for camera in ['itof-1110','双目']:
            shutil.copytree(self.folder,self.source/camera/'pig_7'/'capture_1')
        rows=discover(self.source)
        self.assertEqual(len({r['id'] for r in rows}),3)
        self.assertEqual({r['camera_type'] for r in rows},{'itof','stereo','unknown'})


    def test_failure_survives_restart_csv_export_and_manual_recovery(self):
        shutil.copytree(self.folder,self.source/'pig_7'/'capture_2')
        def process(row,out,model):
            if row['capture_id']=='capture_2':raise ValueError('测试分割失败：深度无效')
            return write_draft(row,out)
        self.manager.processor=process
        self.start();self.manager.worker.join(timeout=10)
        d=self.manager.dataset
        good,bad=d.rows
        self.assertEqual(d.metadata(bad)['status'],'failed')
        d.decide({'id':good['id'],'revision':0,'decision':'keep','notes':''})
        downloaded=list(csv.DictReader(io.StringIO(self.manager.csv(d.dataset_id).decode('utf-8-sig'))))
        self.assertEqual(downloaded[1]['分割状态'],'failed')
        self.assertIn('深度无效',downloaded[1]['失败原因'])
        self.assertTrue((d.output/'segmentation_failures.json').exists())
        self.assertEqual(len(d.failure_records()),1)  # job + summary + durable record are deduplicated
        restored=Workspace(self.root/'restored',initial_output=d.output)
        self.assertEqual(restored.listing()['frames'][1]['status'],'failed')
        target,_=self.export()
        self.assertEqual(self.manager.export_job['status'],'completed')
        self.assertEqual(len(json.loads((target/'dataset.json').read_text())['frames']),1)
        rows=list(csv.DictReader(io.StringIO((target/'筛选记录.csv').read_text(encoding='utf-8-sig'))))
        self.assertEqual(len(rows),2)
        self.assertEqual(rows[0]['成果已导出'],'是')
        self.assertEqual(rows[1]['成果已导出'],'否（仅失败记录）')
        self.assertFalse((target/bad['relative_dir']).exists())
        self.assertIn('测试分割失败',(target/'分割失败记录.csv').read_text(encoding='utf-8-sig'))
        self.manager.open_results(str(target))
        self.assertEqual(len(self.manager.dataset.failure_records()),1)
        self.assertIn('深度无效',self.manager.csv(self.manager.dataset.dataset_id).decode('utf-8-sig'))
        # Recover on the original grid without losing failure history.
        self.manager.open_results(str(d.output))
        write_draft(bad,d.output)
        record=self.manager.dataset.failure_records()[0]
        self.assertEqual(record['failure_status'],'resolved')
        recovered=list(csv.DictReader(io.StringIO(self.manager.csv(self.manager.dataset.dataset_id).decode('utf-8-sig'))))
        self.assertEqual(recovered[1]['分割状态'],'draft')
        self.assertEqual(recovered[1]['失败处理状态'],'已补充分割')


    def test_legacy_failure_and_failure_only_export(self):
        output=self.result()
        row=discover(self.source)[0]
        atomic_json(output/'batch_job.json',{'status':'completed','errors':[{'relative_dir':row['relative_dir'],'error':'旧版推理失败'}]})
        atomic_json(output/'summary.json',{'errors':[{'relative_dir':row['relative_dir'],'error':'旧版推理失败'}]})
        self.manager.open_results(str(output))
        self.assertEqual(self.manager.listing()['frames'][0]['status'],'failed')
        self.assertEqual(len(self.manager.dataset.failure_records()),1)
        target,_=self.export(scope='all')
        self.assertEqual(self.manager.export_job['status'],'completed')
        self.assertEqual(json.loads((target/'dataset.json').read_text())['frames'],[])
        self.assertIn('旧版推理失败',(target/'筛选记录.csv').read_text(encoding='utf-8-sig'))
        with self.assertRaisesRegex(ValueError,'仅包含失败报告'):
            self.manager.open_results(str(target))


    def test_model_load_error_marks_unprocessed_frames_and_is_reported(self):
        def fail():raise RuntimeError('模型加载失败')
        self.manager.model_factory=fail
        self.start();self.manager.worker.join(timeout=10)
        self.assertEqual(self.manager.listing()['frames'][0]['status'],'failed')
        self.assertIn('模型加载失败',self.manager.csv(self.manager.dataset.dataset_id).decode('utf-8-sig'))
