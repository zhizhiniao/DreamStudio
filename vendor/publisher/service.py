"""Private browser publisher. The application is the only public gateway."""
import hashlib
import hmac
import json
import os
import re
import signal
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from urllib.parse import urlsplit
from flask import Flask, request, jsonify, abort
from state import JOBS, PLATFORMS, TITLE_LIMITS, TERMINAL, account_path, read_job, save_job, public_job

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 64 * 1024
lock = threading.Lock()
browser_slots = threading.Semaphore(2)
active = set()
engagement_busy = set()
TOKEN = Path(os.environ.get('PUBLISHER_TOKEN_FILE', '/run/secrets/publisher_token')).read_text().strip()
if len(TOKEN) < 32:
    raise RuntimeError('Publisher token missing')

# A process restart never silently resubmits a possibly published video.
for path in JOBS.glob('*.json'):
    job = json.loads(path.read_text())
    # A verification command is useful only to the original living worker.
    (JOBS / (job['id'] + '.verification')).unlink(missing_ok=True)
    (JOBS / (job['id'] + '.verification.tmp')).unlink(missing_ok=True)
    if job['status'] not in TERMINAL:
        uncertain = job.get('phase') == 'submitting'
        job.update(status='needs_check' if uncertain else 'failed', retryable=not uncertain,
                   qrcode='', message='服务曾重启；请先核对平台投稿记录。' if uncertain else '服务曾重启，请重试。')
        save_job(job)

@app.before_request
def authenticate():
    if request.path != '/health' and not hmac.compare_digest(request.headers.get('X-Publisher-Token', ''), TOKEN):
        abort(401)

@app.get('/health')
def health():
    return jsonify(ok=True)

@app.get('/accounts')
def accounts():
    result = {p: {'saved': account_path(p).is_file()} for p in sorted(PLATFORMS)}
    with lock:
        for job_id in active:
            job = read_job(job_id)
            result[job['platform']]['job'] = public_job(job)
    response = jsonify(accounts=result)
    response.headers['Cache-Control'] = 'no-store'
    return response

def run(job_id):
    retry = False
    try:
        with browser_slots:
            job = read_job(job_id)
            job.update(status='running', message='正在打开平台…')
            save_job(job)
            try:
                process = subprocess.Popen([sys.executable, str(Path(__file__).with_name('runner.py')), job_id],
                                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                           start_new_session=(os.name != 'nt'),
                                           creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
                try:
                    code = process.wait(timeout=420 if job['action'] == 'login' else 4200)
                    if code:
                        raise subprocess.CalledProcessError(code, 'publisher worker')
                finally:
                    # Browser children belong to this worker's process group.
                    try:
                        if os.name == 'nt':
                            if process.poll() is None:
                                subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW)
                        else:
                            os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
            except (subprocess.SubprocessError, OSError):
                job = read_job(job_id)
                if job['status'] not in TERMINAL:
                    uncertain = job.get('phase') == 'submitting'
                    job.update(status='needs_check' if uncertain else 'failed', retryable=not uncertain, qrcode='',
                               message='未确认发表结果，请先检查平台投稿记录，勿重复提交。' if uncertain else '平台响应超时，请重新登录或稍后重试。')
                    save_job(job)
            latest = read_job(job_id)
            attempts = latest.get('attempt', 1)
            if (latest.get('action') == 'upload' and latest.get('status') == 'failed'
                    and latest.get('recovery') == 'connection' and latest.get('phase') not in ('submitting', 'submitted')
                    and attempts < 3):
                latest.update(status='queued', phase='preparing', attempt=attempts+1,
                    recovery=None, error_type=None, failure_step=None, failure_message=None, step=None,
                    message=f'平台连接中断，正在自动恢复原任务（第 {attempts+1}/3 次）；请勿重复发布。')
                save_job(latest)
                # Release the browser slot before retrying in a fresh process.
                retry = True
            else:
                retry = False
    finally:
        (JOBS / (job_id + '.verification')).unlink(missing_ok=True)
        if not retry:
            with lock:
                active.discard(job_id)
    if retry:
        time.sleep(5 * attempts)
        run(job_id)

