"""Recover only idempotent UPOS part PUTs; never replay publication requests."""
from urllib.parse import urlsplit
import logging
import httpx
from .utils.network import BiliAPIResponse


async def request_chunk(client, **kwargs):
    try:
        return await client.request(**kwargs)
    except Exception as exc:
        target = urlsplit(kwargs.get('url', ''))
        text = str(exc).lower()
        recoverable = any(f'curl: ({code})' in text for code in (7, 16, 18, 28, 35, 52, 55, 56, 92))
        if (kwargs.get('method') != 'PUT' or target.scheme != 'https'
                or not (target.hostname or '').endswith('.bilivideo.com') or not recoverable):
            raise
        # A separate TLS implementation and fresh connection avoid reusing a
        # failed libcurl connection. Keep the same signed upload ID / part index.
        proxies = getattr(client.get_wrapped_session(), 'proxies', {}) or {}
        proxy = proxies.get('https') or proxies.get('all') or None
        logging.getLogger('bili_upload_transport').info('视频分块连接异常，改用备用传输（%s）；上传编号与分块内容保持不变。', type(exc).__name__)
        async with httpx.AsyncClient(proxy=proxy, timeout=90, follow_redirects=False) as fallback:
            response = await fallback.request(**kwargs)
        return BiliAPIResponse(code=response.status_code, headers=dict(response.headers),
                               cookies={}, raw=response.content, url=str(response.url))
