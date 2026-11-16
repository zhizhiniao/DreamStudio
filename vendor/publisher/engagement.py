"""Private API and single-owner scheduler, sharing publisher browser capacity."""
import os
import subprocess
import sys
import threading
from pathlib import Path

from flask import request, jsonify

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from modules.account_center.automation_store import Store


def register(app, lock, slots, active, read_job, busy):
    root = Path(os.environ.get('PUBLISHER_STATE', '/state'))
    store = Store(root / 'account-center/automation.sqlite3', backup=True)
    wake = threading.Event()

    @app.route('/engagement/<account_id>', methods=['GET', 'POST'])
    def engagement(account_id):
        if not account_id.isdigit():
            return jsonify(message='账号标识无效。'), 400
        if request.method == 'POST':
            data = request.get_json(silent=True) or {}
            if not isinstance(data, dict):
                return jsonify(message='请求格式无效。'), 400
            try:
                action = data.get('action')
                if action == 'save':
                    if not isinstance(data.get('rule'), dict):
                        raise ValueError('规则格式无效。')
                    store.save(account_id, data.get('rule') or {})
                elif action == 'toggle' and type(data.get('enabled')) is bool:
                    store.toggle(account_id, str(data.get('id')), data['enabled'])
                elif action == 'pause':
                    store.pause(account_id)
                else:
                    raise ValueError('无效操作。')
            except ValueError as exc:
                return jsonify(message=str(exc)), 400
            wake.set()
        result = store.status(account_id)
        result['account_id'] = account_id
        result['safe_start'] = bool(os.environ.get('DREAM_ROOM_SAFE_START'))
        return jsonify(result)

    def loop():
        # OS lock survives neither a crash nor a reboot, and prevents duplicate schedulers.
        guard = (root / 'account-center/scheduler.lock').open('a+b')
        try:
            guard.seek(0)
            if os.name == 'nt':
                import msvcrt
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
        store.recover()
        # The port-owning publisher process is the scheduler owner. No app/Flask worker thread races.
        while True:
            try:
                if os.environ.get('DREAM_ROOM_SAFE_START'):
                    store.heartbeat('paused', '安全启动模式下后台发送已暂停。')
                elif not any(r['enabled'] for r in store.rules()):
                    store.heartbeat('idle', '后台待命；没有启用的规则。')
                elif not store.due_rule() and not store.next_job():
                    store.heartbeat('idle', '后台待命；每条视频每 10 分钟检查，命中请求自动入队。')
                else:
                    acquired = False
                    with lock:
                        if not busy and not any(read_job(j)['platform'] == 'douyin' for j in active):
                            busy.add('douyin')
                            acquired = True
                    if acquired:
                        try:
                            with slots:
                                process = subprocess.Popen([sys.executable, str(Path(__file__).with_name('engagement_worker.py'))],
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
                                try:
                                    process.wait(timeout=180)
                                except subprocess.TimeoutExpired:
                                    if os.name == 'nt':
                                        subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW)
                                    else:
                                        process.kill()
                                    process.wait(timeout=15)
                                    store.heartbeat('error', '平台响应超时，本轮已停止；不明发送结果保留待核对。')
                                finally:
                                    store.recover()
                        finally:
                            with lock:
                                busy.discard('douyin')
                    else:
                        store.heartbeat('waiting', '抖音正在登录或上传，后台回复稍后继续。')
            except Exception:
                store.heartbeat('error', '后台服务暂时异常，稍后重试。')
            wake.wait(15)
            wake.clear()

    if os.environ.get('ENGAGEMENT_NO_THREAD') != '1':
        threading.Thread(target=loop, name='engagement-scheduler', daemon=True).start()
    return store
