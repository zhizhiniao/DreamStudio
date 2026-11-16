import copy
import json
import threading
import time
import uuid
from pathlib import Path
from flask import request, jsonify, send_file
from PIL import Image, ImageOps
from . import production as flow, production_ai as ai, youtube
from .production_errors import public_failure
from .ai_budget import task_scope
from .media import checksum as media_checksum


def register(app, root, data, runtime, store, archive, record, save_archive, lock):
    from .chapter_routes import register as register_chapters
    register_chapters(app,root,data,runtime,store,record,lock)
    from .production_auto import register as register_auto
    register_auto(app,root,data,runtime,store,archive,record,save_archive,lock)
    from .narration import register as register_narration
    register_narration(app,root,data,runtime,store,archive,record,save_archive,lock)
    from .subtitle_workspace import register as register_subtitles
    register_subtitles(app,root,data,runtime,store,archive,record,save_archive,lock)
    # Recover public source metadata when it exists; never replace edited task text.
    for asset in store.list('assets'):
        folder=Path(asset.get('project_path') or Path(asset.get('path','')).parent.parent)
        source=folder/'01_原始视频/source.info.json'
        if source.is_file():
            try:
                metadata=json.loads(source.read_text(encoding='utf-8'))
                for field in ('description','tags'):
                    if field not in asset and metadata.get(field):asset[field]=metadata[field]
            except (OSError,ValueError):pass
        image=folder/'01_原始视频/source.jpg'
        if image.is_file() and not asset.get('original_thumbnail'):asset['original_thumbnail']=str(image)
        store.put('assets',asset)
    for old in store.list('tasks'):
        if old.get('status')=='cancelled':continue
        task=flow.hydrate(old, record('assets',old['asset_id']))
        # Adopt only an unchanged, already-rendered legacy cover; never infer approval.
        if (not task.get('cover_signature') and old.get('status') in ('review','approved')
                and task.get('cover_title')==old.get('cover_title')
                and all(Path(task.get(k) or '__missing__').is_file()
                        and media_checksum(task[k])==task.get(k+'_hash')
                        for k in ('cover_landscape_path','cover_portrait_path'))):
            from .media import cover_signature
            task['cover_signature']=cover_signature(record('assets',old['asset_id']),task)
        if task.get('cover_title')!=old.get('cover_title',''):
            task.update(version=task['version']+1,reviewed_version=None,status='draft',confirmed=min(task['confirmed'],2))
            task.pop('prepared_version',None)
        if 'flow_version' not in old and old.get('status')=='review':
            task.update(status='draft',progress='已有成片保留，请先补齐制作步骤。')
        if task['production_job'].get('status')=='running':
            task['production_job'].update(status='failed',message='处理曾中断，可重试；已完成文件保留。')
        if task!=old:store.put('tasks',task)

    @app.post('/api/drafts/<key>/sync-version')
    def sync_draft(key):
        with lock:
            draft=record('drafts',key);task=record('tasks',draft['task_id'])
            if any(p.get('draft_id')==key and p.get('status') in ('queued','running','succeeded','needs_check') for p in store.list('publications')):
                raise ValueError('此稿件已有投稿记录，不能替换版本。')
            if task.get('reviewed_version')!=task['version']:raise ValueError('请先审核当前成片。')
            draft.update(task_version=task['version'],reviewed=False,status='draft')
            store.put('drafts',draft)
            return jsonify(draft)

    @app.get('/api/production/capabilities')
    def capabilities():
        cfg=ai.config(data)
        return jsonify(ai_ready=bool(cfg.get('OPENAI_API_KEY')),model=cfg.get('OPENAI_MODEL_NAME','未配置'),
                       notice='使用已配置 AI 服务，按服务商规则计费；不会自动公开投稿。')

    @app.get('/api/tasks/<key>/cover-frames/<frame_id>')
    def cover_frame(key,frame_id):
        task=record('tasks',key)
        frame=next((f for f in task.get('cover_frames',[]) if f['id']==frame_id),None)
        if not frame:raise ValueError('候选帧不存在，请重新提取。')
        path=Path(frame['file']).resolve()
        if not path.is_relative_to(archive.root.resolve()):raise ValueError('帧文件不在项目目录。')
        return send_file(path,conditional=True)

    @app.post('/api/tasks/<key>/confirm')
    def confirm(key):
        with lock:
            task=record('tasks',key);values=request.get_json() or {}
            task=flow.change(task,values)
            step=values.get('step')
            if not isinstance(step,int):raise ValueError('步骤无效。')
            flow.confirm(task,step,record('assets',task['asset_id']))
            task=store.put('tasks',task);save_archive(record('assets',task['asset_id']))
            return jsonify(task)

    @app.post('/api/tasks/<key>/production')
    def operation(key):
        values=request.get_json() or {};kind=values.get('kind')
        if kind=='dub':raise ValueError('请先审核讲稿并选择音色，然后点击确认生成。')
        if kind=='auto':
            return jsonify(app.extensions['production_auto'](key,values.get('version'))),202
        if kind not in ('title','description','tags','cover','frames','transcribe','translate','retranslate','dub','metadata'):
            raise ValueError('制作动作无效。')
        if kind not in ('transcribe','metadata','frames') and values.get('ai_confirmed') is not True:
            raise ValueError('请确认使用已配置 AI 服务。')
        with lock:
            task=record('tasks',key);flow.editable(task)
            if values.get('version')!=task['version']:raise ValueError('版本已变化，请刷新。')
            if task.get('production_job',{}).get('status')=='running':raise ValueError('当前任务正在处理，请勿重复提交。')
            if kind=='dub' and task.get('mode')!='dub':raise ValueError('请先选择中文配音模式。')
            if kind in ('translate','retranslate') and task.get('mode')=='original':raise ValueError('保留原片模式不需要制作字幕。')
            if kind in ('translate','retranslate') and task.get('mode')=='subtitles':
                asset=record('assets',task['asset_id'])
                from .media import cover_signature
                if task.get('cover_review_hash')!=cover_signature(asset,task):raise ValueError('请先确认当前封面。')
                if any(p.get('task_id')==key and p.get('status') in ('queued','running','succeeded','needs_check') for p in store.list('publications')):
                    raise ValueError('已有投稿记录，请创建独立重做版本。')
                store.backup('before-subtitle-translation')
                task.pop('subtitle_review_key',None)
                task.update(status='draft',workflow_stage='subtitle_translation',reviewed_version=None)
            if kind in ('translate','retranslate') and values.get('reset_translation_attempts') is True:
                from .translation import reset_attempts
                store.backup('before-subtitle-retry-reset')
                from .subtitle_workspace import workspace
                asset=record('assets',task['asset_id'])
                reset_attempts(workspace(archive,asset,task,True) if task.get('mode')=='subtitles' else archive.project(asset)/'02_字幕/translation-cache')
            job_id=uuid.uuid4().hex;version=task['version']
            task['production_job']={'id':job_id,'kind':kind,'status':'running','message':'正在准备','started_at':time.time()}
            store.put('tasks',task)
        def progress(message):
            with lock:
                latest=record('tasks',key)
                if latest['production_job'].get('id')!=job_id:return
                latest['production_job']['message']=message;store.put('tasks',latest)
        @task_scope(key)
        def work():
            try:
                asset=record('assets',task['asset_id']);folder=archive.project(asset)/'02_字幕'
                changes={};asset_changes={};candidate=None
                if kind=='frames':
                    from .cover_frames import ensure,defaults,with_original
                    gallery=with_original(ensure(runtime,asset,progress),asset,task)
                    changes.update(cover_frames=gallery,cover_selection=task.get('cover_selection') or defaults(gallery,asset['duration']),cover_crops=task.get('cover_crops',{}))
                elif kind=='metadata':
                    progress('正在重新取得原始标题、简介、标签与封面；保留人工编辑')
                    meta=youtube.refresh_metadata(asset.get('source_url',''),archive.project(asset)/'01_原始视频'/'metadata-refresh',runtime,data/'youtube-cookies.txt')
                    asset_changes.update(description=meta.get('description',''),tags=meta.get('tags') or [])
                    if meta.get('original_thumbnail'):asset_changes['original_thumbnail']=meta['original_thumbnail']
                    if not task.get('description') and task['confirmed']<1:changes['description']=asset_changes['description']
                    if not task.get('tags') and task['confirmed']<2:changes['tags']=asset_changes['tags']
                elif kind in ('title','description','tags','cover'):
                    candidate=ai.suggest(root,data,asset,task,kind,progress)
                elif kind=='transcribe':
                    cues,path=ai.transcribe(asset,runtime,progress);asset_changes['source_subtitles']=path
                elif kind in ('translate','retranslate'):
                    from .subtitle_workspace import workspace
                    cache=workspace(archive,asset,task,True) if task.get('mode')=='subtitles' else folder/'translation-cache'
                    translated,path,source=ai.prepare_subtitles(root,data,asset,runtime,cache,progress,force=kind=='retranslate' or (cache/'manual-v1').exists())
                    out=folder/(job_id+'.zh.srt');ai.write_srt(translated,out)
                    changes['translated_subtitles']=str(out)
                    changes['subtitle_source']=source
                    if task.get('mode')=='subtitles':
                        changes.update(speech_source_subtitles=str(out),translated_subtitles_hash=media_checksum(out))
                elif kind=='dub':
                    path=task.get('translated_subtitles')
                    if not path or not Path(path).is_file():raise ValueError('请先制作或导入中文字幕，再生成配音。')
                    cues=ai.read_cues(path)
                    # Clip overlap before fitting voices into individual slots.
                    for a,b in zip(cues,cues[1:]):a['end']=min(a['end'],b['start'])
                    if any(c['start']>=c['end'] for c in cues):raise ValueError('字幕时间轴重叠，请先修正。')
                    changes['voice_path']=ai.dub(root,data,runtime,cues,archive.project(asset)/'03_制作'/key/'voice-cache',progress)
                with lock:
                    latest=record('tasks',key)
                    if latest['version']!=version or latest['production_job'].get('id')!=job_id:return
                    if candidate:
                        latest['suggestions'][kind]={**candidate,'version':version,'created_at':time.time()}
                    if changes:
                        latest.update(changes,version=version+1,reviewed_version=None,status='draft',progress='')
                        if kind in ('translate','retranslate'):
                            latest.pop('speech_review_hash',None)
                            latest.pop('speech_plan',None)
                            if task.get('mode')=='subtitles':latest['workflow_stage']='subtitle_review'
                    latest['production_job'].update(status='completed',message='候选已生成，请选择采用。' if candidate else '处理完成，文件已保存。')
                    if kind in ('translate','retranslate') and task.get('mode')=='subtitles':
                        latest['production_job']['message']='全文翻译已完成，请人工审核字幕；确认后才能独立封装。'
                    asset.update(asset_changes);save_archive(asset)
                    archive.manifest(asset,[latest,*[t for t in store.list('tasks') if t['asset_id']==asset['id'] and t['id']!=key]])
                    # Publish completion last, after files and archive are durable.
                    store.put('tasks',latest)
            except Exception as exc:
                with lock:
                    latest=record('tasks',key)
                    if latest['production_job'].get('id')==job_id:
                        paused=getattr(exc,'code',None)=='subtitle_review'
                        latest['production_job'].update(status='paused' if paused else 'failed',**public_failure(exc))
                        if kind in ('translate','retranslate') and task.get('mode')=='subtitles':
                            latest.update(status='draft',workflow_stage='subtitle_review' if paused else 'subtitle_translation',error='')
                        store.put('tasks',latest)
        threading.Thread(target=work,daemon=True).start()
        return jsonify(task),202

    @app.post('/api/tasks/<key>/production-file')
    def upload(key):
        with lock:
            task=record('tasks',key);flow.editable(task)
            if str(task['version'])!=request.form.get('version'):raise ValueError('版本已变化，请刷新。')
            kind=request.form.get('kind');file=request.files.get('file')
            if kind not in ('original_cover','source_subtitles','translated_subtitles') or not file:
                raise ValueError('请选择封面或字幕文件。')
            asset=record('assets',task['asset_id'])
            folder=archive.project(asset)/'03_制作'/key/'imports';folder.mkdir(parents=True,exist_ok=True)
            if kind=='original_cover':
                path=folder/(uuid.uuid4().hex+'.jpg')
                try:
                    image=Image.open(file.stream);image.load()
                    ImageOps.exif_transpose(image).convert('RGB').save(path,quality=95)
                except Exception:raise ValueError('封面无法解码，请选择有效的图片。') from None
                task['original_thumbnail']=str(path);task['confirmed']=min(task['confirmed'],2)
            else:
                content=file.stream.read(2*1024*1024+1)
                if len(content)>2*1024*1024:raise ValueError('字幕文件最多 2 MB。')
                path=folder/(uuid.uuid4().hex+'.srt');path.write_bytes(content)
                try:
                    cues=ai.read_cues(path)
                    if kind=='translated_subtitles':
                        from .translation import valid_text
                        invalid=[(i+1,c['text']) for i,c in enumerate(cues) if not valid_text(c['text'],c['text'],'proper_name')]
                        if invalid:
                            i,text=invalid[0]
                            raise ValueError(f'第 {i} 段「{text[:80]}」不是可用中文字幕或独立专名，或含乱码；请修正后导入。')
                    if max(c['end'] for c in cues)>asset['duration']+2:raise ValueError('字幕时间轴超出视频时长。')
                except UnicodeError:
                    path.unlink(missing_ok=True);raise ValueError('字幕编码不是 UTF-8，请转换编码后导入。') from None
                except ValueError as exc:
                    path.unlink(missing_ok=True);raise ValueError(str(exc)) from None
                if kind=='source_subtitles':
                    asset['source_subtitles']=str(path)
                    for field in ('translated_subtitles','speech_source_subtitles','translated_subtitles_hash','subtitle_source'):task.pop(field,None)
                else:
                    task['translated_subtitles']=str(path)
                    task['speech_source_subtitles']=str(path)
                    task['translated_subtitles_hash']=media_checksum(path)
                    task['subtitle_source']='manual_import'
                    from .subtitle_workspace import workspace
                    (workspace(archive,asset,task)/'semantic-enabled.json').unlink(missing_ok=True)
                task.pop('speech_review_hash',None)
                task.pop('speech_plan',None)
                task.pop('speech_preparation',None)
            task.update(version=task['version']+1,reviewed_version=None,status='draft',progress='')
            store.put('tasks',task);save_archive(asset)
            return jsonify(task)
