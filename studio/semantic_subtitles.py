"""Translate sentence units, then align Chinese meaning chunks to source word spans.

Source cue timestamps are anchors. Word times inside a cue are estimates, not ASR
word timestamps. Never invent a second clock or distribute Chinese by character count.
"""
import hashlib
import json
import re
import time
from pathlib import Path
from .youtube import read_json, write_json
from .translation import cue_key, human_text, valid_text, folder_lock, check_pause, MAX_ATTEMPTS
from .production_errors import ProductionFailure

POLICY = 'sentence-zh-v1'


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def sentence_units(cues):
    words=[]
    for i,c in enumerate(cues):
        tokens=re.findall(r'\S+',c['text'])
        start=float(c['start']);end=float(c['end'])
        if i+1<len(cues):end=min(end,float(cues[i+1]['start']))
        if not tokens or end<=start:
            raise ValueError('原文时间轴有重叠或空段，请先检查源字幕。')
        weights=[max(1,len(t)) for t in tokens];total=sum(weights);cursor=0
        for token,weight in zip(tokens,weights):
            words.append(dict(text=token,start=start+(end-start)*cursor/total,
                              end=start+(end-start)*(cursor+weight)/total,cue=i))
            cursor+=weight
    units=[];pending=[]
    def flush(uncertain=False):
        if pending:
            units.append(dict(start=pending[0]['start'],end=pending[-1]['end'],
                text=' '.join(w['text'] for w in pending),words=list(pending),uncertain=uncertain))
            pending.clear()
    for w in words:
        # Long unpunctuated transcripts remain context-rich blocks; mark uncertain boundaries.
        if pending and (w['start']-pending[-1]['end']>2 or len(pending)>=100 or w['end']-pending[0]['start']>40):flush(True)
        pending.append(w)
        token=w['text'].rstrip('\"\'”’)]')
        abbreviation=bool(re.fullmatch(r'(?:Mr|Mrs|Ms|Dr|Prof|Sr|Jr|vs|etc|e\.g|i\.e|[A-Z]|(?:[A-Za-z]\.)+[A-Za-z])\.',token,re.I))
        if re.search(r'[.!?。！？]$',token) and not abbreviation:flush()
    flush(True)
    return units


def plan(folder,cues,model):
    units=sentence_units(cues)
    for i,u in enumerate(units):
        u['before']=[x['text'] for x in units[max(0,i-2):i]]
        u['after']=[x['text'] for x in units[i+1:i+3]]
        u['key']=fingerprint([POLICY,model,u])
    value=dict(policy=POLICY,source_hash=fingerprint(cues),model=model,units=units)
    write_json(Path(folder)/'semantic-plan.json',value)
    return value


def source_row(unit,part=None,index=0):
    words=unit['words'] if part is None else unit['words'][part['start_word']:part['end_word']+1]
    return dict(start=round(words[0]['start'],3),end=round(words[-1]['end'],3),
                text=' '.join(w['text'] for w in words),unit_key=unit['key'],part=index)


def normalize(text):
    # Preserve Latin product names, URLs and decimal punctuation; normalize Chinese neighbours.
    text=' '.join(text.strip().split())
    text=re.sub(r'(?<=[\u4e00-\u9fff])\.(?!\.)','。',text)
    return re.sub(r'(?<=[\u4e00-\u9fff])[,;:!?]|[,;:!?](?=[\u4e00-\u9fff])',
                  lambda m:dict(zip(',;:!?','，；：！？'))[m[0]],text)


def compact_segments(unit,parts):
    """Join short dependent clauses within one sentence; keep exact words and clock anchors."""
    result=[];i=0
    while i<len(parts):
        part=dict(parts[i]);i+=1
        while i<len(parts):
            following=parts[i]
            duration=unit['words'][part['end_word']]['end']-unit['words'][part['start_word']]['start']
            combined=part['text']+following['text']
            span=unit['words'][following['end_word']]['end']-unit['words'][part['start_word']]['start']
            dependent=bool(re.match(r'^(?:但)?(?:一旦|如果|只要|因为|虽然)',part['text']) and not re.search(r'[。！？]$',part['text']))
            dangling=bool(re.search(r'(?:但是|而且|并且|因为|所以|一旦|如果|以及|但|就)[，；：]*$',part['text']))
            contrast=bool(re.match(r'^(?:但|然而|不过|否则)',following['text']))
            if (following['start_word']!=part['end_word']+1 or span>6 or len(combined)>48
                    or len(re.findall(r'[\u4e00-\u9fff]',combined))>32
                    or re.search(r'[。！？]$',part['text']) or contrast
                    or not (duration<1.2 or dependent or dangling)):
                break
            part.update(text=combined,end_word=following['end_word']);i+=1
        result.append(part)
    return result


