"""Require two visibly applied covers before allowing a Douyin submission."""
import asyncio
import base64
import hashlib
import io
import re
from pathlib import Path
from PIL import Image, ImageOps, ImageStat, ImageChops
from patchright.async_api import expect
from uploader.douyin_uploader.main import DouYinVideo
from douyin_upload import UploadRejected, UploadTrace


class DouyinCoverError(RuntimeError):
    pass


def compatible_cover(value, portrait=False):
    """The input accept list includes WebP, but the editor currently rejects it."""
    try:
        path = Path(value)
        with Image.open(path) as image:
            image.load()
            image = ImageOps.exif_transpose(image).convert('RGB')
            size = (1080, 1440) if portrait else (1440, 1080)
            image = ImageOps.pad(image, size, method=Image.Resampling.LANCZOS, color=(16,18,19))
            folder = path.parent / 'douyin-compatible'
            folder.mkdir(exist_ok=True)
            target = folder / (hashlib.sha256(path.read_bytes()).hexdigest()[:20] + ('-portrait' if portrait else '-landscape') + '.jpg')
            image.save(target, format='JPEG', quality=95)
        return str(target)
    except Exception:
        raise DouyinCoverError('抖音封面缺失或无法解码，请先恢复封面。') from None


def visible_picture(data):
    with Image.open(io.BytesIO(data)) as image:
        stats = ImageStat.Stat(image.convert('L'))
        return stats.mean[0] > 8 and stats.stddev[0] > 4


def picture_matches(data, source):
    """Allow the platform's content-addressed URL to stay unchanged for the same cover."""
    with Image.open(io.BytesIO(data)) as actual, Image.open(source) as expected:
        # The editor canvas includes gray side gutters outside the centered
        # 4:3 / 3:4 crop frame. Compare only the output frame, not the gutters.
        actual = ImageOps.fit(actual.convert('RGB'), expected.size, method=Image.Resampling.LANCZOS)
        actual = actual.resize((64,64), Image.Resampling.LANCZOS)
        expected = expected.convert('RGB').resize((64,64), Image.Resampling.LANCZOS)
        difference = ImageStat.Stat(ImageChops.difference(actual, expected))
        return max(difference.mean) < 24 and max(difference.rms) < 42


