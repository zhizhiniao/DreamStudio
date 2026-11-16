"""Run a bounded browser session. Never repeat an uncertain publish click."""
import asyncio
import base64
import json
import sys
import time
from pathlib import Path
from state import read_job, save_job, account_path, STATE
from submission_guard import VerificationRequired, wait_for_submission, complete_sms_verification
from submission_guard import FormRejected, check_form_errors

job = None

def progress(**fields):
    job.update(fields)
    save_job(job)

def qrcode(info):
    login_state = info.get('login_state')
    messages = {
        'scanned': '平台已识别扫码，正在等待手机授权及页面跳转；若手机已确认，请勿重复扫码。',
        'saving': '平台已完成授权，正在保存本机登录状态…',
        'refreshing': '平台二维码已过期，正在生成新码…',
    }
    if login_state in messages:
        progress(login_state=login_state, step='account_login_' + login_state,
                 message=messages[login_state], qrcode='')
    data = info.get('image_data_url', '')
    path = info.get('image_path')
    if not data.startswith('data:image/') and path and Path(path).is_file():
        data = 'data:image/png;base64,' + base64.b64encode(Path(path).read_bytes()).decode()
    if data.startswith('data:image/'):
        stamp = job.get('qrcode_at') if data == job.get('qrcode') else time.time()
        next_action = '完成后账号状态会更新。' if job['action'] == 'login' else '完成后原任务自动继续，不用再次发布。'
        progress(qrcode=data, qrcode_at=stamp or time.time(), login_state='waiting_scan',
                 message='请使用对应平台手机 App 扫码并确认登录；' + next_action)

async def submit_once(page, label, url, confirmed_title=None, manage_url=None):
    from patchright.async_api import expect
    button = page.get_by_role('button', name=label, exact=True).first
    await button.wait_for(state='visible', timeout=60000)
    # A disabled button has not submitted anything; keep this a retryable form error.
    await check_form_errors(page,job['platform'])
    await expect(button).to_be_enabled(timeout=15000)
    receipt_dir = (STATE / 'receipts')
    receipt_dir.mkdir(exist_ok=True)
    await page.screenshot(path=str(receipt_dir / (job['id'] + '-before.png')))
    progress(phase='submitting', message='正在提交，等待平台确认…', qrcode='')
    await button.click(timeout=15000)
    async def verify(current_page):
        await complete_sms_verification(current_page, job, progress)
    await wait_for_submission(page, url, job['platform'], verify=verify)
    if confirmed_title:
        await page.get_by_text(confirmed_title, exact=False).first.wait_for(state='visible', timeout=60000)
    await page.screenshot(path=str(receipt_dir / (job['id'] + '-after.png')))
    # Receipt denotes accepted submission, not approval by the platform.
    progress(status='succeeded', phase='submitted', message='平台已接收投稿，审核进度请在创作者中心查看。',
             qrcode='', receipt={'status': 'succeeded', 'platform': job['platform'], 'manage_url': manage_url or page.url})

def observe_upload_steps(cls):
    """Keep progress and a local failure screenshot before the browser closes."""
    steps = {
        'upload_video_file': '正在向平台传输视频…',
        'upload_video_content': '正在向小红书传输视频…',
        'prepare_video_for_publish': '正在填写投稿信息…',
        'wait_for_upload_complete': '等待平台完成视频上传及处理…',
        'fill_meta': '正在填写标题、简介与话题…',
        'check_original_declaration': '正在填写内容来源声明…',
        'apply_original_statement': '正在设置视频内容标注…',
        'set_thumbnail': '正在设置投稿封面…',
        'set_short_title': '正在填写视频号短标题…',
        'submit_publish': '视频已准备好，正在提交投稿…',
    }
    for name, message in steps.items():
        if not hasattr(cls, name):
            continue
        original = getattr(cls, name)

        async def observed(self, page, *args, _method=original, _message=message, **kwargs):
            progress(message=_message, step=_method.__name__)
            try:
                result = await _method(self, page, *args, **kwargs)
                if _method.__name__ in ('wait_for_upload_complete', 'set_thumbnail') and job['platform'] in ('xiaohongshu', 'kuaishou', 'douyin'):
                    directory = (STATE / 'receipts')
                    directory.mkdir(exist_ok=True)
                    suffix = '-cover-ready.png' if _method.__name__ == 'set_thumbnail' else '-upload-ready.png'
                    if job['platform'] == 'kuaishou':
                        from kuaishou import save_page_evidence
                        await save_page_evidence(page, directory / (job['id'] + suffix))
                    else:
                        await page.screenshot(path=str(directory / (job['id'] + suffix)))
                return result
            except Exception:
                if not job.get('failure_step'):
                    progress(failure_step=job.get('step') or _method.__name__, failure_message=job.get('message', _message).rstrip('…'))
                try:
                    directory = (STATE / 'receipts')
                    directory.mkdir(exist_ok=True)
                    if job['platform'] == 'kuaishou':
                        from kuaishou import save_page_evidence
                        await save_page_evidence(page, directory / (job['id'] + '-error.png'))
                    else:
                        await page.screenshot(path=str(directory / (job['id'] + '-error.png')))
                    if job['platform'] in ('xiaohongshu','douyin','kuaishou','wechat_channels'):
                        controls = await page.locator('input, button, [role="button"], [class*="cover"], [class*="modal"], [class*="dialog"]').evaluate_all('''elements => elements.map(el => {
                            const r = el.getBoundingClientRect();
                            return {tag: el.tagName, class: el.className, role: el.getAttribute('role'),
                                type: el.getAttribute('type'), accept: el.getAttribute('accept'),
                                placeholder: el.getAttribute('placeholder'), text: (el.innerText || '').trim().slice(0, 120),
                                visible: r.width > 0 && r.height > 0, parentClass: el.parentElement?.className};
                        })''')
                        (directory / (job['id'] + '-controls.json')).write_text(json.dumps(controls, ensure_ascii=False), encoding='utf-8')
                except Exception:
                    pass
                raise

        setattr(cls, name, observed)
    return cls

