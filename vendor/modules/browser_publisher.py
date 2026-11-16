"""Manual publishing gateway for Douyin and WeChat Channels."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import threading
import time
import requests
from modules.publishing_platforms import metadata_limits, topic_limit

_upload_locks = {p: threading.Lock() for p in ('douyin', 'wechat_channels', 'kuaishou', 'xiaohongshu')}


def ensure_account_health(platforms):
    """Validate targets; each uploader verifies credentials live before transfer."""
    from modules.publishing_platforms import PLATFORM_LABELS
    # A stale or unavailable diagnostic cache is not an authorization decision.
    # BilibiliUploader validates remotely; browser workers call ensure_ready.
    for platform in platforms:
        if platform not in PLATFORM_LABELS:
            raise ValueError('不支持的发布平台')

def publisher_request(method, path, data=None):
    token = Path(os.environ.get('PUBLISHER_TOKEN_FILE', '/run/secrets/publisher_token')).read_text().strip()
    base = os.environ.get('PUBLISHER_URL', 'http://y2a-publisher:5001').rstrip('/')
    # Retry reads only. A lost POST response must never create another publication.
    attempts = 3 if method.upper() == 'GET' else 1
    for attempt in range(attempts):
        try:
            response = requests.request(method, base + path, json=data,
                                        headers={'X-Publisher-Token': token}, timeout=20)
            if response.status_code >= 400:
                try:
                    message = response.json().get('message')
                except ValueError:
                    message = None
                if method.upper() == 'GET' and response.status_code in (408, 429, 500, 502, 503, 504) and attempt + 1 < attempts:
                    time.sleep(attempt + 1)
                    continue
                raise RuntimeError(message or f'发布服务响应异常（HTTP {response.status_code}），请稍后检查任务状态。')
            return response.json()
        except (requests.RequestException, ValueError):
            if attempt + 1 == attempts:
                raise RuntimeError('发布服务连接暂时中断，请稍后检查任务回执。') from None
            time.sleep(attempt + 1)


def compatible_video(source):
    """Keep originals; convert incompatible video/audio for browser creator portals."""
    source = Path(source).resolve()
    from modules.utils import get_app_subdir
    root = Path(get_app_subdir('downloads')).resolve()
    if not source.is_relative_to(root) or not source.is_file():
        raise ValueError('本地视频不存在')
    probe = subprocess.run(['ffprobe', '-v', 'error', '-show_streams', '-of', 'json', str(source)],
                           capture_output=True, text=True, check=True, timeout=30)
    streams = json.loads(probe.stdout)['streams']
    videos = [s for s in streams if s.get('codec_type') == 'video']
    audios = [s for s in streams if s.get('codec_type') == 'audio']
    if videos and videos[0]['codec_name'] == 'h264' and videos[0].get('pix_fmt') == 'yuv420p' and all(s['codec_name'] == 'aac' for s in audios) and source.suffix.lower() == '.mp4':
        return str(source)
    stat = source.stat()
    key = hashlib.sha256(f'{source}:{stat.st_size}:{stat.st_mtime_ns}:v1'.encode()).hexdigest()[:12]
    target = source.with_name(f'publish-compatible-{key}.mp4')
    if target.is_file() and target.stat().st_size > 0:
        return str(target)
    temporary = target.with_suffix('.part.mp4')
    try:
        subprocess.run(['ffmpeg', '-nostdin', '-hide_banner', '-loglevel', 'error', '-y', '-i', str(source),
                        '-map', '0:v:0', '-map', '0:a:0?', '-vf',
                        "scale=w='min(1920,iw)':h='min(1080,ih)':force_original_aspect_ratio=decrease:force_divisible_by=2",
                        '-c:v', 'libx264', '-preset', 'fast', '-crf', '20', '-pix_fmt', 'yuv420p',
                        '-c:a', 'aac', '-b:a', '192k', '-movflags', '+faststart', str(temporary)],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True, timeout=7200)
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
    return str(target)

def validate_metadata(task):
    from modules.description_policy import clean_description
    target = task['upload_target']
    limits = metadata_limits(target)
    title = str(task.get('video_title_translated') or task.get('video_title_original') or '').strip()
    description = clean_description(task.get('description_translated') or task.get('description_original'), task.get('youtube_url'))
    tags = json.loads(task.get('tags_generated') or '[]')
    if not title or len(title) > limits['title_limit']:
        raise ValueError(f"请先审核标题，最多 {limits['title_limit']} 字")
    if target == 'wechat_channels' and len(title) < 6:
        raise ValueError('视频号短标题需要 6–16 字')
    if len(description) > limits['description_limit']:
        raise ValueError(f"请先缩减简介至 {limits['description_limit']} 字以内")
    if not isinstance(tags, list) or len(tags) > topic_limit(target) or any(not isinstance(t, str) for t in tags):
        raise ValueError(f'话题最多 {topic_limit(target)} 个')
    tags = [t.strip() for t in tags if t.strip()]
    if any(len(t) > 20 or any(c in t for c in '#,\n\r') for t in tags):
        raise ValueError('每个话题最多 20 字，请去掉井号、逗号或换行')
    return title, description, tags

def upload_reviewed_task(task, logger):
    from modules.task_manager import update_task, get_task, _task_has_upload_response
    task_id = task['id']
    with _upload_locks[task['upload_target']]:
        task = get_task(task_id)
        if _task_has_upload_response(task):
            return
        try:
            from modules.title_cover import repair_missing_inherited_cover
            task = repair_missing_inherited_cover(task)
            title, description, tags = validate_metadata(task)
            cover_path = task.get('cover_path_local') or ''
            from modules.title_cover import cover_paths
            generated_covers = cover_paths(task)
            if task['upload_target'] == 'xiaohongshu':
                from modules.xiaohongshu_workflow import cover_for_upload
                cover_path = cover_for_upload(task)
            accounts = publisher_request('GET', '/accounts')['accounts']
            if not accounts.get(task['upload_target'], {}).get('saved'):
                raise ValueError('请先在账号管理的“账号与登录”中扫码登录目标平台')
            update_task(task_id, status='uploading', upload_progress=None, error_message=None)
            video = compatible_video(task.get('video_path_local') or '')
            current = get_task(task_id)
            if not current or current.get('status') != 'uploading':
                return
            job = publisher_request('POST', '/uploads', {
                'task_id': task_id, 'platform': task['upload_target'], 'reviewed': True,
                'title': title, 'description': description, 'tags': tags,
                'video_path': video, 'cover_path': cover_path,
                'cover_portrait_path': generated_covers.get('portrait') if task['upload_target'] in ('douyin', 'wechat_channels') else None,
                'cover_landscape_path': generated_covers.get('landscape') if task['upload_target'] == 'wechat_channels' else None,
                'source_url': task.get('youtube_url') or '',
            })
            deadline = time.monotonic() + 12700
            while job['status'] not in {'succeeded', 'failed', 'needs_check'}:
                if time.monotonic() > deadline:
                    raise RuntimeError('等待投稿结果超时。请先检查平台记录，再决定是否重试。')
                time.sleep(3)
                try:
                    job = publisher_request('GET', '/jobs/' + job['id'])
                except RuntimeError:
                    update_task(task_id, upload_progress=None, error_message='正在等待后台发布回执，请勿重复投稿。')
                    continue
            if job['status'] != 'succeeded':
                raise RuntimeError(job['message'])
            update_task(task_id, status='completed', external_upload_response=json.dumps(job['receipt'], ensure_ascii=False),
                        upload_progress=None, error_message=None, error_category=None)
        except Exception as exc:
            # Never include requests headers or platform browser exception dumps in task errors.
            message = str(exc) if isinstance(exc, (ValueError, RuntimeError)) else '发布服务连接异常，请在设置页检查账号和服务状态。'
            logger.warning('外部平台投稿未完成: %s', message)
            update_task(task_id, status='ready_for_upload', error_message=message, upload_progress=None)

def register_publisher_routes(app, login_required):
    from flask import request, jsonify, abort
    from urllib.parse import urlsplit
    from modules.publishing_platforms import (PLATFORM_LABELS, EXTERNAL_PLATFORMS, CREATOR_URLS,
                                            MANAGE_URLS, LOGIN_HELP, platform_label, metadata_limits)
    app.jinja_env.globals.update(platform_label=platform_label, platform_choices=PLATFORM_LABELS,
                                 metadata_limits=metadata_limits, external_platforms=EXTERNAL_PLATFORMS,
                                 creator_urls=CREATOR_URLS, manage_urls=MANAGE_URLS, login_help=LOGIN_HELP)

    @app.before_request
    def enforce_publishing_boundary():
        if '/acfun/' in request.path:
            abort(410)
        if request.method == 'POST' and request.path.startswith('/publisher/'):
            origin = request.headers.get('Origin')
            if request.headers.get('X-Requested-With') != 'XMLHttpRequest' or (origin and urlsplit(origin).netloc != request.host):
                abort(403)

    @app.get('/publisher/accounts')
    @login_required
    def publisher_accounts():
        try:
            return jsonify(publisher_request('GET', '/accounts'))
        except Exception:
            return jsonify(message='发布组件暂未就绪，请稍后重试'), 503

    @app.route('/publisher/account-health', methods=['GET'])
    @login_required
    def account_health_status():
        try:
            return jsonify(publisher_request('GET', '/account-health'))
        except Exception:
            return jsonify(message='账号检测服务暂不可用，不能确认登录状态。'), 503

    @app.post('/publisher/account-health/<platform>')
    @login_required
    def account_health_refresh(platform):
        if platform not in (*EXTERNAL_PLATFORMS, 'bilibili', 'all'):
            abort(404)
        try:
            return jsonify(publisher_request('POST', '/account-health/' + platform)), 202
        except Exception:
            return jsonify(message='自检暂时无法启动，请稍后重试。'), 503

    @app.post('/publisher/login/<platform>')
    @login_required
    def publisher_login(platform):
        if platform not in EXTERNAL_PLATFORMS:
            abort(404)
        try:
            return jsonify(publisher_request('POST', '/login/' + platform))
        except Exception:
            return jsonify(message='无法启动登录，请稍后重试'), 503

    @app.post('/publisher/jobs/<job_id>/refresh-qrcode')
    @login_required
    def publisher_refresh_qrcode(job_id):
        import re
        if not re.fullmatch(r'[a-f0-9]{32,64}', job_id):
            abort(404)
        try:
            return jsonify(publisher_request('POST', '/jobs/' + job_id + '/refresh-qrcode'))
        except Exception:
            return jsonify(message='刷新未完成；若登录已结束，请重新连接账号。'), 409

    @app.get('/publisher/jobs/<job_id>')
    @login_required
    def publisher_job(job_id):
        import re
        if not re.fullmatch(r'[a-f0-9]{32,64}', job_id):
            abort(404)
        try:
            result = jsonify(publisher_request('GET', '/jobs/' + job_id))
            result.headers['Cache-Control'] = 'no-store'
            return result
        except Exception:
            return jsonify(message='状态读取失败，请稍后重试'), 503

    @app.post('/publisher/tasks/<task_id>/confirm-not-published')
    @login_required
    def publisher_confirm_not_published(task_id):
        from modules.task_manager import get_task, update_task
        task = get_task(task_id)
        if not task or task.get('upload_target') not in EXTERNAL_PLATFORMS:
            abort(404)
        if (request.get_json(silent=True) or {}).get('checked_not_published') is not True:
            abort(400)
        job_id = hashlib.sha256((task['upload_target'] + ':' + task_id).encode()).hexdigest()
        try:
            publisher_request('POST', '/jobs/' + job_id + '/confirm-not-published', {'checked_not_published': True})
            update_task(task_id, status='ready_for_upload', error_message=None)
            return jsonify(message='已解除待核对状态；审核文案后可再次点击上传。')
        except RuntimeError as exc:
            return jsonify(message=str(exc)), 409
        except Exception:
            return jsonify(message='发布组件暂不可用，请稍后重试'), 503

    @app.get('/publisher/tasks/<task_id>/verification')
    @login_required
    def task_verification(task_id):
        from modules.task_manager import get_task
        task = get_task(task_id)
        if not task or task.get('upload_target') not in _upload_locks:
            abort(404)
        job_id = hashlib.sha256((task['upload_target'] + ':' + task_id).encode()).hexdigest()
        try:
            job = publisher_request('GET', '/jobs/' + job_id)
            response = jsonify(id=job_id, verification=job.get('verification') if job.get('status') == 'running' else None,
                               status=job.get('status'), message=job.get('message'),
                               phase=job.get('phase'), qrcode_at=job.get('qrcode_at'),
                               qrcode=job.get('qrcode') if job.get('status') == 'running' else None)
            response.headers['Cache-Control'] = 'no-store'
            return response
        except Exception:
            return jsonify(verification=None)

    @app.post('/publisher/jobs/<job_id>/verification')
    @login_required
    def submit_verification(job_id):
        import re
        if not re.fullmatch(r'[a-f0-9]{64}', job_id):
            abort(404)
        try:
            return jsonify(publisher_request('POST', '/jobs/' + job_id + '/verification', request.get_json(silent=True) or {})), 202
        except Exception:
            return jsonify(message='提交失败：验证会话可能已结束，或上一操作仍在处理。'), 409
