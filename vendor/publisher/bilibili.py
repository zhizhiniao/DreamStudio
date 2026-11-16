"""Account-isolated SDK adapter. Submission is attempted once, receipt required."""
import os
from pathlib import Path


async def upload(job,progress):
    from modules.bilibili_runtime import configure_bilibili_runtime
    configure_bilibili_runtime()
    from modules.bilibili_auth import load_credential_from_file
    from modules.bili_sdk import video_uploader as sdk
    from modules.bili_sdk.utils.network import request_settings
    request_settings.set_wbi_retry_times(1)
    from account_health_worker import bilibili as health
    result=await health()
    if result['state']!='valid':raise ValueError('B站账号未就绪，请先检查或重新导入该账号会话。')
    data=job['payload']
    credential=load_credential_from_file(os.environ['DREAMSTUDIO_ACCOUNT_FILE'])
    class Once(sdk.VideoUploader):
        async def _submit(self,videos,cover_url=''):
            progress(phase='submitting',step='submit_publish',message='B站视频与封面已上传，正在提交投稿…')
            return await super()._submit(videos,cover_url)
    meta=sdk.VideoMeta(tid=int(data['bilibili_tid']),title=data['title'],desc=data['description'],
                      tags=data['tags'][:12],original=False,source=data.get('source_url') or '网络转载',
                      cover=data['cover_landscape_path'])
    uploader=Once(pages=[sdk.VideoUploaderPage(path=data['video_path'],title=data['title'])],
                  meta=meta,credential=credential,cover=data['cover_landscape_path'])
    @uploader.on(sdk.VideoUploaderEvents.AFTER_CHUNK.value)
    def chunk(event):
        progress(step='upload_video_file',message='正在上传B站视频分块…')
    @uploader.on(sdk.VideoUploaderEvents.PRE_COVER.value)
    def cover(event):progress(step='set_thumbnail',message='正在上传B站封面…')
    progress(phase='preparing',message='正在准备B站视频上传…')
    result=await uploader.start()
    receipt=result.get('data',result) if isinstance(result,dict) else {}
    bvid=receipt.get('bvid');aid=receipt.get('aid')
    if not bvid and not aid:raise RuntimeError('B站未返回明确投稿编号，请核对平台记录。')
    progress(status='succeeded',phase='submitted',message='B站已接收投稿，等待平台审核。',
             receipt={'bvid':bvid,'aid':aid,'manage_url':'https://member.bilibili.com/platform/upload-manager/article'})
