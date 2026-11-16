"""Reviewed video publishing, with bounded waits and one submission attempt.

Login and form selectors come from the pinned social-auto-upload vendor module.
The vendor's retrying publish loop is deliberately never called here.
"""
import asyncio
import os
import re
from pathlib import Path
from urllib.parse import urlsplit

from patchright.async_api import async_playwright, expect, TimeoutError as BrowserTimeoutError
from uploader.xiaohongshu_uploader import main as upstream
from utils.base_social_media import set_init_script

PUBLISH_URL = 'https://creator.xiaohongshu.com/publish/publish?from=homepage&target=video'
MANAGE_URL = 'https://creator.xiaohongshu.com/new/note-manager'


class UploadStateError(RuntimeError):
    """A safe, specific upload-state message, without browser exception details."""


def upload_ready(state):
    # A title field alone also exists during upload. Require the complete editing
    # controls AND no visible transfer/transcode indicator for two observations.
    return bool(not state['busy'] and state['title'] and
                (state['legacy_complete'] or
                 (state['reupload'] and state['cover'] and state['preview'])))


def accepted_url(value):
    parsed = urlsplit(value)
    return parsed.scheme == 'https' and parsed.netloc == 'creator.xiaohongshu.com' and parsed.path.rstrip('/') == '/publish/success'


async def cookie_auth(account_file):
    """Require the authenticated upload control, not merely absence of a login box."""
    if not Path(account_file).is_file():
        return False
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, channel='chromium')
        try:
            context = await browser.new_context(storage_state=account_file)
            context = await set_init_script(context)
            page = await context.new_page()
            await page.goto(PUBLISH_URL, wait_until='domcontentloaded', timeout=60000)
            await page.locator('input.upload-input').first.wait_for(state='attached', timeout=20000)
            if urlsplit(page.url).path.startswith('/login'):
                return False
            await context.storage_state(path=account_file)
            os.chmod(account_file, 0o600)
            return True
        except Exception:
            return False
        finally:
            await browser.close()


# Both saved-account checks and post-scan checks use the same positive evidence.
upstream.cookie_auth = cookie_auth
upstream.print_terminal_qrcode = lambda *a, **kw: None
setup = upstream.xiaohongshu_setup


