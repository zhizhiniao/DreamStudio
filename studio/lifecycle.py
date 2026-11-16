"""Derived lifecycle keeps original tasks and receipts intact."""
def project(tasks, assets, publications):
    result = []
    for original in tasks:
        task = dict(original)
        receipts = [p for p in publications if p.get('task_id') == task['id']]
        submitted = any(p.get('status') == 'succeeded' for p in receipts)
        uncertain = any(p.get('status') == 'needs_check' for p in receipts)
        targets=set(task.get('targets') or [])
        accepted={p.get('account_id') for p in receipts if p.get('status')=='succeeded'}
        pending=any(p.get('status') in ('queued','running','needs_check') for p in receipts)
        complete=submitted and not pending and (targets.issubset(accepted) if targets else True)
        cancelled = task.get('status') == 'cancelled'
        manually_complete = bool(task.get('completion')) and not pending
        task.update(lifecycle='cancelled' if cancelled else 'completed' if manually_complete else 'submitted' if complete else 'active',
                    in_history=cancelled or complete or manually_complete,
                    task_complete=complete or manually_complete,
                    publication_complete=complete,
                    has_submitted=submitted, has_pending_publication=uncertain,
                    publication_state='submitted' if submitted else 'needs_check' if uncertain else 'unpublished')
        result.append(task)
    material = []
    for original in assets:
        asset = dict(original)
        linked = [t for t in result if t.get('asset_id') == asset['id']]
        used = any(t.get('status') != 'cancelled' or t.get('used_before_cancel') for t in linked)
        publication = 'submitted' if any(t['publication_state'] == 'submitted' for t in linked) else 'needs_check' if any(t['publication_state'] == 'needs_check' for t in linked) else 'unpublished'
        asset.update(used=used, publication_state=publication,
                     has_submitted=any(t['has_submitted'] for t in linked),
                     has_pending_publication=any(t['has_pending_publication'] for t in linked),
                     active_task_count=sum(not t['in_history'] for t in linked),
                     returned_to_library=any(t['lifecycle'] == 'cancelled' for t in linked))
        material.append(asset)
    return result, material
