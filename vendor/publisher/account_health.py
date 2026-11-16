"""12-hour account checks owned by the resident publisher, including manual checks."""
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from flask import jsonify, request
from state import STATE, PLATFORMS, account_path

INTERVAL = 12 * 3600
ALL = ('bilibili', *sorted(PLATFORMS))


def register(app, lock, slots, active, read_job, busy):
    directory = STATE / 'account-health'
    directory.mkdir(exist_ok=True)
    pending, checking = set(), set()
    wake = threading.Event()

    def stamp(platform):
        if platform == 'bilibili':
            from account_health_worker import bili_path
            path = bili_path()
        else:
            path = account_path(platform)
        return path.stat().st_mtime_ns if path.is_file() else 0

    def read(platform):
        try:
            data = json.loads((directory / (platform + '.json')).read_text(encoding='utf-8'))
        except (OSError, ValueError):
            data = dict(state='unchecked', message='尚未在线检测，等待首次自检。', checked_at=None, next_check_at=0)
        if data.get('stamp') != stamp(platform):
            data.update(state='unchecked', message='登录凭据已更新，等待重新检测。', next_check_at=0)
        data['stale'] = bool(data.get('checked_at') and time.time() >= data.get('next_check_at', 0))
        data['checking'] = platform in checking
        data['queued'] = platform in pending
        return data

    @app.get('/account-health')
    def account_health_status():
        with lock:
            data = {p:read(p) for p in ALL}
        response = jsonify(accounts=data, interval_hours=12)
        response.headers['Cache-Control'] = 'no-store'
        return response

    @app.post('/account-health/<platform>')
    def refresh(platform):
        if platform not in (*ALL, 'all'):
            return jsonify(message='不支持的平台'), 404
        with lock:
            for p in ALL if platform == 'all' else (platform,):
                # Debounce repeated clicks. Each platform has at most one active check.
                if p not in checking:
                    pending.add(p)
        wake.set()
        return jsonify(message='已安排在线自检，平台正在使用时会排队。'), 202

    def loop():
        # Prevent duplicate schedulers even if another process imports this service.
        guard = (directory / 'scheduler.lock').open('a+b')
        try:
            if os.name == 'nt':
                import msvcrt
                guard.seek(0)
                if guard.read(1) == b'':
                    guard.write(b'0'); guard.flush()
                guard.seek(0)
                msvcrt.locking(guard.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(guard, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            guard.close()
            return
        while True:
            try:
                if not os.environ.get('DREAM_ROOM_SAFE_START'):
                    with lock:
                        for p in ALL:
                            if time.time() >= read(p).get('next_check_at', 0):
                                pending.add(p)
                    for p in ALL:
                        with lock:
                            if p not in pending or p in busy or any(read_job(j)['platform'] == p for j in active):
                                continue
                            pending.discard(p); checking.add(p); busy.add(p)
                        try:
                            with slots:
                                before = stamp(p)
                                output = directory / (p + '.result')
                                output.unlink(missing_ok=True)
                                process = subprocess.Popen([sys.executable, str(Path(__file__).with_name('account_health_worker.py')), p, str(output)],
                                    cwd=Path(__file__).resolve().parents[1], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
                                try:
                                    process.wait(timeout=115)
                                except subprocess.TimeoutExpired:
                                    if os.name == 'nt':
                                        subprocess.run(['taskkill','/PID',str(process.pid),'/T','/F'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=subprocess.CREATE_NO_WINDOW)
                                    else:
                                        process.kill()
                                data = json.loads(output.read_text(encoding='utf-8')) if output.is_file() else dict(state='unavailable',message='检测超时，无法确认登录；请稍后重试。')
                                now = time.time()
                                data.update(checked_at=now, next_check_at=now+(600 if data.get('state') == 'unavailable' else INTERVAL), stamp=stamp(p))
                                # Bilibili QR login lives in the app, outside browser locks.
                                if p == 'bilibili' and before != stamp(p):
                                    data.update(state='unchecked',message='账号已变更，等待重新检测。',next_check_at=0)
                                temp = directory / (p + '.tmp')
                                temp.write_text(json.dumps(data,ensure_ascii=False),encoding='utf-8')
                                temp.replace(directory / (p + '.json'))
                        finally:
                            with lock:
                                checking.discard(p); busy.discard(p)
            except Exception:
                app.logger.warning('账号自检暂时中断，将继续尝试；未输出凭据。')
            wake.wait(30); wake.clear()
    threading.Thread(target=loop,daemon=True,name='account-health').start()
