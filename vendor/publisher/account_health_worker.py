"""Read-only account probes. Never upload a file, publish, or send a message."""
import asyncio
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from state import account_path


def record_health(platform, payload):
    """A live publish preflight supersedes the older periodic-check cache."""
    from state import STATE
    path = bili_path() if platform == 'bilibili' else account_path(platform)
    directory = STATE / 'account-health'
    directory.mkdir(exist_ok=True)
    now = time.time()
    record = {k:v for k,v in payload.items() if k in ('state','message','name','avatar_url')}
    record.update(checked_at=now, next_check_at=now+(600 if record.get('state')=='unavailable' else 43200),
                  stamp=path.stat().st_mtime_ns if path.is_file() else 0)
    target = directory / (platform + '.json')
    temporary = directory / (platform + '.preflight.tmp')
    temporary.write_text(json.dumps(record,ensure_ascii=False),encoding='utf-8')
    temporary.replace(target)


def result(state, message, name=''):
    return dict(state=state, message=message, name=name)


def bili_path():
    if os.environ.get('DREAMSTUDIO_ACCOUNT_FILE'):
        return Path(os.environ['DREAMSTUDIO_ACCOUNT_FILE'])
    from modules.config_manager import load_config
    path = Path(load_config().get('BILIBILI_COOKIES_PATH') or 'cookies/bili_cookies.json')
    return path if path.is_absolute() else Path(__file__).resolve().parents[1] / path


async def bilibili():
    from modules.bilibili_runtime import configure_bilibili_runtime
    configure_bilibili_runtime()
    from modules.bilibili_auth import load_credential_from_file, HEADERS, get_client
    path = bili_path()
    if not path.is_file():
        return result('missing', '尚未连接账号，请扫码登录。')
    try:
        credential = load_credential_from_file(str(path))
    except (ValueError, KeyError):
        return result('expired', '凭据不完整，请重新扫码登录。')
    response = await get_client().request(method='GET', url='https://api.bilibili.com/x/web-interface/nav',
                                          headers=HEADERS.copy(), cookies=credential.get_cookies())
    if response.code != 200:
        return result('unavailable', '平台或网络暂时不可用，未判定账号失效。')
    data = response.json()
    if data.get('code') == -101 or (data.get('code') == 0 and not data.get('data', {}).get('isLogin')):
        return result('expired', '平台确认登录已失效，请重新扫码。')
    if data.get('code') == 0 and data.get('data', {}).get('isLogin'):
        return {**result('valid', '已通过平台在线登录校验。', data['data'].get('uname', '')), 'avatar_url':data['data'].get('face','')}
    return result('unavailable', '平台未返回明确登录结果，请稍后重试。')


async def header_profile(page):
    """Read only an account/avatar control, not arbitrary images in a feed."""
    try:
        return await page.evaluate('''() => {
          const selectors=['header img[class*="avatar"]','[class*="user-info"] img','[class*="userInfo"] img',
            '[class*="account-info"] img','[class*="header"] [class*="avatar"] img','img[class*="avatar"]',
            'img[class*="finder-head"]'];
          for(const selector of selectors){
            const images=[...document.querySelectorAll(selector)].filter(x=>x.getBoundingClientRect().width>0);
            if(images.length===1 && /^https:\\/\\//.test(images[0].currentSrc||images[0].src))
              return {avatar_url:images[0].currentSrc||images[0].src};
          }
          return {};
        }''')
    except Exception:return {}


async def channels_profile(account):
    from patchright.async_api import async_playwright
    async with async_playwright() as pw:
        browser=await pw.chromium.launch(headless=True,executable_path=os.environ.get('DREAM_ROOM_CHROME') or None)
        try:
            context=await browser.new_context(storage_state=str(account))
            page=await context.new_page()
            await page.goto('https://channels.weixin.qq.com/platform',wait_until='domcontentloaded',timeout=30000)
            for _ in range(8):
                profile=await header_profile(page)
                if profile:return profile
                await asyncio.sleep(.5)
            return {}
        finally:await browser.close()


