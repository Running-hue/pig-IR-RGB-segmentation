"""Camera labels and durable batch failures, including older result folders."""
from pathlib import Path
import json
import re

CAMERA_LABELS={'itof':'iToF','stereo':'双目','unknown':'未识别'}
SUCCESS={'draft','corrected','reviewed'}


def camera_info(folder):
    folder=Path(folder)
    name=''
    try:
        data=json.loads((folder/'reconstruction.json').read_text())
        name=str(data.get('runtime',{}).get('device',{}).get('name',''))
    except (OSError,ValueError,TypeError,AttributeError):
        pass
    # Prefer recorded hardware; path tokens support older captures without metadata.
    def classify(text):
        text=text.lower()
        if 'femto' in text or re.search(r'(^|[^a-z])itof([^a-z]|$)',text):return 'itof'
        if 'gemini' in text or '双目' in text or re.search(r'(^|[^a-z])stereo([^a-z]|$)',text):return 'stereo'
        return 'unknown'
    kind=classify(name)
    origin='device' if kind!='unknown' else 'unknown'
    if kind=='unknown':
        for part in reversed(folder.parts):
            kind=classify(part)
            if kind!='unknown':origin='path';break
    return {'camera_type':kind,'camera_label':CAMERA_LABELS[kind],
            'camera_name':name,'camera_identified_by':origin}


def load_failures(output,rows):
    """Merge current records with old job/summary errors without duplicating a failure."""
    output=Path(output);by_rel={r['relative_dir']:r for r in rows};found={}
    for filename in ['segmentation_failures.json','batch_job.json','summary.json']:
        path=output/filename
        if not path.is_file():continue
        data=json.loads(path.read_text())
        entries=data.get('records',[]) if filename=='segmentation_failures.json' else data.get('errors',[])
        for item in entries:
            rel=item.get('relative_dir','');error=str(item.get('error',''))
            if not error:continue
            key=(rel,error)
            if key in found:continue
            row=by_rel.get(rel,{})
            found[key]={**item,**row,'relative_dir':rel,'error':error,
                        'stage':item.get('stage','segmentation'),'at':item.get('at',data.get('finished_at',data.get('started_at','')))}
        # A batch-level model/worker failure is distinct from a per-frame inference error.
        if data.get('error'):
            key=('',str(data['error']))
            found.setdefault(key,{'relative_dir':'','error':str(data['error']),'stage':'batch',
                                  'at':data.get('finished_at',data.get('started_at','')),'failure_status':'failed'})
    return list(found.values())
