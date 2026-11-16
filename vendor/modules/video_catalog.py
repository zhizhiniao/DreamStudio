"""Read-only video projection over platform jobs, including legacy submissions.

Group before filtering/pagination. No task, media file or receipt is rewritten.
"""
import json
import re
from urllib.parse import parse_qs, urlsplit

from modules.publishing_platforms import PLATFORM_LABELS, MANAGE_URLS

IN_FLIGHT = {'fetching_info', 'translating', 'generating_tags', 'partitioning', 'moderating',
             'downloading', 'translating_subtitle', 'asr_transcribing', 'encoding_video', 'uploading'}


def source_key(task):
    url = str(task.get('youtube_url') or '').strip()
    try:
        parsed = urlsplit(url)
        host = (parsed.hostname or '').lower()
        parts = parsed.path.strip('/').split('/')
        video_id = ''
        if host == 'youtu.be':
            video_id = parts[0]
        elif host in ('youtube.com', 'www.youtube.com', 'm.youtube.com', 'music.youtube.com'):
            video_id = parse_qs(parsed.query).get('v', [''])[0] if parsed.path == '/watch' else (
                parts[1] if len(parts) >= 2 and parts[0] in ('shorts', 'embed', 'live') else '')
        if re.fullmatch(r'[A-Za-z0-9_-]{11}', video_id):
            return 'youtube:' + video_id
    except ValueError:
        pass
    return 'url:' + url if url else 'task:' + task['id']


def json_value(value, default):
    try:
        result = json.loads(value or '')
        return result if isinstance(result, type(default)) else default
    except (ValueError, TypeError):
        return default


def submitted(task):
    from modules.task_manager import _task_has_upload_response
    try:
        return _task_has_upload_response(task)
    except ValueError:
        return False


def load_groups():
    from modules.task_manager import get_db_connection
    with get_db_connection() as db:
        tasks = [dict(r) for r in db.execute('SELECT * FROM tasks ORDER BY created_at,id')]
        links = [dict(r) for r in db.execute('SELECT root_id,task_id,platform FROM publication_jobs')]
        selections = [dict(r) for r in db.execute('SELECT * FROM publication_sets')]
    by_id = {t['id']: t for t in tasks}
    parents = {k: k for k in by_id}

    def find(k):
        while parents[k] != k:
            parents[k] = parents[parents[k]]
            k = parents[k]
        return k

    def union(a, b):
        if a in parents and b in parents:
            parents[find(b)] = find(a)

    sources = {}
    for t in tasks:
        key = source_key(t)
        union(sources.setdefault(key, t['id']), t['id'])
    for link in links:
        union(link['root_id'], link['task_id'])
    buckets = {}
    for task in tasks:
        buckets.setdefault(find(task['id']), []).append(task)
    explicit_roots = {r['root_id'] for r in links if r['root_id'] in by_id}
    groups = []
    for members in buckets.values():
        # Existing material root remains stable as new platform jobs are added.
        roots = [t for t in members if t['id'] in explicit_roots]
        root = (roots or members)[0]
        ids = {t['id'] for t in members}
        states = [s for s in selections if s['root_id'] in ids]
        planned = {p for s in states for p in json_value(s['targets'], []) if p in PLATFORM_LABELS}
        groups.append({'root': root, 'members': members, 'member_ids': ids, 'planned': planned, 'states': states})
    return groups


def group_for(task_id, groups=None):
    return next((g for g in (groups if groups is not None else load_groups()) if task_id in g['member_ids']), None)


