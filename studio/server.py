import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from flask import Flask, jsonify, request, send_file, send_from_directory
from werkzeug.exceptions import HTTPException
from werkzeug.utils import secure_filename
from .store import Store
from . import draft_covers
from . import publication_state
from . import scheduling
from .accounts import AccountService,PLATFORMS
from .hub import Hub
from . import media
from .lifecycle import project as lifecycle_project
from .archive import Archive
from . import youtube
from .youtube_settings import register as register_youtube_settings
from .production_settings import register as register_production_settings
from . import production
from .production_ai import config as production_config
from .production_routes import register as register_production
from .batch_publish import register as register_batch_publish
from .platform_rules import channels_short_title, adapt_draft, title_issues

ROOT=Path(__file__).resolve().parents[1]


def create_app(data_root=None,automatic_checks=True):
    root=ROOT
    data=Path(data_root or root/'data')
    store=Store(data)
    archive=Archive(data/'projects' if data_root else root/'项目素材')
    runtime=Path(json.loads((root/'runtime.json').read_text(encoding='utf-8'))['runtime'])
    accounts=AccountService(root,store,runtime)
    if data_root:
        # Test credentials and state never touch the real project account tree.
        accounts.folder=lambda a: (data/'accounts'/a['id'])
    hub=Hub(root)
    app=Flask(__name__,static_folder=str(root/'static'),static_url_path='/static')
    app.config.update(MAX_CONTENT_LENGTH=2*1024**3)
    app.extensions.update(store=store,accounts=accounts,hub=hub,runtime=runtime,data=data)
    youtube_settings=register_youtube_settings(app,data,runtime)
    register_production_settings(app,data)
    from .account_management import register as register_account_management
    job_locks={}
    lock=threading.RLock()
    register_account_management(app,store,accounts,lock)
    for a in store.list("accounts"):
        if a.get("state")=="checking":
            a.update(state="unavailable",message="检查因服务重启中断，请重试")
            store.put("accounts",a)
    for job in store.list('downloads'):
        if job.get('subtitle_busy'):
            job.update(subtitle_busy=False,warnings=['字幕补下曾中断，可再次补下；视频已保留。'])
            store.put('downloads',job)
        if job.get('status') in ('queued','running'):
            job.update(status='failed',message='服务曾中断，保留已下载文件，可重试。')
            store.put('downloads',job)

    # Correct historical YouTube provenance after Store created a consistent startup backup.
    youtube_assets={a['id'] for a in store.list('assets') if a.get('source')=='YouTube'}
    for item in store.list('downloads'):
        youtube_assets.add(item.get('asset_id',''))
        if item.get('source_account'):
            item.update(source_account='',source_platform='youtube');store.put('downloads',item)
    for kind in ('assets','tasks'):
        for item in store.list(kind):
            if (item['id'] if kind=='assets' else item.get('asset_id')) in youtube_assets and item.get('source_account'):
                item['source_account']='';store.put(kind,item)

    def save_archive(asset):
        project=archive.project(asset)
        for field in ('path','thumbnail'):
            source=Path(asset.get(field,''))
            if source.is_file() and not source.resolve().is_relative_to(archive.root.resolve()):
                target=project/'01_原始视频'/source.name
                shutil.copy2(source,target)
                asset[field]=str(target)
        archive.manifest(asset,[t for t in store.list('tasks') if t['asset_id']==asset['id']])
        store.put('assets',asset)

    def account_title(a):
        name=a.get('name') or '昵称待确认'
        if name==PLATFORMS.get(a.get('platform'),'')+'账号':name='昵称待确认'
        alias=a.get('alias','')
        if alias.startswith('CreatorHub ') or alias=='造梦室存量号':alias=''
        return PLATFORMS.get(a.get('platform'),'账号')+' · '+name+((' / '+a['identifier']) if a.get('identifier') else ((' · '+alias) if alias else ''))

    def readable_events():
        names={a['id']:account_title(accounts.public(a)) for a in store.list('accounts')}
        statuses={'valid':'有效','checking':'检查中','expired':'已失效','unavailable':'未确认','failed':'失败','completed':'已完成'}
        events=store.events()
        for event in events:
            detail=event['detail']
            for key,name in names.items():detail=detail.replace(key,name)
            for key,name in statuses.items():detail=re.sub(r'\b'+key+r'\b',name,detail)
            event['detail']=detail
        return events

    for task in store.list('tasks'):
        if task.get('status')=='rendering':
            task.update(status='failed',error='制作曾中断，原始素材与参数保留，可重新制作。')
            store.put('tasks',task)
    publication_state.sync(store, accounts, interrupted=True)
    for job in store.list('sync_jobs'):
        if job.get('status')=='running':
            job.update(status='failed',message='同步曾中断，请重新读取平台数据');store.put('sync_jobs',job)

    def body():
        return request.get_json(silent=True) or {}

    def record(kind,key):
        value=store.get(kind,key)
        if value is None:
            raise ValueError('记录不存在')
        return value

    def context():
        return store.get('settings','context',{'id':'context','source':'','targets':[]})

    def dependencies():
        ctx=context()
        return ctx.get('targets',[])

    def require_gate(ids=None):
        ids=ids if ids is not None else dependencies()
        if not ids or not all(ids):
            raise ValueError('请在首页选择本次关键账号。')
        issues=accounts.issues(ids)
        if issues:
            return jsonify(error='关键账号尚未就绪，请前往首页处理。',issues=issues),409

    @app.before_request
    def local_guard():
        host=request.host.split(':')[0]
        if host not in ('127.0.0.1','localhost','[::1]'):
            return jsonify(error='仅允许本机访问'),403
        origin=request.headers.get('Origin')
        allowed_origins=('http://127.0.0.1:8020','http://localhost:8020')
        if app.testing:allowed_origins=(*allowed_origins,request.host_url.rstrip('/'))
        extension_upload=(request.path=='/cookiecloud/update' and origin and
                          origin.startswith(('chrome-extension://','moz-extension://')))
        if request.method not in ('GET','HEAD','OPTIONS') and origin and origin not in allowed_origins and not extension_upload:
            return jsonify(error='来源不允许'),403
        if request.method not in ('GET','HEAD','OPTIONS') and request.mimetype not in ('application/json','multipart/form-data'):
            return jsonify(error='请使用 JSON 或文件表单'),415

    @app.errorhandler(ValueError)
    def invalid(exc):
        return jsonify(error=str(exc)),400

    @app.errorhandler(Exception)
    def unexpected(exc):
        if isinstance(exc,HTTPException):
            return jsonify(error=exc.description),exc.code
        app.logger.error('request failed: %s',type(exc).__name__)
        return jsonify(error='操作未完成，请查看本机日志。'),500

    @app.get('/')
    def index():
        return send_from_directory(root/'static','index.html')

    @app.get('/health')
    def health():
        return jsonify(application='dreamstudio',ok=True)

    @app.get('/api/state')
    def state():
        ctx=context()
        accs=[accounts.public(a) for a in store.list('accounts')]
        issues=accounts.issues([k for k in dependencies() if k])
        cfg=production_config(data)
        tasks,assets=lifecycle_project(store.list('tasks'),store.list('assets'),store.list('publications'))
        return jsonify(workflow_features={'task_completion':True,'history_targets':True,'milestone_history':True},production_ai={'ready':bool(cfg.get('OPENAI_API_KEY')),'model':cfg.get('OPENAI_MODEL_NAME','未配置')},accounts=accs,account_check=app.extensions["account_check_progress"](),context=ctx,ready=bool(ctx.get('targets') and not issues),issues=issues,youtube_connection={k:v for k,v in youtube_settings.public().items() if k in ('session','check','sync','enabled')},
                       assets=assets,tasks=tasks,drafts=store.list('drafts'),
                       publications=store.list('publications'),collections=store.list('collections'),monitors=store.list('monitors'),
                       sync_jobs=store.list('sync_jobs'),youtube_discovery=app.extensions['youtube_discovery'].state(),
                       downloads=store.list('downloads'),storage_root=str(archive.root),events=readable_events(),capabilities={p:{'login':p in ('douyin','kuaishou','xiaohongshu','wechat_channels'),
                         'collect':p in ('douyin','xiaohongshu'),'publish':p in ('douyin','kuaishou','xiaohongshu','wechat_channels','bilibili')} for p in PLATFORMS})

    @app.post('/api/context')
    def choose_context():
        values=body()
        source=values.get('source',context().get('source',''))
        if source:record('accounts',source)
        targets=list(dict.fromkeys(values.get('targets',[])))
        for key in targets:record('accounts',key)
        ctx=store.put('settings',{'id':'context','source':source,'targets':targets})
        store.event('切换工作账号',','.join([ctx['source'],*targets]))
        return jsonify(ctx)

    @app.post('/api/enter')
    def enter():
        return jsonify(ok=True)

    def account_tags(value):
        if not isinstance(value,list) or any(not isinstance(t,str) for t in value):
            raise ValueError('账号标签必须是文字列表')
        tags=list(dict.fromkeys(t.strip() for t in value if t.strip()))
        if len(tags)>8 or any(len(t)>20 for t in tags):
            raise ValueError('每个账号最多 8 个标签，每个标签最多 20 字')
        return tags

    @app.post('/api/accounts')
    def add_account():
        values=body()
        if values.get('platform') not in PLATFORMS:
            raise ValueError('不支持的平台')
        tags=account_tags(values.get('tags',[]))
        store.backup('before-account-add')
        a=store.put('accounts',{'tags':tags,'platform':values['platform'],'name':str(values.get('name') or '未连接账号')[:80],
                     'alias':str(values.get('alias') or '新账号')[:80],'state':'missing','message':'请连接账号','paused':False})
        accounts.folder(a).mkdir(parents=True,exist_ok=True)
        store.event('添加账号',a['id'])
        return jsonify(accounts.public(a)),201

    @app.post('/api/accounts/batch')
    def batch():
        values=body();keys=values.get('ids',[])
        if not keys or len(keys)>100:raise ValueError('请选择 1 至 100 个账号')
        for key in keys:
            a=record('accounts',key)
            if values.get('action')=='check':accounts.check(key)
            elif values.get('action') in ('pause','enable'):
                a['paused']=values['action']=='pause';store.put('accounts',a)
            else:raise ValueError('操作无效')
        return jsonify(ok=True)

    @app.post('/api/accounts/<key>/login')
    def login(key):
        return jsonify(accounts.start_login(key)),202

    @app.patch('/api/accounts/<key>')
    def edit_account(key):
        values=body()
        tags=account_tags(values['tags']) if 'tags' in values else None
        a=record('accounts',key)
        store.backup('before-account-edit')
        for field in ('name','alias'):
            if field in values:a[field]=str(values[field])[:80]
        if tags is not None:a['tags']=tags
        store.put('accounts',a)
        return jsonify(accounts.public(a))

    @app.get('/api/accounts/<key>/login/<job_id>')
    def login_progress(key,job_id):
        if not re.fullmatch('[a-f0-9]{32}',job_id):raise ValueError('编号无效')
        path=accounts.folder(record('accounts',key))/'jobs'/(job_id+'.json')
        result=json.loads(path.read_text(encoding='utf-8')) if path.is_file() else {'status':'missing'}
        return jsonify({k:v for k,v in result.items() if k in ('id','status','message','qrcode','login_state')})

    @app.post('/api/accounts/<key>/session')
    def import_session(key):
        a=record('accounts',key)
        file=request.files.get('file')
        if not file:raise ValueError('请选择会话 JSON 文件')
        try:values=json.load(file.stream)
        except (ValueError,UnicodeError):raise ValueError('不是有效的 JSON 文件')
        if not isinstance(values,dict) or (a['platform']!='bilibili' and not isinstance(values.get('cookies'),list)):
            raise ValueError('请选择该账号的浏览器 storage_state JSON')
        path=accounts.state_path(a)
        if request.form.get('purpose')=='read':
            path=accounts.folder(a)/'read'/(a['platform']+'.json')
        path.parent.mkdir(parents=True,exist_ok=True)
        temporary=path.with_suffix('.tmp');temporary.write_text(json.dumps(values),encoding='utf-8');temporary.replace(path)
        if request.form.get('purpose')=='read':
            a.update(read_state='unchecked',read_message='读取会话已保存，等待实际平台读取验证');store.put('accounts',a)
        else:
            a.update(state='unchecked',checked_at=None,message='会话已保存，等待在线检查');store.put('accounts',a)
            accounts.check(key)
        return jsonify(ok=True),202

    @app.post('/api/assets/upload')
    def upload_asset():
        file=request.files.get('file')
        if not file:raise ValueError('请选择视频素材')
        key=uuid.uuid4().hex
        project={'id':key,'title':request.form.get('title') or file.filename}
        folder=archive.project(project)/'01_原始视频'
        path=folder/'source.mp4';file.save(path)
        try:info=media.probe(runtime,path)
        except Exception:
            path.unlink(missing_ok=True);raise
        cover=folder/'thumbnail.jpg';media.screenshot(runtime,path,cover)
        a=store.put('assets',{'id':key,'title':request.form.get('title') or file.filename,'source':'本机导入',
                       'source_account':'','source_url':'','author':request.form.get('author',''),
                       'path':str(path),'thumbnail':str(cover),'project_path':project['project_path'],'status':'available',**info})
        save_archive(a)
        store.event('导入素材',a['title'])
        return jsonify(a),201

    @app.post('/api/local-files/<kind>/<key>/reveal')
    def reveal_local_file(kind,key):
        if kind not in ('assets','tasks'):
            raise ValueError('不支持的文件类型')
        item=record(kind,key)
        path=item.get('path') if kind=='assets' else item.get('video_path')
        if not path:
            raise ValueError('原视频尚未下载' if kind=='assets' else '成片尚未生成')
        resolved=Path(path).resolve()
        if not (resolved.is_relative_to(data.resolve()) or resolved.is_relative_to(archive.root.resolve())):
            raise ValueError('文件不在独立项目媒体目录')
        if not resolved.is_file():
            return jsonify(error='文件已移动或删除，请检查本地路径：'+str(resolved)),404
        if sys.platform!='win32':
            return jsonify(error='打开文件位置仅支持 Windows 本机版'),400
        try:
            subprocess.Popen(['explorer.exe','/select,',str(resolved)])
        except OSError:
            return jsonify(error='无法打开资源管理器，请重试或复制文件路径手动打开'),500
        return jsonify(ok=True,path=str(resolved))

    @app.get('/api/media/<kind>/<key>/<file_name>')
    def get_media(kind,key,file_name):
        r=record(kind,key)
        if kind=='tasks' and file_name=='video' and 'chapter_navigation' in r and r.get('prepared_version')!=r.get('version'):
            return jsonify(error='当前章节设置尚未封装，请先生成并审核新版成片。'),409
        mapping={'video':r.get('video_path') or r.get('path'),'thumbnail':r.get('thumbnail'),
                 'landscape':r.get('cover_landscape_path'),'portrait':r.get('cover_portrait_path'),
                 'original-cover':r.get('original_thumbnail'),'subtitles':r.get('translated_subtitles')}
        path=mapping.get(file_name)
        if not path:raise ValueError('文件尚未生成')
        resolved=Path(path).resolve()
        if not (resolved.is_relative_to(data.resolve()) or resolved.is_relative_to(archive.root.resolve())):raise ValueError('文件不在独立项目媒体目录')
        return send_file(resolved,conditional=True)

    @app.before_request
    def protect_cancelled_task():
        if request.method in ('GET','HEAD','OPTIONS'):
            return
        parts=request.path.strip('/').split('/')
        task=None
        if len(parts)>=3 and parts[1]=='tasks':
            task=store.get('tasks',parts[2])
        elif len(parts)>=3 and parts[1]=='drafts':
            draft=store.get('drafts',parts[2])
            if draft:task=store.get('tasks',draft.get('task_id'))
        if task and parts[1]=='tasks' and parts[-1] not in ('cancel','redo','targets','complete') and (task.get('completion') or any(p.get('task_id')==task['id'] and p.get('status')=='succeeded' for p in store.list('publications'))):
            return jsonify(error='已投稿任务保留在历史记录；如需重新制作，请从素材库创建新任务。'),409
        if task and task.get('status')=='cancelled' and parts[-1]!='cancel':
            return jsonify(error='任务已取消，请从素材库重新创建制作任务。'),409

    @app.post('/api/tasks/<key>/cancel')
    def cancel_task(key):
        with lock:
            task=record('tasks',key)
            if task.get('status')=='cancelled':return jsonify(task)
            production.editable(task)
            if any(j.get('task_id')==key and (j.get('status') in ('queued','running') or j.get('subtitle_busy')) for j in store.list('downloads')):
                raise ValueError('下载或字幕仍在处理中，请完成后取消。')
            if any(p.get('task_id')==key and p.get('status') in ('queued','running','succeeded','needs_check') for p in store.list('publications')):
                raise ValueError('任务已有投稿或待核实记录，请在历史与投稿记录中处理。')
            store.backup('before-cancel-task')
            used=bool(task.get('video_path') or task.get('confirmed',-1)>=0 or task.get('production_job',{}).get('started_at'))
            task.update(status='cancelled',cancelled_at=time.time(),used_before_cancel=used,reviewed_version=None,progress='已取消制作，素材已保留')
            task=store.put('tasks',task)
            store.event('取消制作并退回素材库',task['title'])
            return jsonify(task)

    @app.post('/api/tasks')
    def create_task():
        values=body();asset=record('assets',values.get('asset_id'))
        task=store.put('tasks',{'title':str(values.get('title') or asset['title'])[:100],'asset_id':asset['id'],
                       'source_account':asset.get('source_account',''),'targets':context()['targets'],'status':'draft','version':1,
                       'start':0,'duration':asset['duration'],'subtitle':'','reviewed_version':None})
        task=store.put('tasks',production.hydrate(task,asset))
        store.event('创建制作任务',task['title'])
        save_archive(asset)
        return jsonify(task),201

    @app.put('/api/tasks/<key>')
    def edit_task(key):
        with lock:
            old=record('tasks',key);values=body()
            if 'mode' in values and values['mode']!=old.get('mode'):
                if any(p.get('task_id')==key and p.get('status') in ('queued','running','succeeded','needs_check') for p in store.list('publications')):
                    raise ValueError('已有投稿记录，请重新制作独立版本后更换处理方式。')
                production.editable(old)
                store.backup('before-production-mode-change')
            task=production.change(old,values)
            store.put('tasks',task)
            save_archive(record('assets',task['asset_id']))
            return jsonify(task)

    @app.post('/api/tasks/<key>/voice')
    def voice(key):
        task=record('tasks',key)
        production.editable(task)
        file=request.files.get('file')
        if not file:raise ValueError('请选择配音文件')
        asset=record('assets',task['asset_id'])
        folder=archive.project(asset)/'03_制作'/key;folder.mkdir(parents=True,exist_ok=True)
        path=folder/('voice-'+uuid.uuid4().hex+'.wav');file.save(path)
        try:
            info=json.loads(media.run([runtime/'bin/ffprobe.exe','-v','error','-show_streams','-of','json',path],60))
            if not any(stream.get('codec_type')=='audio' for stream in info.get('streams',[])):raise ValueError('没有音轨')
        except Exception:
            path.unlink(missing_ok=True);raise ValueError('配音文件无法读取，请上传有效音频。') from None
        task.update(voice_path=str(path),version=task['version']+1,reviewed_version=None,status='draft');store.put('tasks',task)
        return jsonify(task)

    @app.post('/api/tasks/<key>/render')
    def render_task(key):
        task=record('tasks',key)
        production.editable(task)
        if task.get('mode')=='subtitles':raise ValueError('请在字幕工作台审核字幕，再执行独立封装。')
        if task.get('mode')=='dub':raise ValueError('中文配音须先审核讲稿并选择音色，再点击确认生成。')
        if task.get('confirmed',-1)<3:raise ValueError('请先依次确认标题、简介、标签与封面。')
        with lock:
            if key in job_locks:raise ValueError('任务正在制作中')
            job_locks[key]=True
        task.update(status='rendering',progress='正在准备制作',error='');store.put('tasks',task)
        def work():
            try:
                def progress(message):
                    latest=record('tasks',key);latest['progress']=message;store.put('tasks',latest)
                asset=record('assets',task['asset_id'])
                output=archive.project(asset)/'03_制作'/key/f"v{task['version']}"/'video.mp4'
                result=media.render(root,runtime,record('assets',task['asset_id']),task,output,progress)
                store.event('制作完成',task['title'])
                task.update(**result,status='review',progress='制作完成，等待审核')
                archive.manifest(asset,[task,*[t for t in store.list('tasks') if t['asset_id']==asset['id'] and t['id']!=key]])
                store.put('assets',asset)
                store.put('tasks',task)
            except Exception as exc:
                task.update(status='failed',error=str(exc)[:150],progress='');store.put('tasks',task)
            finally:
                with lock:job_locks.pop(key,None)
        threading.Thread(target=work,daemon=True).start()
        return jsonify(task),202

    @app.post('/api/tasks/<key>/review')
    def review(key):
        task=record('tasks',key)
        if task.get('prepared_version')==task['version']:
            for step in range(4):production.confirm(task,step,record('assets',task['asset_id']))
        elif task.get('confirmed',-1)<3:raise ValueError('请先完成制作流程的四项确认。')
        if task.get('status') not in ('review','approved') or not Path(task.get('video_path','')).is_file():
            raise ValueError('请先生成当前版本成片')
        if body().get('version')!=task['version']:raise ValueError('成片版本已变化，请重新审片')
        if not body().get('confirmed'):raise ValueError('请确认画面、声音、字幕和来源')
        for field in ('cover_landscape_path','cover_portrait_path'):
            if not Path(task.get(field,'')).is_file() or media.checksum(task[field])!=task.get(field+'_hash'):
                raise ValueError('封面已变化或缺失，请重新制作。')
        if media.checksum(task['video_path'])!=task['content_hash']:raise ValueError('成片文件已变化，请重新制作')
        task.update(status='approved',progress='审核通过，可生成平台草稿',reviewed_version=task['version'],reviewed_at=time.time());store.put('tasks',task)
        store.event('审核通过',task['title']+' · v'+str(task['version']))
        make_drafts(key)
        return jsonify(task)

    @app.post('/api/tasks/<key>/complete')
    def complete_task(key):
        with lock:
            task=record('tasks',key)
            if task.get('status')!='approved' or task.get('reviewed_version')!=task.get('version'):
                raise ValueError('请先完成当前版本的制作与审核。')
            production.editable(task)
            if any(p.get('task_id')==key and p.get('status') in ('queued','running','needs_check') for p in store.list('publications')):
                raise ValueError('请先等待发布结束，或核对未确认的发布结果。')
            if any(j.get('task_id')==key and (j.get('status') in ('queued','running') or j.get('subtitle_busy')) for j in store.list('downloads')):
                raise ValueError('任务仍在处理中，请完成后再更新状态。')
            if task.get('completion'):return jsonify(task)
            store.backup('before-complete-task')
            task['completion']={'kind':'manual','at':time.time()}
            store.put('tasks',task)
            store.event('标记任务完成',task['title'])
            return jsonify(task)

    @app.post('/api/tasks/<key>/targets')
    def add_task_targets(key):
        with lock:
            task=record('tasks',key)
            if task.get('status')=='cancelled':raise ValueError('已取消任务不能添加发布账号')
            targets=body().get('targets',[])
            if not isinstance(targets,list) or not targets or not all(isinstance(v,str) for v in targets):
                raise ValueError('请选择至少一个发布账号')
            for account_id in targets:
                account=record('accounts',account_id)
                if account['platform'] not in ('douyin','kuaishou','xiaohongshu','wechat_channels','bilibili'):
                    raise ValueError('此账号不支持发布')
            production.editable(task)
            if any(p.get('task_id')==key and p.get('status') in ('queued','running') for p in store.list('publications')):
                raise ValueError('请等待当前发布结束后再添加账号。')
            store.backup('before-task-targets')
            task['targets']=list(dict.fromkeys([*task.get('targets',[]),*targets]))
            accepted={p.get('account_id') for p in store.list('publications') if p.get('task_id')==key and p.get('status')=='succeeded'}
            if task.get('completion') and any(v not in accepted for v in targets):
                task.setdefault('completion_history',[]).append(task.pop('completion'))
            store.put('tasks',task)
            if task.get('reviewed_version')==task['version']:make_drafts(key)
            return jsonify(task)

    @app.post('/api/tasks/<key>/drafts')
    def make_drafts(key):
        with lock:
            task=record('tasks',key)
            if task.get('reviewed_version')!=task['version']:raise ValueError('请先审核当前版本')
            for account_id in task['targets']:
                account=store.get('accounts',account_id)
                if not account:continue  # Removed accounts must not invalidate a completed content review.
                old=next((d for d in store.list('drafts') if d['task_id']==key and d['account_id']==account_id),{})
                if old and any(p.get('draft_id')==old['id'] and p.get('status') in ('queued','running','succeeded','needs_check') for p in store.list('publications')):continue
                if old and (old.get('task_version')==task['version'] or old.get('manually_edited') or old.get('status') in ('published','submitted','succeeded')):
                    continue
                draft={**old,'task_id':key,'account_id':account_id,'platform':account['platform'],'title':task['title'],
                       'body':task.get('description',''),'tags':list(task.get('tags',[])),'task_version':task['version'],'reviewed':False,'status':'draft','source_url':record('assets',task['asset_id']).get('source_url','')}
                if account['platform']=='kuaishou':draft['tags']=draft['tags'][:3]
                from .bilibili_partition import apply
                apply(draft,task)
                adapt_draft(draft, reset_short=True)
                store.put('drafts',draft)
            store.event('生成平台草稿',task['title'])
            return jsonify(ok=True)

    @app.put('/api/drafts/<key>')
    def edit_draft(key):
        draft=record('drafts',key)
        draft_covers.editable(store,draft)
        if 'planned_at' in body():scheduling.timestamp(body()['planned_at'])
        for field in ('title','short_title','body','tags','source_url','planned_at','bilibili_tid'):
            if field in body():draft[field]=body()[field]
        adapt_draft(draft, reset_short='title' in body() and 'short_title' not in body())
        issues=title_issues(draft)
        if issues:raise ValueError('；'.join(issues))
        if draft['platform']=='kuaishou':draft['tags']=draft['tags'][:3]
        if draft['platform']=='bilibili' and draft.get('bilibili_tid'):
            if int(draft['bilibili_tid']) not in {z['tid'] for z in bili_zones()}:raise ValueError('请选择有效B站分区')
        store.backup('before-draft-edit')
        draft.update(reviewed=False,status='draft',manually_edited=True);store.put('drafts',draft)
        return jsonify(draft)

    @app.post('/api/drafts/<key>/review')
    def review_draft(key):
        draft=record('drafts',key);task=record('tasks',draft['task_id'])
        if task.get('reviewed_version')!=task['version'] or task['version']!=draft['task_version']:
            raise ValueError('成片版本已变化，请重新审核并生成草稿')
        if draft['platform']=='kuaishou' and len(draft.get('tags',[]))>3:raise ValueError('快手最多 3 个话题，请先挑选并保存。')
        issues=title_issues(draft)
        if issues:raise ValueError('；'.join(issues))
        if not body().get('confirmed'):raise ValueError('请确认文案、署名和平台声明')
        draft.update(reviewed=True,status='ready',ai_declaration=bool(body().get('ai_declaration')));store.put('drafts',draft)
        return jsonify(draft)

    @app.post('/api/drafts/<key>/publish')
    def publish(key):
        with lock:
            draft=record('drafts',key);task=record('tasks',draft['task_id']);account=record('accounts',draft['account_id'])
            publication_state.sync(store, accounts, draft_id=key)
            if any(p.get('draft_id')==key and p.get('status') in publication_state.BLOCKING for p in store.list('publications')):
                raise ValueError('此稿件已有执行或待核实记录，请先核对回执')
            if not draft.get('reviewed') or task.get('reviewed_version')!=task['version'] or draft['task_version']!=task['version']:
                raise ValueError('当前版本尚未审核')
            issues=title_issues(draft)
            if issues:raise ValueError('；'.join(issues))
            if not body().get('confirmed'):raise ValueError('请明确确认本次公开投稿')
            schedule_issue=scheduling.issue(draft)
            if schedule_issue:raise ValueError(schedule_issue)
            if account['platform'] not in ('douyin','kuaishou','xiaohongshu','wechat_channels','bilibili'):
                raise ValueError('此平台投稿适配尚未接入')
            from .bilibili_partition import apply
            if apply(draft,task):
                store.backup('before-auto-bilibili-partition')
                store.put('drafts',draft)
            if account['platform']=='bilibili' and int(draft.get('bilibili_tid') or 0) not in {z['tid'] for z in bili_zones()}:
                raise ValueError('请先选择B站投稿分区')
            display=accounts.public(account)
            if (not account.get('paused') and account.get('state') in ('valid','unchecked','unavailable')
                    and display.get('session_saved') and
                    (display.get('stale') or time.time()-(account.get('checked_at') or 0)>300)):
                accounts.check(account['id']);return jsonify(error='已安排发布前在线复查，完成后再次确认投稿'),409
            blocked=require_gate([draft['account_id']])
            if blocked:return blocked
            for old in store.list('publications'):
                if old['draft_id']==key and old['status'] in ('queued','running','succeeded','needs_check'):
                    raise ValueError('此稿件已有执行或待核实记录，请先核对回执')
            if media.checksum(task['video_path'])!=task['content_hash']:raise ValueError('成片文件发生变化，请重新审核')
            draft_covers.validate(draft,task)
            pub=store.put('publications',{'draft_id':key,'account_id':account['id'],'task_id':task['id'],'status':'queued',
                                         'phase':'preparing','platform':account['platform'],'message':'等待执行','title':draft['title']})
            payload={'title':draft['title'],'description':draft['body'],'tags':draft['tags'],
                     'video_path':task['video_path'],'cover_landscape_path':draft_covers.selected(draft,task,'landscape'),
                     'cover_portrait_path':draft_covers.selected(draft,task,'portrait'),'source_url':draft['source_url']}
            if account['platform']=='wechat_channels':
                payload['short_title']=draft['short_title']
            if account['platform']=='bilibili':payload['bilibili_tid']=int(draft['bilibili_tid'])
            job={**pub,'action':'upload','payload':payload}
            jobs=accounts.folder(account)/'jobs';jobs.mkdir(exist_ok=True)
            path=jobs/(pub['id']+'.json');youtube.write_json(path,job)
            def execute():
                with accounts.mutex(account['id']),accounts.slots:
                    process=None
                    try:
                        if record('publications',pub['id']).get('status') not in publication_state.ACTIVE:return
                        job.update(status='running',message='正在准备平台投稿')
                        youtube.write_json(path,job)
                        process=subprocess.Popen([sys.executable,str(root/'vendor/publisher/runner.py'),pub['id']],cwd=root/'vendor/publisher',
                                env=accounts.env(account),stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
                                creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
                        deadline=time.monotonic()+4200
                        while process.poll() is None and time.monotonic()<deadline:
                            with lock:publication_state.sync(store,accounts,draft_id=key)
                            time.sleep(2)
                        if process.poll() is None:
                            process.terminate()
                            try:process.wait(timeout=5)
                            except subprocess.TimeoutExpired:process.kill();process.wait(timeout=5)
                        with lock:publication_state.sync(store,accounts,interrupted=True,draft_id=key)
                    except Exception:
                        if process is not None and process.poll() is None:
                            process.terminate()
                            try:process.wait(timeout=5)
                            except subprocess.TimeoutExpired:process.kill();process.wait(timeout=5)
                        with lock:
                            latest=record('publications',pub['id'])
                            if latest.get('status')!='succeeded' and not latest.get('resolved_by'):
                                latest.update(status='needs_check',message='结果未确认，请核对平台记录')
                                store.put('publications',latest)
            threading.Thread(target=execute,daemon=True).start()
            return jsonify(pub),202
    

    @app.get('/api/publications/<key>')
    def publication_detail(key):
        pub=record('publications',key)
        account=record('accounts',pub['account_id'])
        path=accounts.folder(account)/'jobs'/(pub['id']+'.json')
        current=publication_state.reconcile(pub,path)
        result={k:v for k,v in current.items() if k in ('id','platform','status','message','phase','qrcode','verification','receipt','failure_step','error_type')}
        result.update(draft_id=pub.get('draft_id'),account_id=pub.get('account_id'))
        evidence=accounts.folder(account)/'receipts'/(key+'-error.png')
        if evidence.is_file():result['evidence_url']='/api/publications/'+key+'/evidence'
        return jsonify(result)

    @app.get('/api/publications/<key>/evidence')
    def publication_evidence(key):
        pub=record('publications',key);account=record('accounts',pub['account_id'])
        path=accounts.folder(account)/'receipts'/(key+'-error.png')
        if not path.is_file():raise ValueError('没有保存失败现场')
        return send_file(path,mimetype='image/png')

    def bili_zones():
        zones=json.loads((root/'vendor/modules/bili_sdk/data/video_zone.json').read_text(encoding='utf-8'))
        return [{'tid':int(s['tid']),'name':z['name']+' / '+s['name']} for z in zones for s in (z.get('sub') or []) if s.get('tid')]

    @app.get('/api/bilibili/zones')
    def bilibili_zones():return jsonify(items=bili_zones())

    @app.post('/api/publications/<key>/resolve')
    def resolve_publication(key):
        with lock:
            pub=record('publications',key)
            if pub['status']!='needs_check':raise ValueError('仅结果待核实的任务可以确认结果')
            if body().get('confirmed') is not True or body().get('outcome') not in ('not_published','published'):
                raise ValueError('请先核对平台记录并明确确认结果')
            store.backup('before-publication-resolution')
            done=body()['outcome']=='published'
            pub.update(status='succeeded' if done else 'failed',phase='submitted' if done else 'confirmed_not_published',
                       resolved_by='user',resolved_at=time.time(),
                       message='用户已核对平台记录：已发布。' if done else '用户已核对平台记录：未发布，可重新确认投稿。')
            store.put('publications',pub)
            return jsonify(pub)

    @app.post('/api/publications/<key>/verification')
    def publication_verification(key):
        pub=record('publications',key);account=record('accounts',pub['account_id'])
        jobs=accounts.folder(account)/'jobs';path=jobs/(pub['id']+'.json')
        if not path.is_file():raise ValueError('原执行会话不存在')
        current=json.loads(path.read_text(encoding='utf-8'));values=body()
        if current.get('status')!='running' or not current.get('verification') or values.get('nonce')!=current['verification']:
            raise ValueError('验证入口已变化，请重新打开原任务')
        action=values.get('action')
        if action not in ('send','verify'):raise ValueError('验证动作无效')
        command={'action':action,'nonce':current['verification']}
        if action=='verify':
            code=str(values.get('code',''))
            if not re.fullmatch(r'\d{4,8}',code):raise ValueError('请输入 4 至 8 位验证码')
            command['code']=code
        target=jobs/(pub['id']+'.verification')
        if target.exists():raise ValueError('上一次验证正在处理中')
        temporary=target.with_suffix('.verification.tmp');temporary.write_text(json.dumps(command),encoding='utf-8');temporary.replace(target)
        return jsonify(ok=True),202

    @app.post('/api/collections')
    def collect():
        blocked=require_gate([context().get('source')])
        if blocked:return blocked
        a=record('accounts',context()['source'])
        if a['platform'] not in ('douyin','xiaohongshu'):raise ValueError('关键词采集目前支持抖音、小红书')
        hub_id=hub.bind(a,accounts.state_path(a))
        job=hub.request('POST','collections',{'platform':'xhs' if a['platform']=='xiaohongshu' else a['platform'],
               'account_id':hub_id,'keywords':body().get('keywords',[]),'max_contents_per_keyword':min(int(body().get('limit',10)),100),
               'download_media':True,'max_comments_per_content':0,'max_pages_per_keyword':3})
        saved=store.put('collections',{'hub_id':job['id'],'account_id':a['id'],'keywords':body()['keywords'],'status':job.get('status','pending')})
        store.event('创建采集任务','、'.join(body()['keywords']))
        return jsonify(saved),202

    @app.get('/api/collections/<key>')
    def collection_result(key):
        job=record('collections',key)
        status=hub.request('GET',f"collections/{job['hub_id']}")
        job['status']=status.get('status','pending');store.put('collections',job)
        contents=hub.request('GET',f"collections/{job['hub_id']}/contents")
        a=record('accounts',job['account_id'])
        if int(contents.get('total',0))>0:
            a.update(read_state='valid',read_message='已取得平台采集结果',read_checked_at=time.time());store.put('accounts',a)
        elif status.get('status')=='failed':
            a.update(read_state='unavailable',read_message='采集读取尚未确认；登录检查与读取能力分别判断');store.put('accounts',a)
        return jsonify(job=status,contents=contents)

    @app.post('/api/collections/<key>/import')
    def import_collection(key):
        job=record('collections',key)
        blocked=require_gate([job['account_id']])
        if blocked:return blocked
        contents=hub.request('GET',f"collections/{job['hub_id']}/contents?page_size=100")
        items=contents if isinstance(contents,list) else contents.get('items',contents.get('contents',[]))
        if isinstance(contents,dict):
            for page in range(2,min(int(contents.get('pages',1)),20)+1):
                result=hub.request('GET',f"collections/{job['hub_id']}/contents?page_size=100&page={page}")
                items.extend(result.get('items',[]))
        imported=0
        for item in items:
            local=item.get('local_path') or item.get('local_dir') or item.get('download_path') or ''
            folder=Path(local)
            if not folder.is_absolute():folder=root/'vendor/creatorhub'/folder
            for video in ([folder] if folder.is_file() and folder.suffix.lower()=='.mp4' else folder.glob('*.mp4')) if local else []:
                if any(a.get('hub_content_id')==item['id'] for a in store.list('assets')):continue
                key=uuid.uuid4().hex;dest=data/'media'/key;dest.mkdir(parents=True,exist_ok=True)
                path=dest/'source.mp4';shutil.copy2(video,path)
                info=media.probe(runtime,path);thumb=dest/'thumbnail.jpg';media.screenshot(runtime,path,thumb)
                asset=store.put('assets',{'id':key,'title':item.get('title') or item.get('desc') or item.get('description') or video.stem,'source':'CreatorHub采集',
                     'source_account':job['account_id'],'source_url':item.get('url') or item.get('share_url') or item.get('source_url',''),
                     'author':item.get('author_name',''),'path':str(path),'thumbnail':str(thumb),'status':'available','hub_content_id':item['id'],**info})
                save_archive(asset)
                imported+=1
        return jsonify(imported=imported)

    @app.post('/api/collections/<key>/cancel')
    def cancel_collection(key):
        job=record('collections',key)
        result=hub.request('POST',f"collections/{job['hub_id']}/cancel",{})
        job['status']='cancelled';store.put('collections',job)
        return jsonify(result)

    def run_youtube(job):
        # Legacy retries also shed the unrelated domestic source association.
        job.update(source_account='',source_platform='youtube')
        def work():
            job.update(status='running',message='准备后台下载',error_code='',retry_at=0,warnings=[]);store.put('downloads',job)
            try:
                asset={'id':job['asset_id'],'title':'YouTube_'+job['url'].split('v=')[-1]}
                project=archive.project(asset)
                cookies=youtube_settings.prepare()
                def progress(message):
                    job['message']=message;store.put('downloads',job)
                # Reuse subtitles obtained before an earlier failure.
                for subtitle in (project/'01_原始视频').glob('*.vtt'):
                    target=project/'02_字幕'/subtitle.name
                    if not target.exists():shutil.copy2(subtitle,target)
                video,meta=youtube.download(job['url'],project/'01_原始视频',runtime,cookies,
                                           progress=progress,subtitle_folder=project/'02_字幕')
                job.update({k:meta.get(k) for k in ('subtitle_status','subtitle_error','subtitle_retry_at','subtitle_files','warnings','video_reused')})
                job['warnings']=meta.get('warnings',[])
                info=media.probe(runtime,video)
                thumb=project/'01_原始视频'/'thumbnail.jpg';media.screenshot(runtime,video,thumb)
                asset.update(title=meta['title'] or asset['title'],path=str(video),thumbnail=str(thumb),
                             source='YouTube',source_url=job['url'],author=meta['uploader'],
                             description=meta.get('description',''),tags=meta.get('tags',[]),original_thumbnail=meta.get('original_thumbnail',''),
                             source_account=job['source_account'],status='available',**info)
                store.put('assets',asset)
                if job.get('download_only'):
                    save_archive(asset)
                    job.update(status='completed',message='原片、原始封面与来源资料已入库；可从素材库选择开始制作。')
                    return
                task=store.get('tasks',job.get('task_id',''))
                if not task:
                    task=store.put('tasks',{'title':asset['title'],'cover_title':'','mode':job.get('mode','original'),
                        'asset_id':asset['id'],'source_account':job['source_account'],'targets':job['targets'],
                        'status':'draft','version':1,'start':0,'duration':info['duration'],'subtitle':'','reviewed_version':None})
                job['task_id']=task['id'];store.put('downloads',job)
                task=store.put('tasks',production.hydrate(task,asset))
                save_archive(asset)
                app.extensions['production_auto'](task['id'])
                job.update(status='completed',message='视频已下载，AI 正在自动准备文案、封面与成片；完成后统一人工检查。')
            except Exception as exc:
                job.update(status='failed',message=str(exc)[:200] if isinstance(exc,ValueError) else '处理未完成，可重试；已下载文件保留。',
                           error_code=getattr(exc,'code','processing'),error_stage=getattr(exc,'stage','processing'),attempts=getattr(exc,'attempts',1))
                if job['error_code']=='rate_limited':job['retry_at']=time.time()+1800
                task=store.get('tasks',job.get('task_id',''))
                if task:
                    task.update(status='failed',error=job['message']);store.put('tasks',task)
            finally:
                try:
                    from importlib.metadata import version
                    report={k:job.get(k) for k in ('id','url','status','message','error_code','error_stage','attempts','subtitle_status','subtitle_error','subtitle_retry_at','warnings','video_reused')}
                    report.update(at=time.time(),downloader_version=version('yt-dlp'))
                    report_dir=data/'download-reports';report_dir.mkdir(exist_ok=True)
                    youtube.write_json(report_dir/(job['id']+'.json'),report)
                except OSError:app.logger.warning('Download report could not be saved')
                try:store.put('downloads',job)
                finally:
                    with lock:job_locks.pop(job['id'],None)
        with lock:
            if job['id'] in job_locks:raise ValueError('下载仍在后台运行')
            job_locks[job['id']]=True
        threading.Thread(target=work,daemon=True).start()

    def enqueue_youtube(url,mode='original',download_only=False):
        url=youtube.canonical_url(url)
        if mode not in production.MODES:raise ValueError('视频处理方式无效。')
        with lock:
            old=next((j for j in store.list('downloads') if j['url']==url and j['status'] in ('queued','running','completed')),None)
            if old:return old
            store.backup('before-youtube-download')
            job=store.put('downloads',{'url':url,'mode':mode,'status':'queued','message':'等待后台处理',
                        'download_only':download_only,'asset_id':uuid.uuid4().hex,'source_account':'','source_platform':'youtube','targets':[] if download_only else context()['targets']})
        run_youtube(job)
        return job

    @app.post('/api/youtube')
    def youtube_start():
        return jsonify(enqueue_youtube(body().get('url',''),body().get('mode','original'))),202

    @app.post('/api/youtube/<key>/retry')
    def youtube_retry(key):
        job=record('downloads',key)
        if job['status']!='failed':raise ValueError('仅失败任务可重试')
        if job.get('retry_at',0)>time.time():raise ValueError('平台仍在限流冷却期，请稍后重试。')
        job.update(status='queued',message='等待重试');store.put('downloads',job)
        run_youtube(job)
        return jsonify(job),202

    @app.post('/api/youtube/<key>/subtitles')
    def youtube_subtitles(key):
        job=record('downloads',key)
        if job['status']!='completed':raise ValueError('请先完成视频下载制作')
        if job.get('subtitle_retry_at',0)>time.time():raise ValueError('字幕接口仍在冷却期，到期后可补下；无需重新下载视频。')
        asset=record('assets',job['asset_id'])
        with lock:
            if key in job_locks:raise ValueError('此任务仍在后台处理')
            job_locks[key]=True
        job.update(subtitle_busy=True);store.put('downloads',job)
        def work():
            try:
                result=youtube.subtitle_download(job['url'],archive.project(asset)/'02_字幕',runtime,
                    youtube_settings.prepare(),data/'youtube-subtitle-cooldown.json')
                job.update(result)
                job['message']='视频与横竖封面已归档，等待人工审核'+('；字幕需补充' if result['warnings'] else '')
            except Exception:
                job.update(warnings=['字幕补下未完成，已有视频和封面保留。'])
            finally:
                job['subtitle_busy']=False;store.put('downloads',job)
                with lock:job_locks.pop(key,None)
        threading.Thread(target=work,daemon=True).start()
        return jsonify(job),202

    @app.post('/api/download')
    def download():
        blocked=require_gate([context().get('source')])
        if blocked:return blocked
        a=record('accounts',context()['source']);hub_id=hub.bind(a,accounts.state_path(a))
        result=hub.request('POST','share-download',{'share_text':body().get('url',''),'account_id':hub_id,'all_links':False},timeout=180)
        imported=0
        for item in result.get('results',[]):
            if not item.get('ok'):continue
            for file in item.get('files',[]):
                path=Path(file.get('path',''))
                if path.suffix.lower()!='.mp4' or not path.is_file():continue
                key=uuid.uuid4().hex;dest=data/'media'/key;dest.mkdir(parents=True,exist_ok=True)
                saved=dest/'source.mp4';shutil.copy2(path,saved)
                info=media.probe(runtime,saved);thumb=dest/'thumbnail.jpg';media.screenshot(runtime,saved,thumb)
                meta=item.get('metadata') or {}
                asset=store.put('assets',{'id':key,'title':item.get('title') or meta.get('title') or path.stem,'source':'链接下载',
                          'source_account':a['id'],'source_url':item.get('url') or body().get('url',''),
                          'author':meta.get('uploader',''),'path':str(saved),'thumbnail':str(thumb),'status':'available',**info})
                save_archive(asset)
                imported+=1
        return jsonify(ok=result.get('ok'),imported=imported,errors=[str(i.get('error',''))[:200] for i in result.get('results',[]) if not i.get('ok')])

    @app.post('/api/monitors')
    def monitor():
        blocked=require_gate([context().get('source')])
        if blocked:return blocked
        a=record('accounts',context()['source']);hub_id=hub.bind(a,accounts.state_path(a))
        if a['platform'] not in ('douyin','xiaohongshu','kuaishou'):
            raise ValueError('此平台的外部作品监控尚未接入')
        values={**body(),'url_or_secuid':body().get('url',''),'alias':body().get('name',''),'account_id':hub_id,'platform':{'xiaohongshu':'xhs','wechat_channels':'channels'}.get(a['platform'],a['platform'])}
        result=hub.request('POST','monitors',values)
        saved=store.put('monitors',{'hub_id':result['id'],'account_id':a['id'],'label':values.get('name') or values.get('url',''),'status':'active'})
        return jsonify(saved)

    @app.post('/api/monitors/<key>/<action>')
    def monitor_action(key,action):
        item=record('monitors',key)
        if action not in ('toggle','run-now'):raise ValueError('操作无效')
        if action=='run-now':
            blocked=require_gate([item['account_id']])
            if blocked:return blocked
        result=hub.request('POST',f"monitors/{item['hub_id']}/{action}",{})
        if action=='toggle':
            item['status']='active' if result.get('enabled') else 'paused';store.put('monitors',item)
        return jsonify(result)

    @app.get('/api/monitors/<key>/contents')
    def monitor_contents(key):
        item=record('monitors',key)
        return jsonify(hub.request('GET',f"monitors/{item['hub_id']}/contents"))

    @app.get('/api/operations/<key>/<view>')
    def operations(key,view):
        a=record('accounts',key)
        blocked=require_gate([key])
        if blocked:return blocked
        hub_id=hub.bind(a,accounts.state_path(a))
        paths={'works':f'account-works?account_id={hub_id}','follows':f'follows?account_id={hub_id}',
               'messages':f'dm/conversations?account_id={hub_id}'}
        if view=='comments':
            works=hub.request('GET',paths['works'])
            comments=[]
            for work in works[:10]:
                result=hub.request('GET',f"account-works/{work['id']}/comments")
                comments.extend(result if isinstance(result,list) else result.get('items',[]))
            return jsonify(comments)
        if view not in paths:raise ValueError('页面无效')
        result=hub.request('GET',paths[view])
        return jsonify(result)

    @app.post('/api/operations/<key>/sync')
    def sync_operations(key):
        a=record('accounts',key)
        blocked=require_gate([key])
        if blocked:return blocked
        hub_id=hub.bind(a,accounts.state_path(a));view=body().get('view','works')
        if view not in ('works','follows','dm','comments'):raise ValueError('同步类型无效')
        pending=next((j for j in store.list('sync_jobs') if j['account_id']==key and j['view']==view and j['status']=='running'),None)
        if pending:return jsonify(message='此账号正在同步',id=pending['id']),202
        job=store.put('sync_jobs',{'account_id':key,'view':view,'status':'running','message':'正在读取平台数据'})
        def work():
            try:
                if view=='comments':
                    works=hub.request('GET',f'account-works?account_id={hub_id}')
                    for work_item in works[:5]:hub.request('POST',f"account-works/{work_item['id']}/comments/sync",{},timeout=150)
                    result={'ok':True}
                else:
                    result=hub.request('POST',f'accounts/{hub_id}/{view}/sync',{},timeout=180)
                job.update(status='completed',message='平台数据读取完成')
                if result.get('fetched',0)>0:
                    current=record('accounts',key);current.update(read_state='valid',read_message='已读取本账号平台数据',read_checked_at=time.time());store.put('accounts',current)
            except Exception:
                job.update(status='failed',message='平台读取未完成，请查看账号状态或稍后重试')
                current=record('accounts',key);current.update(read_state='unavailable',read_message='读取能力未确认，保留登录与原会话');store.put('accounts',current)
            store.put('sync_jobs',job);store.event('运营同步',key+' · '+job['status'])
        threading.Thread(target=work,daemon=True).start()
        return jsonify(message='同步已安排',id=job['id']),202

    @app.post('/api/backup')
    def backup():
        path=store.backup('manual')
        return jsonify(file=path.name)

    # Atomic read/validate/write boundaries across concurrent browser tabs.
    from functools import wraps
    def serialized(handler):
        @wraps(handler)
        def wrapper(*args,**kwargs):
            with lock:return handler(*args,**kwargs)
        return wrapper
    for endpoint in ('batch','login','import_session','edit_account','edit_task','voice','render_task','review','make_drafts','edit_draft','review_draft'):
        app.view_functions[endpoint]=serialized(app.view_functions[endpoint])
    draft_covers.register(app,store,record,lock)
    register_batch_publish(app,store,accounts,record,lock,publish)
    register_production(app,root,data,runtime,store,archive,record,save_archive,lock)
    from .youtube_discovery import register as register_discovery
    register_discovery(app,store,runtime,youtube_settings,enqueue_youtube,automatic_checks)

    if automatic_checks:
        for a in store.list('accounts'):
            current=accounts.public(a)
            if not a.get('paused') and (current.get('stale') or current.get('state') in ('checking','unchecked') or time.time()-a.get('checked_at',0)>300):
                accounts.check(a['id'])
    return app


if __name__=='__main__':
    from waitress import serve
    serve(create_app(),host='127.0.0.1',port=8020,threads=8)
