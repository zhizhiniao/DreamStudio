"""YouTube discovery and persistent subscriptions, independent of publisher accounts."""
import json
import base64
import re
import threading
import time
from urllib.parse import urlsplit, urlencode
from flask import jsonify, request
from . import youtube


def channel_url(value):
    value=str(value).strip()
    if value.startswith('@'):value='https://www.youtube.com/'+value
    u=urlsplit(value)
    if u.scheme!='https' or u.hostname not in ('youtube.com','www.youtube.com') or u.username or u.password or u.port:
        raise ValueError('请输入 YouTube 频道链接或 @频道名')
    path=u.path.rstrip('/')
    path=re.sub(r'/(videos|shorts|streams|featured)$','',path)
    if not re.fullmatch(r'/(@[^/?#\s]+|channel/UC[A-Za-z0-9_-]{22}|c/[^/\s]+|user/[^/\s]+)',path):
        raise ValueError('请使用频道主页链接，不能填写单个视频或其他平台链接')
    return 'https://www.youtube.com'+path+'/videos'


def normalized(values):
    kind=values.get('kind','search')
    if kind not in ('search','channel'):raise ValueError('采集类型无效')
    query=str(values.get('query','')).strip()
    if kind=='channel':query=channel_url(query)
    elif not query or len(query)>200:raise ValueError('请输入 1 至 200 字的 YouTube 搜索关键词')
    limit=int(values.get('limit',10));interval=int(values.get('interval_hours',6))
    if not 1<=limit<=50:raise ValueError('每次发现数量应为 1 至 50')
    if interval not in (1,3,6,12,24):raise ValueError('检查间隔应为 1、3、6、12 或 24 小时')
    sort=values.get('sort','date');period=values.get('period','all');min_views=int(values.get('min_views',0))
    if sort not in ('date','relevance','views'):raise ValueError('排序方式无效')
    if period not in ('all','day','week','month','year'):raise ValueError('发布时间范围无效')
    if not 0<=min_views<=10**12:raise ValueError('最低播放量无效')
    return dict(kind=kind,query=query,label=str(values.get('label') or query)[:120],limit=limit,interval_hours=interval,
                enabled=bool(values.get('enabled',kind=='channel')),sort=sort if kind=='search' else 'date',
                period=period if kind=='search' else 'all',min_views=min_views if kind=='search' else 0)


def search_params(source):
    order={'relevance':0,'date':2,'views':3}[source.get('sort','date')]
    filters=bytes([16,1])  # Search videos, not channels or playlists.
    period={'day':2,'week':3,'month':4,'year':5}.get(source.get('period'))
    if period:filters=bytes([8,period])+filters
    payload=(bytes([8,order]) if order else b'')+bytes([18,len(filters)])+filters
    return base64.b64encode(payload).decode()


class DiscoveryRows(list):
    channel_info=None


def fetch(source,runtime,cookies):
    # Fetch a bounded candidate window, deduplicate before applying new-video quota.
    count=min(150,max(30,source['limit']*3))
    target=source['query'] if source['kind']=='channel' else 'https://www.youtube.com/results?'+urlencode({'search_query':source['query'],'sp':search_params(source)})
    command=youtube.base_command(runtime,cookies)
    command.remove('--no-playlist')
    command+=['--flat-playlist','--skip-download','--dump-single-json','--extractor-args','youtubetab:approximate_date','--playlist-end',str(count),target]
    result=youtube.execute(command,180)
    if result.returncode:
        code,message=youtube.classify(result.stderr.decode('utf-8','replace'))
        raise youtube.DownloadError(code,message,'discovery')
    try:payload=json.loads(result.stdout.decode('utf-8'))
    except (ValueError,UnicodeError):raise ValueError('YouTube 返回的数据不完整，请稍后重试') from None
    rows=DiscoveryRows()
    if source['kind']=='channel':
        rows.channel_info={k:v for k,v in dict(name=payload.get('channel') or payload.get('uploader'),channel_id=payload.get('channel_id'),
            url=payload.get('channel_url') or payload.get('uploader_url') or source['query'].removesuffix('/videos'),description=str(payload.get('description') or '')[:1000]).items() if v}
    for e in payload.get('entries',[]):
        if not e or not re.fullmatch(r'[A-Za-z0-9_-]{11}',str(e.get('id',''))):continue
        if e.get('availability') in ('private','premium_only','subscriber_only') or e.get('live_status') in ('is_live','is_upcoming'):continue
        key=e['id'];duration=e.get('duration')
        published=e.get('timestamp') or e.get('release_timestamp')
        cutoff={'day':86400,'week':7*86400,'month':30*86400,'year':365*86400}.get(source.get('period'))
        if cutoff and published and published<time.time()-cutoff:continue
        if source.get('min_views',0) and (e.get('view_count') is None or e['view_count']<source['min_views']):continue
        rows.append(dict(id=key,title=e.get('title') or key,channel=e.get('channel') or e.get('uploader') or payload.get('uploader',''),
                         url='https://www.youtube.com/watch?v='+key,thumbnail='https://i.ytimg.com/vi/'+key+'/hqdefault.jpg',
                         duration=duration,view_count=e.get('view_count'),published_at=published,published_at_estimated=bool(published),
                         description=str(e.get('description') or '')[:1200],
                         source_ids=[source['id']]))
    if source.get('sort')=='views':rows.sort(key=lambda r:r.get('view_count') or 0,reverse=True)
    return rows