def project(group):
    root, members = group['root'], group['members']
    task = dict(root)
    platforms = []
    for platform, label in PLATFORM_LABELS.items():
        records = [t for t in members if t.get('upload_target') == platform]
        successes = [t for t in records if submitted(t)]
        current = max(successes or records, key=lambda t: (t.get('updated_at') or t.get('created_at') or '', t['id'])) if records else None
        if not current and platform not in group['planned']:
            continue
        done = bool(successes)
        record_status = current.get('status') if current else 'awaiting_manual_review'
        error = current.get('error_message') if current and not done else None
        needs_check = bool(current and not done and (record_status == 'completed' or '未确认发表结果' in (error or '')))
        if done:
            state_label = '已提交'
        elif needs_check:
            state_label = '待核对'
        elif error or record_status == 'failed':
            state_label = '待处理'
        elif record_status == 'uploading':
            state_label = '上传中'
        else:
            state_label = '待发布'
        url = MANAGE_URLS.get(platform)
        receipt_id = ''
        if done and platform == 'bilibili':
            receipt = json_value(current.get('bilibili_upload_response'), {})
            receipt_id = str(receipt.get('bvid') or '')
            url = 'https://www.bilibili.com/video/' + receipt_id + '/' if re.fullmatch(r'BV[A-Za-z0-9]+', receipt_id) else 'https://member.bilibili.com/platform/upload-manager/article'
        platforms.append(dict(platform=platform, label=label, submitted=done, state=state_label,
                              url=url if done else None, receipt_id=receipt_id, task_id=current['id'] if current else root['id'],
                              error=error, record=current, needs_check=needs_check))
    pending = [p for p in platforms if not p['submitted']]
    done_count = len(platforms) - len(pending)
    phases = {s.get('phase') for s in group['states']}
    in_flight = [p['record'] for p in pending if p['record'] and p['record']['status'] in IN_FLIGHT]
    state_task = in_flight[-1] if in_flight else next((p['record'] for p in pending if p['record']), root)
    all_done = bool(platforms) and not pending and not phases.intersection({'preparing', 'sending'})
    if all_done:
        state, label = 'completed', '已完成'
    elif in_flight:
        state, label = state_task['status'], None
    elif 'sending' in phases:
        state, label = 'uploading', '正在发布'
    elif 'preparing' in phases:
        state, label = 'translating', '准备发布文案'
    elif any(p['error'] or p['needs_check'] or (p['record'] and p['record']['status'] == 'failed') for p in pending):
        state, label = 'failed', '部分待处理' if done_count else '待处理'
    else:
        state = state_task.get('status') or 'pending'
        if state == 'completed':
            state = 'ready_for_upload'
        label = '待补充发布' if done_count else None
    tags, seen = [], set()
    for row in [root] + members:
        for tag in json_value(row.get('tags_generated'), []):
            if isinstance(tag, str) and tag.strip() and tag.strip().casefold() not in seen:
                tags.append(tag.strip()); seen.add(tag.strip().casefold())
    task.update(status=state, list_status_label=label, group_category='completed' if all_done else 'active',
                member_ids=sorted(group['member_ids']), group_platforms=platforms, group_records=members,
                submitted_count=done_count, platform_count=len(platforms),
                group_can_supplement=done_count > 0 and done_count < len(PLATFORM_LABELS),
                group_busy=bool(in_flight or phases.intersection({'preparing', 'sending'})),
                tags_generated=json.dumps(tags, ensure_ascii=False),
                updated_at=max((t.get('updated_at') or t.get('created_at') or '') for t in members),
                upload_progress=state_task.get('upload_progress'),
                error_message=state_task.get('error_message'))
    return task


def paginate(page=1, per_page=20, category='all'):
    rows = [project(g) for g in load_groups()]
    rows.sort(key=lambda t: (t['updated_at'], t['id']), reverse=True)
    rows.sort(key=lambda t: t['group_category'] == 'completed')
    counts = {'all': len(rows), 'active': sum(t['group_category']=='active' for t in rows),
              'completed': sum(t['group_category']=='completed' for t in rows)}
    category = category if category in counts else 'all'
    selected = [t for t in rows if category == 'all' or t['group_category'] == category]
    total = len(selected)
    per_page = max(1, per_page)
    pages = max(1, (total + per_page - 1)//per_page)
    page = max(1, min(page, pages))
    return dict(tasks=selected[(page-1)*per_page:page*per_page], total=total, page=page, per_page=per_page,
                total_pages=pages, has_prev=page>1, has_next=page<pages, prev_page=page-1, next_page=page+1,
                category=category, counts=counts, failed_count=sum(any(p['error'] or (p['record'] and p['record']['status']=='failed') for p in t['group_platforms']) for t in rows))
