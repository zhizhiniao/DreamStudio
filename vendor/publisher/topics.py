"""Async topic reads share the publisher's Douyin lock and browser budget."""
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from flask import jsonify, request
from modules.topic_library import CATEGORIES


def register(app, lock, slots, active, read_job, busy):
    root = Path(os.environ['PUBLISHER_STATE'])
    directory = root / 'topics'
    directory.mkdir(exist_ok=True)
    states = {}
    last_attempt = {}

    def read(category):
        try:
            data = json.loads((directory / (category + '.json')).read_text(encoding='utf-8'))
            if data.get('stamp') != (root / 'accounts/douyin.json').stat().st_mtime_ns:
                return {}
            return {k:v for k,v in data.items() if k != 'stamp'}
        except (OSError, ValueError):
            return {}

    def work(category):
        try:
            with slots:
                process = subprocess.Popen([sys.executable, str(Path(__file__).with_name('topic_worker.py')), category],
                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
                try:
                    output, _ = process.communicate(timeout=75)
                except subprocess.TimeoutExpired:
                    if os.name == 'nt':
                        subprocess.run(['taskkill','/PID',str(process.pid),'/T','/F'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=subprocess.CREATE_NO_WINDOW)
                    else:
                        process.kill()
                    process.communicate(timeout=10)
                    raise ValueError('timeout')
                data = json.loads(output)
                if process.returncode or 'error' in data:
                    raise ValueError('read failed')
                tmp = directory / (category + '.tmp')
                tmp.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
                tmp.replace(directory / (category + '.json'))
                with lock:
                    states[category] = {'state':'ready','message':'已读取平台话题；按内容相关性选择，不自动替换现有标签。'}
        except Exception:
            with lock:
                states[category] = {'state':'error','message':'本次读取未完成；如有旧数据会保留并显示采集时间。可稍后重试或粘贴导入。'}
        finally:
            with lock:
                busy.discard('douyin')

    @app.route('/topics/<category>', methods=['GET','POST'])
    def topic_list(category):
        if category not in CATEGORIES:
            return jsonify(message='分类无效'),400
        with lock:
            if request.method == 'POST':
                if not (root / 'accounts/douyin.json').exists():
                    return jsonify(message='请先连接抖音账号。'),409
                if busy or any(read_job(j)['platform']=='douyin' for j in active):
                    return jsonify(message='抖音正在处理其他任务，请稍后刷新话题。'),409
                if time.monotonic()-last_attempt.get(category, -1000)<60:
                    return jsonify(message='请间隔一分钟再刷新。'),429
                last_attempt[category]=time.monotonic()
                busy.add('douyin')
                states[category]={'state':'loading','message':'正在读取抖音话题，通常需要十几秒…'}
                threading.Thread(target=work,args=(category,),daemon=True).start()
            status=dict(states.get(category) or {'state':'idle','message':'点击读取抖音话题，获取当前平台候选。'})
        return jsonify(**read(category), **status)
