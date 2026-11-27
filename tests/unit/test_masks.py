import base64
import json
from pathlib import Path
import tempfile
import unittest

from backend.core import Image, np, atomic_json, input_fingerprint, png_bytes, validate_output, write_draft
from backend.dataset import Dataset


class MaskWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        base = Path(self.temp.name)
        self.source, self.output = base/'source', base/'out'
        self.folder = self.source/'pig_7'/'capture_1'
        self.folder.mkdir(parents=True)
        yy, xx = np.indices((120, 160))
        pig = ((xx-90)/55)**2 + ((yy-58)/27)**2 < 1
        ir = np.full((120, 160), 20, np.uint16)
        ir[pig] = 1000
        depth = np.full(ir.shape, 2200, np.uint16)
        depth[pig] = 900
        np.save(self.folder/'infrared.npy', ir)
        np.save(self.folder/'depth_mm.npy', depth)
        Image.fromarray(ir).save(self.folder/'infrared.png')
        self.dataset = Dataset(self.source, self.output)
        self.row = self.dataset.rows[0]
        self.dest = self.output/self.row['relative_dir']
        self.mask = pig.astype(np.uint8)*255
        self.before = input_fingerprint(self.folder)

    def tearDown(self):
        self.assertEqual(self.before, input_fingerprint(self.folder))
        self.temp.cleanup()

    def payload(self, mask=None, **kw):
        mask = self.mask if mask is None else mask
        return {'id': self.row['id'], 'revision': 0, 'reviewed': False, 'head_side': 'left',
                'mask': 'data:image/png;base64,'+base64.b64encode(png_bytes(mask)).decode(), **kw}

    def test_draft_is_binary_aligned_and_not_approved(self):
        meta = write_draft(self.row, self.output)
        result = np.asarray(Image.open(self.dest/'trunk_mask_draft.png'))
        self.assertEqual(result.shape, self.mask.shape)
        self.assertTrue(np.isin(result, [0,255]).all())
        self.assertFalse(meta['reviewed'])
        self.assertFalse((self.dest/'trunk_mask.png').exists())

    def test_roundtrip_approval_revision_and_revocation(self):
        draft = self.dataset.save(self.payload())
        self.assertEqual(draft['status'], 'corrected')
        self.assertFalse((self.dest/'trunk_mask.png').exists())
        approved = self.dataset.save(self.payload(revision=1, reviewed=True))
        self.assertTrue(approved['reviewed'])
        self.assertTrue(np.array_equal(np.asarray(Image.open(self.dest/'trunk_mask.png')), self.mask))
        with self.assertRaises(ValueError):
            self.dataset.save(self.payload(revision=0))
        changed = self.mask.copy(); changed[50:55,90:95] = 0
        self.dataset.save(self.payload(changed, revision=2))
        self.assertFalse((self.dest/'trunk_mask.png').exists())
        self.assertTrue((self.dest/'revisions'/'00002'/'mask.png').exists())
        self.assertEqual(self.dataset.detail(self.row['id'])['revision'],3)

    def test_bad_masks_and_missing_direction_are_rejected(self):
        bad_masks = [np.zeros_like(self.mask), np.ones_like(self.mask), np.zeros((30,30),np.uint8)]
        for bad in bad_masks:
            with self.subTest(shape=bad.shape, maximum=int(bad.max())):
                with self.assertRaises(ValueError):
                    self.dataset.save(self.payload(bad))
        with self.assertRaises(ValueError):
            self.dataset.save(self.payload(reviewed=True, head_side='unknown'))
        self.assertFalse((self.dest/'trunk_mask.png').exists())

    def test_changed_source_is_rejected(self):
        self.dataset.save(self.payload())
        meta_path = self.dest/'segmentation.json'
        meta = json.loads(meta_path.read_text());meta['source_sha256']['infrared.npy'] = 'stale'
        atomic_json(meta_path, meta)
        with self.assertRaises(ValueError):
            self.dataset.save(self.payload(revision=1))

    def test_output_must_not_overlap_input(self):
        for target in [self.source, self.source/'derived', self.source.parent]:
            with self.assertRaises(ValueError):
                validate_output(self.source, target)

    def test_unknown_capture_cannot_write(self):
        with self.assertRaises(ValueError):
            self.dataset.save(self.payload(id='../../source/pig_7'))

    def test_sam_preview_is_unsaved_and_provenance_follows_save(self):
        mask = self.mask
        class Model:
            last_raw_mask = mask
            def predict(self, gray, prompts, key):
                return mask, {'model':'test-model','prompts':prompts}
        self.dataset.sam = Model()
        result = self.dataset.predict_sam({'id':self.row['id'],
                                          'prompts':{'points':[[90,58]],'labels':[1]}})
        self.assertFalse(self.dest.exists())
        saved = self.dataset.save(self.payload(sam_prediction_id=result['prediction_id']))
        self.assertEqual(saved['method'],'sam2_assisted_manual_correction')
        self.assertEqual(saved['last_sam_prediction']['model'],'test-model')
        self.assertTrue((self.dest/'revisions'/'00001'/'sam_raw_prediction.png').exists())
        self.assertFalse(saved['reviewed'])

    def test_unknown_sam_preview_cannot_be_saved(self):
        with self.assertRaises(ValueError):
            self.dataset.save(self.payload(sam_prediction_id='expired-or-foreign'))
        self.assertFalse(self.dest.exists())


if __name__ == '__main__':
    unittest.main()
