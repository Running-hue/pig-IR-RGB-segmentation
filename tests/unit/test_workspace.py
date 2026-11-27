import json
import base64
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from backend.dataset import Dataset
from backend.core import Image, np, atomic_json, discover, input_fingerprint, png_bytes, write_draft
from backend.workspace import Workspace


class BrowserWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.root=Path(self.tmp.name)
        self.source=self.root/'source'
        self.folder=self.source/'pig_7'/'capture_1'
        self.folder.mkdir(parents=True)
        y,x=np.indices((120,160));pig=((x-90)/55)**2+((y-58)/27)**2<1
        ir=np.where(pig,1000,20).astype(np.uint16)
        np.save(self.folder/'infrared.npy',ir)
        np.save(self.folder/'depth_mm.npy',np.where(pig,900,2200).astype(np.uint16))
        Image.fromarray(ir).save(self.folder/'infrared.png')
        self.before=input_fingerprint(self.folder)
        self.parent=self.root/'results';self.parent.mkdir()
        self.manager=Workspace(self.root/'state',model_factory=lambda:object(),
                               processor=lambda row,out,model:write_draft(row,out))

    def tearDown(self):
        self.manager.stop()
        if self.manager.worker:self.manager.worker.join(timeout=10)
        if self.manager.export_worker:self.manager.export_worker.join(timeout=10)
        self.assertEqual(self.before,input_fingerprint(self.folder))
        self.tmp.cleanup()

    def start(self,**kw):
        return self.manager.start({'source':str(self.source),'parent':str(self.parent),'name':'run',**kw})

    def result(self,name='old',rows=None):
        path=self.parent/name;path.mkdir()
        atomic_json(path/'dataset.json',{'source':str(self.source),'backend':'sam2',
                                       'frames':discover(self.source) if rows is None else rows})
        return path

    def test_empty_startup_and_scan_do_not_write_source(self):
        self.assertEqual(self.manager.listing()['frames'],[])
        self.assertEqual(self.manager.scan(str(self.source))['frames'],1)
        self.assertFalse(self.manager.settings_file.exists())
        self.assertEqual(self.manager.directories(str(self.parent))['path'],str(self.parent.resolve()))

    def test_browser_batch_generates_then_reopens_results_and_decisions(self):
        listing=self.start();self.manager.worker.join(timeout=10)
        self.assertEqual(self.manager.job['status'],'completed')
        self.assertEqual(self.manager.job['success'],1)
        row=self.manager.dataset.rows[0]
        self.assertTrue((Path(listing['output'])/row['relative_dir']/'trunk_mask_draft.png').exists())
        d=self.manager.dataset
        d.decide({'id':row['id'],'revision':0,'decision':'keep','notes':'保留'})
        reopened=Workspace(self.root/'state')
        self.assertEqual(reopened.listing()['frames'][0]['decision'],'keep')
        self.assertIn('保留',reopened.csv(reopened.dataset.dataset_id).decode('utf-8-sig'))

    def test_existing_output_is_not_overwritten(self):
        output=self.parent/'run';output.mkdir();sentinel=output/'do-not-change';sentinel.write_text('original')
        with self.assertRaisesRegex(ValueError,'已有文件'):self.start()
        self.assertEqual(sentinel.read_text(),'original')
        self.assertFalse((output/'dataset.json').exists())

    def test_source_overlap_and_path_name_traversal_rejected(self):
        for kw in [{'parent':str(self.source)}, {'name':'../escape'}, {'name':'.'}, {'parent':str(self.root),'name':'source'}]:
            with self.subTest(kw=kw),self.assertRaises(ValueError):self.start(**kw)

    def test_selection_is_exactly_manifest_subset_and_stale_sessions_rejected(self):
        # Source can contain more captures than the 12-frame trial manifest.
        import shutil
        shutil.copytree(self.folder,self.source/'pig_7'/'capture_2')
        row=discover(self.source)[0]
        first=self.manager.open_results(str(self.result(rows=[row])))
        self.assertEqual(len(first['frames']),1)
        second=self.manager.open_results(str(self.result(name='other',rows=[row])))
        self.assertNotEqual(first['dataset_id'],second['dataset_id'])
        with self.assertRaisesRegex(ValueError,'已切换'):self.manager.require_dataset(first['dataset_id'])
        self.assertIs(self.manager.require_dataset(second['dataset_id']),self.manager.dataset)

    def test_running_batch_blocks_switch_and_cancel_finishes_current_capture(self):
        import shutil
        shutil.copytree(self.folder,self.source/'pig_7'/'capture_2')
        entered=threading.Event();release=threading.Event()
        def process(row,out,model):
            entered.set();release.wait(timeout=10);write_draft(row,out)
        self.manager.processor=process
        old=self.result()
        try:
            self.start();self.assertTrue(entered.wait(timeout=5))
            with self.assertRaisesRegex(ValueError,'正在运行'):self.manager.open_results(str(old))
            self.assertEqual(self.manager.stop()['status'],'stopping')
        finally:
            release.set()
            if self.manager.worker:self.manager.worker.join(timeout=10)
        self.assertFalse(self.manager.worker.is_alive())
        self.assertEqual(self.manager.job['success'],1)
        self.assertEqual(self.manager.job['status'],'stopped')
        self.assertEqual(self.manager.job['done'],1)
        self.manager.open_results(str(old))

    def test_model_failure_is_visible_and_partial_result_can_open(self):
        def fail():raise RuntimeError('model load failed')
        self.manager.model_factory=fail
        listing=self.start();self.manager.worker.join(timeout=5)
        self.assertEqual(self.manager.job['status'],'failed')
        self.assertIn('model load failed',self.manager.job['error'])
        saved=json.loads((Path(listing['output'])/'batch_job.json').read_text())
        self.assertEqual(saved['status'],'failed')
        self.manager.open_results(listing['output'])

    def test_reject_requires_note_and_does_not_change_mask_approval(self):
        self.start();self.manager.worker.join(timeout=10)
        d=self.manager.dataset;row=d.rows[0]
        with self.assertRaisesRegex(ValueError,'淘汰原因'):
            d.decide({'id':row['id'],'revision':0,'decision':'reject','notes':''})
        m=d.decide({'id':row['id'],'revision':0,'decision':'reject','notes':'出画'})
        self.assertEqual(m['status'],'draft');self.assertFalse(m['reviewed'])
        with self.assertRaisesRegex(ValueError,'已经更新'):
            d.decide({'id':row['id'],'revision':0,'decision':'keep','notes':''})

    def test_invalid_manifest_cannot_escape_source_or_output(self):
        row={**discover(self.source)[0],'relative_dir':'../../outside'}
        with self.assertRaises(ValueError):self.manager.open_results(str(self.result(rows=[row])))

    def test_relocated_source_is_remembered_without_rewriting_manifest(self):
        result=self.result()
        path=result/'dataset.json';data=json.loads(path.read_text())
        data['source']=str(self.root/'missing_source');atomic_json(path,data)
        self.manager.open_results(str(result),source_override=str(self.source))
        self.assertEqual(json.loads(path.read_text())['source'],data['source'])
        restored=Workspace(self.root/'state')
        self.assertEqual(restored.dataset.source,self.source.resolve())
        self.assertEqual(restored.startup_warning,'')

    def export(self, name='filtered', **kw):
        result=self.manager.start_export({'dataset_id':self.manager.dataset.dataset_id,
                                         'parent':str(self.parent),'name':name,'scope':'keep',**kw})
        self.manager.export_worker.join(timeout=10)
        self.assertFalse(self.manager.export_worker.is_alive())
        return (self.parent/name).resolve(),result

    def test_export_keeps_exact_selection_history_and_saved_masks(self):
        import shutil
        shutil.copytree(self.folder,self.source/'pig_7'/'capture_2')
        self.start();self.manager.worker.join(timeout=10)
        d=self.manager.dataset;row=d.rows[0]
        d.decide({'id':row['id'],'revision':0,'decision':'keep','notes':'保留自动初稿'})
        (d.dest(row)/'revisions'/'00001').mkdir(parents=True)
        (d.dest(row)/'revisions'/'00001'/'history.txt').write_text('history')
        before={str(p.relative_to(d.output)):p.read_bytes() for p in d.output.rglob('*') if p.is_file()}
        target,_=self.export()
        self.assertEqual(self.manager.export_job['status'],'completed',self.manager.export_job)
        self.assertEqual(self.manager.dataset,d)  # Export does not switch the open results.
        manifest=json.loads((target/'dataset.json').read_text())
        self.assertEqual([r['id'] for r in manifest['frames']],[row['id']])
        self.assertEqual(manifest['output'],str(target))
        self.assertEqual((target/row['relative_dir']/'trunk_mask_draft.png').read_bytes(),
                         (d.dest(row)/'trunk_mask_draft.png').read_bytes())
        self.assertTrue((target/row['relative_dir']/'revisions'/'00001'/'history.txt').is_file())
        meta=json.loads((target/row['relative_dir']/'segmentation.json').read_text())
        self.assertEqual(meta['decision'],'keep');self.assertFalse(meta['reviewed'])
        self.assertEqual(len(meta['decision_history']),1)
        self.assertFalse((target/d.rows[1]['relative_dir']).exists())
        self.assertIn(str(target),(target/'筛选记录.csv').read_text(encoding='utf-8-sig'))
        self.assertEqual(before,{str(p.relative_to(d.output)):p.read_bytes() for p in d.output.rglob('*') if p.is_file()})
        self.assertEqual(len(self.manager.open_results(str(target))['frames']),1)
        self.assertEqual(self.manager.settings['last_export_parent'],str(self.parent.resolve()))

    def test_export_protects_existing_paths_source_and_empty_selection(self):
        self.start();self.manager.worker.join(timeout=10)
        d=self.manager.dataset
        with self.assertRaisesRegex(ValueError,'没有可导出'):self.export()
        for kw in [{'parent':str(self.source)}, {'parent':str(d.output)},
                   {'parent':str(self.manager.state_dir)}, {'name':'../escape'},
                   {'dataset_id':'stale'}, {'scope':'invalid'}]:
            with self.subTest(kw=kw),self.assertRaises(ValueError):self.export(**{'scope':'all',**kw})
        target=self.parent/'existing';target.mkdir()
        sentinel=target/'keep';sentinel.write_text('untouched')
        with self.assertRaisesRegex(ValueError,'已存在'):self.export(name='existing',scope='all')
        self.assertEqual(sentinel.read_text(),'untouched')
        self.assertFalse((target/'dataset.json').exists())

    def test_export_failure_cleans_partial_copy_and_does_not_follow_symlinks(self):
        self.start();self.manager.worker.join(timeout=10)
        d=self.manager.dataset;row=d.rows[0]
        (d.dest(row)/'escape').symlink_to(self.source,target_is_directory=True)
        target,_=self.export(scope='all')
        self.assertEqual(self.manager.export_job['status'],'failed')
        self.assertIn('符号链接',self.manager.export_job['error'])
        self.assertFalse(target.exists())
        self.assertEqual(list(self.parent.glob('.itof-export-*')),[])

    def test_approved_export_requires_keep_and_preserves_approval(self):
        self.start();self.manager.worker.join(timeout=10)
        d=self.manager.dataset;row=d.rows[0]
        meta=d.decide({'id':row['id'],'revision':0,'decision':'keep','notes':''})
        with self.assertRaisesRegex(ValueError,'没有可导出'):self.export(scope='keep_reviewed')
        mask=np.asarray(Image.open(d.dest(row)/'trunk_mask_draft.png'))
        d.save({'id':row['id'],'revision':meta['revision'],'reviewed':True,'head_side':'left',
                'mask':'data:image/png;base64,'+base64.b64encode(png_bytes(mask)).decode()})
        target,_=self.export(scope='keep_reviewed')
        self.assertEqual(self.manager.export_job['status'],'completed')
        saved=json.loads((target/row['relative_dir']/'segmentation.json').read_text())
        self.assertTrue(saved['reviewed']);self.assertEqual(saved['decision'],'keep')
        self.assertEqual((target/row['relative_dir']/'trunk_mask.png').read_bytes(),
                         (d.dest(row)/'trunk_mask.png').read_bytes())

    def test_export_pauses_mutations_and_blocks_duplicate_export(self):
        from backend.workspace import copy_results_tree
        self.start();self.manager.worker.join(timeout=10)
        entered=threading.Event();release=threading.Event()
        def slow_copy(*args):
            entered.set();release.wait(timeout=10);return copy_results_tree(*args)
        with patch('backend.workspace.copy_results_tree',side_effect=slow_copy):
            try:
                self.manager.start_export({'dataset_id':self.manager.dataset.dataset_id,
                                          'parent':str(self.parent),'name':'slow','scope':'all'})
                self.assertTrue(entered.wait(timeout=5))
                with self.assertRaisesRegex(ValueError,'正在导出'):self.manager._idle()
                with self.assertRaisesRegex(ValueError,'正在导出'):
                    self.manager.start_export({})
            finally:
                release.set();self.manager.export_worker.join(timeout=10)
        self.assertEqual(self.manager.export_job['status'],'completed')
        self.manager._idle()


if __name__=='__main__':unittest.main()
