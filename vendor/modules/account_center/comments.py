"""Read comments from the authenticated creator portal; never post replies."""
import hashlib
import json
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

import requests

from .live import AccountReadError, _read, avatar_url, count, snapshot

_guard = threading.Lock()
_runs = {}


def root():
    return Path(os.environ.get('PUBLISHER_STATE', str(Path(__file__).resolve().parents[2] / 'publisher-state')))


def storage(uid):
    # Only hashed identifiers form file names, never client-supplied paths.
    return root() / 'account-center' / ('douyin-' + hashlib.sha256(uid.encode()).hexdigest()[:24] + '-comments.json')


def key(uid, work_id, comment_id):
    return hashlib.sha256(f'douyin:{uid}:{work_id}:{comment_id}'.encode()).hexdigest()


def normalize_comment(row, uid, work, parent=None):
    person = row.get('user_info') or {}
    if not row.get('comment_id') or not isinstance(row.get('text'), str):
        raise AccountReadError('invalid_response', '部分评论字段不完整，请稍后重试。')
    return {'id': key(uid, work['id'], str(row['comment_id'])), 'work_id': work['id'],
            'work_title': work['title'], 'parent_id': parent,
            'text': row['text'], 'author': str(person.get('screen_name') or '未提供昵称'),
            'avatar': avatar_url(person.get('avatar_url')),
            'is_author': row.get('is_author') is True,
            'created_at': count(row.get('create_time')), 'likes': count(row.get('digg_count')),
            'reply_count': count(row.get('reply_count'))}


def collect(account):
    uid = account['profile']['id']
    path = root() / 'accounts/douyin.json'
    stamp = path.stat().st_mtime_ns
    saved = json.loads(path.read_text(encoding='utf-8'))
    own_works = {w['id']: w for w in account['works']}
    comments, seen, errors, scanned = [], set(), [], 0
    started = time.monotonic()
    calls = 0
    with requests.Session() as session:
        for c in saved.get('cookies', []):
            if c.get('domain', '').lstrip('.') in ('douyin.com', 'creator.douyin.com'):
                session.cookies.set(c['name'], c['value'], domain=c['domain'], path=c.get('path', '/'))
        session.headers.update({'User-Agent': 'Mozilla/5.0', 'Referer': 'https://creator.douyin.com/creator-micro/interactive/comment'})
        if str((_read(session, '/web/api/media/user/info/').get('user') or {}).get('uid')) != uid:
            raise AccountReadError('account_changed', '账号已切换，请重新同步。')

        def pages(endpoint, params, field, limit=20):
            nonlocal calls
            cursor, cursors = 0, set()
            for _ in range(limit):
                if calls >= 100 or time.monotonic() - started > 90:
                    raise AccountReadError('partial', '本轮同步达到读取上限，仅显示已获取的评论。')
                calls += 1
                data = _read(session, endpoint, dict(params, cursor=cursor, count=20))
                rows = data.get(field)
                if not isinstance(rows, list):
                    raise AccountReadError('invalid_response', '平台未返回有效评论列表。')
                yield rows
                if not data.get('has_more'):
                    return
                next_cursor = data.get('cursor')
                if next_cursor is None or str(next_cursor) in cursors or str(next_cursor) == str(cursor):
                    raise AccountReadError('partial', '平台分页游标未前进，仅显示已获取的评论。')
                cursors.add(str(cursor))
                cursor = next_cursor
            raise AccountReadError('partial', '评论分页达到本轮上限，仅显示已获取的评论。')

        def add(row, work, parent=None):
            item = normalize_comment(row, uid, work, parent)
            if item['id'] not in seen:
                comments.append(item)
                seen.add(item['id'])
            return item

        catalog = []
        try:
            for rows in pages('/aweme/v1/creator/item/list/', {}, 'item_info_list', 10):
                catalog.extend(rows)
        except AccountReadError as exc:
            errors.append(exc.message)
        catalog_seen = set()
        for item in catalog:
            work_id = str(item.get('item_id_plain') or '')
            # Creator-specific encrypted item_id is required, not the public video ID.
            if work_id not in own_works or work_id in catalog_seen or not item.get('item_id'):
                continue
            catalog_seen.add(work_id)
            work = own_works[work_id]
            if count(item.get('comment_count')) == 0:
                continue
            try:
                for rows in pages('/aweme/v1/creator/comment/list/', {'item_id': item['item_id'], 'sort': 1}, 'comment_info_list'):
                    for row in rows:
                        parent = add(row, work)
                        if count(row.get('reply_count')):
                            try:
                                for replies in pages('/aweme/v1/creator/comment/reply/list/', {'comment_id': row['comment_id'], 'sort': 1}, 'comment_info_list'):
                                    for reply in replies:
                                        add(reply, work, parent['id'])
                            except AccountReadError as exc:
                                errors.append(exc.message)
                scanned += 1
            except AccountReadError as exc:
                errors.append(exc.message)
        if account.get('works_partial'):
            errors.append('作品列表未完整读取，评论同步范围也可能不完整。')
        missing = {w['id'] for w in own_works.values() if w.get('comments')} - catalog_seen
        if missing:
            errors.append('部分作品未出现在评论管理列表中，未将它们当作无评论。')
    if path.stat().st_mtime_ns != stamp:
        raise AccountReadError('account_changed', '登录状态已更新，请重新同步。')
    result = {'platform': 'douyin', 'account_id': uid, 'state': 'partial' if errors else 'ready',
              'comments': sorted(comments, key=lambda c: c['created_at'] or 0, reverse=True),
              'updated_at': datetime.now(timezone.utc).isoformat(), 'works_scanned': scanned,
              'source': '抖音创作者中心评论管理', 'errors': list(dict.fromkeys(errors)),
              'reply_enabled': False}
    # Never replace a successful snapshot with an empty failed read.
    if errors and not comments:
        raise AccountReadError('temporarily_unavailable', errors[0])
    target = storage(uid)
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_suffix('.tmp')
    temp.write_text(json.dumps(result, ensure_ascii=False), encoding='utf-8')
    temp.replace(target)
    return result


