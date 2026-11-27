#!/usr/bin/env python3
"""Download or verify the exact SAM source snapshot and checkpoint, using stdlib only."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import sys
import tempfile
import time
from urllib.parse import quote, urlparse
from urllib.request import urlopen

from backend.paths import CONFIG_DIR, PROJECT_ROOT

MANIFEST = CONFIG_DIR / 'model_manifest.json'


def inside(root, relative):
    relative = PurePosixPath(relative)
    if relative.is_absolute() or not relative.parts or any(p in {'.', '..'} or '\\' in p for p in relative.parts):
        raise ValueError(f'模型清单路径无效：{relative}')
    target = root.joinpath(*relative.parts)
    for parent in [target, *target.parents]:
        if parent == root:
            break
        if parent.is_symlink():
            raise ValueError(f'模型路径不能包含符号链接：{parent}')
    if root.resolve() not in target.resolve().parents:
        raise ValueError(f'模型清单路径越界：{relative}')
    return target


def items(manifest, root):
    repo = manifest['repository']
    for entry in repo['files']:
        yield inside(root, repo['destination']+'/'+entry['path']), repo['raw_base_url']+quote(entry['path'], safe='/'), entry
    checkpoint = manifest['checkpoint']
    yield inside(root, checkpoint['destination']), checkpoint['url'], checkpoint


def verify(path, expected):
    if not path.is_file() or path.stat().st_size != expected['size_bytes']:
        return False
    digest = hashlib.sha256()
    with path.open('rb') as source:
        for block in iter(lambda: source.read(1024*1024), b''):
            digest.update(block)
    return digest.hexdigest() == expected['sha256']


def download(path, url, expected):
    if urlparse(url).scheme != 'https':
        raise ValueError('模型下载仅允许 HTTPS 地址')
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(prefix='.'+path.name+'.', suffix='.part', dir=path.parent, delete=False) as dest:
            temporary = Path(dest.name)
            digest = hashlib.sha256()
            count, last_report = 0, time.monotonic()
            with urlopen(url, timeout=60) as response:
                while block := response.read(1024*1024):
                    dest.write(block)
                    digest.update(block)
                    count += len(block)
                    if count > expected['size_bytes']:
                        raise ValueError(f'下载文件大于清单记录：{path.name}')
                    if time.monotonic()-last_report >= 3:
                        print(f'  {count/1024/1024:.1f} / {expected["size_bytes"]/1024/1024:.1f} MiB', flush=True)
                        last_report = time.monotonic()
            if count != expected['size_bytes'] or digest.hexdigest() != expected['sha256']:
                raise ValueError(f'下载校验失败，未替换原文件：{path.name}')
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def prepare(root=PROJECT_ROOT, *, check=False, repair=False, manifest=None):
    root = Path(root).resolve()
    manifest = manifest if manifest is not None else json.loads(MANIFEST.read_text(encoding='utf-8'))
    targets = list(items(manifest, root))
    valid, problems = 0, []
    for index, (path, url, expected) in enumerate(targets, 1):
        if verify(path, expected):
            valid += 1
            continue
        if check:
            problems.append(str(path.relative_to(root)))
            continue
        if path.exists() and not repair:
            raise ValueError(f'已有模型文件与固定版本不符：{path}；先备份，再使用 --repair 修复')
        print(f'[{index}/{len(targets)}] 下载 {path.relative_to(root)}', flush=True)
        download(path, url, expected)
        valid += 1
    if problems:
        raise ValueError('模型缺失或校验不符：\n'+'\n'.join(problems)+'\n运行 python3 -m scripts.prepare_models 补齐；已有损坏文件需使用 --repair')
    repo = manifest['repository']
    record = {'repository': repo['url'], 'commit': repo['commit'],
              'files': [entry['path'] for entry in repo['files']]}
    provenance = inside(root, repo['destination']+'/SOURCE.json')
    try:
        recorded = json.loads(provenance.read_text(encoding='utf-8')) if provenance.is_file() else None
    except ValueError:
        recorded = None
    if recorded != record:
        if check:
            raise ValueError('SOURCE.json 来源记录缺失或与清单不符；运行 python3 -m scripts.prepare_models 重新生成')
        provenance.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(prefix='.SOURCE.', suffix='.tmp', dir=provenance.parent, delete=False) as dest:
                temporary = Path(dest.name)
                dest.write(json.dumps(record, ensure_ascii=False, indent=2).encode('utf-8'))
            os.replace(temporary, provenance)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
    print(f'模型校验通过：{valid} 个文件；{manifest["model_name"]}', flush=True)
    return valid


def main():
    parser = argparse.ArgumentParser(description='下载固定版本 SAM 2.1 代码与权重；不会安装依赖或读取猪只数据')
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--check', action='store_true', help='只离线核对大小与 SHA256，不联网、不改文件')
    mode.add_argument('--repair', action='store_true', help='重新下载缺失或校验不符的模型文件')
    args = parser.parse_args()
    try:
        prepare(check=args.check, repair=args.repair)
    except (OSError, ValueError, KeyError) as error:
        print(f'模型准备失败：{error}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
