"""YouTube-only cookie import and an authenticated downloader check."""
import os
import subprocess
import tempfile
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

LOGIN_NAMES = {'SAPISID', '__Secure-1PAPISID', '__Secure-3PAPISID'}


def normalize_cookies(content):
    if not isinstance(content, str) or len(content.encode('utf-8')) > 128 * 1024:
        raise ValueError('Cookie 文件为空或过大，请只导出 YouTube 站点。')
    rows = []
    for line in content.lstrip('\ufeff').replace('\r\n', '\n').splitlines():
        line = line.removeprefix('#HttpOnly_')
        if not line or line.startswith('#'):
            continue
        fields = line.split('\t')
        if len(fields) != 7:
            raise ValueError('请使用 Netscape cookies.txt 格式，每行应有 7 个制表符分隔字段。')
        domain, subdomains, path, secure, expires, name, value = fields
        if domain.lstrip('.') not in ('youtube.com', 'www.youtube.com', 'm.youtube.com'):
            continue
        if not expires.isdigit() or not path.startswith('/') or secure not in ('TRUE', 'FALSE') or subdomains not in ('TRUE', 'FALSE'):
            raise ValueError('Cookie 格式不正确，请重新导出。')
        if int(expires) and int(expires) <= time.time():
            continue
        if name and value:
            rows.append(fields)
    if not any(row[5] in LOGIN_NAMES for row in rows):
        raise ValueError('未发现有效的 YouTube 登录凭据；请先登录 YouTube，再导出本站 Cookies。')
    return '# Netscape HTTP Cookie File\n' + ''.join('\t'.join(row) + '\n' for row in rows), len(rows)


def save_cookies(content, destination):
    normalized, count = normalize_cookies(content)
    path = Path(destination)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix='.youtube-', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8', newline='\n') as output:
            output.write(normalized)
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return count


def cookie_status(path):
    try:
        file = Path(path)
        content = file.read_text(encoding='utf-8-sig')
        _, count = normalize_cookies(content)
        updated = datetime.fromtimestamp(file.stat().st_mtime).astimezone().strftime('%Y-%m-%d %H:%M:%S')
        source = ('CookieCloud 同步' if 'CookieCloud integration' in content[:200] else
                  '历史 CookieHub 导入' if '# Updated by local CookieHub;' in content[:200] else '已保存文件')
        return {'saved': True, 'login_fields_present': True, 'cookie_count': count,
                'source': source, 'updated_at': updated,
                'message': f'本地文件可读取：{count} 条 YouTube 凭据（{source}），更新于 {updated}。下载可用性以上方实际验证结果为准。'}
    except FileNotFoundError:
        return {'saved': False, 'login_fields_present': False, 'message': '尚未导入 YouTube 登录凭据。'}
    except PermissionError:
        return {'saved': True, 'login_fields_present': False, 'error_code': 'permission_denied',
                'message': 'Cookie 文件存在，但 Y2A 没有读取权限。请修复同步服务与 Y2A 的文件用户权限，无需重新登录。'}
    except OSError:
        return {'saved': True, 'login_fields_present': False, 'error_code': 'read_failed', 'message': 'Cookie 文件读取失败，请检查文件路径与存储。'}
    except ValueError:
        return {'saved': True, 'login_fields_present': False, 'message': '凭据已过期或不完整，请重新导入。'}


