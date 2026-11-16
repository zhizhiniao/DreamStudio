"""Persistent account check batches and local-only account removal."""
import json
import time
import uuid


def register(app, store, accounts, lock):
    from flask import jsonify, request

    def progress():
        job = store.get('settings', 'account-check-batch')
        if not job:
            return None
        rows = []
        for item in job['items']:
            a = store.get('accounts', item['id'])
            pending = a and a.get('state') == 'checking'
            rows.append({**item, 'state': a.get('state', 'unchecked') if a else 'removed',
                         'message': a.get('message', '') if a else '账号已移除', 'pending': bool(pending)})
        return {**job, 'items': rows, 'running': any(x['pending'] for x in rows),
                'completed': sum(not x['pending'] for x in rows), 'total': len(rows)}

    @app.get('/api/accounts/check-batch')
    def account_check_progress():
        return jsonify(progress())

    @app.post('/api/accounts/check-batch')
    def account_check_start():
        with lock:
            previous = progress()
            if previous and previous['running']:
                return jsonify(previous), 202
            values = request.get_json(silent=True) or {}
            if values.get('retry'):
                ids = [x['id'] for x in (previous or {}).get('items', []) if x['state'] != 'valid']
            else:
                ids = [a['id'] for a in store.list('accounts')]
            records = [store.get('accounts', key) for key in ids]
            records = [a for a in records if a]
            if not records:
                return jsonify(error='没有需要检查的账号'), 400
            store.backup('before-account-check')
            job = {'id': 'account-check-batch', 'batch_id': uuid.uuid4().hex, 'started_at': time.time(),
                   'items': [{'id': a['id'], 'name': a.get('name', ''), 'platform': a['platform']} for a in records]}
            for a in records:
                accounts.check(a['id'])
            store.put('settings', job)
            return jsonify(progress()), 202

    @app.delete('/api/accounts/<key>')
    def delete_account(key):
        with lock, accounts.lock:
            a = store.get('accounts', key)
            if not a:
                return jsonify(error='账号不存在或已删除'), 404
            if (request.get_json(silent=True) or {}).get('confirm') != key:
                return jsonify(error='请确认要删除的账号'), 400
            folder = accounts.folder(a)
            busy = key in accounts.locks
            for path in (folder / 'jobs').glob('*.json'):
                job = json.loads(path.read_text(encoding='utf-8'))
                busy |= job.get('status') in ('queued', 'running')
            for kind in ('publications', 'sync_jobs', 'collections', 'downloads', 'monitors'):
                busy |= any((j.get('account_id') == key or j.get('source_account') == key) and
                            j.get('status') in ('queued', 'running', 'pending', 'active') for j in store.list(kind))
            if busy:
                return jsonify(error='账号正在检查、登录或执行任务，请完成任务或停止作品监控后再删除'), 409
            store.backup('before-account-delete')
            backup = store.root / 'backups' / ('removed-account-' + key + '-' + str(time.time_ns()))
            if folder.exists():
                folder.rename(backup)
            try:
                with store.connect() as db:
                    row = db.execute("SELECT data FROM records WHERE kind='settings' AND id='context'").fetchone()
                    if row:
                        ctx = json.loads(row[0])
                        if ctx.get('source') == key: ctx['source'] = ''
                        ctx['targets'] = [x for x in ctx.get('targets', []) if x != key]
                        db.execute("UPDATE records SET data=? WHERE kind='settings' AND id='context'", (json.dumps(ctx),))
                    db.execute("DELETE FROM records WHERE kind='accounts' AND id=?", (key,))
            except Exception:
                if backup.exists(): backup.rename(folder)
                raise
            store.event('删除本系统账号', key)
            return jsonify(ok=True)

    app.extensions['account_check_progress'] = progress
