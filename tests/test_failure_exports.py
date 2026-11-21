import csv
import io
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
import urllib.request
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app import ThreadingHTTPServer, make_handler
from core import Image, np, atomic_bytes, atomic_json, discover, png_bytes
from workspace import Workspace


class FailureExportTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='失败记录 中文 ')
        self.root=Path(self.tmp.name)
        self.source=self.root/'raw'
        for rel in ['itof-1110/captures/pig_0007/20261110_090001',
                    '双目-1110/captures/pig_0008/20261110_090002',
                    '双目-1110/captures/pig_0009/20261110_090003']:
            folder=self.source/rel;folder.mkdir(parents=True)
            values=np.arange(64,dtype=np.uint16).reshape(8,8)+1
            np.save(folder/'infrared.npy',values)
            np.save(folder/'depth_mm.npy',values)
            Image.fromarray(values).save(folder/'infrared.png')
        self.rows=discover(self.source)
        self.good,self.bad,self.pending=self.rows
        self.output=self.root/'original';self.output.mkdir()
        atomic_json(self.output/'dataset.json',{'source':str(self.source),'frames':self.rows,'backend':'sam2'})
        self.write_mask(self.good)
        self.error='读取失败，文件为空；路径含中文 "采集"\nNo data left in file'
        atomic_json(self.output/'summary.json',{'errors':[{'relative_dir':self.bad['relative_dir'],'error':'older reason'}]})
        atomic_json(self.output/'batch_job.json',{'id':'failed-fixture','status':'completed','done':2,'total':3,
                    'success':1,'failed':1,'output':str(self.output),'errors':[
                        {'relative_dir':self.bad['relative_dir'],'error':self.error}]})
        self.manager=Workspace(self.root/'state')
        self.manager.open_results(str(self.output))
        self.before={str(p):p.read_bytes() for p in self.source.rglob('*') if p.is_file()}

    def tearDown(self):
        if self.manager.export_worker:self.manager.export_worker.join(timeout=10)
        self.assertEqual(self.before,{str(p):p.read_bytes() for p in self.source.rglob('*') if p.is_file()})
        self.tmp.cleanup()

    def write_mask(self,row):
        dest=self.output/row['relative_dir']
        atomic_bytes(dest/'trunk_mask_draft.png',png_bytes(np.ones((8,8),np.uint8)*255))
        atomic_json(dest/'segmentation.json',{**row,'status':'draft','reviewed':False,'revision':0,'decision':'keep'})

    @staticmethod
    def read_csv(value):
        return list(csv.DictReader(io.StringIO(value.decode('utf-8-sig'))))

    def export(self,name='filtered'):
        d=self.manager.dataset
        self.manager.start_export({'dataset_id':d.dataset_id,'parent':str(self.root),'name':name,'scope':'keep'})
        self.manager.export_worker.join(timeout=10)
        self.assertFalse(self.manager.export_worker.is_alive())
        self.assertEqual(self.manager.export_job['status'],'completed',self.manager.export_job)
        return self.root/name

    def test_csv_distinguishes_failure_from_unprocessed_and_preserves_chinese_errors(self):
        value=self.manager.csv(self.manager.dataset.dataset_id)
        self.assertTrue(value.startswith(b'\xef\xbb\xbf'))
        rows=self.read_csv(value);self.assertEqual(len(rows),3)
        by_rel={r['采集相对路径']:r for r in rows}
        failed=by_rel[self.bad['relative_dir']]
        self.assertEqual(failed['分割状态'],'分割失败')
        self.assertEqual(failed['分割失败原因'],self.error)
        self.assertEqual(failed['相机类型'],'双目')
        self.assertEqual(failed['源目录'],self.bad['source_dir'])
        self.assertEqual(failed['成果目录'],'')
        self.assertEqual(by_rel[self.pending['relative_dir']]['分割状态'],'pending')
        self.assertEqual(by_rel[self.pending['relative_dir']]['分割失败原因'],'')
        self.assertEqual(by_rel[self.good['relative_dir']]['相机类型'],'iToF')
        records=self.manager.listing()['segmentation_failures']
        self.assertEqual(len(records),1)
        self.assertEqual(records[0]['error'],self.error)

    def test_exports_failures_outside_keep_scope_and_reexports_without_duplicates(self):
        target=self.export()
        manifest=json.loads((target/'dataset.json').read_text(encoding='utf-8'))
        self.assertEqual([r['id'] for r in manifest['frames']],[self.good['id']])
        self.assertEqual(len(manifest['segmentation_failures']),1)
        self.assertFalse((target/self.bad['relative_dir']).exists())
        combined=self.read_csv((target/'筛选记录.csv').read_bytes())
        self.assertEqual(len(combined),2)
        failure_rows=self.read_csv((target/'分割失败记录.csv').read_bytes())
        self.assertEqual(len(failure_rows),1)
        self.assertEqual(failure_rows[0]['分割失败原因'],self.error)
        archived=json.loads((target/'分割失败记录.json').read_text(encoding='utf-8'))
        self.assertEqual(archived['failed'],1)
        self.assertEqual(archived['failures'][0]['camera_type'],'stereo')
        self.assertEqual(self.manager.export_job['segmentation_failed'],1)
        self.manager.open_results(str(target))
        # The failure is absent from editable frames but remains in subsequent CSVs.
        csv_rows=self.read_csv(self.manager.csv(self.manager.dataset.dataset_id))
        self.assertEqual(len(csv_rows),2)
        again=self.export('reexport')
        self.assertEqual(len(self.read_csv((again/'筛选记录.csv').read_bytes())),2)
        self.assertEqual(len(self.read_csv((again/'分割失败记录.csv').read_bytes())),1)
        self.assertEqual(json.loads((again/'分割失败记录.json').read_text(encoding='utf-8'))['failed'],1)

    def test_repaired_failures_are_removed_and_empty_reports_have_headers(self):
        self.write_mask(self.bad)
        self.assertEqual(self.manager.listing()['segmentation_failures'],[])
        rows=self.read_csv(self.manager.csv(self.manager.dataset.dataset_id))
        repaired=next(r for r in rows if r['采集相对路径']==self.bad['relative_dir'])
        self.assertEqual(repaired['分割状态'],'draft');self.assertEqual(repaired['分割失败原因'],'')
        target=self.export()
        self.assertEqual(self.read_csv((target/'分割失败记录.csv').read_bytes()),[])
        self.assertEqual(json.loads((target/'分割失败记录.json').read_text(encoding='utf-8'))['failed'],0)
        self.assertIn('分割失败原因',(target/'分割失败记录.csv').read_text(encoding='utf-8-sig'))

    def test_summary_is_a_fallback_and_unrelated_live_job_is_ignored(self):
        (self.output/'batch_job.json').unlink()
        self.manager.job={'output':str(self.root/'other'),'errors':[{'relative_dir':self.pending['relative_dir'],'error':'unrelated'}]}
        records=self.manager.segmentation_failures(self.manager.dataset)
        self.assertEqual(len(records),1);self.assertEqual(records[0]['error'],'older reason')

    def test_csv_head_direction_labels_and_unrecorded_defaults(self):
        path=self.output/self.good['relative_dir']/'segmentation.json'
        original=json.loads(path.read_text(encoding='utf-8'))
        cases=[('left','左侧'),('right','右侧'),('up','上侧'),('down','下侧'),
               ('unknown','尚未记录'),('', '尚未记录'),(None,'尚未记录'),
               ('invalid','尚未记录'),('missing','尚未记录')]
        for value,label in cases:
            with self.subTest(value=value):
                meta=dict(original)
                if value!='missing':meta['head_side']=value
                atomic_json(path,meta)
                rows=self.read_csv(self.manager.csv(self.manager.dataset.dataset_id))
                by_rel={row['采集相对路径']:row for row in rows}
                self.assertEqual(by_rel[self.good['relative_dir']]['猪头侧方向'],label)
                self.assertEqual(by_rel[self.bad['relative_dir']]['猪头侧方向'],'尚未记录')
                self.assertEqual(by_rel[self.pending['relative_dir']]['猪头侧方向'],'尚未记录')

    def test_exported_and_reopened_csv_preserve_head_direction(self):
        path=self.output/self.good['relative_dir']/'segmentation.json'
        meta=json.loads(path.read_text(encoding='utf-8'));meta['head_side']='right';atomic_json(path,meta)
        target=self.export('head_direction')
        rows=self.read_csv((target/'筛选记录.csv').read_bytes())
        by_rel={row['采集相对路径']:row for row in rows}
        self.assertEqual(by_rel[self.good['relative_dir']]['猪头侧方向'],'右侧')
        self.assertEqual(by_rel[self.bad['relative_dir']]['猪头侧方向'],'尚未记录')
        failed=self.read_csv((target/'分割失败记录.csv').read_bytes())
        self.assertEqual(failed[0]['猪头侧方向'],'尚未记录')
        self.manager.open_results(str(target))
        rows=self.read_csv(self.manager.csv(self.manager.dataset.dataset_id))
        self.assertEqual(next(row for row in rows if row['采集相对路径']==self.good['relative_dir'])['猪头侧方向'],'右侧')

    def test_download_endpoint_includes_failure_reason(self):
        path=self.output/self.good['relative_dir']/'segmentation.json'
        meta=json.loads(path.read_text(encoding='utf-8'));meta['head_side']='left';atomic_json(path,meta)
        server=ThreadingHTTPServer(('127.0.0.1',0),make_handler(self.manager,'fixture-token'))
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            with urllib.request.urlopen(f'http://127.0.0.1:{server.server_port}/api/review.csv?dataset_id={self.manager.dataset.dataset_id}') as response:
                self.assertIn('text/csv',response.headers['Content-Type'])
                rows=self.read_csv(response.read())
            failed=next(r for r in rows if r['分割状态']=='分割失败')
            self.assertEqual(failed['分割失败原因'],self.error)
            self.assertEqual(failed['猪头侧方向'],'尚未记录')
            self.assertEqual(next(row for row in rows if row['采集相对路径']==self.good['relative_dir'])['猪头侧方向'],'左侧')
        finally:
            server.shutdown();server.server_close();thread.join(timeout=5)

    def test_link_rejection_cleans_partial_export_without_os_symlink_privileges(self):
        escape=self.output/self.good['relative_dir']/'escape'
        escape.mkdir()
        actual_is_symlink=Path.is_symlink
        def is_symlink(path):
            return path==escape or actual_is_symlink(path)
        with patch.object(Path,'is_symlink',is_symlink):
            self.manager.start_export({'dataset_id':self.manager.dataset.dataset_id,
                                      'parent':str(self.root),'name':'blocked','scope':'keep'})
            self.manager.export_worker.join(timeout=10)
        self.assertEqual(self.manager.export_job['status'],'failed')
        self.assertIn('符号链接',self.manager.export_job['error'])
        self.assertFalse((self.root/'blocked').exists())
        self.assertEqual(list(self.root.glob('.itof-export-*')),[])

    def test_archived_failure_paths_cannot_escape_the_dataset(self):
        manifest=json.loads((self.output/'dataset.json').read_text(encoding='utf-8'))
        for path in ['../escape',r'..\escape','/absolute',r'C:\absolute']:
            with self.subTest(path=path):
                manifest['segmentation_failures']=[{'relative_dir':path,'error':'invalid'}]
                atomic_json(self.output/'dataset.json',manifest)
                with self.assertRaisesRegex(ValueError,'相对路径'):
                    self.manager.csv(self.manager.dataset.dataset_id)


if __name__=='__main__':unittest.main()
