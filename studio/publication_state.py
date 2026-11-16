"""Reconcile durable worker receipts without making an uncertain submission retryable."""
import json

ACTIVE = ('queued', 'running')
BLOCKING = (*ACTIVE, 'succeeded', 'needs_check')
FIELDS = {'status', 'phase', 'message', 'receipt', 'qrcode', 'qrcode_at', 'login_state',
          'verification', 'failure_step', 'error_type', 'retryable', 'step', 'recovery'}


def reconcile(pub, path=None, interrupted=False):
    # An explicit human reconciliation supersedes the old worker file.
    if pub.get('resolved_by'):
        return dict(pub)
    result = dict(pub)
    job = None
    damaged = False
    if path is not None:
        try:
            job = json.loads(path.read_text(encoding='utf-8'))
            if not isinstance(job, dict) or job.get('id') != pub['id']:
                raise ValueError('receipt identity mismatch')
            for field in ('account_id', 'task_id', 'draft_id', 'platform'):
                if field in job and field in pub and job[field] != pub[field]:
                    raise ValueError('receipt owner mismatch')
            if job.get('status') not in (*BLOCKING, 'failed'):
                raise ValueError('invalid receipt status')
        except FileNotFoundError:
            job = None
        except (OSError, ValueError, TypeError):
            job = None
            damaged = True
    if job:
        result.update({k:v for k,v in job.items() if k in FIELDS})
    # Successful submissions never regress, even if an older job file is replayed.
    if pub.get('status') == 'succeeded':
        return dict(pub)
    if result.get('status') == 'succeeded':
        return result
    # A temporary read failure while the owner still runs must not expose "resolve".
    if damaged and pub.get('status') in ACTIVE and not interrupted:
        return dict(pub)
    uncertain = (damaged or pub.get('status') == 'needs_check'
                 or (interrupted and result.get('status') in ACTIVE)
                 or (pub.get('status') == 'failed' and job and job.get('status') in ACTIVE))
    if uncertain:
        result.update(status='needs_check', retryable=False,
                      message='执行结果尚未确认，请先核对平台作品记录，勿重复投稿。')
    return result


def job_path(accounts, store, pub):
    account = store.get('accounts', pub.get('account_id', ''))
    return accounts.folder(account)/'jobs'/(pub['id']+'.json') if account else None


def sync(store, accounts, interrupted=False, draft_id=None):
    changes = []
    for pub in store.list('publications'):
        if draft_id is not None and pub.get('draft_id') != draft_id:
            continue
        value = reconcile(pub, job_path(accounts, store, pub), interrupted)
        value.pop('verification', None)
        if value != pub:
            changes.append(value)
    if changes:
        store.backup('before-reconcile-publications')
        for value in changes:
            store.put('publications', value)
    return changes
