"""Reviewed Kuaishou video publishing. Never use the vendor's publish retry loop."""
import asyncio
import base64
import json
import os
import re
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit
from caption_rules import kuaishou_caption, kuaishou_issues
from kuaishou_checks import caption_key, cover_matches
from submission_guard import FormRejected

from patchright.async_api import async_playwright, expect, TimeoutError as BrowserTimeout
from uploader.ks_uploader.main import KSVideo, KUAISHOU_UPLOAD_URL, KUAISHOU_LOGIN_URL

MANAGE_URL = 'https://cp.kuaishou.com/article/manage/video'


class KuaishouError(RuntimeError):
    """Messages safe to show without credentials or browser exception details."""


async def cover_pixels(page, locator):
    """Capture rendered Chromium pixels without waiting for unrelated web fonts."""
    await locator.scroll_into_view_if_needed(timeout=5000)
    box = await locator.bounding_box()
    if not box or min(box['width'], box['height']) < 32:
        return None
    offset = await page.evaluate('({x:window.scrollX,y:window.scrollY})')
    client = await page.context.new_cdp_session(page)
    try:
        result = await client.send('Page.captureScreenshot', dict(format='png', fromSurface=True,
            captureBeyondViewport=True, clip=dict(x=box['x']+offset['x'], y=box['y']+offset['y'],
                width=box['width'], height=box['height'], scale=1)))
        return base64.b64decode(result['data'])
    finally:
        await client.detach()


async def save_page_evidence(page, path):
    """Save the currently rendered page without a document-wide font dependency."""
    client = await page.context.new_cdp_session(page)
    try:
        result = await client.send('Page.captureScreenshot', dict(format='png', fromSurface=True))
        Path(path).write_bytes(base64.b64decode(result['data']))
    finally:
        await client.detach()


def accepted_url(value):
    parsed = urlsplit(value)
    return parsed.scheme == 'https' and parsed.netloc == 'cp.kuaishou.com' and parsed.path.rstrip('/') == '/article/manage/video'


async def save_account(context, account):
    target = Path(account)
    temp = target.with_suffix('.tmp')
    await context.storage_state(path=str(temp), indexed_db=True)
    os.chmod(temp, 0o600)
    temp.replace(target)


@asynccontextmanager
async def session(account, headless=True):
    # One profile for login and upload; service.py serializes work per platform.
    profile = Path(account).with_suffix('').with_name('kuaishou-browser')
    async with async_playwright() as pw:
        context = await pw.chromium.launch_persistent_context(str(profile), headless=headless, channel='chromium')
        try:
            yield context
        finally:
            await context.close()


async def authenticated(page):
    return (urlsplit(page.url).hostname == 'cp.kuaishou.com' and
            await page.locator("button[class^='_upload-btn']").first.is_visible())


async def open_upload(page):
    try:
        await page.goto(KUAISHOU_UPLOAD_URL, wait_until='domcontentloaded', timeout=60000)
        await page.locator("button[class^='_upload-btn'], main#login-form, a:has-text('立即登录')").first.wait_for(state='visible', timeout=20000)
    except Exception:
        raise KuaishouError('快手页面连接失败或结构变化；已保存的登录状态保留，请稍后重试。') from None