def check_download(url, cookie_file, config, logger):
    """Exercise an actual media transfer, not metadata extraction alone."""
    from modules import youtube_handler as yh
    with tempfile.TemporaryDirectory(prefix='dream-youtube-probe-') as folder:
        command = [*yh._find_yt_dlp_command(logger), '--ignore-config', '--no-playlist',
                   '--test', '--format', 'bestvideo/best', '--output', str(Path(folder)/'probe.%(ext)s'),
                   '--no-part', '--no-progress', '--socket-timeout', '20', '--retries', '0', '--extractor-retries', '0']
        yh._append_yt_dlp_network_args(command, cookies_path=str(cookie_file) if Path(cookie_file).is_file() else None,
                                       proxy_url=yh.build_proxy_url(config))
        try:
            result = subprocess.run([*command, url], capture_output=True, text=True, timeout=90, encoding='utf-8', errors='replace')
        except subprocess.TimeoutExpired:
            return {'success': False, 'message': '视频下载验证超时，请检查网络；这不等于登录失效。'}, 504
        output = (result.stdout or '') + (result.stderr or '')
        received = sum(p.stat().st_size for p in Path(folder).glob('probe.*') if p.suffix in ('.mp4', '.webm', '.mkv'))
        ok = result.returncode == 0 and received > 0
        stale = 'account cookies are no longer valid' in output.lower()
        message = (f'实际视频下载验证通过，已读取 {received:,} 字节视频数据，可以重试任务。' if ok else
                   'YouTube 要求本人登录验证，请在日常浏览器打开原视频完成验证，并让 CookieCloud 扩展同步后重试。'
                   if yh._looks_like_youtube_bot_challenge(output) else
                   '视频数据下载未通过，请检查视频可用性与网络；尚不能确认 Cookie 失效。')
        if ok and stale:
            message += ' 当前公开视频可下载，但已保存的账号凭据被 YouTube 拒绝，受限视频仍需更新登录。'
        return {'success': ok, 'message': message, 'verified_bytes': received, 'login_rejected': stale}, 200 if ok else 422


def register_routes(app, login_required):
    from flask import request, jsonify
    from modules.config_manager import update_config, load_config
    from modules.cookiecloud import resolve_cookie_output_path, refresh_cookiecloud_for_download
    from modules.youtube_handler import get_app_subdir

    def path():
        return Path(resolve_cookie_output_path(load_config().get('YOUTUBE_COOKIES_PATH') or 'cookies/yt_cookies.txt'))

    def same_origin():
        return (request.headers.get('X-Requested-With') == 'XMLHttpRequest'
                and request.headers.get('Origin', '').rstrip('/') == request.host_url.rstrip('/'))

    @app.get('/youtube/auth/status')
    @login_required
    def youtube_auth_status():
        return jsonify(cookie_status(path()))

    @app.post('/youtube/auth/import')
    @login_required
    def youtube_auth_import():
        if not same_origin():
            return jsonify(success=False, message='请从本机设置页导入。'), 403
        payload = request.get_json(silent=True) or {}
        try:
            count = save_cookies(payload.get('cookies'), path())
        except ValueError as exc:
            return jsonify(success=False, message=str(exc)), 400
        except OSError:
            return jsonify(success=False, message='凭据未保存：请检查 Y2A 对 cookies 目录的写入权限。'), 503
        return jsonify(success=True, cookie_count=count, message='YouTube 登录凭据已保存，可验证下载并重试失败任务。')

    @app.post('/youtube/auth/check')
    @login_required
    def youtube_auth_check():
        if not same_origin():
            return jsonify(success=False, message='请从本机设置页验证。'), 403
        payload = request.get_json(silent=True) or {}
        url = str(payload.get('url') or '').strip()
        parsed = urlsplit(url)
        if parsed.scheme != 'https' or parsed.hostname not in ('www.youtube.com', 'youtube.com', 'youtu.be'):
            return jsonify(success=False, message='请填写 HTTPS YouTube 视频链接。'), 400
        config = load_config()
        refreshed, sync_info = refresh_cookiecloud_for_download(config)
        cookie_file = Path(sync_info['output_path']) if refreshed else path()
        status = cookie_status(cookie_file)
        if not status['login_fields_present']:
            return jsonify(success=False, message=status['message']), 422
        result, status_code = check_download(url, cookie_file, config, app.logger)
        result['credentials_refreshed'] = refreshed
        return jsonify(result), status_code