async def main():
    if job['platform']=='bilibili' and job['action']=='upload':
        from bilibili import upload
        await asyncio.wait_for(upload(job,progress),timeout=3500)
        return
    from uploader.douyin_uploader.main import douyin_setup
    from douyin import ReviewedDouyin
    from uploader.tencent_uploader.main import TencentVideo, tencent_setup
    from xiaohongshu import ReviewedXiaohongshu, setup as xiaohongshu_setup
    from kuaishou import ReviewedKuaishou, setup as kuaishou_setup
    import utils.login_qrcode as qr
    import uploader.douyin_uploader.main as dy
    qr.print_terminal_qrcode = lambda *a, **kw: None
    dy.print_terminal_qrcode = lambda *a, **kw: None
    account = str(account_path(job['platform']))
    if job['action'] == 'login':
        setup = {'douyin': douyin_setup, 'wechat_channels': tencent_setup, 'xiaohongshu': xiaohongshu_setup, 'kuaishou': kuaishou_setup}[job['platform']]
        def refresh_requested():
            marker = STATE / 'jobs' / (job['id'] + '.refresh')
            if not marker.exists():
                return False
            marker.unlink(missing_ok=True)
            progress(qrcode='', message='正在刷新二维码…')
            return True
        options = {'refresh_requested': refresh_requested} if job['platform'] == 'douyin' else {}
        result = await asyncio.wait_for(setup(account, handle=True, return_detail=True,
                                              qrcode_callback=qrcode, headless=True, **options), timeout=390)
        if result.get('success'):
            from account_health_worker import record_health
            try:
                record_health(job['platform'], {'state':'valid', 'message':'已完成平台授权并保存本机账号。'})
            except OSError:
                pass
        failure = result.get('message') if job['platform'] == 'wechat_channels' else None
        progress(status='succeeded' if result.get('success') else 'failed', qrcode='',
                 login_state='saved' if result.get('success') else result.get('status', 'failed'),
                 message='登录成功，账号已保存。' if result.get('success') else failure or '登录未完成或二维码过期，请重新扫码。', retryable=True)
        return

    from recovery import ensure_ready
    from account_health_worker import record_health
    setup = {'douyin': douyin_setup, 'wechat_channels': tencent_setup,
             'xiaohongshu': xiaohongshu_setup, 'kuaishou': kuaishou_setup}[job['platform']]
    await ensure_ready(job['platform'], account, progress, qrcode, setup,
                       health=lambda result: record_health(job['platform'], result))

    class ReviewedTencent(TencentVideo):
        async def apply_original_statement(self, page):
            # Content labeling is separate from the copyright/original checkbox.
            # Our translated narration is synthetic; never declare the source original.
            entry = page.get_by_text('选择视频标注', exact=True).first
            if await entry.count():
                await entry.click()
                label = page.get_by_text('含AI生成内容', exact=True).first
                await label.wait_for(state='visible', timeout=10000)
                await label.click()

        async def set_short_title(self, page, title, short_title=None):
            field = page.locator('input[placeholder="填写短标题有机会获得更多流量"]').first
            if not await field.count():
                field = page.get_by_text('短标题', exact=True).locator('..').locator('xpath=following-sibling::div').locator('input')
            sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
            from studio.platform_rules import channels_short_title
            safe=data.get('short_title') or channels_short_title(title)
            if not safe:raise FormRejected('视频号短标题没有有效文字，请修改标题。')
            await field.fill(safe)
            await field.press('Tab')
            await check_form_errors(page,'wechat_channels')

        async def wait_for_realtime_verification(self, page, **kwargs):
            dialog = page.locator('div.weui-desktop-dialog__wrp:visible').filter(has_text='实名验证').first
            if await dialog.count() and await dialog.is_visible():
                progress(qrcode='data:image/png;base64,' + base64.b64encode(await dialog.screenshot()).decode(),
                         message='视频号要求管理员实名验证：请在左侧发布任务中打开本账号详情，使用管理员微信扫描二维码，5 分钟内完成。')
                from patchright.async_api import TimeoutError as BrowserTimeout
                from uploader.tencent_uploader.session import TencentSessionError
                try:
                    await dialog.wait_for(state='hidden', timeout=300000)
                except BrowserTimeout:
                    raise TencentSessionError('视频号管理员实名验证超时（等待 5 分钟）；尚未提交。下次经人工确认重试后，请在发布任务详情中用管理员微信扫描新二维码；旧二维码已失效。') from None
                progress(qrcode='', message='管理员实名验证已完成，继续准备投稿。')
                progress(qrcode='', message='验证已完成，正在上传…')

        async def submit_publish(self, page):
            await self.wait_for_realtime_verification(page)
            from urllib.parse import urlsplit
            def submitted_url(value):
                parsed = urlsplit(value)
                return parsed.netloc == 'channels.weixin.qq.com' and parsed.path.rstrip('/') in ('/platform', '/platform/post/list')
            await submit_once(page, '发表', submitted_url, confirmed_title=self.title)

    data = job['payload']
    cls = observe_upload_steps({'douyin': ReviewedDouyin, 'wechat_channels': ReviewedTencent,
                                'xiaohongshu': ReviewedXiaohongshu, 'kuaishou': ReviewedKuaishou}[job['platform']])
    extra = {'submit': submit_once, 'progress': progress, 'source_url': data.get('source_url', '')} if job['platform'] == 'xiaohongshu' else {}
    if job['platform'] == 'kuaishou':
        extra = {'progress': progress, 'job_id': job['id']}
    if job['platform'] == 'douyin':
        extra = {'submit': submit_once, 'progress': progress, 'job_id': job['id']}
    if job['platform'] in ('douyin', 'wechat_channels', 'kuaishou') and data.get('cover_portrait_path'):
        extra['thumbnail_portrait_path'] = data['cover_portrait_path']
    uploader = cls(title=data['title'], file_path=data['video_path'], tags=data['tags'], publish_date=0,
                   account_file=account, desc=data['description'], headless=True, debug=False,
                   thumbnail_landscape_path=data.get('cover_landscape_path') or data.get('cover_path') or None, **extra)
    progress(message='正在上传视频，较大的文件需要等待…')
    await asyncio.wait_for(uploader.main(), timeout=3500)
    if job['status'] != 'succeeded':
        raise RuntimeError('未取得明确的投稿回执')

