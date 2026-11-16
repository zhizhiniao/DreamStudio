"""Task-local subtitle editing; read live checkpoints without holding the worker lock."""
import hashlib
import json
import shutil
import time
from pathlib import Path
from flask import request, jsonify
from . import production_ai as ai, production as flow, media
from .translation import cue_key, human_text, valid_text
from .youtube import read_json, write_json

def review_stamp(row):
    return hashlib.sha256(json.dumps([row['source'],row['text'],row['start'],row['end']],ensure_ascii=False).encode()).hexdigest()


def workspace(archive,asset,task,create=False):
    folder=archive.project(asset)/'03_制作'/task['id']/'subtitle-workspace'
    if create and not (folder/'interactive.json').exists():
        folder.mkdir(parents=True,exist_ok=True)
        legacy=archive.project(asset)/'02_字幕/translation-cache'
        for name in ('cues-v2','attempts-v1'):
            if (legacy/name).is_dir():shutil.copytree(legacy/name,folder/name,dirs_exist_ok=True)
        write_json(folder/'interactive.json',{'created_at':time.time()})
        # New workspaces default to sentence translation. Existing workspaces migrate explicitly.
        saved=Path(task.get('speech_source_subtitles') or task.get('translated_subtitles') or '__missing__')
        if not saved.is_file():write_json(folder/'semantic-enabled.json',{'policy':'sentence-zh-v1'})
    return folder


def snapshot(folder,asset,task,data):
    manifest=read_json(folder/'manifest.json')
    cues=ai.source_cues(asset)[0] or manifest.get('cues') or []
    model=manifest.get('model',ai.config(data).get('OPENAI_MODEL_NAME'))
    if (folder/'semantic-enabled.json').exists() and task.get('subtitle_source')!='manual_import':
        from .semantic_subtitles import fingerprint, plan, view
        model=ai.config(data).get('OPENAI_MODEL_NAME')
        value=read_json(folder/'semantic-plan.json')
        if value.get('source_hash')!=fingerprint(cues) or value.get('model')!=model:
            # Pure planning: no model call. Discard no old cache or manual correction.
            value=plan(folder,cues,model)
        result=view(folder,value)
        reviews=read_json(folder/'reviews.json')
        for row in result['rows']:
            row['reviewed']=row.get('accepted',False) or reviews.get(str(row['id']))==review_stamp(row)
            if row['reason']=='正在等待整句翻译返回' and task.get('production_job',{}).get('status')=='running':row['state']='translating'
        return result
    # A saved SRT may have a different segmentation from the English source.
    saved=[]
    path=Path(task.get('speech_source_subtitles') or task.get('translated_subtitles') or '__missing__')
    if path.is_file() and not str(task.get('subtitle_source','')).startswith('原文时间轴'):
        saved=ai.read_cues(path)
    if saved:
        original=cues
        aligned=len(original)==len(saved) and all(a['start']==b['start'] and a['end']==b['end'] for a,b in zip(original,saved))
        cues=[dict(c,text=original[i]['text'] if aligned else ' '.join(s['text'] for s in original if s['start']<c['end'] and s['end']>c['start']) or c['text']) for i,c in enumerate(saved)]
    signature=hashlib.sha256(json.dumps([cues,model],ensure_ascii=False,sort_keys=True).encode()).hexdigest()
    rows=[]
    for i,c in enumerate(cues):
        key=cue_key(c,model)
        cached=read_json(folder/'cues-v2'/(key+'.json'))
        manual=read_json(folder/'manual-v1'/(key+'.json'))
        failure=read_json(folder/'attempts-v1'/(key+'.json')).get('failure',{})
        text=saved[i]['text'] if saved else cached.get('text','')
        complete=bool(saved) or valid_text(c['text'],text,cached.get('preserve_as'))
        if not complete and valid_text(c['text'],c['text']):text=c['text'];complete=True
        human=task.get('subtitle_source')!='manual_import' and human_text(c['text'],manual.get('text'),manual.get('keep_original'))
        if human:text=manual['text'];complete=True
        waiting=task.get('production_job',{}).get('status')=='running' and failure.get('code')=='requesting'
        rows.append(dict(id=i,start=c['start'],end=c['end'],source=c['text'],text=text if complete else '',
            candidate=failure.get('candidate') or '',reason='' if complete else failure.get('reason',''),
            state='manual' if human else 'completed' if complete else 'translating' if waiting else 'problem' if failure else 'pending',
            keep_original=bool(manual.get('keep_original')),accepted=bool(manual.get('accepted')) and human))
    reviews=read_json(folder/'reviews.json')
    for row in rows:row['reviewed']=row.get('accepted',False) or reviews.get(str(row['id']))==review_stamp(row)
    return dict(rows=rows,total=len(rows),completed=sum(r['state'] in ('completed','manual') for r in rows),
                signature=signature,model=model,cues=cues)


