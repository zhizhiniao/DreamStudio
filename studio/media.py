"""Actual local media processing; no paid model calls."""
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont


def run(command, timeout=600):
    result = subprocess.run([str(p) for p in command],capture_output=True,timeout=timeout,
                            creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
    if result.returncode:
        raise ValueError('媒体处理失败，请检查素材格式和制作参数。')
    return result.stdout


def probe(runtime, path):
    info=json.loads(run([Path(runtime)/'bin/ffprobe.exe','-v','error','-show_format','-show_streams','-of','json',path],60))
    video=next((s for s in info.get('streams',[]) if s.get('codec_type')=='video'),None)
    if not video:
        raise ValueError('所选文件没有可用视频轨道。')
    return {'duration':float(info['format'].get('duration') or 0),'width':video['width'],'height':video['height'],
            'has_audio':any(s.get('codec_type')=='audio' for s in info['streams'])}


def checksum(path):
    h=hashlib.sha256()
    with open(path,'rb') as f:
        for block in iter(lambda:f.read(1024*1024),b''):
            h.update(block)
    return h.hexdigest()


def screenshot(runtime, video, path):
    run([Path(runtime)/'bin/ffmpeg.exe','-y','-i',video,'-frames:v','1',path],60)


def font_path(root):
    files=list((Path(root)/'vendor/fonts').glob('*Smiley*'))+list((Path(root)/'vendor/fonts').glob('*.ttf'))+list((Path(root)/'vendor/fonts').glob('*.otf'))
    return files[0] if files else Path('C:/Windows/Fonts/msyh.ttc')


def make_cover(root, title, output, portrait=False):
    size=(1080,1440) if portrait else (1280,720)
    canvas=Image.new('RGB',size,'#bac0ac')
    draw=ImageDraw.Draw(canvas)
    font=ImageFont.truetype(str(font_path(root)),88 if portrait else 76)
    lines=[]
    current=''
    for char in title[:100]:
        if char=='\n' or draw.textlength(current+char,font=font)>size[0]-180:
            if current:lines.append(current)
            current='' if char=='\n' else char
        else:current+=char
    if current:lines.append(current)
    y=(size[1]-len(lines)*110)//2
    for i,line in enumerate(lines):
        if i%2:
            width=draw.textlength(line,font=font)
            draw.rectangle((75,y-4,100+width,y+100),fill='#29382e')
        draw.text((90,y),line,font=font,fill='#edf0df' if i%2 else '#28332c')
        y+=110
    canvas.save(output,quality=95)


def render(root,runtime,asset,task,output,progress):
    from .production_ai import read_cues
    import shutil
    folder=Path(output).parent
    folder.mkdir(parents=True,exist_ok=True)
    source=Path(asset['path']);info=probe(runtime,source)
    mode=task.get('mode','original')
    start=float(task.get('start',0));duration=float(task.get('duration') or info['duration'])
    if start<0 or duration<=0 or start+duration>info['duration']+.1:
        raise ValueError('片段范围超出素材时长。')
    caption_path=task.get('translated_subtitles')
    voice=task.get('voice_path') if mode=='dub' else None
    if mode!='original' and (not caption_path or not Path(caption_path).is_file()):
        raise ValueError('请先制作或导入中文字幕。')
    if mode=='dub' and (not voice or not Path(voice).is_file()):raise ValueError('请先生成或导入中文配音。')
    if mode=='original' and (start!=0 or abs(duration-info['duration'])>.1):raise ValueError('保留原片模式不能裁剪。')
    from . import chapter_navigation
    chapters = chapter_navigation.for_render(task)
    inputs = {'source':checksum(source),'mode':mode,'start':start,'duration':duration,
              'subtitles':checksum(caption_path) if mode!='original' else '', 'voice':checksum(voice) if voice else ''}
    if chapters:
        inputs['chapters'] = [1,chapters]
    signature=hashlib.sha256(json.dumps(inputs,sort_keys=True).encode()).hexdigest()
    previous=Path(task.get('video_path') or '__missing__')
    if task.get('media_signature')==signature and previous.is_file() and checksum(previous)==task.get('content_hash'):
        progress('复用未变化的视频，只更新封面与文案');output=previous
    elif mode=='original':
        progress('保留原片，不重新编码');shutil.copy2(source,output)
    else:
        cues=read_cues(caption_path)
        clipped=[dict(c,start=max(0,c['start']-start),end=min(duration,c['end']-start)) for c in cues if c['end']>start and c['start']<start+duration]
        if not clipped:raise ValueError('中文字幕没有覆盖所选片段。')
        sys.path.insert(0,str(Path(root)/'vendor'))
        from modules.chinese_dubbing import write_captions
        write_captions(folder,clipped,info['width'],info['height'],max(18,round(info['height']*.065)))
        escape=lambda p:str(p).replace('\\','/').replace(':','\\:').replace("'","\\'")
        subtitle_filter=f"ass=filename='{escape(folder/'dub.zh.ass')}':fontsdir='{escape(Path(root)/'vendor/fonts')}'"
        if chapters:
            subtitle_filter += ',' + chapter_navigation.video_filter(folder,root,chapters,info['width'],info['height'],duration)
        command=[Path(runtime)/'bin/ffmpeg.exe','-y','-ss',str(start),'-i',source]
        if voice:command+=['-ss',str(start),'-i',voice,'-map','0:v:0','-map','1:a:0','-af','apad']
        else:command+=['-map','0:v:0','-map','0:a?']
        command+=['-t',str(duration),'-vf',subtitle_filter,'-c:v','libx264','-preset','veryfast','-crf','20',
                  '-c:a','aac','-pix_fmt','yuv420p','-movflags','+faststart',output]
        progress('正在生成中文配音成片' if voice else '正在保留原声并烧录中文字幕')
        run_video(command,duration,progress)
    covers = render_covers(root, runtime, asset, task, folder, progress)
    return {**probe(runtime,output), **covers, 'video_path':str(output),
            'content_hash':checksum(output), 'media_signature':signature}


def cover_signature(asset, task):
    values = {k:task.get(k) for k in ('title','cover_title','cover_style','cover_category','cover_emphasis','cover_color','cover_selection','cover_crops')}
    original = task.get('original_thumbnail') or asset.get('original_thumbnail')
    values['original'] = checksum(original) if original and Path(original).is_file() else ''
    return hashlib.sha256(json.dumps(values,ensure_ascii=False,sort_keys=True).encode()).hexdigest()


def render_covers(root, runtime, asset, task, folder, progress):
    from pathlib import Path
    folder = Path(folder); folder.mkdir(parents=True,exist_ok=True)
    signature = cover_signature(asset,task)
    if task.get('cover_signature') == signature and all(
        Path(task.get(k) or '__missing__').is_file() and checksum(task[k]) == task.get(k+'_hash')
        for k in ('cover_landscape_path','cover_portrait_path')):
        return {k:task[k] for k in ('cover_landscape_path','cover_portrait_path','cover_landscape_path_hash','cover_portrait_path_hash','cover_signature')}
    progress('正在准备横竖封面')
    landscape=folder/'cover-landscape.jpg';portrait=folder/'cover-portrait.jpg'
    if task.get('cover_style')=='original':
        original=task.get('original_thumbnail') or asset.get('original_thumbnail')
        if not original or not Path(original).is_file():raise ValueError('原始封面缺失，请上传原封面。')
        with Image.open(original) as image:
            image.convert('RGB').save(landscape,quality=95)
            image.convert('RGB').save(portrait,quality=95)
    else:
        title=task.get('cover_title') or task['title']
        lines=[line.strip() for line in title.split('\n') if line.strip()]
        if len(''.join(lines))>30 or not 1<=len(lines)<=3:raise ValueError('封面标题请填写最多 30 字、1 至 3 行。')
        from .cover_frames import ensure,validate,defaults,with_original
        gallery=with_original(ensure(runtime,asset,progress),asset,task)
        selection=task.get('cover_selection') or defaults(gallery,asset['duration'])
        crops=task.get('cover_crops',{})
        validate(selection,crops,gallery)
        sys.path.insert(0,str(Path(root)/'vendor'))
        from modules import cover_collage,cover_materials
        frames=[next(f for f in gallery if f['id']==key) for key in selection]
        files,records=cover_materials.prepare(frames,crops,folder/'cropped-frames')
        (folder/'frame-selection.json').write_text(json.dumps(records,ensure_ascii=False,indent=2),encoding='utf-8')
        cover_collage.FONT=font_path(root)
        cover_collage.render(files,lines,folder,{'cover-landscape':(1280,720),'cover-portrait':(1080,1440)},category=task.get('cover_category','影像创作'),emphasis=min(task.get('cover_emphasis',1),len(lines)-1),emphasis_color=task.get('cover_color','#E52620'),auto_focus=[crops.get(f['id'],[0,0,1,1])==[0,0,1,1] for f in frames])
    frame_state={'cover_frames':gallery,'cover_selection':selection,'cover_crops':crops} if task.get('cover_style')!='original' else {}
    return {**frame_state, 'cover_landscape_path':str(landscape), 'cover_portrait_path':str(portrait),
            'cover_landscape_path_hash':checksum(landscape), 'cover_portrait_path_hash':checksum(portrait),
            'cover_signature':cover_signature(asset,{**task,**frame_state})}


def run_video(command, duration, progress):
    """Bound FFmpeg wall time and stalled output; retain diagnostics on failure."""
    import tempfile
    import threading
    import queue
    import time
    from .production_errors import ProductionFailure
    with tempfile.TemporaryFile() as errors:
        process=subprocess.Popen([str(x) for x in [*command[:-1],'-progress','pipe:1','-nostats',command[-1]]],
            stdout=subprocess.PIPE,stderr=errors,text=True,encoding='utf-8',errors='replace',
            creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
        lines=queue.Queue()
        def read():
            try:
                for line in process.stdout:lines.put(line)
            finally:lines.put(None)
        reader=threading.Thread(target=read,daemon=True);reader.start()
        def failed(reason,code='render_failed'):
            if process.poll() is None:process.kill();process.wait()
            errors.seek(0);raw=errors.read().decode('utf-8',errors='replace')
            log=Path(command[-1]).parent/'ffmpeg-error.log'
            try:log.write_text(raw,encoding='utf-8')
            except OSError:pass
            lower=raw.lower()
            for phrase,message in [('no space left','磁盘空间不足'),('permission denied','输出文件被占用或没有写入权限'),
                ('no such file','素材、字幕或字体文件不存在'),('invalid data found','输入媒体文件损坏或格式无法解析'),
                ('no such filter','当前 FFmpeg 缺少所需字幕滤镜'),('error initializing filter','字幕滤镜初始化失败')]:
                if phrase in lower:reason=message;break
            raise ProductionFailure(code,'字幕烧录失败：'+reason+'。已保存字幕，无需重新翻译；故障日志：'+str(log),False,
                {'stage':'render','log_path':str(log),'exit_code':process.returncode})
        try:
            previous=-1;started=time.monotonic();last=started
            while True:
                now=time.monotonic()
                if now-started>3600 or now-last>120:failed('超过处理时限或 120 秒没有进度输出','render_timeout')
                try:line=lines.get(timeout=1)
                except queue.Empty:continue
                if line is None:break
                last=time.monotonic()
                if line.startswith('out_time_us='):
                    try:percent=min(99,max(0,int(line.split('=',1)[1])/1e6/max(duration,.01)*100))
                    except ValueError:continue
                    percent=int(percent)
                    if percent!=previous:progress(f'封装进度 {percent}%');previous=percent
            try:code=process.wait(timeout=10)
            except subprocess.TimeoutExpired:failed('进度流已结束但编码器未退出','render_timeout')
            if code:failed(f'FFmpeg 退出码 {code}')
        except BaseException:
            if process.poll() is None:process.kill();process.wait()
            raise
        finally:
            reader.join(timeout=2);process.stdout.close()