class Discovery:
    def __init__(self,store,runtime,settings,enqueue):
        self.store,self.runtime,self.settings,self.enqueue=store,runtime,settings,enqueue
        self.lock=threading.RLock();self.slots=threading.BoundedSemaphore(1)
        for s in store.list('youtube_sources'):
            if s.get('status') in ('queued','running'):
                s.update(status='failed',message='上次检查中断，已保留发现记录，可重新检查',next_run_at=time.time()+3600)
                store.put('youtube_sources',s)

    def state(self):
        downloads={d['url']:d for d in self.store.list('downloads')}
        assets={a.get('source_url'):a for a in self.store.list('assets')}
        items=[]
        sources=[s for s in self.store.list('youtube_sources') if not s.get('deleted_at')]
        active={s['id'] for s in sources}
        for r in self.store.list('youtube_discoveries'):
            if not active.intersection(r.get('source_ids',[])):continue
            d=downloads.get(r['url']);a=assets.get(r['url'])
            items.append({**r,'download_status':'completed' if a else d.get('status','') if d else '',
                          'download_id':d['id'] if d else '', 'asset_id':a['id'] if a else ''})
        return {'sources':sources,'items':items,'features':{'filters':True,'delete_source':True}}

    def create(self,values):
        config=normalized(values)
        with self.lock:
            old=next((s for s in self.store.list('youtube_sources') if not s.get('deleted_at') and s['kind']==config['kind'] and s['query']==config['query']),None)
            if old:
                if any(old.get(k,{'sort':'date','period':'all','min_views':0}.get(k))!=v for k,v in config.items()):return self.edit(old['id'],config)
                return old
            self.store.backup('before-youtube-source')
            return self.store.put('youtube_sources',{**config,'revision':1,'status':'idle','message':'尚未检查','created_at':time.time(),'last_success_at':0,'next_run_at':0})

    def edit(self,key,values):
        with self.lock:
            s=self.store.get('youtube_sources',key)
            if not s or s.get('deleted_at'):raise ValueError('订阅不存在')
            allowed=('enabled','limit','interval_hours','label','sort','period','min_views')
            config=normalized({**s,**{k:v for k,v in values.items() if k in allowed}})
            changed=any(config[k]!=s.get(k,{'sort':'date','period':'all','min_views':0}.get(k)) for k in ('limit','sort','period','min_views'))
            if changed and s.get('status') in ('queued','running'):raise ValueError('请等待当前搜索结束后再修改筛选')
            self.store.backup('before-youtube-source-edit');s.update(config)
            if changed:
                s['revision']=s.get('revision',1)+1;s['visible_ids']=[]
            s['next_run_at']=time.time()+s['interval_hours']*3600
            return self.store.put('youtube_sources',s)

    def remove(self,key):
        with self.lock:
            s=self.store.get('youtube_sources',key)
            if not s:raise ValueError('订阅不存在')
            if s.get('deleted_at'):return s
            self.store.backup('before-youtube-source-delete')
            s.update(deleted_at=time.time(),enabled=False,status='deleted',next_run_at=0)
            return self.store.put('youtube_sources',s)

    def run(self,key):
        with self.lock:
            s=self.store.get('youtube_sources',key)
            if not s or s.get('deleted_at'):raise ValueError('订阅不存在')
            if s.get('status') in ('queued','running'):return s
            if s.get('retry_at',0)>time.time():raise ValueError('YouTube 限流冷却中，请稍后重试')
            s.update(status='queued',message='等待读取 YouTube 视频列表')
            self.store.put('youtube_sources',s)
        threading.Thread(target=self.work,args=(key,),daemon=True).start()
        return s

    def work(self,key):
        with self.slots:
            with self.lock:
                s=self.store.get('youtube_sources',key)
                if not s or s.get('deleted_at') or s.get('status')!='queued':return
                s.update(status='running',message='正在读取 YouTube 视频列表');self.store.put('youtube_sources',s)
            try:
                rows=fetch(s,self.runtime,self.settings.prepare())
                added=0
                with self.lock:
                    latest=self.store.get('youtube_sources',key)
                    if not latest or latest.get('deleted_at') or latest.get('revision',1)!=s.get('revision',1):return
                    self.store.backup('before-youtube-discovery')
                    new_ids=[]
                    for rank,row in enumerate(rows):
                        old=self.store.get('youtube_discoveries',row['id'])
                        linked=old and key in old.get('source_ids',[])
                        if not linked and added>=s['limit']:continue
                        if old:
                            old.update({k:v for k,v in row.items() if k!='source_ids' and v is not None and v!=''})
                            old['source_ids']=list(dict.fromkeys(old.get('source_ids',[])+[key]))
                        else:old={**row,'discovered_at':time.time()}
                        old.setdefault('source_ranks',{})[key]=rank
                        self.store.put('youtube_discoveries',old)
                        if not linked:added+=1;new_ids.append(row['id'])
                    latest=self.store.get('youtube_sources',key)
                    latest.update(status='completed',message=f'本次发现 {added} 条新视频，候选窗口 {len(rows)} 条',last_success_at=time.time(),
                                  next_run_at=time.time()+latest['interval_hours']*3600,new_count=added,new_item_ids=new_ids,
                                  visible_ids=[r['id'] for r in rows if key in (self.store.get('youtube_discoveries',r['id']) or {}).get('source_ids',[])],
                                  error_code='',retry_at=0)
                    if getattr(rows,'channel_info',None):latest['channel_info']=rows.channel_info
                    self.store.put('youtube_sources',latest)
            except Exception as exc:
                with self.lock:
                    latest=self.store.get('youtube_sources',key)
                    if not latest or latest.get('deleted_at') or latest.get('revision',1)!=s.get('revision',1):return
                    latest.update(status='failed',message=str(exc)[:200] if isinstance(exc,ValueError) else '读取失败，请检查 YouTube 连接设置后重试',
                                  error_code=getattr(exc,'code','discovery'),next_run_at=time.time()+3600)
                    if getattr(exc,'code','')=='rate_limited':latest['retry_at']=time.time()+3600
                    self.store.put('youtube_sources',latest)

    def tick(self):
        for s in self.store.list('youtube_sources'):
            if not s.get('deleted_at') and s.get('enabled') and s.get('next_run_at',0)<=time.time() and s.get('status') not in ('running','queued'):
                try:self.run(s['id'])
                except ValueError:pass

    def schedule(self):
        while True:
            try:self.tick()
            except Exception:pass
            time.sleep(60)


def register(app,store,runtime,settings,enqueue,automatic=True):
    service=Discovery(store,runtime,settings,enqueue);app.extensions['youtube_discovery']=service

    @app.get('/api/youtube/discovery')
    def discovery_state():return jsonify(service.state())

    @app.post('/api/youtube/sources')
    def source_create():
        s=service.create(request.get_json(silent=True) or {})
        return jsonify(service.run(s['id'])),202

    @app.post('/api/youtube/sources/<key>/check')
    def source_check(key):return jsonify(service.run(key)),202

    @app.patch('/api/youtube/sources/<key>')
    def source_edit(key):
        return jsonify(service.edit(key,request.get_json(silent=True) or {}))

    @app.delete('/api/youtube/sources/<key>')
    def source_delete(key):return jsonify(service.remove(key))

    @app.post('/api/youtube/discovery/<key>/download')
    def discovery_download(key):
        r=store.get('youtube_discoveries',key)
        if not r:raise ValueError('视频记录不存在')
        # Discovery downloads only: never invoke paid production without a separate choice.
        return jsonify(enqueue(r['url'],download_only=True)),202

    if automatic:threading.Thread(target=service.schedule,daemon=True).start()
    return service
