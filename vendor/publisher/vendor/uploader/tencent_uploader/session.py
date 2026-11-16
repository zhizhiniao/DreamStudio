"""One on-disk browser profile per account; no background login or publishing."""
import asyncio
import json
import os
import time
from pathlib import Path
from urllib.parse import urlsplit


class TencentSessionError(RuntimeError):
    """Only fixed, credential-free messages may reach the UI."""


class TencentLoginRequired(TencentSessionError):
    def __init__(self):
        super().__init__('视频号需要重新授权，请在账号管理中连接账号后继续。')


class TencentSessionUnavailable(TencentSessionError):
    def __init__(self):
        super().__init__('视频号网络或页面暂时无法确认登录，已保留账号状态，请稍后重试；无需立即重新扫码。')


async def open_session(playwright, account_file, launch_kwargs):
    account = Path(account_file)
    profile = account.parent / (account.stem + '-browser')
    profile.mkdir(parents=True, exist_ok=True, mode=0o700)
    context = await playwright.chromium.launch_persistent_context(str(profile), **launch_kwargs)
    try:
        # Import the legacy snapshot once. Never overwrite a newer profile on restart.
        marker = profile / '.legacy-imported'
        if not marker.exists():
            if account.is_file():
                state = json.loads(account.read_text(encoding='utf-8'))
                await context.add_cookies(state.get('cookies', []))
                for origin in state.get('origins', []):
                    page = await context.new_page()
                    url = origin['origin'].rstrip('/') + '/'
                    if urlsplit(url).hostname != 'channels.weixin.qq.com':
                        await page.close()
                        continue
                    # Populate localStorage without contacting the platform during migration.
                    await page.route(url, lambda route: route.fulfill(status=200, body='<html></html>'))
                    await page.goto(url)
                    await page.evaluate('(items) => { for (const x of items) localStorage.setItem(x.name, x.value); }', origin.get('localStorage', []))
                    await page.close()
            marker.touch(mode=0o600)
        return context
    except Exception:
        await context.close()
        raise


async def save_session(context, account_file):
    target = Path(account_file)
    temp = target.with_suffix('.tmp')
    await context.storage_state(path=str(temp), indexed_db=True)
    os.chmod(temp, 0o600)
    temp.replace(target)


async def has_login_prompt(page):
    if '/login.html' in urlsplit(page.url).path:
        return True
    for selector in ('iframe[src*="open.weixin.qq.com/connect/qrconnect"]',
                     'div.login-qrcode-wrap', 'div.qrcode-wrap'):
        if await page.locator(selector).first.is_visible():
            return True
    return False


async def login_ready(page):
    """Require a real authenticated control; container help text is not proof."""
    if urlsplit(page.url).hostname != 'channels.weixin.qq.com' or await has_login_prompt(page):
        return False
    for selector in ('button:has-text("发表视频")', 'button:has-text("发表")', 'button:has-text("保存草稿")'):
        if await page.locator(selector).first.is_visible():
            return True
    return False


async def require_login(page, timeout=30):
    """A route alone is not evidence that the client has authenticated."""
    if '/login.html' in urlsplit(page.url).path:
        raise TencentLoginRequired()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if await has_login_prompt(page):
            raise TencentLoginRequired()
        if await login_ready(page):
            return
        await asyncio.sleep(0.25)
    raise TencentSessionUnavailable()


async def checkpoint(context, account_file):
    # A failed upload must not discard refreshed credentials, or replace the upload error.
    try:
        await save_session(context, account_file)
    except Exception:
        from utils.log import tencent_logger
        tencent_logger.warning('视频号状态快照保存失败；浏览器目录仍保留，请检查存储空间与权限。')
