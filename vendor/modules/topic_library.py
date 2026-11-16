"""Platform candidates with explicit provenance; matching never invents heat."""
import re
from datetime import datetime, timezone

CATEGORIES = {'all': '全部', 'tech': '科技', 'anime': '二次元'}
SOURCE = 'https://creator.douyin.com/creator-micro/creative-guidance?discover_menu=3'


def clean_tags(text):
    values = re.findall(r'#([^#\s,，、;；]+)', text) if '#' in text else re.split(r'[\n,，、;；]+', text)
    result = []
    for value in values:
        value = value.strip()
        if value and len(value) <= 20 and value.casefold() not in {s.casefold() for s in result}:
            result.append(value)
    return result[:100]


def matches(rows, title, description, tags):
    # Conservative literal matching. No model call and no unsupported semantic claim.
    content = (title + '\n' + description).casefold()
    seeds = {str(t).strip().casefold() for t in tags if str(t).strip()}
    result = []
    for row in rows:
        name = row.get('name', '')
        token = name.casefold()
        if not token or len(name) > 20:
            continue
        if token in seeds:
            reason = '与现有内容标签一致'
        elif re.search(r'(?<![a-z0-9])' + re.escape(token) + r'(?![a-z0-9])', content) if token.isascii() else token in content:
            reason = '标题或简介明确提及该话题'
        else:
            continue
        result.append({**row, 'reason': reason})
    return result[:12]


def register(app, login_required):
    from flask import request, jsonify, abort
    from urllib.parse import urlsplit
    from modules.task_manager import get_task
    from modules.browser_publisher import publisher_request

    @app.route('/tasks/<task_id>/topic-candidates', methods=['GET', 'POST'])
    @login_required
    def topic_candidates(task_id):
        task = get_task(task_id)
        if not task:
            abort(404)
        category = request.args.get('category', 'all')
        if category not in CATEGORIES:
            abort(400)
        if request.method == 'POST':
            if request.headers.get('X-Requested-With') != 'XMLHttpRequest':
                abort(403)
            origin = request.headers.get('Origin')
            if origin and urlsplit(origin).netloc != request.host:
                abort(403)
        try:
            result = publisher_request(request.method, '/topics/' + category)
            response = jsonify(result)
            response.headers['Cache-Control'] = 'no-store'
            return response
        except RuntimeError as exc:
            return jsonify(message=str(exc)), 503

    @app.post('/tasks/<task_id>/topic-match')
    @login_required
    def topic_match(task_id):
        if not get_task(task_id):
            abort(404)
        origin = request.headers.get('Origin')
        if request.headers.get('X-Requested-With') != 'XMLHttpRequest' or (origin and urlsplit(origin).netloc != request.host):
            abort(403)
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            abort(400)
        title, description, text = (data.get(k, '') for k in ('title', 'description', 'text'))
        tags = data.get('tags', [])
        if not all(isinstance(v,str) and len(v)<=10000 for v in (title,description,text)) or not isinstance(tags,list) or len(tags)>6 or any(not isinstance(v,str) or len(v)>20 for v in tags):
            abort(400)
        if data.get('source') == 'import':
            result = {'rows':[{'name':t} for t in clean_tags(text)], 'source':'用户粘贴导入', 'period':'未核实', 'captured_at':None}
        else:
            category = data.get('category','all')
            if category not in CATEGORIES:
                abort(400)
            try:
                result = publisher_request('GET','/topics/'+category)
            except RuntimeError as exc:
                return jsonify(message=str(exc)),503
        result['matched'] = matches(result.get('rows',[]),title,description,tags)
        return jsonify(result)
