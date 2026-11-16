"""Local YouTube session settings. Public responses never contain credentials."""
import json
import tempfile
import threading
import time
from importlib.metadata import version
from pathlib import Path
from urllib.parse import urlsplit

from vendor.modules import cookiecloud as cc
from vendor.modules.youtube_auth import cookie_status, save_cookies
from . import youtube


class YouTubeSettings:
    def __init__(self, data, runtime):
        self.data, self.runtime = Path(data), Path(runtime)
        self.path = self.data/'youtube-settings.json'
        self.cookies = self.data/'youtube-cookies.txt'
        self.state_path = self.data/'youtube-session-status.json'
        self.lock = threading.RLock()
        self.check_lock = threading.Lock()
        from .youtube_browser import BrowserSession
        self.browser = BrowserSession(self.data,self.runtime)

    def source(self):
        cfg=self.config()
        return cfg.get('session_source') or ('external' if cfg.get('COOKIECLOUD_ENABLED') else 'browser')

    def config(self):
        return youtube.read_json(self.path)

    def public(self):
        cfg = self.config()
        return dict(enabled=bool(cfg.get('COOKIECLOUD_ENABLED')),
                    session_source=self.source(),browser=self.browser.status(),
                    local_service={'ready':True,'url':'http://127.0.0.1:8020/cookiecloud'},
                    server_url=cfg.get('COOKIECLOUD_SERVER_URL',''),
                    uuid_saved=bool(cfg.get('COOKIECLOUD_UUID')),
                    password_saved=bool(cfg.get('COOKIECLOUD_PASSWORD')),
                    crypto_type=cfg.get('COOKIECLOUD_CRYPTO_TYPE','auto'),
                    proxy_url=cfg.get('proxy_url',''),
                    session=cookie_status(self.cookies),
                    component={'downloader':version('yt-dlp'),
                               'javascript_ready':(self.runtime/'bin/node.exe').is_file()},
                    **youtube.read_json(self.state_path))

    def state(self, **values):
        with self.lock:
            youtube.write_json(self.state_path, {**youtube.read_json(self.state_path), **values})

    def save(self, values):
        with self.lock:
            cfg = self.config()
            source=values.get('session_source', 'external' if values.get('enabled') else self.source())
            if source not in ('browser','local_cloud','external','manual'):
                raise ValueError('请选择有效的会话来源')
            if self.browser.status()['running'] and source!='browser':
                raise ValueError('请先关闭 YouTube 登录窗口，再切换会话来源')
            cfg['session_source']=source
            if not isinstance(values.get('enabled',False),bool):
                raise ValueError('同步开关必须为开或关')
            cfg['COOKIECLOUD_ENABLED'] = source=='external' and values.get('enabled',False)
            for field,key in [('server_url','COOKIECLOUD_SERVER_URL'),('uuid','COOKIECLOUD_UUID'),
                              ('password','COOKIECLOUD_PASSWORD'),('crypto_type','COOKIECLOUD_CRYPTO_TYPE')]:
                if field in values:
                    if not isinstance(values[field],str) or len(values[field])>2048:
                        raise ValueError('设置字段格式不正确')
                    value=values[field].strip()
                    if field in ('uuid','password') and not value:continue
                    cfg[key]=value
            proxy=values.get('proxy_url',cfg.get('proxy_url',''))
            if not isinstance(proxy,str):raise ValueError('代理地址必须是文字')
            proxy=proxy.strip()
            if proxy:
                parsed=urlsplit(proxy)
                if (parsed.scheme not in ('http','https','socks5','socks5h') or not parsed.hostname
                        or parsed.username or parsed.password or parsed.query or parsed.fragment
                        or parsed.path not in ('','/')):
                    raise ValueError('代理请填写不含账号密码的 HTTP / SOCKS 地址，例如 http://127.0.0.1:7890')
                try:parsed.port
                except ValueError:raise ValueError('代理端口不正确') from None
            cfg['proxy_url']=proxy
            cfg['COOKIECLOUD_ALLOW_PLAINTEXT_EXPORT']=True
            cfg['YOUTUBE_COOKIES_PATH']=str(self.cookies)
            if cfg['COOKIECLOUD_ENABLED']:
                try:cc.validate_cookiecloud_settings(cfg)
                except cc.CookieCloudError as exc:raise ValueError(cc.public_cookiecloud_error(exc)['message']) from None
            elif source=='external' and cfg.get('COOKIECLOUD_SERVER_URL'):
                try:cc.normalize_server_url(cfg['COOKIECLOUD_SERVER_URL'])
                except cc.CookieCloudError as exc:raise ValueError(cc.public_cookiecloud_error(exc)['message']) from None
            youtube.write_json(self.path,cfg)
            self.state(check={},sync={})
        return self.public()

    def sync(self, force=True):
        if self.source()=='browser':
            self.browser.refresh(force=force)
            browser=self.browser.status()
            result=dict(ok=browser.get('state')=='saved',at=time.time(),
                message=browser.get('message') or '请先点击登录 YouTube，完成后会自动保存会话。')
            self.state(sync=result)
            return result
        with self.lock:
            previous=youtube.read_json(self.state_path).get('sync',{})
            if (not force and time.time()-previous.get('at',0)<300
                    and (not previous.get('ok') or self.cookies.is_file())):return previous
            try:
                if self.source()=='local_cloud':
                    cfg=self.local_cloud_config()
                    payload=self.local_cloud_payload()
                    if not payload:raise cc.CookieCloudDataError('请先配置浏览器扩展并上传',code='login_missing')
                    clear,_=cc.decrypt_cookiecloud_payload(payload,cfg['COOKIECLOUD_UUID'],cfg['COOKIECLOUD_PASSWORD'])
                    content,count=cc.build_youtube_netscape_cookies(clear)
                    result={'content':content,'cookie_count':count}
                else:result=cc.test_cookiecloud_youtube_sync(self.config())
                try:save_cookies(result['content'],self.cookies)
                except ValueError:
                    raise cc.CookieCloudDataError('登录凭据不可用',code='login_missing') from None
                except OSError:
                    raise cc.CookieCloudWriteError('下载会话未保存') from None
                status=dict(ok=True,at=time.time(),count=result['cookie_count'],
                            message='下载会话已同步。请检测视频读取，再重试失败任务。')
                self.state(sync=status,check={})
            except cc.CookieCloudError as exc:
                status=dict(ok=False,at=time.time(),**cc.public_cookiecloud_error(exc))
                self.state(sync=status)
            return status

    def prepare(self):
        if self.source()=='browser':
            self.browser.refresh()
        elif self.source()=='local_cloud' or self.config().get('COOKIECLOUD_ENABLED'):
            result=self.sync(force=False)
            if not result.get('ok') and not cookie_status(self.cookies)['login_fields_present']:
                raise youtube.DownloadError('session_sync',result['message'])
        return self.cookies

    def import_file(self, content):
        with self.lock:
            if self.browser.status()['running']:raise ValueError('请先关闭登录窗口，再导入会话')
            count=save_cookies(content,self.cookies)
            # Manual import is an explicit source choice: prevent automatic overwrite.
            cfg=self.config();cfg['COOKIECLOUD_ENABLED']=False
            cfg['session_source']='manual'
            youtube.write_json(self.path,cfg)
            self.state(check={},sync={},imported_at=time.time())
        return dict(ok=True,count=count,message='会话已导入，已切换为手动会话；请检测视频读取。')

    def check(self, url):
        url=youtube.canonical_url(url)
        if not self.check_lock.acquire(blocking=False):raise ValueError('正在检测，请等待结果')
        try:
            cookie_file=self.prepare()
            with tempfile.TemporaryDirectory(prefix='dreamstudio-youtube-check-') as folder:
                command=youtube.base_command(self.runtime,cookie_file)+[
                    '--test','--no-part','--extractor-retries','0','-f','bv*[height<=1080]/b',
                    '-o',str(Path(folder)/'probe.%(ext)s'),url]
                result=youtube.execute(command,timeout=90)
                received=sum(p.stat().st_size for p in Path(folder).glob('probe.*')
                             if p.suffix in ('.mp4','.webm','.mkv') and p.is_file())
                if result.returncode or not received:
                    code,message=youtube.classify(result.stderr.decode('utf-8','replace'))
                    status=dict(ok=False,error_code=code,message=message)
                else:
                    status=dict(ok=True,verified_bytes=received,
                                message=f'已实际读取 {received:,} 字节视频数据，可返回重试。此结果只代表当前视频可读取。')
        except youtube.DownloadError as exc:
            status=dict(ok=False,error_code=exc.code,message=str(exc))
        finally:
            self.check_lock.release()
        status.update(at=time.time(),url=url)
        self.state(check=status)
        return status


