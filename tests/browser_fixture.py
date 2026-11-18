"""Isolated real-model browser smoke fixture; copies one read-only capture."""
import json
from pathlib import Path
import secrets
import shutil
import sys
import tempfile
from http.server import ThreadingHTTPServer

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app import make_handler
from workspace import Workspace

root=Path(tempfile.mkdtemp(prefix='itof-browser-')).resolve()
source=root/'raw'
capture=source/'pig_1000'/'20261109_002543_623'
shutil.copytree(Path('/Users/mugua-zhaojiangtao/Documents/my_file/数据/itof/pig_1000/20261109_002543_623'),capture)
(root/'results').mkdir()
workspace=Workspace(root/'state')
server=ThreadingHTTPServer(('127.0.0.1',8791),make_handler(workspace,secrets.token_urlsafe(32)))
print(json.dumps({'root':str(root),'source':str(source),'parent':str(root/'results'),'url':'http://127.0.0.1:8791'}),flush=True)
try:
    server.serve_forever()
except KeyboardInterrupt:
    workspace.stop()
    if workspace.worker:workspace.worker.join()
finally:
    server.server_close()
