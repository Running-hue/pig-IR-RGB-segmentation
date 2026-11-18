"""Native macOS folder selection for the loopback web app."""
from pathlib import Path
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
    return sys.platform == 'darwin'


def choose_directory(value=None, title='选择文件夹'):
    if not supported():
        raise ValueError('当前系统不支持 macOS 文件夹选择窗口，请直接填写目录路径')
    if not PICKER_LOCK.acquire(blocking=False):
        raise ValueError('已有一个文件夹选择窗口，请先选择或取消')
    try:
        initial = Path(value).expanduser() if isinstance(value, str) and value.strip() else Path.home()
        while not initial.is_dir() and initial != initial.parent:
            initial = initial.parent
        initial = initial.resolve()
        # Paths and titles are arguments, never interpolated into executable code.
        result = subprocess.run(
            ['/usr/bin/osascript', '-l', 'JavaScript', '-e', PICK_FOLDER_SCRIPT,
             str(title)[:200], str(initial)],
            capture_output=True, text=True, timeout=600, check=False)
        if result.returncode:
            raise ValueError('系统文件夹选择窗口未能打开：'+result.stderr.strip()[:500])
        selected = result.stdout.removesuffix('\n')
        if not selected:
            return {'cancelled': True, 'path': None}
        path = Path(selected).resolve()
        if not path.is_dir():
            raise ValueError('选择的文件夹不存在，请重新选择')
        return {'cancelled': False, 'path': str(path)}
    except subprocess.TimeoutExpired as e:
        raise ValueError('文件夹选择已超时，请重新点击选择') from e
    finally:
        PICKER_LOCK.release()
