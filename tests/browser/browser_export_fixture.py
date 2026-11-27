"""Synthetic dataset for browser export checks; never opens actual user data."""
import json
from pathlib import Path
import shutil
import tempfile
from http.server import ThreadingHTTPServer

from backend.app import make_handler
from backend.core import Image, np, atomic_json, discover, write_draft
from backend.workspace import Workspace

root=Path(tempfile.mkdtemp(prefix='itof-export-browser-')).resolve()
source=root/'原始 数据'
for index in [1,2]:
    folder=source/'pig_7'/f'capture_{index}';folder.mkdir(parents=True)
    y,x=np.indices((120,160));pig=((x-90)/55)**2+((y-58)/27)**2<1
    ir=np.where(pig,1000,20).astype(np.uint16)
    np.save(folder/'infrared.npy',ir)
    np.save(folder/'depth_mm.npy',np.where(pig,900,2200).astype(np.uint16))
    Image.fromarray(ir).save(folder/'infrared.png')
    Image.fromarray(np.zeros((120,160,3),np.uint8)).save(folder/'color.png')
    Image.fromarray(np.zeros((120,160,3),np.uint8)).save(folder/'depth_preview.png')
output=root/'成果';output.mkdir()
(root/'导出 位置').mkdir()
rows=discover(source)
atomic_json(output/'dataset.json',{'source':str(source),'output':str(output),'backend':'sam2','frames':rows})
for row in rows:write_draft(row,output)
workspace=Workspace(root/'state',initial_output=output)
row=workspace.dataset.rows[0]
workspace.dataset.decide({'id':row['id'],'revision':0,'decision':'keep','notes':'浏览器测试保留'})
server=ThreadingHTTPServer(('127.0.0.1',8792),make_handler(workspace,'test-token'))
print(json.dumps({'root':str(root),'url':'http://127.0.0.1:8792'}),flush=True)
try:server.serve_forever()
except KeyboardInterrupt:pass
finally:
    if workspace.export_worker:workspace.export_worker.join(timeout=15)
    server.server_close();shutil.rmtree(root)
