"""Isolated RGB failures: inference, mismatched geometry, unreadable raw image."""
import json
from pathlib import Path
import shutil
import tempfile
from http.server import ThreadingHTTPServer
from unittest.mock import patch
from backend.app import make_handler
from backend.core import Image,np,atomic_json,discover,png_bytes
from backend.geometry import RGB_VERSION
from tests.fixtures.geometry import calibration
from tests.fixtures.sam import RecordingModel
from backend.workspace import Workspace

root=Path(tempfile.mkdtemp(prefix='failed-review-browser-')).resolve()
source=root/'raw';output=root/'results';output.mkdir();(root/'exports').mkdir()
for name in ['good','model_failed','geometry_failed','unreadable']:
    folder=source/'itof'/'pig_7'/name;folder.mkdir(parents=True)
    y,x=np.indices((120,160));pig=((x-90)/55)**2+((y-58)/27)**2<1
    ir=np.where(pig,1000,20).astype(np.uint16)
    np.save(folder/'infrared.npy',ir);Image.fromarray(ir).save(folder/'infrared.png')
    np.save(folder/'depth_mm.npy',np.full((120,160),900,np.uint16))
    np.save(folder/'depth_aligned_to_color_mm.npy',np.full((120,160),900,np.uint16))
    image=np.repeat((ir/4).clip(0,255).astype(np.uint8)[...,None],3,axis=2)
    if name=='geometry_failed':image=image.transpose(1,0,2)
    Image.fromarray(image).save(folder/'color.png')
    calibration(folder,rgb_size=(160,120),ir_size=(160,120))
rows=discover(source,'rgb')
atomic_json(output/'dataset.json',{'source':str(source),'output':str(output),'frames':rows,
                                  'backend':'sam2','input_mode':'rgb','geometry_version':RGB_VERSION})
workspace=Workspace(root/'state',initial_output=output,model_factory=RecordingModel)
d=workspace.dataset
for row in rows:
    if row['capture_id']=='good':
        import base64
        mask=np.zeros((120,160),np.uint8);mask[30:90,40:140]=255
        d.save({'id':row['id'],'mask':'data:image/png;base64,'+base64.b64encode(png_bytes(mask)).decode()})
    else:
        if row['capture_id']=='unreadable':(Path(row['source_dir'])/'color.png').write_bytes(b'broken PNG')
        d.record_failure(row,{'model_failed':'模型未找到猪体','geometry_failed':'原始 RGB 尺寸与实际彩色流 profile 不符',
                              'unreadable':'无法解码 RGB'}[row['capture_id']])
server=ThreadingHTTPServer(('127.0.0.1',8796),make_handler(workspace,'test-token'))
print(json.dumps({'root':str(root),'url':'http://127.0.0.1:8796'}),flush=True)
try:
    with patch('backend.sam_backend.ready',return_value=True):server.serve_forever()
except KeyboardInterrupt:pass
finally:
    if workspace.export_worker:workspace.export_worker.join(timeout=15)
    server.server_close();shutil.rmtree(root)