class ReviewedXiaohongshu(upstream.XiaoHongShuVideo):
    def __init__(self, *, submit, progress, source_url, thumbnail_landscape_path=None, **kwargs):
        super().__init__(thumbnail_path=thumbnail_landscape_path, **kwargs)
        self.submit = submit
        self.progress = progress
        self.source_url = source_url

    async def fill_title(self, page):
        # Reject overlong reviewed text instead of the vendor's silent truncation.
        if not 1 <= len(self.title.strip()) <= 20:
            raise ValueError('小红书标题需要 1–20 字')
        await page.locator('input[placeholder*="填写标题"]').fill(self.title)

    async def wait_for_upload_complete(self, page):
        await page.locator('input[placeholder*="填写标题"]').wait_for(state='visible', timeout=180000)
        deadline = asyncio.get_running_loop().time() + 1800
        unknown_since = None
        stable = 0
        while asyncio.get_running_loop().time() < deadline:
            await self.dismiss_cover_tip(page)
            state = await self.upload_state(page)
            if state['failed']:
                raise UploadStateError('小红书页面提示视频上传或处理失败；尚未点击发布。')
            stable = stable + 1 if upload_ready(state) else 0
            if stable >= 2:
                self.progress(message='小红书视频上传及处理完成，开始填写投稿信息。')
                return
            if not state['busy'] and not upload_ready(state):
                unknown_since = unknown_since or asyncio.get_running_loop().time()
                if asyncio.get_running_loop().time() - unknown_since >= 90:
                    raise UploadStateError('已进入小红书编辑页，但未能识别视频上传完成状态；尚未点击发布，请检查页面适配。')
            else:
                unknown_since = None
            await asyncio.sleep(2)
        raise UploadStateError('等待小红书视频上传及处理超时；尚未填写文案或点击发布。')

    async def dismiss_cover_tip(self, page):
        tip = page.get_by_text(re.compile(r'视频可以\s*PK\s*封面')).first
        if await tip.is_visible():
            await page.get_by_text('我知道了', exact=True).click(timeout=5000)
            await tip.wait_for(state='hidden', timeout=5000)

    async def upload_state(self, page):
        async def visible(locator):
            return await locator.first.is_visible()
        old_preview = page.locator('input.upload-input').first.locator('xpath=following-sibling::div[contains(@class, "preview-new")]')
        old_text = await old_preview.inner_text() if await old_preview.count() else ''
        return {
            'title': await visible(page.locator('input[placeholder*="填写标题"]')),
            'reupload': await visible(page.get_by_text('重新上传', exact=True)),
            'cover': await visible(page.get_by_text('设置封面', exact=True)),
            'preview': await visible(page.get_by_text('笔记预览', exact=True)),
            'legacy_complete': any(t in old_text for t in ('上传成功', '已上传')),
            'busy': (await visible(page.get_by_text(re.compile(r'^(?:正在上传|上传中|正在处理|处理中|转码中)'))) or
                     await visible(page.locator('[role="progressbar"][aria-valuenow]:not([aria-valuenow="100"])'))),
            'failed': await visible(page.get_by_text(re.compile(r'上传失败|处理失败|格式不支持'))),
        }

    async def set_thumbnail(self, page, thumbnail_path):
        if not thumbnail_path:
            return
        async def cover_step(action, label):
            self.progress(message=label + '…')
            try:
                return await action
            except BrowserTimeoutError:
                raise UploadStateError(label + '超时；尚未提交，审核封面已保留。请在小红书专用流程重试。') from None
        # A reviewed cover must not be silently replaced by a random video frame.
        await page.get_by_text('设置封面', exact=True).first.scroll_into_view_if_needed()
        tile = page.locator('div.cover-plugin-preview div.cover > div.default').first
        if await tile.count():
            # The current editor exposes its edit button only while hovering the tile.
            await cover_step(tile.hover(timeout=15000), '正在显示小红书封面编辑入口')
        entry = page.locator('div.cover-plugin-preview .cover-edit-entry').first
        if not await entry.count():
            entry = page.locator('div.upload-cover').first
        if not await entry.count():
            entry = page.locator('div.cover-plugin-preview div.cover > div.default').first
        await cover_step(entry.click(timeout=15000), '正在打开小红书封面编辑器')
        await cover_step(page.get_by_text('上传封面', exact=True).first.click(timeout=15000), '正在切换到上传审核封面')
        upload = page.locator('div.upload-wrapper input[type="file"][accept*="image"]').first
        if not await upload.count():
            upload = page.locator('input[type="file"][accept*="image"]').last
        await cover_step(upload.set_input_files(thumbnail_path), '正在传输小红书审核封面')
        confirm = page.locator('div.d-modal-footer').get_by_text('确定', exact=True).first
        await cover_step(expect(confirm).to_be_enabled(timeout=30000), '正在等待小红书封面处理完成')
        await cover_step(confirm.click(), '正在确认小红书封面')
        await cover_step(page.locator('div.d-modal').first.wait_for(state='hidden', timeout=30000), '正在保存小红书封面')
        await cover_step(page.get_by_text('封面上传中', exact=True).first.wait_for(state='hidden', timeout=60000), '正在等待小红书接收封面')

    async def check_original_declaration(self, page):
        # Y2A imports YouTube videos. Never select "原创" on the user's behalf.
        if urlsplit(self.source_url).hostname not in ('youtube.com', 'www.youtube.com', 'youtu.be', 'm.youtube.com'):
            raise ValueError('缺少可核对的原视频来源')
        trigger = page.get_by_text('添加内容类型声明', exact=False).first
        if not await trigger.count():
            return  # Source metadata remains independent from the description.
        await trigger.click()
        await page.get_by_text(re.compile(r'^(内容来源声明|来源转载)$')).first.wait_for(state='visible', timeout=10000)
        source_menu = page.get_by_text('内容来源声明', exact=True).first
        if await source_menu.is_visible():
            await source_menu.hover(timeout=10000)
        await page.get_by_text('来源转载', exact=True).first.click(timeout=10000)
        await page.get_by_placeholder('请输入媒体名称').fill('YouTube')
        await page.get_by_role('button', name='确认', exact=True).click()
        await page.get_by_placeholder('请输入媒体名称').wait_for(state='hidden', timeout=10000)

    async def submit_publish(self, page):
        await self.submit(page, '发布', accepted_url, confirmed_title='发布成功', manage_url=MANAGE_URL)

    async def upload_video_content(self, page):
        await page.goto(PUBLISH_URL, wait_until='domcontentloaded', timeout=60000)
        self.progress(message='正在向小红书传输视频…')
        await page.locator('input.upload-input').first.set_input_files(self.file_path)
        await self.wait_for_upload_complete(page)
        self.progress(message='正在填写小红书标题、简介与话题…')
        await self.fill_meta(page)
        await self.set_thumbnail(page, self.thumbnail_path)
        await self.check_original_declaration(page)
        await self.submit_publish(page)

    async def upload(self, playwright):
        await self.validate_upload_args()
        browser = await playwright.chromium.launch(headless=True, channel='chromium')
        context = await browser.new_context(storage_state=self.account_file)
        context = await set_init_script(context)
        try:
            page = await context.new_page()
            await self.upload_video_content(page)
            await context.storage_state(path=self.account_file)
            os.chmod(self.account_file, 0o600)
        finally:
            await context.close()
            await browser.close()
