"""Loopback HTTP service for the workbench and its frontend."""
from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import secrets
from urllib.parse import parse_qs, urlparse
import webbrowser

from .paths import FRONTEND_DIR, STATE_DIR
from .workspace import Workspace

STATIC_ASSETS = {
    '/styles.css': 'text/css; charset=utf-8',
    '/editor.js': 'text/javascript; charset=utf-8',
    '/workflow.js': 'text/javascript; charset=utf-8',
}


def make_handler(workspace, token):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            if args and str(args[0]).startswith('POST'):
                super().log_message(fmt, *args)

        def send(self, body, mime='application/json', status=200):
            if not isinstance(body, bytes):
                body = json.dumps(body, ensure_ascii=False).encode('utf-8')
            self.send_response(status)
            self.send_header('Content-Type', mime)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('X-Frame-Options', 'DENY')
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            try:
                url = urlparse(self.path)
                q = parse_qs(url.query)
                if url.path == '/':
                    page = (FRONTEND_DIR / 'index.html').read_text(encoding='utf-8').replace('__TOKEN__', token)
                    self.send(page.encode(), 'text/html; charset=utf-8')
                elif url.path in STATIC_ASSETS:
                    asset = FRONTEND_DIR / url.path[1:]
                    self.send(asset.read_bytes(), STATIC_ASSETS[url.path])
                elif url.path == '/api/captures':
                    self.send(workspace.listing())
                elif url.path == '/api/job':
                    with workspace.lock:
                        self.send({'job':workspace.job,'export_job':workspace.export_job})
                elif url.path in {'/api/frame','/api/image','/api/review.csv'}:
                    with workspace.lock:
                        dataset=workspace.require_dataset(q.get('dataset_id',[''])[0])
                        if url.path=='/api/frame':
                            self.send(dataset.detail(q['id'][0]))
                        elif url.path=='/api/image':
                            self.send(dataset.media(q['id'][0], q['kind'][0]), 'image/png')
                        else:
                            self.send(workspace.csv(dataset.dataset_id),'text/csv; charset=utf-8')
                else:
                    self.send({'error': '页面不存在'}, status=404)
            except (ValueError, KeyError, OSError, TypeError) as e:
                self.send({'error': str(e)}, status=400)

        def do_POST(self):
            try:
                if self.headers.get('X-App-Token') != token:
                    self.send({'error': '请从本机工作台页面操作'}, status=403)
                    return
                origin = self.headers.get('Origin')
                if origin and origin != f'http://127.0.0.1:{self.server.server_port}':
                    self.send({'error': '不支持跨站请求'}, status=403)
                    return
                length = int(self.headers.get('Content-Length', '0'))
                if length <= 0 or length > 3_000_000:
                    raise ValueError('请求大小不合法')
                if not self.headers.get('Content-Type', '').startswith('application/json'):
                    raise ValueError('需要 JSON 请求')
                payload = json.loads(self.rfile.read(length))
                if self.path=='/api/directories':
                    self.send(workspace.directories(payload.get('path')))
                elif self.path=='/api/choose_directory':
                    from .native_picker import choose_directory
                    self.send(choose_directory(payload.get('path'),payload.get('title','选择文件夹')))
                elif self.path=='/api/export':
                    self.send(workspace.start_export(payload))
                elif self.path=='/api/scan':
                    self.send(workspace.scan(payload.get('source'),payload.get('input_mode','infrared')))
                elif self.path=='/api/open_results':
                    self.send(workspace.open_results(payload.get('output'),payload.get('source_override')))
                elif self.path=='/api/batch/start':
                    self.send(workspace.start(payload))
                elif self.path=='/api/batch/stop':
                    with workspace.lock:
                        if not workspace.job or payload.get('job_id')!=workspace.job['id']:
                            raise ValueError('任务已经切换，请刷新页面')
                        self.send({'job':workspace.stop()})
                elif self.path in {'/api/save','/api/generate','/api/sam_predict','/api/decide'}:
                    with workspace.lock:
                        workspace._idle()
                        dataset=workspace.require_dataset(payload.get('dataset_id'))
                        if self.path=='/api/save':
                            self.send(dataset.save(payload))
                        elif self.path=='/api/generate':
                            self.send(dataset.generate(payload['id']))
                        elif self.path=='/api/decide':
                            self.send(dataset.decide(payload))
                        else:
                            dataset.sam=workspace.get_model()
                            self.send(dataset.predict_sam(payload))
                else:
                    self.send({'error': '接口不存在'}, status=404)
            except (ValueError, KeyError, OSError, TypeError) as e:
                self.send({'error': str(e)}, status=400)
            except Exception as e:
                self.send({'error':f'操作失败：{e}'},status=500)

    return Handler


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', type=Path)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--state',type=Path,default=STATE_DIR)
    parser.add_argument('--port', type=int, default=8768)
    parser.add_argument('--open', action='store_true')
    args = parser.parse_args()
    workspace=Workspace(args.state,initial_output=args.output)
    if args.source and args.output:
        workspace.open_results(str(args.output),str(args.source))
    server = ThreadingHTTPServer(('127.0.0.1', args.port), make_handler(workspace, secrets.token_urlsafe(32)))
    address = f'http://127.0.0.1:{server.server_port}'
    print(f'iToF 躯干分割工作台：{address}\n在浏览器选择原始目录，或打开已有成果目录。', flush=True)
    if args.open:
        webbrowser.open(address)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print('\n分割工作台已停止；已保存进度保留。')
    finally:
        workspace.stop()
        if workspace.worker:
            workspace.worker.join()
        server.server_close()