def run_job(job_id):
    global job
    job = read_job(job_id)
    try:
        asyncio.run(main())
    except Exception as exc:
        if job.get('status') != 'succeeded':
            uncertain = job.get('phase') == 'submitting' and not isinstance(exc,FormRejected)
            from xiaohongshu import UploadStateError
            from kuaishou import KuaishouError
            from uploader.tencent_uploader.session import TencentSessionError
            from recovery import ConnectionUnavailable, LoginIncomplete, retryable_connection_error
            from douyin import DouyinCoverError
            from douyin_upload import UploadRejected
            failure = job.get('failure_message')
            if not failure:
                failure = {'account_check':'投稿入口检查', 'open_upload':'打开投稿入口',
                           'upload_video_file':'视频传输', 'fill_meta':'填写文案',
                           'wait_for_upload_complete':'等待平台处理视频'}.get(job.get('step'))
            detail = (str(exc) if isinstance(exc, (FormRejected, UploadStateError, TencentSessionError, KuaishouError, ConnectionUnavailable, LoginIncomplete, DouyinCoverError, UploadRejected)) else
                      (failure + '失败；尚未提交，请检查该步骤的页面提示。' if failure else
                       '登录或上传未完成，请检查登录状态、视频规格或平台验证要求后重试。'))
            # Never expose browser URLs or authentication details in exceptions.
            progress(status='needs_check' if uncertain else 'failed', retryable=not uncertain, qrcode='',
                     message=str(exc) if isinstance(exc, VerificationRequired) else
                             ((detail + '未确认发表结果，请先检查平台投稿记录，勿重复提交。') if isinstance(exc, KuaishouError) else
                              '未确认发表结果，请先检查平台投稿记录，勿重复提交。') if uncertain else
                             detail,
                     error_type=type(exc).__name__, recovery='connection' if retryable_connection_error(exc, job) else None)


if __name__ == '__main__':
    run_job(sys.argv[1])