def dispatch(job):
    save_job(job)
    active.add(job['id'])
    threading.Thread(target=run, args=(job['id'],), daemon=True).start()
    return jsonify(public_job(job)), 202

@app.post('/login/<platform>')
def login(platform):
    if platform not in PLATFORMS:
        abort(404)
    with lock:
        if platform in engagement_busy:
            return jsonify(message='该平台正在自检或处理后台任务，请稍后连接账号。'), 409
        for job_id in active:
            existing = read_job(job_id)
            if existing['platform'] == platform:
                return jsonify(public_job(existing)), 202
        return dispatch(dict(id=uuid.uuid4().hex, platform=platform, action='login', status='queued', phase='login',
                             message='准备登录…', qrcode=''))

@app.post('/jobs/<job_id>/refresh-qrcode')
def refresh_qrcode(job_id):
    if not re.fullmatch(r'[a-f0-9]{32,64}', job_id) or not (JOBS / (job_id + '.json')).is_file():
        abort(404)
    with lock:
        job = read_job(job_id)
        if job['action'] != 'login' or job['platform'] != 'douyin' or job_id not in active or job['status'] in TERMINAL:
            return jsonify(message='登录任务已结束，请重新连接账号。'), 409
        (JOBS / (job_id + '.refresh')).touch()
    return jsonify(message='正在获取新的二维码'), 202

@app.get('/jobs/<job_id>')
def get_job(job_id):
    if not re.fullmatch(r'[a-f0-9]{32,64}', job_id) or not (JOBS / (job_id + '.json')).is_file():
        abort(404)
    return jsonify(public_job(read_job(job_id)))

@app.post('/jobs/<job_id>/confirm-not-published')
def confirm_not_published(job_id):
    if not re.fullmatch(r'[a-f0-9]{64}', job_id) or not (JOBS / (job_id + '.json')).is_file():
        abort(404)
    if (request.get_json(silent=True) or {}).get('checked_not_published') is not True:
        return jsonify(message='请先在平台确认该视频尚未提交'), 400
    with lock:
        job = read_job(job_id)
        if job_id in active or job['status'] != 'needs_check':
            return jsonify(message='当前任务无需解除待核对状态'), 409
        job.update(status='failed', retryable=True, phase='manually_checked',
                   message='已由用户确认未提交，可再次人工投稿。')
        save_job(job)
        return jsonify(public_job(job))


@app.post('/jobs/<job_id>/verification')
def verify_upload(job_id):
    if not re.fullmatch(r'[a-f0-9]{64}', job_id) or not (JOBS / (job_id + '.json')).is_file():
        abort(404)
    data = request.get_json(silent=True) or {}
    with lock:
        job = read_job(job_id)
        if job_id not in active or job.get('platform') != 'douyin' or not job.get('verification') or job.get('status') in TERMINAL:
            return jsonify(message='验证会话已结束，不能提交验证码。'), 409
        if data.get('nonce') != job['verification'] or data.get('action') not in ('send', 'verify'):
            abort(400)
        if data['action'] == 'verify' and not re.fullmatch(r'\d{4,8}', str(data.get('code', ''))):
            return jsonify(message='请输入 4 至 8 位数字验证码'), 400
        target = JOBS / (job_id + '.verification')
        if target.exists():
            return jsonify(message='上一操作正在处理，请稍候。'), 409
        temp = target.with_suffix('.verification.tmp')
        temp.write_text(json.dumps({k:data[k] for k in ('nonce','action','code') if k in data}), encoding='utf-8')
        os.chmod(temp, 0o600)
        temp.replace(target)
    return jsonify(message='操作已交给原发布会话处理。'), 202