async def browser_check(platform):
    account = account_path(platform)
    if not account.is_file():
        return result('missing', '尚未连接账号，请扫码登录。')
    if platform == 'wechat_channels':
        from uploader.tencent_uploader.main import cookie_auth
        valid = await cookie_auth(str(account))
        if not valid:return result('expired', '平台要求重新扫码登录。')
        try:profile=await channels_profile(account)
        except Exception:profile={}
        return {**result('valid', '已进入登录后的创作者页面。'),**profile}
    from patchright.async_api import async_playwright
    from contextlib import asynccontextmanager
    from utils.base_social_media import set_init_script
    @asynccontextmanager
    async def session():
        if platform == 'kuaishou':
            from kuaishou import session as ks_session
            async with ks_session(str(account)) as context:
                yield context
        else:
            async with async_playwright() as pw:
                browser = await pw.chromium.launch(headless=True, executable_path=os.environ.get('DREAM_ROOM_CHROME') or None)
                try:
                    context = await browser.new_context(storage_state=str(account))
                    await set_init_script(context)
                    yield context
                finally:
                    await browser.close()
    urls = {'douyin':'https://creator.douyin.com/creator-micro/content/upload',
            'xiaohongshu':'https://creator.xiaohongshu.com/publish/publish?from=homepage&target=video',
            'kuaishou':'https://cp.kuaishou.com/article/publish/video'}
    selectors = {'douyin':"input[type='file']",
                 'xiaohongshu':'input.upload-input', 'kuaishou':"button[class^='_upload-btn']"}
    async with session() as context:
        page = await context.new_page()
        response = await page.goto(urls[platform], wait_until='domcontentloaded', timeout=45000)
        if response and response.status >= 400:
            return result('unavailable', '平台页面暂时无法访问，未判定登录失效。')
        await asyncio.sleep(2)  # Allow login redirects and the client application to settle.
        for _ in range(60):
            for text in ('安全验证', '请完成验证', '拖动滑块'):
                if await page.get_by_text(text, exact=False).first.is_visible():
                    return result('verification', '平台要求安全验证，请打开创作者中心完成验证。')
            if '/login' in page.url or any([await page.get_by_text(t, exact=True).first.is_visible() for t in ('扫码登录', '手机号登录', '立即登录')]):
                return result('expired', '平台显示登录入口，请重新扫码。')
            locator = page.locator(selectors[platform]).first
            if await locator.count() and (platform != 'kuaishou' or await locator.is_visible()) and (platform != 'douyin' or page.url.startswith(urls['douyin'])):
                # A new worker must receive cookie/IndexedDB rotations from this probe.
                from kuaishou import save_account
                try:
                    await save_account(context, account)
                except Exception:
                    return result('environment', '登录已确认，但更新后的账号状态保存失败；原账号文件保留，请检查本机存储。')
                profile=await header_profile(page)
                return {**result('valid', '已验证登录后的投稿入口；未上传文件或提交内容。'),**profile}
            await asyncio.sleep(.5)
        return result('unavailable', '创作者页面未加载出投稿入口，暂无法确认登录；将在 10 分钟后自动重试。')


async def check(platform):
    try:
        return await asyncio.wait_for(bilibili() if platform == 'bilibili' else browser_check(platform), timeout=100)
    except Exception as exc:
        text = str(exc).lower()
        if 'certificate' in text or 'curl: (77)' in text or 'cacert' in text:
            return result('environment', '本机证书加载异常，无法验证；这不代表账号过期。')
        return result('unavailable', '检测连接失败或超时，登录状态尚无法确认；请稍后重试。')


if __name__ == '__main__':
    payload = asyncio.run(check(sys.argv[1]))
    target = Path(sys.argv[2])
    target.write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')
