"""Run a browser smoke test on a temporary copy of one real capture."""
import argparse
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import secrets
import shutil
import tempfile

from backend.app import make_handler
from backend.workspace import Workspace


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--capture', type=Path, required=True, help='Path to one original IR capture')
    args = parser.parse_args()
    if not args.capture.is_dir():
        parser.error('--capture must be an existing capture directory')

    with tempfile.TemporaryDirectory(prefix='itof-browser-') as temp:
        root = Path(temp).resolve()
        source = root / 'raw'
        shutil.copytree(args.capture, source / 'pig_1' / 'capture')
        (root / 'results').mkdir()
        workspace = Workspace(root / 'state')
        with ThreadingHTTPServer(('127.0.0.1', 8791), make_handler(workspace, secrets.token_urlsafe(32))) as server:
            print(json.dumps({'root': str(root), 'url': 'http://127.0.0.1:8791'}), flush=True)
            try:
                server.serve_forever()
            except KeyboardInterrupt:
                pass
            finally:
                workspace.stop()
                if workspace.worker:
                    workspace.worker.join()
                if workspace.export_worker:
                    workspace.export_worker.join()


if __name__ == '__main__':
    main()
