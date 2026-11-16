"""One human confirmation for multiple account drafts; execution stays per account."""
import hashlib
import json
import time
from flask import jsonify, request
from . import media, draft_covers
from .platform_rules import adapt_draft, title_issues, KUAISHOU_DESCRIPTION_TARGET
from vendor.publisher.caption_rules import kuaishou_caption, kuaishou_count
from . import bilibili_partition
from . import publication_state
from . import scheduling


def register(app, store, accounts, record, lock, publish):
    def inspect(key):
        publication_state.sync(store, accounts, draft_id=key)
        d = record('drafts', key)
        t = record('tasks', d['task_id'])
        a = record('accounts', d['account_id'])
        errors = []
        if t.get('status')=='cancelled':errors.append('制作任务已取消，不能发布')
        if a['platform']=='bilibili' and not d.get('bilibili_tid'):errors.append('请选择B站投稿分区')
        if t.get('reviewed_version') != t['version'] or d['task_version'] != t['version']:
            errors.append('成片版本变化，请审片并同步稿件')
        supported=a['platform'] in ('douyin', 'kuaishou', 'xiaohongshu', 'wechat_channels','bilibili')
        if not supported:
            errors.append('此平台尚未接入自动投稿，无法通过账号检查修复；可从本批次移除')
        short_title=d.get('short_title','') if a['platform']=='wechat_channels' else ''
        errors.extend(title_issues(d))
        if a['platform']=='kuaishou' and len(d.get('tags',[]))>3:
            errors.append('快手最多 3 个话题')
        account_issue = False
        if not supported:pass
        elif a.get('state')=='checking':
            account_issue = True
            errors.append('正在自动复查账号，完成后将更新结果…')
        elif accounts.issues([a['id']]):
            account_issue = True
            errors.append('账号已停用' if a.get('paused') else a.get('message') or '账号尚未就绪，可一键重新检查')
        elif time.time()-(a.get('checked_at') or 0)>300:
            account_issue = True
            errors.append('需要更新账号状态，可一键检查')
        schedule_issue=scheduling.issue(d)
        if schedule_issue:errors.append(schedule_issue)
        if any(p.get('draft_id')==key and p.get('status') in ('queued','running','succeeded','needs_check') for p in store.list('publications')):
            errors.append('已有执行或待核实记录，请查看回执')
        try:
            if media.checksum(t['video_path'])!=t['content_hash']:
                errors.append('成片文件已变化，请重新审核')
            draft_covers.validate(d,t)
        except ValueError as exc:
            errors.append(str(exc))
        except (OSError,KeyError,TypeError):
            errors.append('成片或封面文件缺失，请重新生成')
        fingerprint=hashlib.sha256(json.dumps([d,t.get('version'),t.get('reviewed_version'),t.get('content_hash')],sort_keys=True,ensure_ascii=False).encode()).hexdigest()
        return {'id':key,'account':a.get('alias') or a.get('name'),'platform':a['platform'],
                'title':d['title'],'body':d.get('body',''),'tags':d.get('tags',[]),
                'short_title':short_title,
                'cover_revision':str(d.get('updated_at','')),
                'caption_count':kuaishou_count(kuaishou_caption(d['title'],d.get('body',''),d.get('tags',[]))) if a['platform']=='kuaishou' else None,
                'description_target':KUAISHOU_DESCRIPTION_TARGET if a['platform']=='kuaishou' else None,
                'bilibili_tid':d.get('bilibili_tid'),'bilibili_zone_name':bilibili_partition.label(d.get('bilibili_tid')) if a['platform']=='bilibili' else '',
                'fingerprint':fingerprint,'issues':errors,'account_id':a['id'],
                'supported':supported,'paused':bool(a.get('paused')),'task_id':t['id'],'scheduled':bool(d.get('planned_at',0)),
                'can_sync':t.get('reviewed_version')==t['version'] and d['task_version']!=t['version'],
                'account_issue':account_issue,'checking':supported and a.get('state')=='checking',
                'needs_login':supported and a.get('state') in ('expired','missing'),
                'repair_note':d.get('batch_repair_note',''),
                'editable':bool(title_issues(d)) or (a['platform']=='kuaishou' and len(d.get('tags',[]))>3),
                'repairable':(a['platform']=='kuaishou' and len(d.get('tags',[]))>3) or (account_issue and not a.get('paused') and a.get('state') not in ('expired','missing','checking'))}

    def ids():
        values=(request.get_json() or {}).get('ids',[])
        if not isinstance(values,list) or not values or len(values)>100 or any(not isinstance(x,str) for x in values):
            raise ValueError('请选择 1 至 100 份稿件')
        return list(dict.fromkeys(values))

    @app.post('/api/drafts/batch-preview')
    def batch_preview():
        with lock:
            keys=ids()
            normalize_tags(keys)
            if not (request.get_json() or {}).get('poll'):
                refresh_accounts(keys)
            return jsonify(items=[inspect(key) for key in keys])

    def normalize_tags(keys):
        changes=[]
        busy={p.get('draft_id') for p in store.list('publications') if p.get('status') in ('queued','running','succeeded','needs_check')}
        for key in keys:
            d=record('drafts',key)
            if key not in busy and d.get('status') not in ('published','submitted','succeeded') and adapt_draft(d):
                changes.append(d)
            if key not in busy and bilibili_partition.apply(d,record('tasks',d['task_id'])):
                changes.append(d)
            if key not in busy and d['platform']=='kuaishou' and len(d.get('tags',[]))>3:
                d.update(tags=d['tags'][:3],reviewed=False,status='draft',manually_edited=True,
                         batch_repair_note='已自动保留前 3 个话题，仅调整快手稿件')
                changes.append(d)
        if changes:
            store.backup('before-auto-platform-tags')
            for d in changes:store.put('drafts',d)

    def refresh_accounts(keys, retry=False):
        seen=set()
        for key in keys:
            a=record('accounts',record('drafts',key)['account_id'])
            if a['id'] in seen:continue
            seen.add(a['id'])
            if a['platform'] not in ('douyin','kuaishou','xiaohongshu','wechat_channels','bilibili') or a.get('paused') or a.get('state') in ('checking','expired','missing'):continue
            if (retry or a.get('state') in ('valid','unchecked')) and (accounts.issues([a['id']]) or time.time()-(a.get('checked_at') or 0)>300):
                accounts.check(a['id'])

    @app.post('/api/drafts/batch-repair')
    def batch_repair():
        with lock:
            keys=ids()
            store.backup('before-batch-repair')
            for key in keys:
                d=record('drafts',key)
                if any(p.get('draft_id')==key and p.get('status') in ('queued','running','succeeded','needs_check') for p in store.list('publications')):continue
                if d.get('status') not in ('published','submitted','succeeded') and adapt_draft(d):store.put('drafts',d)
                if d['platform']=='kuaishou' and len(d.get('tags',[]))>3:
                    d.update(tags=d['tags'][:3],reviewed=False,status='draft',manually_edited=True,
                             batch_repair_note='已按原顺序保留前 3 个话题，仅调整此平台稿件')
                    store.put('drafts',d)
            refresh_accounts(keys,retry=True)
            return jsonify(items=[inspect(key) for key in keys])

    @app.post('/api/drafts/batch-publish')
    def batch_publish():
        values=request.get_json() or {}
        if values.get('confirmed') is not True:
            raise ValueError('请统一确认所选稿件与公开投稿')
        results=[]
        with lock:
            keys=ids()
            batch=record('publish_batches',values['batch_id']) if values.get('batch_id') else store.put('publish_batches',{'draft_ids':keys,'results':[]})
            if any(key not in batch['draft_ids'] for key in keys):raise ValueError('重试项目不属于原批次')
            for key in keys:
                try:
                    row=inspect(key)
                    if row['fingerprint']!=values.get('fingerprints',{}).get(key):
                        raise ValueError('稿件已变化，请重新查看批量确认页')
                    if row['issues']:
                        raise ValueError('；'.join(row['issues']))
                    d=record('drafts',key)
                    d.update(reviewed=True,status='ready',ai_declaration=bool(values.get('ai_declaration')))
                    store.put('drafts',d)
                    response=app.make_response(publish(key))
                    result=response.get_json()
                    if response.status_code!=202:
                        raise ValueError(result.get('error','暂未加入队列'))
                    pub=record('publications',result['id']);pub['batch_id']=batch['id'];store.put('publications',pub)
                    results.append({'id':key,'status':'queued','publication_id':result['id']})
                except (ValueError,KeyError) as exc:
                    results.append({'id':key,'status':'blocked','message':str(exc)})
            previous={r['id']:r for r in batch['results']}
            for r in results:
                # A rejected duplicate must never hide the existing successful/running attempt.
                old=previous.get(r['id'],{})
                if r['status']=='blocked' and old.get('publication_id'):continue
                previous[r['id']]=r
            batch['results']=list(previous.values());store.put('publish_batches',batch)
        return jsonify(results=results,batch_id=batch['id'])

    @app.get('/api/drafts/batches/<key>')
    def batch_status(key):
        batch=record('publish_batches',key)
        rows=[]
        for r in batch['results']:
            d=record('drafts',r['id']);a=record('accounts',d['account_id'])
            p=store.get('publications',r.get('publication_id','')) or {}
            rows.append({**r,'account':a.get('alias') or a.get('name'),'platform':a['platform'],
                         'supported':a['platform'] in ('douyin','kuaishou','xiaohongshu','wechat_channels','bilibili'),
                         'title':d['title'],'status':p.get('status',r['status']),
                         'message':p.get('message',r.get('message','')),'receipt':p.get('receipt',{})})
        return jsonify(id=key,items=rows)

    @app.get('/api/drafts/batches')
    def batches():
        return jsonify(items=[{'id':b['id'],'count':len(b['draft_ids']),'updated_at':b.get('updated_at')} for b in store.list('publish_batches')])
