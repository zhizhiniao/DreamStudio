"""Authenticated public gateway. The client never chooses the sending account."""
from urllib.parse import urlsplit


def register_automation_routes(app, login_required):
    from flask import request, jsonify, abort
    from .live import snapshot
    from modules.browser_publisher import publisher_request

    @app.route('/api/account-center/v1/platforms/douyin/automation', methods=['GET', 'POST'])
    @login_required
    def account_automation():
        if request.method == 'POST':
            origin = request.headers.get('Origin')
            if request.headers.get('X-Requested-With') != 'XMLHttpRequest' or (origin and urlsplit(origin).netloc != request.host):
                abort(403)
        account = snapshot('douyin')
        if not account.get('profile'):
            return jsonify(message='请先连接并读取真实抖音账号。'), 409
        uid = account['profile']['id']
        data = request.get_json(silent=True) or {}
        if not isinstance(data, dict):
            return jsonify(message='请求格式无效。'), 400
        if request.method == 'POST':
            if data.get('account_id') != uid:
                return jsonify(message='账号已变化，请刷新后重试。'), 409
            if data.get('action') == 'save':
                rule = data.get('rule') or {}
                if not isinstance(rule, dict):
                    return jsonify(message='规则格式无效。'), 400
                work = next((w for w in account['works'] if w['id'] == rule.get('work_id')), None)
                if not work:
                    return jsonify(message='只能为当前账号已读取的作品设置规则。'), 400
                rule['work_title'] = work['title']
        try:
            result = publisher_request(request.method, '/engagement/' + uid, data if request.method == 'POST' else None)
            response = jsonify(result)
            response.headers['Cache-Control'] = 'no-store'
            return response
        except RuntimeError as exc:
            return jsonify(message=str(exc)), 503