class ReviewedDouyin(DouYinVideo):
    def __init__(self, *args, submit=None, progress=None, job_id=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.job_id = job_id
        self.submit = submit
        self.progress = progress or (lambda **fields: None)
        self.verified_covers = {}
        self.cover_timeout = 60
        self.cover_save_timeout = 120

    async def upload(self, playwright):
        from state import STATE
        await self.validate_upload_args()
        browser = await playwright.chromium.launch(headless=self.headless, channel='chromium',
            args=['--no-sandbox', '--disable-blink-features=AutomationControlled'])
        context = await browser.new_context(storage_state=self.account_file)
        page = await context.new_page()
        # Standalone diagnostics have no publication ID; give each run its own files.
        from uuid import uuid4
        trace = UploadTrace(STATE / 'receipts', self.job_id or uuid4().hex)
        trace.observe(page)
        authenticated = False
        try:
            # On this runtime add_init_script intercepts page requests. Douyin's
            # video parts stall there even after its upload address is allocated.
            self.progress(step='open_upload', message='正在打开抖音投稿入口…')
            await page.goto('https://creator.douyin.com/creator-micro/content/upload', wait_until='domcontentloaded', timeout=90000)
            # Match the file input itself. New layouts no longer put it under a
            # div whose class starts with "container".
            field = page.locator('input[type="file"][accept*="video"], input.upload-btn-input').first
            await field.wait_for(state='attached', timeout=45000)
            authenticated = True
            await self.monitor_video_upload(page, field, trace)
            await self.apply_self_declaration(page)
            await self.apply_collection(page)
            await self.set_thumbnail(page)
            if self.publish_date:
                await self.set_schedule_time_douyin(page, self.publish_date)
            await self.submit_publish(page)
        except Exception:
            try:
                await trace.save_failure(page)
            except Exception:
                import logging
                logging.getLogger('douyin').warning('未能保存完整的本次抖音失败现场')
            raise
        finally:
            try:
                await trace.flush()
                trace.write()
            except OSError:
                import logging
                logging.getLogger('douyin').warning('未能保存本次抖音上传诊断')
            if authenticated:
                from kuaishou import save_account
                try:
                    await save_account(context, self.account_file)
                except Exception:
                    import logging
                    logging.getLogger('douyin').warning('抖音登录快照保存失败；原快照保留，投稿回执不变。')
            await context.close()
            await browser.close()

    async def monitor_video_upload(self, page, field, trace):
        failure = page.get_by_text(re.compile(r'^(?:视频)?(?:上传|转码|处理)失败(?:$|[\s,，。！!:：])')).and_(
            page.locator(':not(input, textarea, [contenteditable], [contenteditable] *, [role="textbox"], [role="textbox"] *)')
        ).filter(visible=True).first
        transfer = asyncio.create_task(self.prepare_video_upload(page, field))
        rejected = asyncio.create_task(failure.wait_for(state='visible', timeout=0))
        try:
            done, _ = await asyncio.wait((transfer, rejected), return_when=asyncio.FIRST_COMPLETED)
            if rejected in done or await failure.is_visible():
                if rejected in done:
                    await rejected
                try:
                    prompt = re.search(r'(?:上传|转码|处理)失败', await failure.inner_text(timeout=1000))
                except Exception:
                    prompt = None
                trace.page_error = prompt.group() if prompt else '上传失败'
                self.progress(failure_step='upload_video_file', message='抖音提示“' + trace.page_error + '”，正在保存本次失败现场…')
                await trace.flush()
                raise UploadRejected('抖音提示“' + trace.page_error + '”' + trace.summary() + '。尚未提交。')
            await transfer
        finally:
            for task in (transfer, rejected):
                if not task.done():
                    task.cancel()
            await asyncio.gather(transfer, rejected, return_exceptions=True)

    async def prepare_video_upload(self, page, field):
        self.progress(step='upload_video_file', message='正在向抖音传输视频…')
        await field.set_input_files(self.file_path, timeout=180000)
        await page.wait_for_url(re.compile(r'/content/(?:publish|post/video)'), timeout=180000)
        self.progress(step='fill_meta', message='正在填写抖音标题、简介与话题…')
        await self.fill_title_and_description(page, self.title, self.desc, self.tags)
        self.progress(step='wait_for_upload_complete', message='等待抖音完成视频上传和处理…')
        ready = page.locator('[class^="long-card"]').get_by_text('重新上传', exact=True).filter(visible=True).first
        from patchright.async_api import TimeoutError as BrowserTimeout
        try:
            await ready.wait_for(state='visible', timeout=1800000)
        except BrowserTimeout:
            raise UploadRejected('30 分钟内未确认抖音视频上传完成，尚未提交。') from None

    def prepare_covers(self):
        horizontal = self.thumbnail_landscape_path or self.thumbnail_portrait_path
        portrait = self.thumbnail_portrait_path or self.thumbnail_landscape_path
        if not horizontal or not portrait:
            raise DouyinCoverError('抖音需要可用封面，不能改用默认视频帧继续投稿。')
        return {'横封面4:3': compatible_cover(horizontal), '竖封面3:4': compatible_cover(portrait, portrait=True)}

    async def validate_upload_args(self):
        self.prepare_covers()  # Decode before any video transfer.
        await super().validate_upload_args()

    async def check_cover_error(self, page):
        message = page.get_by_text(re.compile(r'不支持的图片格式|封面.*(?:不可用|失败|错误)|图片.*(?:失败|不支持)')).filter(visible=True).first
        if await message.is_visible():
            raise DouyinCoverError('抖音拒绝封面：' + (await message.inner_text())[:120])

    def cover_card(self, page, label):
        return page.locator('[class^="coverControl-"]').filter(has_text=label)

    async def _clear_blocking_overlays(self, page):
        # Never Escape a saving cover editor: it can discard the selected image.
        hint = page.get_by_text('设置竖封面获得更多流量', exact=True)
        if await hint.is_visible():
            skip = page.get_by_role('button', name='暂不设置', exact=True)
            if not await skip.count():skip = page.get_by_text('暂不设置', exact=True)
            await skip.click(timeout=5000)
            await hint.wait_for(state='hidden', timeout=5000)
        if await page.locator('div.dy-creator-content-modal:visible').count():
            return
        # Topic suggestions can reappear after the upload wait. Escape alone
        # leaves the rich-text editor focused; moving focus dismisses its popup.
        await page.keyboard.press('Escape')
        title = page.locator('input[placeholder*="填写作品标题"]').first
        if await title.is_visible():
            await title.click(timeout=5000)
        for button in await page.get_by_role('button', name='我知道了', exact=True).all():
            if await button.is_visible():await button.click(timeout=3000)

    async def read_card(self, page, label):
        image = self.cover_card(page, label).locator('img').first
        # The editor closes before the async cover processing populates cards.
        await image.wait_for(state='visible', timeout=self.cover_save_timeout * 1000)
        await expect(image).to_have_js_property('complete', True, timeout=self.cover_save_timeout * 1000)
        # Locator screenshots include fixed headers over the image. Center the
        # card first; otherwise a correctly saved cover appears cropped/changed.
        await image.evaluate("e=>e.scrollIntoView({block:'center',inline:'center',behavior:'instant'})")
        if not await image.evaluate('(e)=>e.naturalWidth>0'):
            raise DouyinCoverError('抖音封面图片加载失败，已阻止投稿。')
        if not visible_picture(await self.card_pixels(page, label)):
            raise DouyinCoverError('抖音封面预览为空白或黑图，已阻止投稿。')
        return await image.get_attribute('src')

    async def card_pixels(self, page, label):
        """Read the loaded image, excluding platform badges and fixed headers."""
        image = self.cover_card(page, label).locator('img').first
        try:
            encoded = await image.evaluate('''image => {
                const canvas = document.createElement('canvas');
                canvas.width = image.naturalWidth; canvas.height = image.naturalHeight;
                canvas.getContext('2d').drawImage(image, 0, 0);
                return canvas.toDataURL('image/png').split(',')[1];
            }''')
            return base64.b64decode(encoded)
        except Exception:
            # Some CDN variants disallow canvas extraction. Read the observed
            # image URL through this same authenticated browser context.
            response = await page.context.request.get(await image.get_attribute('src'), timeout=30000)
            if not response.ok:
                raise DouyinCoverError('抖音封面图片暂时无法读取，尚未提交。')
            return await response.body()

    async def set_thumbnail(self, page):
        self.verified_covers = {}
        covers = self.prepare_covers()
        # A caller may retry this preparation step after an interrupted edit.
        # Use the editor's explicit Cancel control before restarting both covers.
        discard = page.locator('div.dy-creator-content-modal:visible').filter(has_text='你有未保存的封面编辑效果，是否关闭？')
        if await discard.count():
            await discard.get_by_text('确定', exact=True).click()
            await discard.wait_for(state='hidden', timeout=10000)
        existing = page.locator('div.dy-creator-content-modal:visible')
        if await existing.count():
            cancel = existing.get_by_role('button', name='取消', exact=True)
            if await cancel.count() != 1 or not await cancel.is_visible():
                raise DouyinCoverError('封面编辑仍在进行，请结束当前编辑后重试；尚未投稿。')
            await cancel.click(timeout=10000)
            if await discard.count():
                await discard.get_by_text('确定', exact=True).click()
            await existing.wait_for(state='hidden', timeout=10000)
        await self._clear_blocking_overlays(page)
        for label, file in covers.items():
            card = self.cover_card(page, label)
            if await card.count() != 1:
                raise DouyinCoverError('抖音封面编辑入口发生变化，已停止，不能跳过封面投稿。')
            await card.hover()
            await card.click(timeout=10000)
            modal = page.locator('div.dy-creator-content-modal:visible')
            await modal.wait_for(state='visible', timeout=10000)
            upload = modal.locator('.semi-upload:has(.semi-upload-drag-area-main-text) input.semi-upload-hidden-input')
            if await upload.count() != 1:
                raise DouyinCoverError('未找到唯一的上传封面入口，已停止，不能使用 AI 参考图槽代替。')
            done = modal.get_by_role('button', name='完成', exact=True)
            await expect(done).not_to_have_class(re.compile('.*disabled.*'), timeout=self.cover_timeout * 1000)
            canvas = modal.locator('canvas.lower-canvas[class*="cloudImage-"]:visible').first
            await upload.set_input_files(file)
            deadline = asyncio.get_running_loop().time() + self.cover_timeout
            while True:
                await self.check_cover_error(page)
                pixels = await canvas.screenshot()
                # A spinner/selection change is not the new image. In particular,
                # the portrait editor initially contains a crop of the horizontal cover.
                if visible_picture(pixels) and picture_matches(pixels, file):
                    break
                if asyncio.get_running_loop().time() >= deadline:
                    raise DouyinCoverError('抖音封面上传后没有出现有效的新预览，已阻止投稿。')
                await asyncio.sleep(.25)
            await expect(done).not_to_have_class(re.compile('.*disabled.*'), timeout=self.cover_timeout * 1000)
            await done.click(timeout=10000)
            # The editor can spend over 20 seconds saving its image. Confirm
            # once, then wait for completion; Escape would discard the edit.
            deadline = asyncio.get_running_loop().time() + self.cover_save_timeout
            while await modal.is_visible():
                await self._clear_blocking_overlays(page)
                await self.check_cover_error(page)
                if asyncio.get_running_loop().time() >= deadline:
                    raise DouyinCoverError('抖音封面仍未保存完成，已停止，未提交作品。')
                await asyncio.sleep(.5)
            await self.check_cover_error(page)
            # A valid old card can remain visible while the new image is being
            # processed. Wait for the requested pixels, not merely an <img>.
            deadline = asyncio.get_running_loop().time() + self.cover_save_timeout
            while True:
                current = await self.read_card(page, label)
                if picture_matches(await self.card_pixels(page, label), file):
                    break
                await self.check_cover_error(page)
                if asyncio.get_running_loop().time() >= deadline:
                    raise DouyinCoverError('抖音未应用新封面，已阻止投稿。')
                await asyncio.sleep(.5)
            self.verified_covers[label] = current
        await self.verify_covers(page)

    async def verify_covers(self, page):
        if set(self.verified_covers) != {'横封面4:3', '竖封面3:4'}:
            raise DouyinCoverError('抖音横竖封面尚未全部验证，已阻止投稿。')
        await self.check_cover_error(page)
        covers = self.prepare_covers()
        for label in self.verified_covers:
            current = await self.read_card(page, label)
            pixels = await self.card_pixels(page, label)
            # Processing/reopening can rotate a signed URL without changing
            # the image. Pixel identity is the invariant required for posting.
            if not picture_matches(pixels, covers[label]):
                raise DouyinCoverError('抖音封面在确认后发生变化，已阻止投稿。')
            self.verified_covers[label] = current

    async def submit_publish(self, page):
        await self.verify_covers(page)
        if not self.submit:
            raise DouyinCoverError('未配置投稿回执处理，已停止。')
        await self.submit(page, '发布', 'https://creator.douyin.com/creator-micro/content/manage**')

    async def handle_upload_error(self, page):
        raise RuntimeError('平台拒绝视频上传，请检查账号及文件要求')
