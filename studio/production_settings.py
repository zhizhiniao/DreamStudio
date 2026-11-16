"""Editable local production service configuration without secret echo or paid probes."""
from urllib.parse import urlsplit
from flask import jsonify,request
from .production_ai import config
from .youtube import write_json
from . import ai_budget


def register(app,data):
    def public():
        cfg=config(data);base=cfg.get('OPENAI_BASE_URL','')
        url=urlsplit(base)
        return dict(base_url='' if url.username or url.password or url.query else base,
                    base_saved=bool(base),model=cfg.get('OPENAI_MODEL_NAME',''),
                    key_saved=bool(cfg.get('OPENAI_API_KEY')),timeout=cfg.get('OPENAI_TIMEOUT_SECONDS',120),
                    budget=ai_budget.limits(cfg),usage=ai_budget.summary(data))

    @app.get('/api/production/settings')
    def get_settings():return jsonify(public())

    @app.post('/api/production/settings')
    def save_settings():
        values=request.get_json(silent=True) or {}
        if not isinstance(values,dict):raise ValueError('请填写制作服务设置')
        cfg=config(data)
        for field,key in [('base_url','OPENAI_BASE_URL'),('model','OPENAI_MODEL_NAME'),('api_key','OPENAI_API_KEY')]:
            value=values.get(field,'')
            if not isinstance(value,str) or len(value)>4096:raise ValueError('设置字段格式不正确')
            value=value.strip()
            if not value:continue
            if field=='base_url':
                url=urlsplit(value)
                if url.scheme not in ('http','https') or not url.hostname or url.username or url.password or url.query or url.fragment:
                    raise ValueError('请填写不含密码或查询参数的 HTTP / HTTPS 接口地址')
            cfg[key]=value
        timeout=values.get('timeout',120)
        if type(timeout) is not int or not 30<=timeout<=120:raise ValueError('单次请求超时应为 30–120 秒')
        cfg['OPENAI_TIMEOUT_SECONDS']=timeout
        for field in ai_budget.DEFAULTS:
            if field in values:cfg[field]=values[field]
        ai_budget.limits(cfg)
        if not all(cfg.get(k) for k in ('OPENAI_API_KEY','OPENAI_BASE_URL','OPENAI_MODEL_NAME')):
            raise ValueError('请填写接口地址、模型名称和 API 密钥')
        write_json(data/'production-ai.json',cfg)
        return jsonify(public())