def validate(unit,item):
    if not isinstance(item,dict):raise ValueError('AI 返回缺少整句译文')
    full=item.get('text');parts=item.get('segments')
    if not valid_text(unit['text'],full,item.get('preserve_as')):raise ValueError('整句译文为空、未翻译或格式错误')
    if not isinstance(parts,list) or not parts or len(parts)>len(unit['words']):raise ValueError('缺少中文语义断句或断句数量异常')
    cursor=0;clean=[]
    for p in parts:
        if not isinstance(p,dict):raise ValueError('中文断句格式错误')
        a=p.get('start_word');b=p.get('end_word');text=p.get('text')
        if type(a) is not int or type(b) is not int or a!=cursor or not a<=b<len(unit['words']):raise ValueError('原文词语对齐有遗漏、重复或逆序')
        if not isinstance(text,str) or not text.strip() or len(text)>80 or re.search(r'<[^>]+>|```|\ufffd',text):raise ValueError('中文短句为空、过长或含非法标记')
        text=normalize(text)
        if re.match(r'^[，。！？；：、]',text):raise ValueError('标点不能单独落到下一条字幕')
        if len(re.findall(r'[\u4e00-\u9fff]',text))>32:raise ValueError('中文短句过长，请按语义继续拆分')
        clean.append(dict(text=text,start_word=a,end_word=b));cursor=b+1
    if cursor!=len(unit['words']):raise ValueError('中文断句未覆盖完整原文')
    if re.sub(r'\s','',normalize(full))!=re.sub(r'\s','',''.join(p['text'] for p in clean)):
        raise ValueError('断句与完整译文不一致，可能漏译或重复')
    clean=compact_segments(unit,clean)
    if any(re.search(r'(?:但是|而且|并且|因为|所以|一旦|如果|以及|但|就)[，。；：]*$',p['text']) for p in clean):
        raise ValueError('断句把连接词留在上一条末尾，自动合并后仍无法解决，请人工检查')
    return dict(text=normalize(full),segments=clean,preserve_as=item.get('preserve_as'),segmentation_revision='compact-v1')


def view(folder,value):
    folder=Path(folder);rows=[];cues=[];model=value['model']
    hints=read_json(folder/'semantic-hints.json').get('rows',[])
    flags=read_json(folder/'review-flags.json')
    for u in value['units']:
        cache=read_json(folder/'semantic-v1'/(u['key']+'.json'))
        parts=cache.get('segments') or [None]
        for j,p in enumerate(parts):
            c=source_row(u,p,j);text=p['text'] if p else '';state='completed' if p else 'problem' if cache.get('failure') else 'pending'
            candidate=cache.get('candidate','') if not text else ''
            reason=cache.get('failure','') if not text else ''
            flag=flags.get(cue_key(c,model),{})
            if p and flag.get('text')==text:
                candidate=text;text='';state='problem';reason=flag.get('reason') or '人工检查发现疑点，请复核'
            manual=read_json(folder/'manual-v1'/(cue_key(c,model)+'.json'))
            if human_text(c['text'],manual.get('text'),manual.get('keep_original')):text=manual['text'];state='manual'
            if text:reason=''
            warning='原文缺少明确句末标点，请结合上下文校对' if u['uncertain'] else ''
            if any(h['start']<c['end'] and h['end']>c['start'] for h in hints):warning='此处含旧版人工修订，请核对整句重译是否保留修订意图'
            if c['end']<=c['start']:raise ValueError('字幕时间范围过短，无法可靠对齐，请检查原文时间轴。')
            rows.append(dict(id=len(rows),start=c['start'],end=c['end'],source=c['text'],text=text,state=state,
                candidate=candidate if not text else '',reason=reason,review_warning=warning,
                keep_original=bool(manual.get('keep_original')),accepted=bool(manual.get('accepted')) and state=='manual',unit_key=u['key'],semantic=True))
            cues.append(c)
    return dict(rows=rows,cues=cues,model=model,policy=POLICY,total=len(rows),
                completed=sum(r['state'] in ('completed','manual') for r in rows),
                signature=fingerprint([POLICY,cues,model]))


PROMPT='''先阅读前后文，再将每个 items 的完整英文句子翻译成自然、准确、简洁的简体中文。不得逐个原字幕碎片硬翻，不重复翻译 context。
先完成忠实翻译，再精简为适合阅读的字幕：去掉不承载信息的口头填充、重复主语和冗余套话，使用自然短语，不能删改数字、否定、条件、因果、操作步骤、专名或说话人的不确定语气。不要机械逐词直译；最终 text 和 segments 使用同一份精简译文。
不要把一句话按逗号机械拆条，也不要为了逐个对齐英文短语而切碎中文。优先每条约 1.5–5 秒；“但一旦完成，就永远不用再做了”应是一条，条件与结果尽量同条。字幕条目和画面换行分开：一条可以显示为两行，无需为了换行新建时间条目。
随后按中文语义拆成适合屏幕阅读的 segments，通常每条 8–24 个汉字，最长尽量不超过 32 字。短句不用强拆，不拆词组、人名、产品名，不把“但/因为/如果/一旦/就”等留在上条末尾。
使用中文标点，不因原字幕换行强加句号。忠实保留信息、数字、否定与语气。软件/产品名、网址按惯用原名保留；参考人工修订提示，不能把英文句子当专名绕过翻译。
每个 segment 标注对应 words 的 start_word/end_word（零起点、两端包含）；必须按序连续覆盖该整句所有词，不遗漏、不重复、不逆序。根据对应原文语义选择跨度，不能按中文字数均分时间。
text 是完整译文，segments.text 拼接必须与 text 完全相同（含标点）。仅返回 {"items":[{"id":原id,"text":"完整中文译文","segments":[{"text":"中文短句","start_word":0,"end_word":5}],"preserve_as":null}]}。'''


