"""Read-only account data. Credentials and upstream payloads never reach clients."""
import json
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

import requests

_lock = threading.Lock()
_cache = {}


class AccountReadError(Exception):
    def __init__(self, code, message):
        self.code, self.message = code, message


def count(value):
    try:
        return max(0, int(value)) if value is not None else None
    except (TypeError, ValueError):
        return None


def avatar_url(value):
    if isinstance(value, dict):
        value = next(iter(value.get('url_list') or []), '')
    if not isinstance(value, str):
        return ''
    url = urlsplit(value)
    return value if url.scheme == 'https' and any((url.hostname or '').endswith('.' + d) for d in ('douyinpic.com', 'byteimg.com', 'douyincdn.com')) else ''


def normalize_profile(user, verified):
    uid = str(user.get('uid') or '')
    if not uid or not user.get('nickname'):
        raise AccountReadError('invalid_response', '平台没有返回可验证的账号信息，请重新连接账号。')
    return {
        'id': uid, 'nickname': str(user['nickname']),
        'handle': str(verified.get('douyin_unique_id') or user.get('unique_id') or user.get('short_id') or ''),
        'avatar': avatar_url(user.get('avatar_medium')),
        'signature': str(user.get('signature') or ''),
        'followers': count(user.get('follower_count')),
        'following': count(user.get('following_count')),
        'likes': count(user.get('total_favorited')),
        'works_count': count(user.get('aweme_count')),
    }


def normalize_work(work):
    stats = work.get('statistics') or {}
    description = str(work.get('desc') or work.get('item_title') or '未命名作品')
    short_title = str(work.get('item_title') or description.splitlines()[0]).split('#', 1)[0].strip()
    if not short_title:
        short_title = description.splitlines()[0].strip() or '未命名作品'
    video = work.get('video') or {}
    return {'id': str(work['aweme_id']),
            'title': description,
            'display_title': short_title[:60] + ('…' if len(short_title) > 60 else ''),
            'cover': avatar_url(video.get('cover')) or avatar_url(video.get('origin_cover')),
            'created_at': count(work.get('create_time')),
            **{key: count(stats.get(source)) for key, source in {
                'plays': 'play_count', 'likes': 'digg_count', 'comments': 'comment_count',
                'shares': 'share_count', 'collections': 'collect_count'}.items()}}


def _read(session, path, params=None):
    try:
        response = session.get('https://creator.douyin.com' + path, params=params,
                               timeout=(5, 12), allow_redirects=False)
        if response.status_code in (401, 403):
            raise AccountReadError('authorization_required', '平台拒绝读取，请重新连接抖音账号。')
        if response.status_code != 200:
            raise AccountReadError('temporarily_unavailable', '抖音接口暂时不可用，请稍后刷新。')
        data = json.loads(response.content)
        if not isinstance(data, dict):
            raise AccountReadError('invalid_response', '平台返回格式异常，请稍后重试。')
        if data.get('status_code') == 8:
            raise AccountReadError('authorization_required', '抖音登录状态未被该接口接受，请重新连接或稍后重试。')
        if data.get('status_code') != 0:
            raise AccountReadError('platform_rejected', '平台未返回可用数据，部分评论可能不可读取。')
        return data
    except (requests.RequestException, ValueError):
        raise AccountReadError('temporarily_unavailable', '网络或平台响应异常，暂时无法读取真实数据。') from None


def snapshot(platform):
    if platform != 'douyin':
        return {'platform': platform, 'state': 'not_configured', 'message': '该平台的数据读取尚未接入。', 'profile': None, 'works': []}
    root = Path(os.environ.get('PUBLISHER_STATE', str(Path(__file__).resolve().parents[2] / 'publisher-state')))
    path = root / 'accounts' / 'douyin.json'
    if not path.is_file():
        return {'platform': platform, 'state': 'authorization_required', 'message': '请先连接你的抖音账号。', 'profile': None, 'works': []}
    with _lock:
        stamp = path.stat().st_mtime_ns
        cached = _cache.get(platform)
        if cached and cached[0] == stamp and time.monotonic() - cached[1] < 30:
            return cached[2]
        try:
            saved = json.loads(path.read_text(encoding='utf-8'))
            with requests.Session() as session:
                for cookie in saved.get('cookies', []):
                    if cookie.get('domain', '').lstrip('.') in ('douyin.com', 'creator.douyin.com'):
                        session.cookies.set(cookie['name'], cookie['value'], domain=cookie['domain'], path=cookie.get('path', '/'))
                session.headers.update({'User-Agent': 'Mozilla/5.0', 'Referer': 'https://creator.douyin.com/'})
                user = _read(session, '/web/api/media/user/info/').get('user') or {}
                # Supplement the public handle; the authenticated user is the authority.
                try:
                    verified = _read(session, '/aweme/v1/creator/user/info/').get('douyin_user_verify_info') or {}
                except AccountReadError:
                    verified = {}
                profile = normalize_profile(user, verified)
                works, seen, cursor, more, works_error = [], set(), 0, True, None
                try:
                    for _ in range(3):
                        page = _read(session, '/web/api/media/aweme/post/', {'count': 50, 'max_cursor': cursor})
                        if not isinstance(page.get('aweme_list'), list):
                            raise AccountReadError('invalid_response', '作品列表暂时无法读取。')
                        for work in page['aweme_list']:
                            if work.get('aweme_id') and str(work.get('author_user_id') or (work.get('author') or {}).get('uid')) == profile['id']:
                                item = normalize_work(work)
                                if item['id'] not in seen:
                                    works.append(item)
                                    seen.add(item['id'])
                        more = bool(page.get('has_more'))
                        next_cursor = page.get('max_cursor')
                        if not more or not next_cursor or next_cursor == cursor:
                            break
                        cursor = next_cursor
                except AccountReadError as exc:
                    works_error = exc.message
                    more = True
                result = {'platform': platform, 'state': 'connected', 'profile': profile, 'works': works,
                          'works_partial': more, 'works_error': works_error,
                          'updated_at': datetime.now(timezone.utc).isoformat(),
                          'source': '抖音创作者中心',
                          'comments_state': 'available',
                          'comments_message': '已接通评论读取，可在评论收件箱同步评论及楼中楼回复。',
                          'reply_state': 'not_configured'}
        except AccountReadError as exc:
            result = {'platform': platform, 'state': exc.code, 'message': exc.message, 'profile': None, 'works': []}
        except (OSError, ValueError, KeyError, TypeError):
            result = {'platform': platform, 'state': 'invalid_response', 'message': '账号状态或平台数据格式异常，请重新连接账号。', 'profile': None, 'works': []}
        _cache[platform] = (stamp, time.monotonic(), result)
        return result


def register_live_routes(app, login_required):
    from flask import jsonify, abort
    from .comments import register_comments_routes
    register_comments_routes(app, login_required)
    from .automation_routes import register_automation_routes
    register_automation_routes(app, login_required)

    @app.get('/api/account-center/v1/platforms/<platform>/snapshot')
    @login_required
    def account_center_snapshot(platform):
        if platform not in ('douyin', 'kuaishou', 'wechat_channels', 'xiaohongshu', 'bilibili'):
            abort(404)
        response = jsonify(snapshot(platform))
        response.headers['Cache-Control'] = 'no-store'
        return response
