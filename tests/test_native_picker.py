import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import native_picker


class NativePickerTests(unittest.TestCase):
    def test_native_picker_preserves_unicode_paths_as_arguments(self):
        with tempfile.TemporaryDirectory(prefix='中文 空格') as folder:
            with patch('native_picker.sys.platform','darwin'),patch('native_picker.subprocess.run') as run:
                selected=str(Path(folder).resolve())
                run.return_value=subprocess.CompletedProcess([],0,selected+'\n','')
                result=native_picker.choose_directory(folder+'/missing','选择“分割成果”')
                self.assertEqual(result,{'cancelled':False,'path':selected})
                args=run.call_args.args[0]
                self.assertEqual(args[-2:],['选择“分割成果”',selected])
                self.assertNotIn(folder,native_picker.PICK_FOLDER_SCRIPT)
                self.assertNotIn('shell',run.call_args.kwargs)

    def test_cancel_timeout_and_error_do_not_leave_picker_locked(self):
        with patch('native_picker.sys.platform','darwin'),patch('native_picker.subprocess.run') as run:
            run.return_value=subprocess.CompletedProcess([],0,'\n','')
            self.assertTrue(native_picker.choose_directory()['cancelled'])
            run.side_effect=subprocess.TimeoutExpired('osascript',600)
            with self.assertRaisesRegex(ValueError,'超时'):native_picker.choose_directory()
            run.side_effect=None;run.return_value=subprocess.CompletedProcess([],1,'','panel error')
            with self.assertRaisesRegex(ValueError,'panel error'):native_picker.choose_directory()
        self.assertFalse(native_picker.PICKER_LOCK.locked())


    def test_supported_platforms(self):
        for platform, expected in [('win32', True), ('darwin', True), ('linux', False)]:
            with self.subTest(platform=platform), patch('native_picker.sys.platform', platform):
                self.assertEqual(native_picker.supported(), expected)

    def test_windows_unicode_selection_and_cancel(self):
        with tempfile.TemporaryDirectory(prefix='中文 空格') as folder:
            selected = str(Path(folder).resolve())
            with patch('native_picker.sys.platform', 'win32'), patch('native_picker.subprocess.run') as run:
                run.return_value = subprocess.CompletedProcess([], 0, json.dumps({'path': selected}), '')
                self.assertEqual(native_picker.choose_directory(folder+'/missing', '选择“成果”'),
                                 {'cancelled': False, 'path': selected})
                command = run.call_args.args[0]
                self.assertEqual(command[0], sys.executable)
                self.assertEqual(command[-3:], ['--windows-helper', '选择“成果”', selected])
                self.assertEqual(run.call_args.kwargs['creationflags'], getattr(subprocess, 'CREATE_NO_WINDOW', 0x08000000))
                self.assertEqual(run.call_args.kwargs['encoding'], 'utf-8')
                self.assertNotIn('shell', run.call_args.kwargs)
                for empty in [None, '']:
                    run.return_value = subprocess.CompletedProcess([], 0, json.dumps({'path': empty}), '')
                    self.assertEqual(native_picker.choose_directory(folder), {'cancelled': True, 'path': None})

    def test_windows_errors_release_lock(self):
        with patch('native_picker.sys.platform', 'win32'), patch('native_picker.subprocess.run') as run:
            for invalid in ['broken json', '{}', '[]', '{"path": 42}']:
                run.return_value = subprocess.CompletedProcess([], 0, invalid, '')
                with self.assertRaisesRegex(ValueError, '无效结果'):
                    native_picker.choose_directory()
                self.assertFalse(native_picker.PICKER_LOCK.locked())
            run.return_value = subprocess.CompletedProcess([], 0, json.dumps({'path': str(Path.home()/'missing-native-picker-directory')}), '')
            with self.assertRaisesRegex(ValueError, '不存在'):
                native_picker.choose_directory()
            run.return_value = subprocess.CompletedProcess([], 1, '', 'Tk error')
            with self.assertRaisesRegex(ValueError, 'Tk error'):
                native_picker.choose_directory()
            run.side_effect = subprocess.TimeoutExpired('python', 600)
            with self.assertRaisesRegex(ValueError, '超时'):
                native_picker.choose_directory()
            run.side_effect = OSError('could not start helper')
            with self.assertRaisesRegex(ValueError, 'could not start helper'):
                native_picker.choose_directory()
        self.assertFalse(native_picker.PICKER_LOCK.locked())

    def test_simultaneous_dialog_is_rejected(self):
        with patch('native_picker.sys.platform', 'win32'):
            native_picker.PICKER_LOCK.acquire()
            try:
                with self.assertRaisesRegex(ValueError, '已有一个'):
                    native_picker.choose_directory()
                self.assertTrue(native_picker.PICKER_LOCK.locked())
            finally:
                native_picker.PICKER_LOCK.release()

    def test_windows_tk_lifecycle_and_cancel(self):
        tk = MagicMock()
        with patch.dict(sys.modules, {'tkinter': tk, 'tkinter.filedialog': tk.filedialog}):
            tk.filedialog.askdirectory.return_value = 'C:/中文 成果'
            self.assertEqual(native_picker.windows_directory_dialog('C:/', '选择成果'), 'C:/中文 成果')
            tk.filedialog.askdirectory.assert_called_once_with(
                parent=tk.Tk.return_value, title='选择成果', initialdir='C:/', mustexist=True)
            tk.Tk.return_value.withdraw.assert_called_once()
            tk.Tk.return_value.destroy.assert_called_once()
            tk.reset_mock()
            tk.filedialog.askdirectory.return_value = ''
            self.assertIsNone(native_picker.windows_directory_dialog('C:/', '选择成果'))
            tk.Tk.return_value.destroy.assert_called_once()
            tk.reset_mock()
            tk.filedialog.askdirectory.side_effect = RuntimeError('dialog error')
            with self.assertRaisesRegex(RuntimeError, 'dialog error'):
                native_picker.windows_directory_dialog('C:/', '选择成果')
            tk.Tk.return_value.destroy.assert_called_once()


if __name__=='__main__':unittest.main()
