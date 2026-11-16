import tempfile
from pathlib import Path
from flask import Response, jsonify, request

from . import chapter_navigation as nav, media, production_ai as ai
from .ai_budget import task_scope
from .production import editable


def register(app, root, data, runtime, store, record, lock):
    def context(key, values):
        task = record('tasks',key)
        if task.get('mode') not in ('subtitles','dub'):
            raise ValueError('顶部章节适用于字幕或中文配音成片。')
        if values.get('version') != task['version']:
            raise ValueError('制作版本已变化，请刷新后重新核对章节。')
        editable(task)
        if any(p.get('task_id') == key and p['status'] in ('queued','running','succeeded','needs_check')
               for p in store.list('publications')):
            raise ValueError('已有投稿或待核实记录，请使用独立重做任务。')
        return task, record('assets',task['asset_id'])

    @app.put('/api/tasks/<key>/chapters')
    def save_chapters(key):
        values = request.get_json() or {}
        with lock:
            task,asset = context(key,values)
            config = nav.checked(task,values)
            if config == task.get('chapter_navigation',{'style':'off'}):
                return jsonify(task)
            if config['style'] != 'off':
                with tempfile.TemporaryDirectory() as folder:
                    nav.video_filter(folder,root,config,asset['width'],asset['height'],task['duration'])
            store.backup('before-chapter-save')
            task.update(chapter_navigation=config, version=task['version']+1, reviewed_version=None,
                        status='draft', error='', progress='章节设置已保存，请重新封装成片。')
            task.pop('prepared_version',None)
            if task['mode']=='subtitles':
                task['workflow_stage']='subtitle_packaging'
            return jsonify(store.put('tasks',task))

    @app.post('/api/tasks/<key>/chapters/preview')
    def preview(key):
        values = request.get_json() or {}
        with lock:
            task,asset = context(key,values)
        config = nav.checked(task,values)
        when = nav.seconds(values.get('time',0))
        if when >= task['duration']:
            raise ValueError('预览时刻须早于片尾。')
        with tempfile.TemporaryDirectory() as folder:
            filters = [f'setpts=PTS-STARTPTS+{when}/TB']
            if config['style'] != 'off':
                filters.append(nav.video_filter(folder,root,config,asset['width'],asset['height'],task['duration']))
            filters.append('scale=min(960\\,iw):-2')
            jpeg = media.run([Path(runtime)/'bin/ffmpeg.exe','-v','error','-ss',str(task.get('start',0)+when),
                     '-i',asset['path'],'-frames:v','1','-vf',','.join(filters),
                     '-c:v','mjpeg','-f','image2pipe','pipe:1'],30)
        if not jpeg:
            raise ValueError('此时刻未读到视频帧，请略微向前移动。')
        return Response(jpeg,mimetype='image/jpeg',headers={'Cache-Control':'no-store'})

    @app.post('/api/tasks/<key>/chapters/suggest')
    def suggest(key):
        values = request.get_json() or {}
        with lock:
            task,_ = context(key,values)
        if values.get('ai_confirmed') is not True:
            raise ValueError('请先确认使用已配置的 AI 生成章节草案。')
        if task['mode']=='subtitles':
            path = Path(task.get('subtitle_approved_path') or '__missing__')
            if not path.is_file() or media.checksum(path)!=task.get('subtitle_approved_hash'):
                raise ValueError('请先完成字幕审核。')
            rows = ai.read_cues(path)
        else:
            rows = [dict(start=g['start'],end=g['end'],text=g['speech']) for g in task.get('speech_plan',[]) if g.get('speech')]
        start = task.get('start',0)
        cues = [dict(start=max(0,round(r['start']-start,2)),text=r['text']) for r in rows
                if r['end']>start and r['start']<start+task['duration']]
        if not cues or sum(len(r['text']) for r in cues)>60000:
            raise ValueError('当前讲稿或字幕为空或过长，请手动填写章节。')
        result = task_scope(key)(ai.call)(root,data,
            '根据全文的真实话题转折划分 1–8 章。每章用简短中文标题，尽量 2–8 字。返回 {"chapters":[{"cue":1,"title":"…"}]}。cue 是每章首条字幕的编号，首章必须为 1。',
            {'operation':'chapter_navigation','字幕':[{'cue':i,'text':r['text']} for i,r in enumerate(cues,1)]})
        items = result.get('chapters') if isinstance(result,dict) else None
        if not isinstance(items,list) or not items or any(not isinstance(r,dict) or type(r.get('cue')) is not int
                or not 1 <= r['cue'] <= len(cues) for r in items) or items[0]['cue']!=1:
            raise ValueError('AI 返回的字幕编号无效，请手动编辑。')
        chapters = [{'start':0 if i==0 else cues[r['cue']-1]['start'],'title':r.get('title')} for i,r in enumerate(items)]
        return jsonify(chapters=nav.checked(task,{'style':'dash','chapters':chapters})['chapters'],version=task['version'])
