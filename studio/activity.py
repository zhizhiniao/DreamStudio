"""Capture milestone transitions, independently of progress polling."""
import copy


def track(kind, value, previous, at):
    if kind not in ('tasks', 'downloads', 'publications'):
        return value
    old = previous or {}
    history = copy.deepcopy(old.get('history', []))
    value['history'] = history
    def add(event, **fields):
        history.append(dict(event=event, at=at, **fields))
    if previous is None:
        value['created_at'] = at
        add('created')
    elif old.get('created_at'):
        value['created_at'] = old['created_at']
    # Never accept or drop audit history supplied by a caller.
    if value.get('status') != old.get('status'):
        add('status', status=value.get('status', ''), phase=value.get('phase', ''))
    elif kind == 'publications' and value.get('phase') != old.get('phase'):
        add('phase', phase=value.get('phase', ''), status=value.get('status', ''))
    if kind == 'publications' and value.get('step') and value.get('step') != old.get('step'):
        add('upload_step', step=value['step'])
    if kind == 'publications' and value.get('cover_verified') is True and old.get('cover_verified') is not True:
        add('cover_verified')
    if kind == 'tasks':
        if previous and value.get('confirmed', -1) > old.get('confirmed', -1):
            add('confirmed', step=value['confirmed'])
        if previous and value.get('workflow_stage') and value.get('workflow_stage') != old.get('workflow_stage'):
            add('workflow', stage=value['workflow_stage'])
        if value.get('speech_review_hash') and value.get('speech_review_hash') != old.get('speech_review_hash'):
            add('speech_reviewed')
        if previous and value.get('voice_id') and value.get('voice_id') != old.get('voice_id'):
            add('voice_selected', voice_id=value['voice_id'])
        job, earlier = value.get('production_job') or {}, old.get('production_job') or {}
        if job and (job.get('id'), job.get('kind'), job.get('status')) != (earlier.get('id'), earlier.get('kind'), earlier.get('status')):
            add('production', kind=job.get('kind', ''), status=job.get('status', ''))
        if value.get('reviewed_at') and value.get('reviewed_at') != old.get('reviewed_at'):
            add('reviewed', version=value.get('reviewed_version'))
        if value.get('completion') and value.get('completion') != old.get('completion'):
            add('completed')
        if old.get('completion') and not value.get('completion'):
            add('reopened')
        if previous and value.get('targets', []) != old.get('targets', []):
            add('targets', accounts=[v for v in value.get('targets', []) if v not in old.get('targets', [])])
    return value