def translate(root,data,cues,folder,progress,call,cfg):
    folder=Path(folder)
    with folder_lock(folder):
        folder.mkdir(parents=True,exist_ok=True)
        value=plan(folder,cues,cfg.get('OPENAI_MODEL_NAME'));units=value['units']
        write_json(folder/'semantic-enabled.json',{'policy':POLICY})
        entries=folder/'semantic-v1';entries.mkdir(exist_ok=True)
        hints=read_json(folder/'semantic-hints.json').get('rows',[])
        def current():return view(folder,value)
        def report():
            result=current();write_json(folder/'semantic-progress.json',result)
            pending_review=sum(r['state']=='problem' and r['reason']!='正在等待整句翻译返回' for r in result['rows'])
            progress(f'整句翻译与中文断句 · 已完成 {result["completed"]}/{result["total"]} 条字幕 · 待定 {pending_review} 条')
            return result
        def stop():
            result=report();failed=[r for r in result['rows'] if r['state']=='problem']
            raise ProductionFailure('subtitle_review',f'全文翻译已处理完，剩余 {len(failed)} 条待定。请在人工审核区修改或点击“确定无异常”，确认后生成视频。',False,
                dict(total=result['total'],completed=result['completed'],failures=failed,failed_ids=[r['id'] for r in failed]))
        report()
        for offset in range(0,len(units),6):
            check_pause(folder);ids=[]
            state=current()
            for i in range(offset,min(offset+6,len(units))):
                u=units[i];cache=read_json(entries/(u['key']+'.json'))
                related=[r for r in state['rows'] if r['unit_key']==u['key']]
                if related and all(r['state'] in ('completed','manual') for r in related):continue
                if cache.get('segments') or cache.get('failure') or cache.get('attempts',0)>=MAX_ATTEMPTS:continue
                ids.append(i)
            if not ids:continue
            payload=dict(operation='subtitle_sentence_translation',policy=POLICY,items=[])
            for i in ids:
                u=units[i];old=read_json(entries/(u['key']+'.json'))
                write_json(entries/(u['key']+'.json'),dict(attempts=old.get('attempts',0)+1,failure='正在等待整句翻译返回'))
                payload['items'].append(dict(id=i,text=u['text'],context_before=u['before'],context_after=u['after'],
                    words=[dict(id=j,**w) for j,w in enumerate(u['words'])],
                    human_corrections=[r for r in hints if r['start']<u['end'] and r['end']>u['start']]))
            report()
            try:
                response=call(root,data,PROMPT,payload)
                items=response.get('items',[]) if isinstance(response,dict) else []
                if not isinstance(items,list):items=[]
                for i in ids:
                    u=units[i];path=entries/(u['key']+'.json');old=read_json(path)
                    matches=[x for x in items if isinstance(x,dict) and type(x.get('id')) is int and x['id']==i]
                    try:
                        if len(matches)!=1:raise ValueError('AI 返回缺少或重复整句编号')
                        result=validate(u,matches[0]);write_json(path,dict(result,attempts=old['attempts']))
                    except ValueError as exc:
                        write_json(path,dict(attempts=old['attempts'],failure=str(exc),candidate=matches[0].get('text','') if matches and isinstance(matches[0].get('text'),str) else ''))
            except ProductionFailure as exc:
                for i in ids:
                    path=entries/(units[i]['key']+'.json');old=read_json(path)
                    write_json(path,dict(attempts=old['attempts'],failure=str(exc)))
                if exc.code not in ('ai_format','ai_response'):
                    report()
                    raise ProductionFailure(exc.code,str(exc)+'；已保存结果保留，未继续请求后文。',False,dict(completed=current()['completed'],total=current()['total'])) from None
            result=report();check_pause(folder)
        result=report();check_pause(folder)
        if any(r['state']=='problem' for r in result['rows']):stop()
        return [dict(start=r['start'],end=r['end'],text=r['text']) for r in result['rows']]


def reset(folder):
    for path in (Path(folder)/'semantic-v1').glob('*.json'):
        entry=read_json(path)
        if not entry.get('segments'):write_json(path,{'attempts':0})
