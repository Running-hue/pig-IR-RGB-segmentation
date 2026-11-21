"""Native Windows and macOS folder selection for the loopback web app."""
import json
from pathlib import Path, PurePosixPath, PureWindowsPath
import subprocess
import sys
import threading


PICKER_LOCK = threading.Lock()
PICK_FOLDER_SCRIPT = r'''
ObjC.import('AppKit');
function run(argv) {
    var app = $.NSApplication.sharedApplication;
    app.setActivationPolicy($.NSApplicationActivationPolicyAccessory);
    var panel = $.NSOpenPanel.openPanel;
    panel.canChooseFiles = false;
    panel.canChooseDirectories = true;
    panel.allowsMultipleSelection = false;
    panel.canCreateDirectories = true;
    panel.title = argv[0];
    panel.prompt = '选择文件夹';
    panel.directoryURL = $.NSURL.fileURLWithPath(argv[1]);
    app.activateIgnoringOtherApps(true);
    if (panel.runModal === $.NSModalResponseOK) {
        return ObjC.unwrap(panel.URL.path);
    }
    return '';
}
'''


def supported():
    return sys.platform in {'darwin', 'win32'}


def initial_directory(value=None):
    value = value.strip() if isinstance(value, str) else ''
    initial = Path(value).expanduser() if value else Path.home()
    # A path remembered on another operating system is not a local relative path.
    if not initial.is_absolute() and (PurePosixPath(value).is_absolute()
                                      or PureWindowsPath(value).is_absolute()):
        initial = Path.home()
    while not initial.is_dir() and initial != initial.parent:
        initial = initial.parent
    return initial.resolve()


def windows_directory_dialog(initial, title):
    # Tk must own its main thread: this runs only inside the helper process.
    import tkinter as tk
    from tkinter import filedialog

    root = tk.Tk()
    try:
        root.withdraw()
        root.attributes('-topmost', True)
        root.update_idletasks()
        return filedialog.askdirectory(
            parent=root, title=title, initialdir=initial, mustexist=True) or None
    finally:
        root.destroy()


def choose_directory(value=None, title='选择文件夹'):
    if not supported():
        raise ValueError('当前系统不支持系统文件夹选择窗口，请直接填写目录路径')
    if not PICKER_LOCK.acquire(blocking=False):
        raise ValueError('已有一个文件夹选择窗口，请先选择或取消')
    try:
        initial = initial_directory(value)
        title = str(title)[:200]
        options = {}
        # Paths and titles are arguments, never interpolated into executable code.
        if sys.platform == 'win32':
            command = [sys.executable, '-B', '-X', 'utf8', str(Path(__file__).resolve()),
                       '--windows-helper', title, str(initial)]
            options['creationflags'] = getattr(subprocess, 'CREATE_NO_WINDOW', 0x08000000)
        else:
            command = ['/usr/bin/osascript', '-l', 'JavaScript', '-e', PICK_FOLDER_SCRIPT,
                       title, str(initial)]
        result = subprocess.run(command, capture_output=True, text=True, encoding='utf-8',
                                timeout=600, check=False, **options)
        if result.returncode:
            raise ValueError('系统文件夹选择窗口未能打开：'+result.stderr.strip()[:500])
        if sys.platform == 'win32':
            try:
                selected = json.loads(result.stdout)['path']
                if selected is not None and not isinstance(selected, str):
                    raise TypeError('invalid path')
            except (ValueError, KeyError, TypeError) as e:
                raise ValueError('系统文件夹选择窗口返回了无效结果，请重试') from e
        else:
            selected = result.stdout.removesuffix('\n')
        if not selected:
            return {'cancelled': True, 'path': None}
        path = Path(selected).resolve()
        if not path.is_dir():
            raise ValueError('选择的文件夹不存在，请重新选择')
        return {'cancelled': False, 'path': str(path)}
    except subprocess.TimeoutExpired as e:
        raise ValueError('文件夹选择已超时，请重新点击选择') from e
    except OSError as e:
        raise ValueError('系统文件夹选择窗口未能打开：'+str(e)) from e
    finally:
        PICKER_LOCK.release()


if __name__ == '__main__':
    if len(sys.argv) != 4 or sys.argv[1] != '--windows-helper':
        sys.exit('This module is a folder-picker helper.')
    try:
        selected = windows_directory_dialog(sys.argv[3], sys.argv[2])
        print(json.dumps({'path': selected}, ensure_ascii=True))
    except Exception as e:
        print(str(e), file=sys.stderr)
        sys.exit(1)
