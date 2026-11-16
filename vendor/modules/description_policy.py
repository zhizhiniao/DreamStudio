"""Keep the description body free of Y2A's old automatic footer.

This only edits description text; source metadata and platform declarations are
independent. Other links, author credits and content about AI remain intact.
"""
import re


BODY_INSTRUCTION = '简介只写视频内容，不自动添加配音／字幕制作声明或原视频来源尾注。'


def clean_description(text, source_url=''):
    text = str(text or '').replace('\r\n', '\n').replace('\r', '\n')
    text = re.sub(r'(?m)^[^\S\n]*中文配音及字幕由[^\S\n]*AI[^\S\n]*辅助制作[。.][^\S\n]*(?:\n|$)', '', text)
    source = str(source_url or '').strip()
    if source:
        # Exact source and a standalone generated footer, not arbitrary URL removal.
        pattern = r'(?m)^[^\S\n]*(?:原视频|来源)[：:][^\S\n]*' + re.escape(source) + r'[^\S\n]*(?:\n|$)'
        text = re.sub(pattern, '', text)
    return re.sub(r'\n[ \t]*\n(?:[ \t]*\n)+', '\n\n', text).strip()


def clean_pending_descriptions():
    """Explicit maintenance action, never run automatically at startup.

    Preserve genuinely current drafts, including a root whose only manual edit
    was removing this footer. Unrelated edits still require draft preparation.
    """
    from datetime import datetime
    from modules import task_manager as tm, multi_publish as mp
    changed = []
    with tm.get_db_connection() as db:
        db.execute('BEGIN IMMEDIATE')
        rows = {r['id']: dict(r) for r in db.execute('SELECT * FROM tasks')}
        jobs = [dict(r) for r in db.execute('SELECT * FROM publication_jobs')]
        if (any(t['status'] not in mp.SAFE_STATES + ('pending',) for t in rows.values()) or
                db.execute("SELECT 1 FROM publication_sets WHERE phase IN ('preparing','sending')").fetchone()):
            raise ValueError('任务正在处理中，请完成后再清理简介尾注。')
        compatible = {}
        for job in jobs:
            root, child = rows.get(job['root_id']), rows.get(job['task_id'])
            if not root or not child:
                continue
            candidates = compatible.setdefault(root['id'], {mp.revision(root)})
            old_text = child.get('description_translated')
            if clean_description(old_text, root['youtube_url']) == clean_description(root.get('description_translated'), root['youtube_url']):
                candidates.add(mp.revision({**root, 'description_translated': old_text}))
        for task in rows.values():
            if mp.submitted(task) or not task.get('description_translated'):
                continue
            cleaned = clean_description(task['description_translated'], task['youtube_url'])
            if cleaned != task['description_translated']:
                db.execute('UPDATE tasks SET description_translated=?,updated_at=? WHERE id=?',
                           (cleaned, datetime.now().strftime('%Y-%m-%d %H:%M:%S'), task['id']))
                task['description_translated'] = cleaned
                changed.append(task['id'])
        for job in jobs:
            if job['source_revision'] in compatible.get(job['root_id'], set()):
                db.execute('UPDATE publication_jobs SET source_revision=? WHERE root_id=? AND platform=?',
                           (mp.revision(rows[job['root_id']]), job['root_id'], job['platform']))
    for task_id in changed:
        tm.publish_task_event('task_updated', {'task_id': task_id})
    return changed