def read_saved(uid):
    try:
        data = json.loads(storage(uid).read_text(encoding='utf-8'))
        if data.get('account_id') == uid:
            return data
    except (OSError, ValueError):
        pass
    return {'platform': 'douyin', 'account_id': uid, 'state': 'idle', 'comments': [], 'errors': []}


def state(account):
    uid = account['profile']['id']
    saved = read_saved(uid)
    with _guard:
        run = dict(_runs.get(uid) or {})
    if run:
        saved = dict(saved, sync_state=run['state'], sync_message=run.get('message', ''))
    return saved


def start(account):
    uid = account['profile']['id']
    with _guard:
        previous = _runs.get(uid) or {}
        if previous.get('state') == 'running' or time.monotonic() - previous.get('started', -1000) < 30:
            return
        _runs[uid] = {'state': 'running', 'message': '正在读取真实评论及楼中楼回复…', 'started': time.monotonic()}

    def worker():
        try:
            result = collect(account)
            update = {'state': 'done', 'message': f"已读取 {len(result['comments'])} 条评论与回复"}
        except AccountReadError as exc:
            update = {'state': 'failed', 'message': exc.message}
        except Exception:
            update = {'state': 'failed', 'message': '评论同步失败，已保留上次成功结果，请稍后重试。'}
        with _guard:
            _runs[uid].update(update)
    threading.Thread(target=worker, daemon=True).start()


def register_comments_routes(app, login_required):
    from flask import jsonify, request, abort

    @app.route('/api/account-center/v1/platforms/douyin/comments', methods=['GET', 'POST'])
    @login_required
    def account_center_comments():
        if request.method == 'POST':
            origin = request.headers.get('Origin')
            if request.headers.get('X-Requested-With') != 'XMLHttpRequest' or (origin and urlsplit(origin).netloc != request.host):
                abort(403)
        account = snapshot('douyin')
        if not account.get('profile'):
            data = {'state': account['state'], 'message': account.get('message'), 'comments': []}
        else:
            if request.method == 'POST':
                start(account)
            data = state(account)
        response = jsonify(data)
        response.headers['Cache-Control'] = 'no-store'
        return response, 202 if data.get('sync_state') == 'running' else 200
