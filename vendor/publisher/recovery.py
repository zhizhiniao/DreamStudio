"""Bounded recovery before submission, using the original reviewed job."""
import asyncio

LABELS = {'douyin':'抖音', 'wechat_channels':'视频号', 'kuaishou':'快手', 'xiaohongshu':'小红书'}


class ConnectionUnavailable(RuntimeError):
    pass


class LoginIncomplete(RuntimeError):
    pass


async def ensure_ready(platform, account, progress, qrcode, setup, health=None):
    from account_health_worker import check
    label = LABELS[platform]
    for attempt in range(3):
        progress(phase='preparing', step='account_check', message=f'正在确认{label}投稿入口与登录状态…')
        result = await check(platform)
        if health:
            health(result)
        if result['state'] == 'valid':
            return
        if result['state'] in ('expired', 'missing'):
            progress(phase='waiting_login', step='account_login', qrcode='',
                     message=f'{label}需要重新授权，正在获取二维码；扫码后本任务自动继续，不用再次发布。')
            logged_in = await asyncio.wait_for(setup(account, handle=True, return_detail=True,
                qrcode_callback=qrcode, headless=True), timeout=390)
            if not logged_in.get('success'):
                if platform == 'wechat_channels' and logged_in.get('message'):
                    raise LoginIncomplete(logged_in['message'])
                raise LoginIncomplete(f'{label}授权未完成；已保留视频与文案，请重新扫码后继续。')
            if health:
                health({'state':'valid','message':'已在本次发布流程完成登录并保存账号。'})
            progress(phase='preparing', step='account_check', qrcode='', message=f'{label}登录已恢复，继续原任务上传…')
            return
        if result['state'] == 'verification':
            raise LoginIncomplete(f'{label}要求安全验证，请在创作者中心完成验证后继续本任务。')
        if attempt < 2:
            progress(message=f'{label}连接暂时中断，{5 * (attempt + 1)} 秒后自动复检（{attempt + 1}/3），不需要重新扫码。')
            await asyncio.sleep(5 * (attempt + 1))
    raise ConnectionUnavailable(f'{label}暂时无法连接；自动复检后仍未恢复，原账号和素材已保留。')


def retryable_connection_error(exc, job):
    if job.get('phase') in ('submitting', 'submitted', 'rejected', 'waiting_login'):
        return False
    if isinstance(exc, ConnectionUnavailable):
        return True
    if type(exc).__name__ == 'TimeoutError' and job.get('step') in ('open_upload', 'upload_video_file'):
        return True
    text = str(exc).lower()
    return any(marker in text for marker in ('net::err_connection_', 'net::err_network_',
        'net::err_proxy_connection_failed', 'net::err_tunnel_connection_failed', 'net::err_timed_out'))
