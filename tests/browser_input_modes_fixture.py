"""Isolated browser fixture: synthetic by default, optional read-only real capture."""
import argparse
from contextlib import nullcontext
import json
from pathlib import Path
import shutil
import sys
import tempfile
from http.server import ThreadingHTTPServer
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app import make_handler
from core import Image, np
from workspace import Workspace
from test_input_modes import RecordingModel
from geometry_fixture import calibration

parser=argparse.ArgumentParser()
parser.add_argument('--capture',type=Path,help='Copy a real capture and use real SAM')
parser.add_argument('--port',type=int,default=8793)
args=parser.parse_args()
root=Path(tempfile.mkdtemp(prefix='input-modes-browser-')).resolve()
source=root/'raw';capture=source/'pig_1'/'capture'
if args.capture:
    shutil.copytree(args.capture,capture)
else:
    capture.mkdir(parents=True)
    y,x=np.indices((120,160));pig=((x-90)/55)**2+((y-58)/27)**2<1
    ir=np.where(pig,1000,20).astype(np.uint16)
    np.save(capture/'infrared.npy',ir)
    np.save(capture/'depth_mm.npy',np.where(pig,900,2200).astype(np.uint16))
    Image.fromarray(ir).save(capture/'infrared.png')
    image=np.zeros((180,320,3),np.uint8);image[:]=[35,55,75]
    image[35:150,50:270]=[210,155,100]
    Image.fromarray(image).save(capture/'color.png')
    np.save(capture/'depth_aligned_to_color_mm.npy',np.full((180,320),1200,np.uint16))
    calibration(capture,rgb_size=(320,180),ir_size=(160,120))
(root/'results').mkdir()
workspace=Workspace(root/'state',model_factory=None if args.capture else RecordingModel)
server=ThreadingHTTPServer(('127.0.0.1',args.port),make_handler(workspace,'test-token'))
print(json.dumps({'root':str(root),'source':str(source),'url':f'http://127.0.0.1:{args.port}',
                  'real_model':bool(args.capture)}),flush=True)
try:
    with patch('sam_backend.ready',return_value=True) if not args.capture else nullcontext():
        server.serve_forever()
except KeyboardInterrupt:pass
finally:
    workspace.stop()
    if workspace.worker:workspace.worker.join()
    if workspace.export_worker:workspace.export_worker.join()
    server.server_close();shutil.rmtree(root)