async def setup(account, handle=False, return_detail=False, qrcode_callback=None, headless=True):
    async with session(account, headless) as context:
        page = context.pages[0] if context.pages else await context.new_page()
        await open_upload(page)
        if await authenticated(page):
            await save_account(context, account)
            return {'success': True} if return_detail else True
        if not handle:
            return {'success': False} if return_detail else False
        await page.goto(KUAISHOU_LOGIN_URL, wait_until='domcontentloaded', timeout=60000)
        form = page.locator('main#login-form')
        await form.wait_for(state='visible', timeout=30000)
        qr = form.locator('div.qr-login img[alt="qrcode"]')
        if not await qr.is_visible():
            await form.locator('div.platform-switch').click(timeout=10000)
        deadline = asyncio.get_running_loop().time() + 300
        last_src = None
        while asyncio.get_running_loop().time() < deadline:
            if await authenticated(page):
                await save_account(context, account)
                return {'success': True} if return_detail else True
            if await qr.is_visible():
                src = await qr.get_attribute('src')
                if src and src != last_src:
                    image = 'data:image/png;base64,' + base64.b64encode(await qr.screenshot()).decode()
                    if qrcode_callback:
                        qrcode_callback({'image_data_url': image})
                    last_src = src
                expired = form.locator('div.qrcode-status-timeout')
                if await expired.is_visible():
                    # Expired QR images are normal, especially on a restored
                    # profile. Refresh in this same job instead of failing it.
                    await expired.click(timeout=10000)
                    last_src = None
                    await asyncio.sleep(1)
            await asyncio.sleep(1)
        raise KuaishouError('等待快手扫码确认超时；请重新登录。')


def reviewed_caption(title, description, tags):
    issues=kuaishou_issues(title,description,tags)
    if issues:raise KuaishouError('；'.join(issues))
    return kuaishou_caption(title,description,tags)


