"""Isolated mixed-camera review fixture with a legacy failed capture."""
import json
from pathlib import Path
import shutil
import tempfile
from http.server import ThreadingHTTPServer
from backend.app import make_handler
from backend.core import Image,np,atomic_json,discover,write_draft
from tests.fixtures.geometry import calibration
from backend.workspace import Workspace

root=Path(tempfile.mkdtemp(prefix='review-browser-')).resolve();source=root/'raw';output=root/'results';output.mkdir();(root/'exports').mkdir()
for camera,indices in [('itof',[1,2]),('stereo',[1,2]),('unlabelled',[3])]:
    for i in indices:
        folder=source/camera/'pig_7'/f'capture_{i}';folder.mkdir(parents=True)
        y,x=np.indices((120,160));pig=((x-90)/55)**2+((y-58)/27)**2<1
        ir=np.where(pig,1000,20).astype(np.uint16)
        np.save(folder/'infrared.npy',ir);np.save(folder/'depth_mm.npy',np.where(pig,900,2200).astype(np.uint16))
        Image.fromarray(ir).save(folder/'infrared.png');Image.fromarray(np.repeat((ir/4).clip(0,255).astype(np.uint8)[...,None],3,axis=2)).save(folder/'color.png')
        d=calibration(folder,rgb_size=(160,120),ir_size=(160,120))
        if camera!='unlabelled':d['runtime']={'device':{'name':'Orbbec Femto Bolt' if camera=='itof' else 'Orbbec Gemini 336L'}}
        atomic_json(folder/'reconstruction.json',d)
rows=discover(source)
atomic_json(output/'dataset.json',{'source':str(source),'output':str(output),'frames':rows,'backend':'sam2'})
for row in rows:
    if row['camera_type']!='unknown':write_draft(row,output)
bad=next(r for r in rows if r['camera_type']=='unknown')
atomic_json(output/'batch_job.json',{'status':'completed','done':5,'total':5,'success':4,'failed':1,'current':'分割结束',
    'errors':[{'relative_dir':bad['relative_dir'],'error':'测试：模型未找到猪体'}]})
workspace=Workspace(root/'state',initial_output=output)
server=ThreadingHTTPServer(('127.0.0.1',8795),make_handler(workspace,'test-token'))
print(json.dumps({'root':str(root),'url':'http://127.0.0.1:8795'}),flush=True)
try:server.serve_forever()
except KeyboardInterrupt:pass
finally:
    if workspace.export_worker:workspace.export_worker.join(timeout=15)
    server.server_close();shutil.rmtree(root)
