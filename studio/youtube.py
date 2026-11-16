"""A bounded, independent YouTube download worker. No publishing or paid calls."""
import json
import os
import re
import subprocess
import sys
import time
import threading
import uuid
from pathlib import Path
from urllib.parse import urlparse, parse_qs

VIDEO_SLOTS=threading.BoundedSemaphore(2)
SUBTITLE_LOCK=threading.Lock()


class DownloadError(ValueError):
    def __init__(self,code,message,stage='video',attempts=1):
        super().__init__(message)
        self.code,self.stage,self.attempts=code,stage,attempts


def classify(error):
    text=str(error).lower()
    if any(v in text for v in ('429','too many requests')):
        return 'rate_limited','YouTube 暂时限制请求频率，请稍后重试；不要连续点击。'
    if any(v in text for v in ('sign in','confirm you','login required','cookies are no longer valid')):
        return 'authentication','YouTube 要求本人登录验证，请更新下载会话后重试。'
    if any(v in text for v in ('certificate','curl: (77)','ssl:')):
        return 'certificate','下载组件无法加载或验证 HTTPS 证书，请检查运行环境。'
    if any(v in text for v in ('no space','disk full','errno 28','winerror 112')):
        return 'disk_full','存储磁盘空间不足，请释放空间后重试。'
    if any(v in text for v in ('private video','video unavailable','removed','not available in your country','copyright')):
        return 'unavailable','该视频已下架、设为私密或有访问范围限制，请核对原网页。'
    if '403' in text or 'requested format is not available' in text:
        return 'format_access','视频流被拒绝或格式已变化，请检查下载组件兼容性；这不等于账号掉线。'
    if any(v in text for v in ('timed out','timeout','connection','resolve','network','502','503','504','reset by peer')):
        return 'network','网络传输中断，有限次数重试后仍未完成；已下载的分片保留。'
    if any(v in text for v in ('challenge solving failed','page needs to be reloaded','javascript runtime')):
        return 'component','YouTube 页面解析未通过，请检查下载组件与 JavaScript 运行时；这不等于账号掉线。'
    return 'component','下载组件未能完成解析或传输，请根据任务错误分类检查组件。'


def read_json(path):
    try:
        value=json.loads(path.read_text(encoding='utf-8'))
        return value if isinstance(value,dict) else {}
    except (OSError,ValueError):return {}


def write_json(path,value):
    temp=path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
    temp.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')
    temp.replace(path)


def verify_video(runtime,path,expected_duration=0):
    from . import media
    info=media.probe(runtime,path)
    if path.stat().st_size==0 or info['duration']<=0:
        raise ValueError('视频内容为空')
    if expected_duration and abs(info['duration']-expected_duration)>max(3,expected_duration*.02):
        raise ValueError('视频时长不完整')
    # Decode beginning and end: a filename or metadata alone is not success.
    for position in (0,max(0,info['duration']-1)):
        media.run([runtime/'bin/ffmpeg.exe','-v','error','-xerror','-ss',str(position),'-i',path,
                   '-t','0.1','-f','null','-'],60)
    return info