def register(app, data, runtime):
    from flask import jsonify, request
    service=YouTubeSettings(data,runtime)
    app.extensions['youtube_settings']=service
    from .cookiecloud_local import register as register_local
    register_local(app,service)

    @app.post('/api/youtube/session/login')
    def session_login():
        with service.lock:
            cfg=service.config()
            cfg.update(session_source='browser',COOKIECLOUD_ENABLED=False)
            youtube.write_json(service.path,cfg)
            service.state(check={},sync={})
            return jsonify(service.browser.start())

    @app.get('/api/youtube/session/browser')
    def session_browser():return jsonify(service.browser.status())

    @app.get('/api/youtube/settings')
    def settings_get():return jsonify(service.public())

    @app.post('/api/youtube/settings')
    def settings_save():
        values=request.get_json(silent=True) or {}
        if not isinstance(values,dict):raise ValueError('设置格式不正确')
        return jsonify(service.save(values))

    @app.post('/api/youtube/session/sync')
    def session_sync():return jsonify(service.sync())

    @app.post('/api/youtube/session/import')
    def session_import():
        file=request.files.get('file')
        if not file:raise ValueError('请选择 YouTube cookies.txt 文件')
        content=file.read(128*1024+1)
        if len(content)>128*1024:raise ValueError('文件过大，请只导出 YouTube 站点')
        try:content=content.decode('utf-8-sig')
        except UnicodeError:raise ValueError('请使用 UTF-8 的 Netscape cookies.txt 文件') from None
        return jsonify(service.import_file(content))

    @app.post('/api/youtube/session/check')
    def session_check():
        return jsonify(service.check((request.get_json(silent=True) or {}).get('url','')))

    return service
