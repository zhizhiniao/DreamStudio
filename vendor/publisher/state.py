import json
import os
import time
from pathlib import Path

STATE = Path(os.environ.get('PUBLISHER_STATE', '/state'))
JOBS = STATE / 'jobs'
JOBS.mkdir(parents=True, exist_ok=True)
TITLE_LIMITS = {'douyin': 30, 'wechat_channels': 16, 'xiaohongshu': 20, 'kuaishou': 30}
PLATFORMS = set(TITLE_LIMITS)
TERMINAL = {'succeeded', 'failed', 'needs_check'}

def read_job(job_id):
    return json.loads((JOBS / (job_id + '.json')).read_text(encoding='utf-8'))

def save_job(job):
    job['updated_at'] = time.time()
    target = JOBS / (job['id'] + '.json')
    temp = target.with_suffix('.tmp')
    temp.write_text(json.dumps(job, ensure_ascii=False), encoding='utf-8')
    os.chmod(temp, 0o600)
    temp.replace(target)

def account_path(platform):
    if platform not in PLATFORMS:
        raise ValueError('不支持的平台')
    folder = STATE / 'accounts'
    folder.mkdir(exist_ok=True)
    return folder / (platform + '.json')

def public_job(job):
    return {k: v for k, v in job.items() if k in {
        'id', 'platform', 'action', 'status', 'phase', 'message', 'updated_at',
        'qrcode', 'qrcode_at', 'login_state', 'receipt', 'retryable', 'step', 'failure_step', 'verification'}}
