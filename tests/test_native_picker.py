from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import native_picker


class NativePickerTests(unittest.TestCase):
    def test_native_picker_preserves_unicode_paths_as_arguments(self):
        with tempfile.TemporaryDirectory(prefix='中文 空格') as folder:
            with patch('native_picker.supported',return_value=True),patch('native_picker.subprocess.run') as run:
                selected=str(Path(folder).resolve())
                run.return_value=subprocess.CompletedProcess([],0,selected+'\n','')
                result=native_picker.choose_directory(folder+'/missing','选择“分割成果”')
                self.assertEqual(result,{'cancelled':False,'path':selected})
                args=run.call_args.args[0]
                self.assertEqual(args[-2:],['选择“分割成果”',selected])
                self.assertNotIn(folder,native_picker.PICK_FOLDER_SCRIPT)
                self.assertNotIn('shell',run.call_args.kwargs)

    def test_cancel_timeout_and_error_do_not_leave_picker_locked(self):
        with patch('native_picker.supported',return_value=True),patch('native_picker.subprocess.run') as run:
            run.return_value=subprocess.CompletedProcess([],0,'\n','')
            self.assertTrue(native_picker.choose_directory()['cancelled'])
            run.side_effect=subprocess.TimeoutExpired('osascript',600)
            with self.assertRaisesRegex(ValueError,'超时'):native_picker.choose_directory()
            run.side_effect=None;run.return_value=subprocess.CompletedProcess([],1,'','panel error')
            with self.assertRaisesRegex(ValueError,'panel error'):native_picker.choose_directory()
        self.assertFalse(native_picker.PICKER_LOCK.locked())


if __name__=='__main__':unittest.main()