def execute(command,timeout=3600):
    try:
        return subprocess.run(command,capture_output=True,timeout=timeout,
                              creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(command,1,b'',b'Connection timed out')
    except OSError as exc:
        code,message=classify(str(exc))
        raise DownloadError(code,message) from None


def base_command(runtime,cookies):
    command=[sys.executable,'-m','studio.youtube_worker','--ignore-config','--no-playlist','--no-progress',
             '--socket-timeout','30','--retries','0','--fragment-retries','2','--abort-on-unavailable-fragments',
             '--concurrent-fragments','1','--ffmpeg-location',str(runtime/'bin')]
    node=Path(runtime)/'bin/node.exe'
    if node.is_file():command+=['--js-runtimes','node:'+str(node)]
    if cookies and cookies.is_file():command+=['--cookies',str(cookies)]
    if cookies:
        proxy=read_json(Path(cookies).parent/'youtube-settings.json').get('proxy_url','')
        if proxy:command+=['--proxy',proxy]
    return command


def subtitle_download(url,folder,runtime,cookies=None,cooldown_file=None,progress=None):
    folder=Path(folder);folder.mkdir(parents=True,exist_ok=True)
    cooldown_file=cooldown_file or folder/'.subtitle-cooldown.json'
    with SUBTITLE_LOCK:
        existing=[p for p in folder.glob('source.*.vtt') if p.stat().st_size and p.read_text(encoding='utf-8-sig').startswith('WEBVTT')]
        cooldown=read_json(cooldown_file)
        retry_at=cooldown.get('retry_at',0)
        error=''
        if retry_at>time.time():error='rate_limited'
        else:
            languages=[lang for lang in ('en','zh-Hans','zh-Hant') if not (folder/f'source.{lang}.vtt') in existing]
            if languages:
                if progress:progress('正在获取可用字幕；字幕失败不会中断视频制作')
                command=base_command(runtime,cookies)+['--skip-download','--write-subs','--write-auto-subs',
                    '--sub-langs',','.join(languages),'--sub-format','vtt','-o',str(folder/'source.%(ext)s'),url]
                try:
                    result=execute(command,180)
                    if result.returncode:error,_=classify(result.stderr.decode('utf-8','replace'))
                except DownloadError as exc:error=exc.code
                if error=='rate_limited':
                    retry_at=time.time()+1800
                    write_json(cooldown_file,{'retry_at':retry_at,'reason':'rate_limited'})
        files=[p for p in folder.glob('source.*.vtt') if p.stat().st_size and p.read_text(encoding='utf-8-sig').startswith('WEBVTT')]
        status=('partial' if files else 'pending') if error else ('available' if files else 'unavailable')
        warning=''
        if error=='rate_limited':warning='字幕接口限流：已保留可用字幕，冷却 30 分钟后可补下；视频继续制作。'
        elif error:warning='部分字幕暂未取得（'+error+'），已有视频保留，可单独补下字幕。'
        elif not files:warning='原视频未提供可下载的中英文字幕；成片保留原声。'
        return {'subtitle_status':status,'subtitle_error':error,'subtitle_retry_at':retry_at if error=='rate_limited' else 0,
                'subtitle_files':[p.name for p in files],'warnings':[warning] if warning else []}


def canonical_url(value):
    url = urlparse(str(value).strip())
    if url.scheme != 'https' or url.username or url.password or url.port:
        raise ValueError('请粘贴 https 的 YouTube 单个视频链接')
    host = (url.hostname or '').lower()
    if host == 'youtu.be':
        key = url.path.strip('/')
    elif host in ('youtube.com', 'www.youtube.com', 'm.youtube.com'):
        key = parse_qs(url.query).get('v', [''])[0] if url.path == '/watch' else url.path.split('/')[-1] if url.path.startswith(('/shorts/', '/live/')) else ''
    else:
        key = ''
    if not re.fullmatch(r'[A-Za-z0-9_-]{11}', key):
        raise ValueError('请粘贴 YouTube 单个视频链接，不支持频道或播放列表')
    return 'https://www.youtube.com/watch?v=' + key


def refresh_metadata(url, folder, runtime, cookies=None):
    """Refresh public metadata only; never overwrite a source video or expose signed URLs."""
    url=canonical_url(url);folder=Path(folder);folder.mkdir(parents=True,exist_ok=True)
    result=execute(base_command(Path(runtime),cookies)+['--skip-download','--write-info-json','--write-thumbnail',
        '--convert-thumbnails','jpg','-o',str(folder/'source.%(ext)s'),url],120)
    info_path=folder/'source.info.json';info=read_json(info_path)
    # Strip raw downloader metadata even if the thumbnail operation failed.
    public={k:info.get(k,'') for k in ('title','uploader','id','duration','description','tags')}
    if info:write_json(info_path,{**public,'source_url':url})
    if public.get('id')!=url.split('v=')[-1]:
        code,message=classify(result.stderr.decode('utf-8','replace'))
        raise DownloadError(code,message)
    thumbnail=folder/'source.jpg'
    public['original_thumbnail']=str(thumbnail) if thumbnail.is_file() else ''
    return public


def download(url, folder, runtime, cookies=None,progress=None,subtitle_folder=None):
    url=canonical_url(url)
    folder=Path(folder);runtime=Path(runtime)
    folder.mkdir(parents=True, exist_ok=True)
    for tool in ('ffmpeg.exe','ffprobe.exe'):
        if not (runtime/'bin'/tool).is_file():raise DownloadError('environment','缺少媒体处理组件：'+tool)
    metadata=folder/'source.info.json';marker=folder/'.video-complete.json'
    saved=read_json(marker);public=read_json(metadata)
    videos=[p for p in folder.glob('source.*') if p.name in ('source.mp4','source.webm','source.mkv')]
    video=None
    if saved.get('source_url')==url and public.get('id')==url.split('v=')[-1]:
        for candidate in videos:
            if candidate.name!=saved.get('file') or candidate.stat().st_size!=saved.get('size'):continue
            try:verify_video(runtime,candidate,public.get('duration') or 0);video=candidate;break
            except (ValueError,subprocess.TimeoutExpired,OSError):pass
    reused=video is not None
    if not video:
        for candidate in videos:
            candidate.replace(candidate.with_name(candidate.name+'.unverified-'+uuid.uuid4().hex[:8]))
        if progress:progress('等待下载通道（最多同时下载 2 条视频）')
        with VIDEO_SLOTS:
            command=base_command(runtime,cookies)+['-f','bv*[height<=1080]+ba/b[height<=1080]',
                '--merge-output-format','mp4','--write-info-json','-o',str(folder/'source.%(ext)s'),url]
            for attempt in range(1,4):
                if progress:progress(f'正在下载视频（第 {attempt}/3 次），支持断点续传')
                result=execute(command)
                info=read_json(metadata)
                if info:
                    public={k:info.get(k,'') for k in ('title','uploader','id','duration','description','tags')}
                    write_json(metadata,{**public,'source_url':url})
                if result.returncode==0:break
                code,message=classify(result.stderr.decode('utf-8','replace'))
                if code!='network' or attempt==3:raise DownloadError(code,message,attempts=attempt)
                if progress:progress(f'网络暂时中断，{attempt*2} 秒后自动续传')
                time.sleep(attempt*2)
            videos=[p for p in folder.glob('source.*') if p.name in ('source.mp4','source.webm','source.mkv')]
            if not videos or public.get('id')!=url.split('v=')[-1]:raise DownloadError('integrity','未获得匹配的完整视频文件，请重试。')
            video=videos[0]
            if progress:progress('正在校验视频时长与首尾画面')
            try:verify_video(runtime,video,public.get('duration') or 0)
            except (ValueError,subprocess.TimeoutExpired,OSError):
                # Preserve corrupt evidence, but don't let yt-dlp keep treating it as complete.
                video.replace(video.with_name(video.name+'.invalid-'+uuid.uuid4().hex[:8]))
                raise DownloadError('integrity','视频校验未通过，异常副本已隔离；重试会重新取得视频。') from None
            write_json(marker,{'source_url':url,'file':video.name,'size':video.stat().st_size})
    elif progress:progress('复用已校验视频，不重复下载')
    subtitle_folder=Path(subtitle_folder or folder)
    cooldown=Path(cookies).parent/'youtube-subtitle-cooldown.json' if cookies else subtitle_folder/'.subtitle-cooldown.json'
    subtitles=subtitle_download(url,subtitle_folder,runtime,cookies,cooldown,progress)
    thumb=folder/'source.jpg'
    thumbnail_retry=folder/'.thumbnail-attempt.json'
    if not thumb.is_file() and read_json(thumbnail_retry).get('retry_at',0)<=time.time():
        if progress:progress('正在获取原始封面；失败不影响已下载视频')
        try:
            execute(base_command(runtime,cookies)+['--skip-download','--write-thumbnail','--convert-thumbnails','jpg','-o',str(folder/'source.%(ext)s'),url],90)
        except (DownloadError,OSError):pass
        if not thumb.is_file():write_json(thumbnail_retry,{'retry_at':time.time()+1800})
    return video,{**public,**subtitles,'video_reused':reused,'original_thumbnail':str(thumb) if thumb.is_file() else ''}