class ReviewedKuaishou(KSVideo):
    def __init__(self, *, progress, job_id, thumbnail_landscape_path=None, thumbnail_portrait_path=None, **kwargs):
        # Profile tiles use the reviewed portrait; never silently crop a horizontal title.
        super().__init__(thumbnail_path=thumbnail_portrait_path, **kwargs)
        self.progress = progress
        self.job_id = job_id
        self.cover_verified = False

    async def close_guide_overlay(self, page):
        # Only dismiss visible guidance via its controls; never remove page DOM.
        guide=page.locator('div[id^="react-joyride-step"] [role="alertdialog"]:visible').first
        if await guide.count():
            close=guide.locator('button[data-action="close"]')
            if await close.count()==1:
                await close.click(timeout=5000)
                await guide.wait_for(state='hidden',timeout=5000)
                return
        for name in ('跳过', '我知道了', '关闭'):
            control = page.get_by_role('alertdialog').get_by_role('button', name=name, exact=True)
            if await control.count() == 1 and await control.is_visible():
                await control.click(timeout=5000)
                return

    async def validate_upload_args(self):
        if not Path(self.account_file).is_file():
            raise KuaishouError('请先在账号管理中扫码登录快手。')
        self.file_path = str(self.validate_video_file(self.file_path))
        try:
            from PIL import Image
            if not self.thumbnail_path:
                raise ValueError('missing cover')
            self.thumbnail_path = str(self.validate_image_file(self.thumbnail_path))
            with Image.open(self.thumbnail_path) as image:
                image.verify()
            with Image.open(self.thumbnail_path) as image:
                image.load()
                if min(image.size) < 1:
                    raise ValueError('empty image')
        except Exception:
            raise KuaishouError('快手封面缺失或图片无法解码；尚未上传视频，请先重新选择可预览的封面。') from None
        reviewed_caption(self.title, self.desc, self.tags)

    async def check_cover_error(self, page):
        # Match a short standalone message, never ancestor text concatenating
        # the caption's "上传" with unrelated recommendation/detection failures.
        messages = page.locator('.ant-message-notice-content, .ant-notification-notice-description, .ant-form-item-explain-error, [role="alert"]')
        error = messages.get_by_text(re.compile(r'^(?:封面|图片|上传)[^\n]{0,48}(?:不可用|失败|不支持|不符合|错误)(?:[，：:；;。！!][^\n]{0,48})?$')).filter(visible=True).first
        if await error.is_visible():
            raise KuaishouError('快手未接受封面；尚未投稿，请检查封面图片和平台提示。')

    async def wait_cover_ready(self, page, ready, message, timeout=30):
        deadline = asyncio.get_running_loop().time() + timeout
        while asyncio.get_running_loop().time() < deadline:
            await self.check_cover_error(page)
            if await ready.is_visible():
                return
            await asyncio.sleep(0.25)
        raise KuaishouError(message)

    async def set_thumbnail(self, page):
        self.cover_verified = False
        await self.close_guide_overlay(page)
        if not self.thumbnail_path:
            raise KuaishouError('快手未选择封面；尚未投稿。')
        modal = page.locator('div[role="document"].ant-modal')
        if not await modal.is_visible():
            label = page.locator('span').filter(has_text='封面设置')
            await label.wait_for(state='visible', timeout=30000)
            await label.locator('xpath=../following-sibling::div[1]').locator('div').nth(0).click()
        await modal.wait_for(state='visible', timeout=30000)
        await modal.get_by_text('上传封面', exact=True).click()
        # Reopening an existing cropper has no file input until its image is cleared.
        clear = modal.get_by_text('清空上传', exact=True)
        if await clear.is_visible():
            await clear.click()
        await modal.locator('input[type="file"]').set_input_files(self.thumbnail_path)
        # This control appears only after the uploaded image has entered the cropper.
        # A fixed sleep can confirm the default video frame before the upload finishes.
        await self.wait_cover_ready(page, modal.get_by_text('清空上传', exact=True),
                                    '未确认快手封面预览就绪；尚未投稿，请稍后重试。')
        await self.verify_uploaded_cover(modal)
        confirm = modal.get_by_role('button', name='确认', exact=True)
        await expect(confirm).to_be_enabled(timeout=10000)
        await self.check_cover_error(page)
        await confirm.click()
        await self.wait_cover_ready(page, page.get_by_text('封面应用成功', exact=True),
                                    '未收到快手封面应用成功提示；尚未投稿，请核对封面。')
        await modal.wait_for(state='hidden', timeout=10000)
        await self.verify_applied_cover(page)
        self.cover_verified = True
        self.progress(message='快手封面预览及应用已确认，继续准备投稿。')

    async def fill_meta(self, page):
        editor = page.locator('[contenteditable="true"]:visible')
        if await editor.count() != 1:
            raise KuaishouError('无法唯一识别快手作品描述编辑器；尚未提交，请检查页面适配。')
        text = reviewed_caption(self.title, self.desc, self.tags)
        await editor.fill(text)
        await self.verify_caption(page)
        await editor.press('Tab')
        await self.check_caption_error(page)

    async def check_caption_error(self, page):
        error = page.get_by_text('作品描述超过字数限制', exact=True).filter(visible=True)
        if await error.count():
            raise FormRejected('快手作品描述超过字数限制；标题、简介、话题合计最多 500 字，请精简快手稿件后重新确认。')

    async def verify_caption(self, page):
        editor = page.locator('[contenteditable="true"]:visible')
        if await editor.count() != 1:
            raise KuaishouError('无法唯一识别快手作品描述编辑器；尚未提交。')
        text = reviewed_caption(self.title, self.desc, self.tags)
        deadline = asyncio.get_running_loop().time() + 5
        while asyncio.get_running_loop().time() < deadline:
            if caption_key(await editor.inner_text()) == caption_key(text):
                return
            await asyncio.sleep(.2)
        raise KuaishouError('快手文案回读与审核内容不一致；尚未提交。')

    async def verify_uploaded_cover(self, modal):
        deadline = asyncio.get_running_loop().time() + 30
        while asyncio.get_running_loop().time() < deadline:
            candidates = modal.locator('img:visible, canvas:visible')
            for index in range(min(await candidates.count(), 12)):
                candidate = candidates.nth(index)
                pixels = await cover_pixels(modal.page, candidate)
                if pixels and cover_matches(self.thumbnail_path, pixels):
                    return
            await asyncio.sleep(.25)
        raise KuaishouError('快手上传封面尚未显示审核图片；已阻止确认默认视频帧。')

    async def verify_applied_cover(self, page):
        preview = page.locator('[class*="_default-cover_"]').filter(visible=True)
        deadline = asyncio.get_running_loop().time() + 20
        stable = 0
        while asyncio.get_running_loop().time() < deadline:
            await self.check_cover_error(page)
            pixels = await cover_pixels(page, preview) if await preview.count() == 1 else None
            if pixels and cover_matches(self.thumbnail_path, pixels):
                stable += 1
                if stable >= 3:
                    import hashlib
                    self.progress(cover_verified=True, cover_hash=hashlib.sha256(Path(self.thumbnail_path).read_bytes()).hexdigest(),
                                  message='快手封面画面连续回读与审核图片一致。')
                    return
            else:
                stable = 0
            await asyncio.sleep(.5)
        self.cover_verified = False
        self.progress(cover_verified=False)
        raise KuaishouError('快手封面画面与审核图片不一致；已阻止投稿，请核对封面预览。')

    async def check_original_declaration(self, page):
        # Never silently claim originality. Unknown declaration UI stops submission.
        trigger = page.get_by_text('作者声明', exact=True)
        if await trigger.count() != 1:
            raise KuaishouError('未识别快手作者声明入口；尚未提交，需要核对平台声明控件。')
        await trigger.locator('xpath=..').locator('.ant-select-selector').click(timeout=10000)
        # Ant Select has hidden ARIA options and separate visible menu items.
        # Match the exact visible label and wait for the portal to finish opening.
        option = page.get_by_text('内容为AI生成', exact=True).filter(visible=True)
        try:
            await option.wait_for(state='visible', timeout=10000)
        except Exception:
            raise KuaishouError('未显示唯一的“内容为AI生成”选项；尚未提交，请核对作者声明菜单。') from None
        await option.click(timeout=10000)
        selected = trigger.locator('xpath=..').locator('.ant-select-selection-item')
        try:
            await expect(selected).to_have_text('内容为AI生成', timeout=5000)
        except Exception:
            raise KuaishouError('AI 内容声明未选中；尚未提交，请核对作者声明。') from None

    async def wait_for_upload_complete(self, page):
        deadline = asyncio.get_running_loop().time() + 1800
        stable = 0
        while asyncio.get_running_loop().time() < deadline:
            if await page.get_by_text(re.compile('上传失败|处理失败')).first.is_visible():
                raise KuaishouError('快手视频上传或处理失败；尚未提交。')
            busy = await page.get_by_text(re.compile('上传中|转码中|处理中')).first.is_visible()
            ready = await page.get_by_text(re.compile('上传成功|重新上传')).first.is_visible()
            stable = stable + 1 if ready and not busy else 0
            if stable >= 2:
                return
            await asyncio.sleep(2)
        raise KuaishouError('未确认快手视频处理完成；尚未提交，请核对平台页面。')

    @staticmethod
    def publish_button(page):
        # The live page uses a CSS-module DIV with no button role. Also support
        # native/ARIA buttons and Chinese spacing, without matching 发布作品.
        return page.locator('button, [role="button"], div[class*="_button-primary_"]').filter(
            has_text=re.compile(r'^\s*发\s*布\s*$')).filter(visible=True)

    async def submit_publish(self, page):
        await self.check_caption_error(page)
        if not self.cover_verified:
            raise KuaishouError('快手封面尚未验证应用成功；已阻止投稿。')
        await self.check_cover_error(page)
        await self.verify_applied_cover(page)
        await self.verify_caption(page)
        from state import STATE
        button = self.publish_button(page)
        try:
            await expect(button).to_be_enabled(timeout=20000)
            await button.click(trial=True, timeout=10000)
        except Exception:
            raise KuaishouError('未找到唯一可点击的快手发布按钮；尚未提交，请检查页面提示。') from None
        receipts = STATE / 'receipts'
        receipts.mkdir(exist_ok=True)
        network = []
        submitted_requests = []
        def record_request(request):
            if request.method == 'POST' and '/submit' in urlsplit(request.url).path:
                submitted_requests.append(True)
        page.on('request', record_request)
        response_path = receipts / (self.job_id + '-network.json')
        response_path.write_text('[]', encoding='utf-8')
        async def record_response(response):
            if urlsplit(response.url).path != '/rest/cp/works/v2/video/pc/submit':
                return
            entry = {'event':'response', 'path':urlsplit(response.url).path, 'status':response.status}
            try:
                data = await response.json()
                if isinstance(data, dict):
                    entry.update({key:data[key] for key in ('result','code','error_code') if isinstance(data.get(key),(int,bool))})
                    if urlsplit(response.url).path == '/rest/cp/works/v2/video/pc/submit':
                        entry['fields'] = list(data)
                        entry.update({key:re.sub(r'https?://\S+', '[URL]', data[key])[:500]
                                      for key in ('error_msg','errorMessage','message','msg') if isinstance(data.get(key),str)})
            except Exception:
                pass
            network.append(entry)
            response_path.write_text(json.dumps(network, ensure_ascii=False), encoding='utf-8')
        page.on('response', record_response)
        await save_page_evidence(page, receipts / (self.job_id + '-before.png'))
        self.progress(phase='submitting', message='正在提交快手稿件，等待平台回执…', qrcode='')
        await button.click(timeout=15000)
        # At most one explicit confirmation; never blindly click a modal primary button.
        for tick in range(180):
            # A page-side rejection is definitive only before any submit request.
            if not submitted_requests:
                await self.check_caption_error(page)
            rejected = next((item for item in network if item.get('event') == 'response'
                             and item.get('path') == '/rest/cp/works/v2/video/pc/submit'
                             and item.get('result') not in (None, 1)), None)
            if rejected:
                (receipts / (self.job_id + '-network.json')).write_text(json.dumps(network, ensure_ascii=False), encoding='utf-8')
                reason = next((rejected[key] for key in ('error_msg','errorMessage','message','msg') if rejected.get(key)), '平台未提供说明')
                if reason.startswith('话题标签数量超过上限'):
                    self.progress(phase='rejected')
                raise KuaishouError(f"快手投稿接口返回 {rejected['result']}：{reason}。请核对作品记录。")
            if tick in (5, 45, 120):
                await save_page_evidence(page, receipts / (self.job_id + '-pending.png'))
                (receipts / (self.job_id + '-network.json')).write_text(json.dumps(network, ensure_ascii=False), encoding='utf-8')
            if accepted_url(page.url):
                await page.get_by_text(self.title, exact=False).first.wait_for(state='visible', timeout=30000)
                await save_page_evidence(page, receipts / (self.job_id + '-after.png'))
                self.progress(status='succeeded', phase='submitted', message='快手已接收投稿，审核结果请到创作者中心查看。',
                              receipt={'status': 'succeeded', 'platform': 'kuaishou', 'manage_url': MANAGE_URL})
                return
            confirm = page.locator('.ant-modal:visible').get_by_role('button', name='确认发布', exact=True)
            if await confirm.count() == 1 and await confirm.is_visible():
                await confirm.click(timeout=10000)
                await page.wait_for_url(accepted_url, timeout=90000)
                continue
            await asyncio.sleep(1)
        raise KuaishouError('未取得明确的快手投稿回执，请先核对作品记录。')

    async def check_upload_rejection(self, page):
        alerts = page.locator('.ant-message-notice-content:visible, .el-message:visible, [role="alert"]:visible')
        text = '\n'.join(await alerts.all_inner_texts())
        if '选择视频时长超过最大限制' in text:
            body = await page.locator('body').inner_text()
            limit = re.search(r'支持时长\s*(\d+)\s*(分钟|小时)内', body)
            detail = f'（当前页面上限 {limit.group(1)} {limit.group(2)}）' if limit else ''
            raise KuaishouError(f'快手拒绝视频：成片时长超过当前账号限制{detail}；尚未提交。请调整快手专用成片并重新审片，不要原样重复发布。')
        if re.search(r'(?:视频|文件).{0,12}(?:格式不支持|格式不正确|大小超过|文件过大)', text):
            raise KuaishouError('快手拒绝视频的格式或大小；尚未提交，请查看失败现场中的平台提示。')

    async def upload_video_file(self, page):
        stage = '选择视频'
        diagnostics = []
        evidence = Path(os.environ['PUBLISHER_STATE']) / 'receipts' / (self.job_id + '-upload-diagnostics.json')
        evidence.parent.mkdir(exist_ok=True)
        def write_diagnostics():
            evidence.write_text(json.dumps(diagnostics, ensure_ascii=False), encoding='utf-8')
        def safe_message(value):
            value = re.sub(r'https?://\S+', '[URL]', str(value))
            return re.sub(r'(?i)(token|cookie|authorization|signature|secret|password)\s*[:=]\s*\S+', r'\1=[redacted]', value)[:500]
        def page_error(error):
            diagnostics.append({'page_error':safe_message(error)});write_diagnostics()
        def request_failed(request):
            diagnostics.append({'failed_host':urlsplit(request.url).hostname,
                                'resource_type':request.resource_type,'error':safe_message(request.failure)})
            write_diagnostics()
        def console_error(message):
            if message.type == 'error':
                diagnostics.append({'console_error':safe_message(message.text)});write_diagnostics()
        async def response_detail(response):
            parsed = urlsplit(response.url)
            if parsed.hostname != 'cp.kuaishou.com' or '/rest/' not in parsed.path:
                return
            entry = {'path': parsed.path, 'status': response.status}
            try:
                payload = await response.json()
                if isinstance(payload, dict):
                    entry.update({key: payload[key] for key in ('result','code') if isinstance(payload.get(key),(int,float))})
                    for key in ('error_msg','errorMessage','message','msg'):
                        if isinstance(payload.get(key), str):
                            entry[key] = re.sub(r'https?://\S+', '[URL]', payload[key])[:300]
            except Exception:
                pass
            diagnostics.append(entry)
            write_diagnostics()
        page.on('response', response_detail)
        page.on('pageerror', page_error)
        page.on('requestfailed', request_failed)
        page.on('console', console_error)
        try:
            self.progress(step='select_video', message='正在打开快手视频选择入口…')
            async with page.expect_file_chooser(timeout=30000) as chooser:
                await page.locator("button[class^='_upload-btn']").click(timeout=15000)
            stage = '交给浏览器读取视频文件'
            self.progress(step='read_video_file', message='正在将视频文件交给快手页面…')
            await (await chooser.value).set_files(self.file_path, timeout=120000)
            diagnostics.append({'files': await page.locator('input[type="file"]').evaluate_all('(els)=>els.map(el=>Array.from(el.files||[]).map(f=>({name:f.name,size:f.size,type:f.type})))')})
            write_diagnostics()
            await asyncio.sleep(2)
            await save_page_evidence(page, evidence.with_name(self.job_id + '-file-selected.png'))
            stage = '等待视频编辑页'
            self.progress(step='wait_video_editor', message='文件已交给页面，等待快手显示视频编辑区…')
            deadline = asyncio.get_running_loop().time() + 120
            while True:
                await self.check_upload_rejection(page)
                if await page.locator('[contenteditable="true"]').first.is_visible():
                    break
                if asyncio.get_running_loop().time() >= deadline:
                    raise BrowserTimeout('等待快手编辑区超时')
                await asyncio.sleep(.25)
        except BrowserTimeout:
            raise KuaishouError(f'快手「{stage}」超时；尚未点击发布。请查看失败现场截图，确认页面是否出现验证、文件限制或加载异常。') from None
        finally:
            page.remove_listener('response', response_detail)
            page.remove_listener('pageerror', page_error)
            page.remove_listener('requestfailed', request_failed)
            page.remove_listener('console', console_error)

    async def main(self):
        await self.validate_upload_args()
        async with session(self.account_file, self.headless) as context:
            page = context.pages[0] if context.pages else await context.new_page()
            await open_upload(page)
            if not await authenticated(page):
                raise KuaishouError('快手登录已失效，请先到账号管理重新扫码。')
            try:
                await self.upload_video_file(page)
                await self.close_guide_overlay(page)
                await self.fill_meta(page)
                await self.wait_for_upload_complete(page)
                await self.set_thumbnail(page)
                await self.check_original_declaration(page)
                await self.submit_publish(page)
            finally:
                await save_account(context, self.account_file)
