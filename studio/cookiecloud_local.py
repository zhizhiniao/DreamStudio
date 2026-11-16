"""Single-user CookieCloud-compatible receiver hosted by the local Flask app."""
import secrets
import time
from flask import jsonify, request
from . import youtube
from vendor.modules import cookiecloud as cc
from vendor.modules.youtube_auth import normalize_cookies


def register(app, service):
    folder=service.data/'cookiecloud'
    identity=folder/'identity.json'
    payload_path=folder/'payload.json'

    def credentials():
        with service.lock:
            current=youtube.read_json(identity)
            if not current:
                folder.mkdir(parents=True,exist_ok=True)
                current=dict(uuid=secrets.token_urlsafe(24),password=secrets.token_urlsafe(32))
                youtube.write_json(identity,current)
            return current

    def local_config():
        return dict(COOKIECLOUD_UUID=credentials()['uuid'],COOKIECLOUD_PASSWORD=credentials()['password'],
                    COOKIECLOUD_CRYPTO_TYPE='auto')

    service.local_cloud_config=local_config
    service.local_cloud_payload=lambda: youtube.read_json(payload_path)

    @app.get('/cookiecloud/',endpoint='cookiecloud_health')
    def health():return jsonify(ok=True,service='DreamStudio CookieCloud')

    @app.post('/api/youtube/cookiecloud/setup')
    def setup():
        response=jsonify(**credentials(),server_url='http://127.0.0.1:8020/cookiecloud')
        response.headers['Cache-Control']='no-store'
        return response

    @app.post('/cookiecloud/update')
    def update():
        if not request.content_length or request.content_length>1024*1024:
            return jsonify(error='数据为空或过大'),413
        incoming=request.get_json(silent=True)
        if not isinstance(incoming,dict):return jsonify(error='数据格式无效'),400
        account=credentials()
        if not isinstance(incoming.get('uuid'),str) or not secrets.compare_digest(incoming['uuid'],account['uuid']):
            return jsonify(error='凭据不匹配'),403
        if not isinstance(incoming.get('encrypted'),str) or not incoming['encrypted']:
            return jsonify(error='仅接收加密数据'),400
        payload={k:incoming[k] for k in ('encrypted','crypto_type') if k in incoming}
        try:
            clear,_=cc.decrypt_cookiecloud_payload(payload,account['uuid'],account['password'])
            content,_=cc.build_youtube_netscape_cookies(clear)
            normalize_cookies(content)
        except (cc.CookieCloudError,ValueError):
            return jsonify(error='会话无效或解密失败，原凭据已保留'),422
        with service.lock:
            youtube.write_json(payload_path,payload)
            service.state(local_cloud_received_at=time.time())
            if service.source()=='local_cloud':service.sync()
        return jsonify(action='done',ok=True)

    @app.route('/cookiecloud/get/<uuid>',methods=['GET','POST'])
    def get(uuid):
        if not secrets.compare_digest(uuid,credentials()['uuid']):return jsonify(error='凭据不匹配'),403
        payload=youtube.read_json(payload_path)
        if not payload:return jsonify(error='尚未收到浏览器同步'),404
        response=jsonify(payload)
        response.headers['Cache-Control']='no-store'
        return response
