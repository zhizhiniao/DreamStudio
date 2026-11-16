"""Durable preparation drafts; only a human review can approve the result."""
import copy
import threading
import time
import uuid
from pathlib import Path
from . import production as flow, production_ai as ai, media
from .production_errors import public_failure
from .ai_budget import task_scope


def register(app, root, data, runtime, store, archive, record, save_archive, lock):
    def start(key, version=None):
        with lock:
            task=record('tasks',key)
            flow.editable(task)
            if version is not None and version!=task['version']:raise ValueError('版本已变化，请刷新。')
            job_id=uuid.uuid4().hex
            task['mode']=task.get('mode') or 'original'
            task['production_job']={'id':job_id,'kind':'auto','status':'running','message':'自动准备中','started_at':time.time()}
            task.pop('prepared_version',None)
            task.update(status='draft',reviewed_version=None,error='')
            needs_text=any(not task.get(field) or not (task.get('confirmed',-1)>=step or
                          task.get('field_sources',{}).get(field) in ('manual','ai','linked'))
                          for step,field in enumerate(('title','description','tags')))
            if needs_text and not ai.config(data).get('OPENAI_API_KEY'):
                task['production_job'].update(status='failed',error_code='ai_config',retryable=False,
                    message='尚未配置 AI 服务，请在“设置 → 制作服务”填写接口与密钥后继续。')
                return store.put('tasks',task)
            store.put('tasks',task)

        def progress(message):
            with lock:
                latest=record('tasks',key)
                latest['production_job']['message']=message
                store.put('tasks',latest)

        def checkpoint(changes=None):
            with lock:
                if changes:task.update(changes,version=task['version']+1,reviewed_version=None)
                latest=record('tasks',key)
                task['production_job']=latest['production_job']
                store.put('assets',asset)
                archive.manifest(asset,[task,*[t for t in store.list('tasks') if t['asset_id']==asset['id'] and t['id']!=key]])
                store.put('tasks',task)

        @task_scope(key)
        def work():
            nonlocal task, asset
            try:
                asset=record('assets',task['asset_id'])
                folder=archive.project(asset)
                if not ai.source_cues(asset)[0] and not asset.get('description') and task.get('confirmed',-1)<1:
                    progress('提取视频内容：正在本机识别原文字幕')
                    _,path=ai.transcribe(asset,runtime,progress)
                    asset['source_subtitles']=path;checkpoint()
                for step,(kind,field) in enumerate((('title','title'),('description','description'),('tags','tags'),('cover','cover_title'))):
                    origin=task.get('field_sources',{}).get(field)
                    if kind=='cover':
                        if task.get('cover_style')=='original':continue
                        text=task.get('cover_title','')
                        if not flow.chinese(text):
                            text=task['title']
                        if not flow.chinese(text) or len(text.replace('\n',''))>30 or not 1<=len(text.splitlines())<=3:
                            raise ValueError('请在封面排版中手动调整中文标题：最多 30 字、1–3 行。')
                        changes={}
                        if text!=task.get('cover_title'):changes['cover_title']=text
                        if text.replace('\n','')!=task['title']:changes['title']=text.replace('\n','')
                        if changes:checkpoint(changes)
                        continue
                    else:valid=bool(task.get(field))
                    if valid and (task.get('confirmed',-1)>=step or origin in ('manual','ai','linked')):continue
                    progress('自动准备'+('封面标题' if kind=='cover' else ['中文标题','内容简介','标签'][step]))
                    candidate=ai.suggest(root,data,asset,task,kind,progress)
                    task['suggestions'][kind]={**candidate,'version':task['version'],'created_at':time.time()}
                    value=candidate['value'] if kind in ('description','tags') else candidate['value'][0]
                    task.setdefault('field_sources',{})[field]='ai'
                    changes={field:value}
                    if kind=='title' and (task['field_sources'].get('cover_title') in ('ai','linked') or not flow.chinese(task.get('cover_title',''))):
                        changes['cover_title']=value
                        task['field_sources']['cover_title']='linked'
                    checkpoint(changes)
                progress('正在生成横竖封面；配音将在人工审核后开始')
                output=folder/'03_制作'/key/f"covers-v{task['version']}-{job_id}"
                result=media.render_covers(root,runtime,asset,task,output,progress)
                with lock:
                    task.update(result,status='draft',workflow_stage='cover_review',progress='封面已就绪，请确认封面后准备字幕与讲稿。')
                    task['production_job'].update(status='completed',message='封面已生成，请确认后进入字幕与讲稿审核。',percent=100)
                    archive.manifest(asset,[task,*[t for t in store.list('tasks') if t['asset_id']==asset['id'] and t['id']!=key]])
                    store.put('tasks',task)
            except Exception as exc:
                with lock:
                    latest=record('tasks',key)
                    latest['production_job'].update(status='failed',**public_failure(exc))
                    store.put('tasks',latest)
        asset=None
        threading.Thread(target=work,daemon=True).start()
        return task
    app.extensions['production_auto']=start
