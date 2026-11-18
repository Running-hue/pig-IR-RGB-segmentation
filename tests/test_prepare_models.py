import contextlib
import copy
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import prepare_models as setup


class PrepareModelsTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='模型 下载 ')
        self.root=Path(self.temp.name).resolve()
        def entry(path,content):
            return {'path':path,'size_bytes':len(content),'sha256':hashlib.sha256(content).hexdigest()}
        self.source=b'pinned config\n'
        self.weight=b'verified test checkpoint\n'
        self.manifest={'model_name':'test-model','repository':{
            'url':'https://example.invalid/repo','commit':'test-commit','destination':'.models/sam2',
            'raw_base_url':'https://example.invalid/source/',
            'files':[entry('sam2/configs/b+.yaml',self.source)]},
            'checkpoint':{**entry('unused',self.weight),'url':'https://example.invalid/weight.pt',
                          'destination':'.models/model.pt'}}
        self.urls=[]

    def tearDown(self):
        self.temp.cleanup()

    def response(self,url,**kw):
        self.urls.append(url)
        return io.BytesIO(self.weight if url.endswith('weight.pt') else self.source)

    def prepare(self,**kw):
        with contextlib.redirect_stdout(io.StringIO()):
            return setup.prepare(self.root,manifest=self.manifest,**kw)

    def test_download_unicode_paths_encoded_plus_and_offline_check(self):
        with patch('prepare_models.urlopen',side_effect=self.response):
            self.assertEqual(self.prepare(),2)
        self.assertIn('https://example.invalid/source/sam2/configs/b%2B.yaml',self.urls)
        self.assertEqual((self.root/'.models/model.pt').read_bytes(),self.weight)
        record=json.loads((self.root/'.models/sam2/SOURCE.json').read_text())
        self.assertEqual(record['commit'],'test-commit')
        before={p:p.stat().st_mtime_ns for p in self.root.rglob('*') if p.is_file()}
        with patch('prepare_models.urlopen',side_effect=AssertionError('offline must not use network')):
            self.assertEqual(self.prepare(check=True),2)
            self.assertEqual(self.prepare(),2)  # A second prepare also reuses everything.
        self.assertEqual(before,{p:p.stat().st_mtime_ns for p in self.root.rglob('*') if p.is_file()})

    def test_check_missing_files_creates_nothing(self):
        with patch('prepare_models.urlopen',side_effect=AssertionError('offline')):
            with self.assertRaisesRegex(ValueError,'模型缺失'):self.prepare(check=True)
        self.assertEqual(list(self.root.iterdir()),[])

    def test_corrupt_download_never_replaces_existing_file(self):
        with patch('prepare_models.urlopen',side_effect=self.response):self.prepare()
        weight=self.root/'.models/model.pt';weight.write_bytes(b'old data')
        with patch('prepare_models.urlopen',side_effect=AssertionError('must require repair')):
            with self.assertRaisesRegex(ValueError,'--repair'):self.prepare()
        with patch('prepare_models.urlopen',return_value=io.BytesIO(b'bad')):
            with self.assertRaisesRegex(ValueError,'校验失败'):self.prepare(repair=True)
        self.assertEqual(weight.read_bytes(),b'old data')
        self.assertEqual(list(self.root.rglob('*.part')),[])
        with patch('prepare_models.urlopen',side_effect=self.response):self.prepare(repair=True)
        self.assertEqual(weight.read_bytes(),self.weight)

    def test_repair_regenerates_invalid_source_record(self):
        with patch('prepare_models.urlopen',side_effect=self.response):self.prepare()
        marker=self.root/'.models/sam2/SOURCE.json';marker.write_text('invalid json')
        with self.assertRaisesRegex(ValueError,'SOURCE.json'):self.prepare(check=True)
        with patch('prepare_models.urlopen',side_effect=AssertionError('no missing downloads')):self.prepare()
        self.assertEqual(json.loads(marker.read_text())['commit'],'test-commit')

    def test_escape_paths_and_model_symlinks_rejected(self):
        broken=copy.deepcopy(self.manifest)
        broken['repository']['files'][0]['path']='../../../../escape'
        with self.assertRaisesRegex(ValueError,'路径无效'):list(setup.items(broken,self.root))
        outside=self.root/'outside';outside.mkdir()
        try:(self.root/'.models').symlink_to(outside,target_is_directory=True)
        except OSError:self.skipTest('symlink permission unavailable')
        with self.assertRaisesRegex(ValueError,'符号链接'):self.prepare()
        self.assertEqual(list(outside.iterdir()),[])

    def test_tracked_manifest_matches_runtime_configuration(self):
        import sam_backend
        manifest=json.loads(setup.MANIFEST.read_text())
        self.assertEqual(manifest['config'],sam_backend.CONFIG)
        self.assertEqual((setup.APP_DIR/manifest['checkpoint']['destination']),sam_backend.CHECKPOINT)
        self.assertEqual((setup.APP_DIR/manifest['repository']['destination']),sam_backend.REPO_DIR)


if __name__=='__main__':unittest.main()
