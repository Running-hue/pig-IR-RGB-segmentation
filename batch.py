#!/usr/bin/env python3
"""Generate reproducible unreviewed masks without modifying input files."""
import argparse
import csv
import io
import json
from pathlib import Path
import time

from core import atomic_bytes, atomic_json, discover, stamp, validate_output, write_draft


def inventory(source):
    return {p.relative_to(source).as_posix(): [p.stat().st_size, p.stat().st_mtime_ns]
            for p in source.rglob('*') if p.is_file() and not p.is_symlink()}


def run(source, output, limit=None):
    source, output = validate_output(source, output)
    rows = discover(source)
    if limit:
        rows = rows[:limit]
    before = inventory(source)
    output.mkdir(parents=True, exist_ok=True)
    atomic_json(output/'source_inventory.json', before)
    atomic_json(output/'dataset.json', {'source': str(source), 'output': str(output), 'frames': rows, 'created_at': stamp()})
    success, errors = [], []
    for i, row in enumerate(rows):
        try:
            meta_path = output/row['relative_dir']/'segmentation.json'
            # Do not regenerate or overwrite work from an existing run.
            if meta_path.exists():
                meta = json.loads(meta_path.read_text())
            else:
                meta = write_draft(row, output)
            success.append(meta)
        except Exception as e:
            errors.append({**row, 'error': str(e)})
        if (i+1) % 10 == 0 or i+1 == len(rows):
            print(f'{i+1}/{len(rows)} 完成，失败 {len(errors)}', flush=True)
    after = inventory(source)
    unchanged = before == after
    summary = {'source': str(source), 'output': str(output), 'generated_at': stamp(),
               'captures': len(rows), 'pigs': len({r['pig_id'] for r in rows}),
               'masks': len(success), 'reviewed': sum(bool(m.get('reviewed')) for m in success),
               'source_files_unchanged_by_size_mtime': unchanged, 'source_file_count': len(before), 'errors': errors}
    atomic_json(output/'summary.json', summary)
    stream = io.StringIO(newline='')
    fields = ['pig_id','capture_id','relative_dir','status','reviewed','area_px','valid_depth_ratio','warnings']
    writer = csv.DictWriter(stream, fieldnames=fields)
    writer.writeheader()
    for meta in success:
        writer.writerow({**{k: meta[k] for k in fields[:5]},
                         **{k: meta['stats'][k] for k in ['area_px','valid_depth_ratio']},
                         'warnings': '；'.join(meta['stats']['warnings'])})
    atomic_bytes(output/'分割记录.csv', ('\ufeff'+stream.getvalue()).encode('utf-8'))
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    if not unchanged:
        raise RuntimeError('原始目录在运行过程中发生变化，请检查数据源')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--limit', type=int)
    args = parser.parse_args()
    run(args.source, args.output, args.limit)
