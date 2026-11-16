"""Content-bound human subtitle approval, independent from translation and rendering."""
import hashlib
import json
from pathlib import Path
from . import media


def review_key(result, task):
    rows=[(r['source'],r['text'],r['start'],r['end']) for r in result['rows']]
    return hashlib.sha256(json.dumps([result['signature'],rows,task.get('start'),task.get('duration')],
                                     ensure_ascii=False).encode()).hexdigest()


def approved(folder, asset, task, data):
    from .subtitle_workspace import snapshot
    try:
        path=Path(task.get('subtitle_approved_path') or '__missing__')
        if not path.is_file() or media.checksum(path)!=task.get('subtitle_approved_hash'):return False
        result=snapshot(folder,asset,task,data)
        return bool(result['rows']) and all(r['state'] in ('completed','manual') for r in result['rows']) and task.get('subtitle_review_key')==review_key(result,task)
    except (OSError, ValueError, KeyError):
        return False