def register(app,root,data,runtime,store,archive,record,save_archive,lock):
    def context(key,edit=False):
        task=record('tasks',key);asset=record('assets',task['asset_id'])
        if task.get('mode')!='subtitles':raise ValueError('此编辑器用于保留原声＋中文字幕任务。')
        locked=any(p.get('task_id')==key and p.get('status') in ('queued','running','succeeded','needs_check') for p in store.list('publications'))
        if edit:
            if locked:raise ValueError('已有投稿记录，请先创建独立重做版本。')
            flow.editable(task)
            if (request.get_json() or {}).get('version')!=task['version']:raise ValueError('版本已变化，请刷新后重试。')
        return task,asset,workspace(archive,asset,task),locked

    @app.post('/api/tasks/<key>/subtitle-sentence-mode')
    def subtitle_sentence_mode(key):
        with lock:
            task,asset,folder,_=context(key,True)
            source,_=ai.source_cues(asset)
            if not source:raise ValueError('请先取得英文源字幕，再使用整句翻译。')
            from .semantic_subtitles import sentence_units
            sentence_units(source)
            old=snapshot(folder if folder.exists() else archive.project(asset)/'02_字幕/translation-cache',asset,task,data)
            store.backup('before-sentence-subtitles')
            folder=workspace(archive,asset,task,True)
            (folder/'history').mkdir(exist_ok=True)
            write_json(folder/'history'/('before-sentence-'+str(time.time_ns())+'.json'),dict(task=task,rows=old['rows']))
            hints=read_json(folder/'semantic-hints.json').get('rows',[])
            hints.extend(dict(start=r['start'],end=r['end'],source=r['source'],text=r['text']) for r in old['rows'] if r['state']=='manual')
            write_json(folder/'semantic-hints.json',{'rows':hints})
            write_json(folder/'semantic-enabled.json',{'policy':'sentence-zh-v1'})
            from .semantic_subtitles import plan
            plan(folder,source,ai.config(data).get('OPENAI_MODEL_NAME'))
            for field in ('translated_subtitles','speech_source_subtitles','translated_subtitles_hash','prepared_version','speech_review_hash','subtitle_review_key'):
                task.pop(field,None)
            task.update(version=task['version']+1,reviewed_version=None,status='draft',subtitle_source='整句翻译待处理',workflow_stage='subtitle_translation',
                production_job={'status':'paused','error_code':'subtitle_review','message':'已切换整句翻译；旧译文已备份，点击开始全文翻译后处理。'})
            (folder/'pause.json').unlink(missing_ok=True)
            store.put('tasks',task);save_archive(asset)
            return jsonify(task)

    @app.get('/api/tasks/<key>/subtitle-workspace')
    def subtitle_workspace(key):
        task,asset,folder,locked=context(key)
        # Old failed tasks are readable immediately, even before their next run.
        if not folder.exists():folder=archive.project(asset)/'02_字幕/translation-cache'
        result=snapshot(folder,asset,task,data)
        result.pop('model');result.pop('cues')
        result.update(version=task['version'],job=task.get('production_job',{}),locked=locked,
                      pause_requested=(workspace(archive,asset,task)/'pause.json').exists())
        from .subtitle_stages import approved
        result['approved']=approved(folder,asset,task,data)
        result['pending']=sum(r['state'] in ('pending','translating') for r in result['rows'])
        result['problems']=sum(r['state']=='problem' for r in result['rows'])
        return jsonify(result)

    @app.post('/api/tasks/<key>/subtitles-approve')
    def subtitles_approve(key):
        with lock:
            task,asset,folder,_=context(key,True)
            result=snapshot(folder,asset,task,data);values=request.get_json() or {}
            if values.get('confirmed') is not True or values.get('signature')!=result['signature']:
                raise ValueError('请审核当前字幕后确认，来源变化时请刷新。')
            if not result['rows'] or any(r['state'] not in ('completed','manual') for r in result['rows']):
                raise ValueError('请先完成全文翻译，并补填或确认待定字幕；已完成内容会保留。')
            from .subtitle_stages import review_key, approved
            if approved(folder,asset,task,data):return jsonify(task)
            store.backup('before-subtitle-approval')
            folder.mkdir(parents=True,exist_ok=True)
            path=folder/('approved-'+str(time.time_ns())+'.srt')
            ai.write_srt([dict(start=r['start'],end=r['end'],text=r['text']) for r in result['rows']],path)
            task.update(subtitle_review_key=review_key(result,task),subtitle_approved_path=str(path),
                        subtitle_approved_hash=media.checksum(path),translated_subtitles=str(path),
                        speech_source_subtitles=str(path),translated_subtitles_hash=media.checksum(path),
                        workflow_stage='subtitle_packaging',status='draft',reviewed_version=None,error='',
                        production_job={'kind':'subtitle_review','status':'completed','message':'字幕审核已确认，下一步独立封装成片。'})
            store.put('tasks',task);save_archive(asset)
            return jsonify(task)

    @app.post('/api/tasks/<key>/subtitle-pause')
    def subtitle_pause(key):
        with lock:
            task,asset,folder,locked=context(key)
            if locked:raise ValueError('已有投稿记录。')
            if task.get('production_job',{}).get('status')!='running':raise ValueError('当前没有正在运行的任务。')
            if task['production_job'].get('kind') not in ('finish','translate','retranslate') or task['production_job'].get('stage')=='render':
                raise ValueError('当前已进入封装或其他制作阶段，完成后可修改字幕并重新生成。')
            folder=workspace(archive,asset,task,True)
            write_json(folder/'pause.json',{'requested_at':time.time()})
            return jsonify(message='已请求暂停；当前请求结束并保存后停止，不再翻译下一批。')

    @app.post('/api/tasks/<key>/subtitle-review')
    def subtitle_review(key):
        with lock:
            task,asset,folder,_=context(key,True)
            legacy=not (folder/'semantic-enabled.json').exists()
            folder=workspace(archive,asset,task,True)
            if legacy:(folder/'semantic-enabled.json').unlink(missing_ok=True)
            result=snapshot(folder,asset,task,data);values=request.get_json() or {}
            if values.get('signature')!=result['signature']:raise ValueError('字幕来源已变化，请刷新后检查。')
            i=values.get('id')
            if type(i) is not int or not 0<=i<len(result['rows']):raise ValueError('字幕段号无效。')
            row=result['rows'][i]
            if row['state'] not in ('completed','manual'):raise ValueError('请先补全或修正这条字幕，再确认校对。')
            if values.get('text')!=row['text']:raise ValueError('译文已变化，请检查最新内容。')
            store.backup('before-subtitle-review')
            reviews=read_json(folder/'reviews.json');reviews[str(i)]=review_stamp(row)
            write_json(folder/'reviews.json',reviews)
            return jsonify(reviewed=True,id=i)

    @app.post('/api/tasks/<key>/subtitle-workspace')
    def subtitle_save(key):
        with lock:
            task,asset,folder,_=context(key,True)
            values=request.get_json() or {}
            if values.get('resume') is True and not values.get('retry') and not values.get('edits') and values.get('accept_id') is None:
                # Navigation/continuation is not an edit and must not migrate a new workspace to the legacy policy.
                store.backup('before-subtitle-resume')
                folder=workspace(archive,asset,task,True)
                (folder/'pause.json').unlink(missing_ok=True)
                return jsonify(task)
            # Do not change an existing editor's policy merely by saving its first edit.
            legacy=not (folder/'semantic-enabled.json').exists()
            folder=workspace(archive,asset,task,True)
            if legacy:(folder/'semantic-enabled.json').unlink(missing_ok=True)
            result=snapshot(folder,asset,task,data);values=request.get_json() or {}
            if values.get('signature')!=result['signature']:raise ValueError('原文或字幕时间轴已变化，请刷新后重新编辑。')
            edits=values.get('edits',[])
            accepted_id=values.get('accept_id')
            if accepted_id is not None:
                if edits or type(accepted_id) is not int or not 0<=accepted_id<len(result['rows']):raise ValueError('待定字幕段号无效。')
                row=result['rows'][accepted_id]
                candidate=row.get('candidate') or ''
                if row['state']!='problem' or not candidate.strip():raise ValueError('此条没有可确认的候选译文，请先补充译文。')
                if values.get('candidate')!=candidate:raise ValueError('候选译文已变化，请重新检查。')
                edits=[dict(id=accepted_id,text=candidate,keep_original=candidate.strip()==row['source'].strip())]
            if not isinstance(edits,list) or len(edits)>len(result['rows']):raise ValueError('字幕修改格式无效。')
            seen=set()
            for edit in edits:
                i=edit.get('id')
                if type(i) is not int or i<0 or i>=len(result['rows']) or i in seen:raise ValueError('字幕段号无效或重复。')
                seen.add(i);row=result['rows'][i]
                if not human_text(row['source'],edit.get('text'),edit.get('keep_original') is True):
                    raise ValueError(f'第 {i+1} 段：请填写中文译文；若确认使用原文，请将左栏原文填入右栏后点击确定。不接受空白、乱码或字幕标记。')
            retry=values.get('retry') is True
            if retry and edits:raise ValueError('请先保存人工修改，再重新翻译问题段。')
            store.backup('before-subtitle-edit')
            target=folder/'manual-v1';target.mkdir(exist_ok=True)
            history=folder/'history';history.mkdir(exist_ok=True)
            write_json(history/(str(time.time_ns())+'.json'),{'version':task['version'],'rows':result['rows']})
            # One manifest binds manual decisions to the exact source timeline/model.
            write_json(folder/'manifest.json',{'cues':result['cues'],'model':result['model']})
            for edit in edits:
                i=edit['id'];row=result['rows'][i]
                write_json(target/(cue_key(result['cues'][i],result['model'])+'.json'),
                           {'text':edit['text'].strip(),'keep_original':edit.get('keep_original') is True,'accepted':i==accepted_id,'at':time.time()})
                row.update(text=edit['text'].strip(),state='manual',reason='')
            if edits:
                for field in ('translated_subtitles','speech_source_subtitles','translated_subtitles_hash','prepared_version','speech_review_hash','subtitle_review_key'):
                    task.pop(field,None)
                if all(r['state'] in ('completed','manual') for r in result['rows']) and result['rows']:
                    out=folder/('edited-'+str(time.time_ns())+'.srt')
                    ai.write_srt([dict(start=r['start'],end=r['end'],text=r['text']) for r in result['rows']],out)
                    task.update(translated_subtitles=str(out),speech_source_subtitles=str(out),translated_subtitles_hash=media.checksum(out))
                task.update(version=task['version']+1,reviewed_version=None,status='draft',subtitle_source='人工审校中文字幕',workflow_stage='subtitle_review')
                complete=sum(r['state'] in ('completed','manual') for r in result['rows'])
                failures=[f for f in task.get('production_job',{}).get('details',{}).get('failures',[]) if f.get('id') not in seen]
                task['production_job']={**task.get('production_job',{}),'status':'paused','error_code':'subtitle_review',
                    'message':f'修改已保存，已完成 {complete}/{len(result["rows"])} 段。未处理部分可继续翻译；全文审核后才能封装。',
                    'details':{'completed':complete,'total':len(result['rows']),'failures':failures}}
            if values.get('resume') is True:
                if retry:
                    from .translation import reset_attempts
                    reset_attempts(folder)
                (folder/'pause.json').unlink(missing_ok=True)
            store.put('tasks',task);save_archive(asset)
            return jsonify(task)
