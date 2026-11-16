"""Classify a platform challenge without retrying a publish click."""
import asyncio
from patchright.async_api import TimeoutError as BrowserTimeout


class VerificationRequired(RuntimeError):
    pass


class FormRejected(RuntimeError):
    """Visible validation rejection, distinct from an unknown submission outcome."""


async def check_form_errors(page, platform):
    if platform!='wechat_channels':return
    for text in ('标题包含特殊字符','短标题不能为空','短标题字数'):
        message=page.get_by_text(text,exact=False).first
        if await message.is_visible():
            raise FormRejected('视频号短标题未通过表单校验；尚未取得投稿回执，请修改短标题后重试。')


async def wait_for_submission(page, url, platform, timeout=90, verify=None):
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        await check_form_errors(page,platform)
        if platform == 'douyin':
            for label in ('接收短信验证码', '请输入验证码', '身份验证', '安全验证'):
                if await page.get_by_text(label, exact=True).first.is_visible():
                    if verify:
                        await verify(page)
                        deadline = asyncio.get_running_loop().time() + timeout
                        break
                    raise VerificationRequired(
                        '抖音要求账号本人完成短信或身份验证，当前未取得投稿回执。'
                        '请到抖音创作者中心核对作品；如未提交，可在平台手动发布并完成验证。'
                        '未确认发表结果，程序已停止，禁止自动重复投稿。')
        try:
            await page.wait_for_url(url, timeout=1000)
            return
        except BrowserTimeout:
            continue
    raise BrowserTimeout('No confirmed submission receipt')


async def complete_sms_verification(page, job, progress):
    """Keep the original upload session alive while its owner enters a code."""
    import json
    import uuid
    from state import JOBS
    nonce = uuid.uuid4().hex
    command = JOBS / (job['id'] + '.verification')
    command.unlink(missing_ok=True)
    progress(verification=nonce, message='抖音需要短信验证：请在发布页点击获取验证码，再输入手机收到的验证码。原投稿会话保留 5 分钟。')
    deadline = asyncio.get_running_loop().time() + 300
    try:
        field = page.get_by_placeholder('请输入验证码', exact=True)
        if await field.count() != 1 or not await field.is_visible():
            raise VerificationRequired('平台要求非短信安全验证，无法在当前页面代办。未确认发表结果，请核对作品记录。')
        while asyncio.get_running_loop().time() < deadline:
            if not await field.is_visible():
                return
            if command.is_file():
                data = json.loads(command.read_text(encoding='utf-8'))
                command.unlink(missing_ok=True)
                if data.get('nonce') != nonce:
                    continue
                if data.get('action') == 'send':
                    await page.get_by_text('获取验证码', exact=True).click(timeout=10000)
                    progress(message='已点击获取验证码，请输入手机收到的短信验证码。')
                elif data.get('action') == 'verify':
                    await field.fill(data['code'])
                    await page.get_by_text('验证', exact=True).click(timeout=10000)
                    progress(message='验证码已提交，正在等待抖音确认；如页面提示错误，可重新输入。')
            for error in ('验证码错误', '验证码已过期', '验证码不正确'):
                if await page.get_by_text(error, exact=False).first.is_visible():
                    progress(message='抖音提示：' + error + '。请重新获取或输入验证码。')
                    break
            await asyncio.sleep(1)
        raise VerificationRequired('短信验证等待超时，未确认发表结果。请核对平台作品记录后再处理。')
    finally:
        command.unlink(missing_ok=True)
        progress(verification=None)