def validate_upload(data):
    platform = data.get('platform')
    if platform not in PLATFORMS:
        raise ValueError('不支持的平台')
    if data.get('reviewed') is not True:
        raise ValueError('请先人工审核')
    if platform == 'xiaohongshu':
        source = urlsplit(str(data.get('source_url') or ''))
        if source.scheme != 'https' or source.hostname not in ('youtube.com', 'www.youtube.com', 'youtu.be', 'm.youtube.com'):
            raise ValueError('请保留有效的 YouTube 原视频来源')
    if not re.fullmatch(r'[a-fA-F0-9-]{36}', str(data.get('task_id', ''))):
        raise ValueError('任务编号无效')
    root = Path(os.environ.get('PUBLISHER_DOWNLOADS', str(Path(__file__).resolve().parent.parent / 'downloads'))).resolve()
    for field in ['video_path', 'cover_path', 'cover_portrait_path', 'cover_landscape_path']:
        if field != 'video_path' and not data.get(field):
            continue
        path = Path(data.get(field, '')).resolve()
        label = {'video_path':'视频', 'cover_path':'封面', 'cover_portrait_path':'竖版封面', 'cover_landscape_path':'横版封面'}[field]
        if not path.is_relative_to(root):
            raise ValueError(label + '路径不在本项目素材目录内，请重新准备该平台稿件。')
        if not path.is_file():
            raise ValueError(label + '文件缺失，请在该平台稿件中恢复或重新选择。')
    title = data.get('title', '')
    if not isinstance(title, str) or not title.strip() or len(title) > TITLE_LIMITS[platform]:
        raise ValueError('标题长度不符合目标平台要求')
    if platform == 'wechat_channels' and len(title) < 6:
        raise ValueError('视频号短标题需要 6–16 字')
    if platform == 'wechat_channels' and any(not (c.isalnum() or c in '《》“”:+?%°， ') for c in title):
        raise ValueError('视频号短标题含不支持的符号；请去掉小数点等字符，例如将 5.5 改为五点五')
    if not isinstance(data.get('description'), str) or len(data['description']) > 800:
        raise ValueError('简介请控制在 800 字以内，为话题预留空间')
    tags = data.get('tags')
    tag_limit = 3 if platform == 'kuaishou' else 6
    if not isinstance(tags, list) or len(tags) > tag_limit or any(not isinstance(t, str) or not 1 <= len(t) <= 20 or any(c in t for c in '#,\n\r') for t in tags):
        raise ValueError(f'话题最多 {tag_limit} 个，每个不超过 20 字')
    return platform

@app.post('/uploads')
def upload():
    data = request.get_json(silent=True) or {}
    try:
        platform = validate_upload(data)
    except (ValueError, TypeError) as exc:
        return jsonify(message=str(exc)), 400
    if not account_path(platform).is_file():
        return jsonify(message='请先在账号管理的“账号与登录”中扫码登录目标平台'), 409
    # One receipt per task and platform, regardless of edits after a successful submission.
    job_id = hashlib.sha256((platform + ':' + data['task_id']).encode()).hexdigest()
    with lock:
        if platform in engagement_busy:
            return jsonify(message='该平台正在自检或处理后台任务，请稍后上传。'), 409
        if (JOBS / (job_id + '.json')).exists():
            previous = read_job(job_id)
            if previous['status'] != 'failed' or not previous.get('retryable'):
                return jsonify(public_job(previous)), 200
        if any(read_job(j)['platform'] == platform for j in active):
            return jsonify(message='该平台正在登录或上传，请稍后重试'), 409
        return dispatch(dict(id=job_id, platform=platform, action='upload', payload=data,
                             status='queued', phase='preparing', message='准备上传…', qrcode=''))

from engagement import register as register_engagement
engagement_store = register_engagement(app, lock, browser_slots, active, read_job, engagement_busy)

from topics import register as register_topics
register_topics(app, lock, browser_slots, active, read_job, engagement_busy)
from account_health import register as register_account_health
register_account_health(app, lock, browser_slots, active, read_job, engagement_busy)
