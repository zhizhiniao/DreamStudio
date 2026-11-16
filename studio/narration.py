"""Persist reviewed utterances; synthesize exactly those lines, with explicit pauses."""
import copy
import hashlib
import json
import re
import sys
import threading
import time
import unicodedata
import uuid
import wave
from array import array
from pathlib import Path

from flask import jsonify, request
from . import production as flow, production_ai as ai, media
from .production_errors import ProductionFailure, public_failure
from .youtube import read_json, write_json

VOICES = (
    ('Chinese_casual_instructor_nv1','休闲讲师'), ('Chinese_deep_voiced_male_nv1','深沉男声'),
    ('Chinese (Mandarin)_Cute_Spirit','可爱精灵'), ('Chinese (Mandarin)_Sweet_Lady','甜美女声'),
    ('Chinese (Mandarin)_Mature_Woman','成熟女声'), ('Chinese_patitent_teacher','耐心老师'),
    ('Chinese_huolishaonv','活力少女'),
)
POLICY = 1
DRAFT_POLICY = 3


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def caption(text):
    text = re.sub(r'<[^>]*>|\{\\[^}]*\}', '', str(text))
    text = unicodedata.normalize('NFKC', text)
    from .translation import standalone_url
    if standalone_url(text):return text.strip()
    text = re.sub(r'(\d+(?:\.\d+)?)%', r'百分之\1', text)
    text = re.sub(r'(?<=\d)\.(?=\d)', '点', text)
    text = re.sub(r'(?<![0-9A-Za-z])-(?=\d)', '负', text)
    for symbol, word in {'+':'加', '=':'等于', '/':'斜杠', '#':'井号', '&':'与', '°C':'摄氏度'}.items():
        text = text.replace(symbol, word)
    text = ''.join(c if unicodedata.category(c)[0] in 'LN' else ' ' for c in text)
    return re.sub(r' +', ' ', text).strip()


def normalize(rows, duration):
    if not isinstance(rows, list) or not rows or len(rows) > 3000:
        raise ValueError('讲稿不能为空，最多 3000 段。')
    result = []; end = 0
    for i, row in enumerate(rows):
        if not isinstance(row, dict):raise ValueError('讲稿格式无效。')
        start, stop = row.get('start'), row.get('end')
        if type(start) not in (float,int) or type(stop) not in (float,int) or not 0 <= end <= start < stop <= duration+.01:
            raise ValueError(f'第 {i+1} 段时间范围或顺序无效。')
        text, pause = row.get('speech'), row.get('pause_ms',240)
        if not isinstance(text,str) or not text.strip() or len(text)>5000:
            raise ValueError(f'请填写第 {i+1} 段讲稿。')
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        if any(not caption(line) or re.search(r'<[^>]+>|\{\\|```',line) for line in lines):
            raise ValueError('讲稿中请只填写朗读内容，不放标签、代码块或纯符号。')
        if type(pause) is not int or not 80 <= pause <= 1200:
            raise ValueError('句间停顿须为 80–1200 毫秒。')
        if (len(lines)-1)*pause/1000 >= stop-start:
            raise ValueError(f'第 {i+1} 段的停顿已超过画面时长，请减少换行。')
        result.append(dict(id=i+1,start=float(start),end=float(stop),speech='\n'.join(lines),pause_ms=pause,
                           source=str(row.get('source') or ''),translation=str(row.get('translation') or '')))
        end=stop
    return result


def script_hash(task):
    return digest([POLICY, task.get('speech_plan'), task.get('translated_subtitles_hash'), task.get('start'), task.get('duration')])


def reviewed(task):
    path=Path(task.get('speech_source_subtitles') or task.get('translated_subtitles') or '__missing__')
    return bool(task.get('speech_plan') and task.get('speech_review_hash')==script_hash(task)
                and path.is_file() and media.checksum(path)==task.get('translated_subtitles_hash'))


def script_groups(asset,task):
    # Continuing a draft must not move or overwrite the user's existing edits.
    if task.get('speech_plan'):
        return [{k:r.get(k,'' if k in ('source','translation') else None)
                 for k in ('id','start','end','source','translation')} for r in task['speech_plan']]
    raw=ai.read_cues(task['translated_subtitles']); original,_=ai.source_cues(asset)
    # Dubbing follows original speech, not a machine-translated subtitle clock.
    use_original=task.get('mode')=='dub' and bool(original)
    timeline=original if use_original else raw
    groups=[]; pending=[]
    def emit():
        if not pending:return
        start,end=pending[0]['start'],pending[-1]['end']
        groups.append(dict(id=len(groups)+1,start=start,end=end,
            translation='' if use_original else ' '.join(c['text'] for c in pending),
            source=' '.join(c['text'] for c in pending) if use_original else ''))
        pending.clear()
    for cue in timeline:
        if cue['end']<=task.get('start',0) or cue['start']>=task.get('start',0)+task['duration']:continue
        if re.fullmatch(r'[\[【(（].*(?:音乐|掌声|music|applause).*[\]】)）]',cue['text'],re.I):continue
        c=dict(cue,start=max(task.get('start',0),cue['start']),end=min(task.get('start',0)+task['duration'],cue['end']))
        if pending and (c['start']-pending[-1]['end']>1 or c['end']-pending[0]['start']>18):emit()
        if pending and c['start']<pending[-1]['end']:pending[-1]['end']=c['start']
        pending.append(c)
        if c['end']-pending[0]['start']>=4 and re.search('[。！？.!?]$',c['text']):emit()
    emit()
    if not groups:raise ValueError('所选片段没有可审核的对白。')
    return groups


def speech_budget(group, pause_ms=240, lines=1):
    slot=group['end']-group['start']
    return dict(slot_seconds=round(slot,3),max_speech_units=max(1,int(max(0,slot-(lines-1)*pause_ms/1000)*4)))


def speech_units(text):
    # Conservative drafting estimate only, never a claim about measured audio.
    return len(re.sub(r'\s|[^\w\u4e00-\u9fff]','',caption(text)))


def draft(root,data,asset,task,folder,progress,checkpoint=None,cache_only=False,initial_rejections=None):
    """Validate before caching, repair only missing IDs, expose durable partial rows."""
    groups=script_groups(asset,task)
    folder=Path(folder);folder.mkdir(parents=True,exist_ok=True)
    rows_folder=folder/'rows-v2';rows_folder.mkdir(exist_ok=True)
    model=ai.config(data).get('OPENAI_MODEL_NAME');accepted={};calls=0;repairs=0;last_failure={}
    keys={g['id']:digest([DRAFT_POLICY,g,model]) for g in groups}
    by_id={g['id']:g for g in groups};rejections={}
    def accept(items,ids,save=True,human=False,budget_margin=1.):
        if not isinstance(items,list):return
        found={};duplicates=set()
        for item in items:
            if not isinstance(item,dict) or type(item.get('id')) is not int or item['id'] not in ids:continue
            i=item['id']
            if i in found:duplicates.add(i)
            found[i]=item
        for i,item in found.items():
            if i in duplicates:continue
            try:row=normalize([dict(by_id[i],speech=item.get('speech'),pause_ms=item.get('pause_ms',240))],asset['duration'])[0]
            except ValueError as exc:
                rejections[i]=dict(previous_speech=item.get('speech'),failure=str(exc));continue
            if not human and speech_units(row['speech'])>speech_budget(row,row['pause_ms'],len(row['speech'].splitlines()))['max_speech_units']*budget_margin:
                rejections[i]=dict(previous_speech=row['speech'],failure='译稿超出朗读预算；直接表达原意，去掉铺垫与重复，不能省略操作、数字、否定或条件。',actual_units=speech_units(row['speech']))
                continue
            row['id']=i;accepted[i]=row
            if save:write_json(rows_folder/(keys[i]+'.json'),row)
    # Migrate only the old batches whose full input signature still matches.
    for offset in range(0,len(groups),8):
        batch=groups[offset:offset+8];key=digest([DRAFT_POLICY,batch,model])
        try:items=json.loads((folder/(key+'.json')).read_text(encoding='utf-8'))
        except (OSError,ValueError):continue
        accept(items,{g['id'] for g in batch})
    for g in groups:accept([read_json(rows_folder/(keys[g['id']]+'.json'))],{g['id']},False)
    # Explicitly saved human edits take precedence over cached model output.
    for row in task.get('speech_plan',[]):
        g=by_id.get(row.get('id')) if isinstance(row,dict) else None
        if g and all(row.get(k)==g.get(k) for k in ('start','end','source','translation')):
            accept([row],{g['id']},False,human=True)
    rejections.update(initial_rejections or {})
    reused=len(accepted)
    def report(status='running'):
        rows=[accepted.get(g['id'],dict(g,speech='',pause_ms=240)) for g in groups]
        state=dict(status=status,total=len(groups),completed=len(accepted),complete=len(accepted)==len(groups),
                   reused=reused,calls=calls,repairs=repairs,last_failure=last_failure,missing_ids=[g['id'] for g in groups if g['id'] not in accepted])
        write_json(folder/'progress.json',state)
        write_json(folder/'rejections.json',rejections)
        if checkpoint:checkpoint(rows,state)
        return rows,state
    report()
    if cache_only:return report('completed' if len(accepted)==len(groups) else 'paused')[0]
    batch_repairs=0
    def context(i):
        index=next(j for j,g in enumerate(groups) if g['id']==i)
        return dict(topic=asset.get('title',''),context_before=groups[index-1].get('source','') if index else '',
                    context_after=groups[index+1].get('source','') if index+1<len(groups) else '')
    def request_batch(ids,depth=0):
        nonlocal calls,repairs,last_failure,batch_repairs
        if not ids:return
        if depth:
            if batch_repairs>=12:return
            repairs+=1;batch_repairs+=1
        calls+=1
        progress(f'中文情境与断句整理 {len(accepted)}/{len(groups)} · '+('缩小范围补全' if depth else '正在处理下一组'))
        try:
            answer=ai.call(root,data,
                '你在做等时中文配音翻译，不是扩写教程。source 是本时段唯一原文；没有 source 时才使用 translation。'
                '用简短自然的中文直接表达原意。不得新增解释、总结、铺垫或重复句；保留操作、数字、否定和条件。'
                '不要逐词直译。可省略无信息的口头禅（你明白吧、相信我、好了）和重复赞叹，不删事实。'
                '每段优先写一行完整自然的口语，用中文标点停顿；不要为了字幕显示而换行，字幕会按逐字时间另行切分。不要标签或舞台指令。'
                '每个 id 独立翻译，禁止提前写入后段内容或挪到前段；禁止把多段翻完后按句子重新分配 id。'
                'topic 和 context_before/after 只用于识别专有名词、代词与未完句，不翻译相邻段，不猜测未完成的句尾。'
                '歌曲名称、歌词与引用应保留其文字含义，不能改译为工具功能或生成方式。'
                'slot_seconds 为可用秒数，max_speech_units 为朗读字数预算；标点不计，每次换行还要预留0.24秒。'
                'previous_speech 是上次失败稿，failure 是具体原因；重新翻译本段，不能原样返回失败稿。'
                '预算是口语译稿目标，请改用简洁表达但不删关键信息；必须给出完整候选，不用空字符串回避问题。'
                '只返回 {"items":[{"id":原id,"speech":"第一句\\n第二句"}]}，id须完整对应。',
                {'items':[dict(by_id[i],**speech_budget(by_id[i]),**rejections.get(i,{}),**context(i)) for i in ids]})
            items=answer.get('items') if isinstance(answer,dict) else None
            # The estimate is not measured audio. A final near-budget candidate may proceed to real timing,
            # but only after independent semantic validation; gross expansion remains rejected.
            accept(items,set(ids),False,budget_margin=1.15 if depth>=3 else 1.)
            audit_ids=[i for i in ids if i in accepted and by_id[i].get('source') and task.get('mode')=='dub']
            if audit_ids:
                audit=ai.call(root,data,
                    '独立核对翻译：逐段对比 source 与 speech，检查漏译、扩写、串段，尤其操作、数字、名称、否定与条件。'
                    '允许省略无事实信息的口头禅和重复赞叹，不能把这种省略判为漏译；不完整衔接词不必硬译，也不能补写未说出的事实。'
                    '相邻原文仅用于消歧，不要求本段译出相邻段的内容；主题可用于纠正转写中的专名拼写。'
                    '不能因简短而放行事实缺失。只返回 {"items":[{"id":原id,"faithful":true或false,"reason":"具体问题或通过理由"}]}。',
                    {'items':[dict(source=by_id[i]['source'],speech=accepted[i]['speech'],id=i,**context(i)) for i in audit_ids]})
                checks=audit.get('items',[]) if isinstance(audit,dict) else []
                for i in audit_ids:
                    matches=[r for r in checks if isinstance(r,dict) and r.get('id')==i]
                    if len(matches)==1 and matches[0].get('faithful') is True:continue
                    row=accepted.pop(i)
                    (rows_folder/(keys[i]+'.json')).unlink(missing_ok=True)
                    rejections[i]=dict(previous_speech=row['speech'],failure=str(matches[0].get('reason') if matches else '原意核对结果缺失'))
            for i in ids:
                if i in accepted:write_json(rows_folder/(keys[i]+'.json'),accepted[i])
        except ProductionFailure as exc:
            for i in ids:accepted.pop(i,None)
            last_failure=dict(code=exc.code,**{k:v for k,v in exc.details.items() if k in ('response_chars','json_error_at','finish_reason')})
            if not exc.retryable:raise
            # Network errors stop this round instead of multiplying a service outage.
            if exc.code not in ('ai_format','ai_response'):raise
        report()
        missing=[i for i in ids if i not in accepted]
        if missing and depth<3:
            size=max(1,(len(missing)+1)//2)
            for start in range(0,len(missing),size):request_batch(missing[start:start+size],depth+1)
    try:
        for offset in range(0,len(groups),8):
            batch_repairs=0
            ids=[g['id'] for g in groups[offset:offset+8] if g['id'] not in accepted]
            request_batch(ids)
        if len(accepted)<len(groups):
            _,state=report('failed')
            raise ProductionFailure('script_incomplete',
                f'已检查全稿并保存 {len(accepted)}/{len(groups)} 段；剩余段落自动修订未通过。可继续未完成部分，已完成段落保留。',True,state)
    except Exception:
        report('failed');raise
    output,_=report('completed')
    progress(f'中文情境与断句整理 {len(groups)}/{len(groups)} · 已完成，等待人工审核')
    return normalize(output,asset['duration'])


def trim_edges(pcm, rate):
    """Trim only low-level outer padding, retaining 80 ms guards and all inner pauses."""
    samples=array('h');samples.frombytes(pcm)
    if sys.byteorder!='little':samples.byteswap()
    first=next((i for i,v in enumerate(samples) if abs(v)>104),None)  # -50 dBFS
    if first is None:raise ProductionFailure('narration_audio','配音文件没有可辨认的声音，请检查音频。')
    last=len(samples)-next(i for i,v in enumerate(reversed(samples)) if abs(v)>104)
    guard=round(.08*rate)
    start=max(0,first-guard) if first/rate>=.12 else 0
    end=min(len(samples),last+guard) if (len(samples)-last)/rate>=.12 else len(samples)
    return pcm[start*2:end*2],dict(raw_seconds=len(samples)/rate,trimmed_seconds=(start+len(samples)-end)/rate,trim_start_seconds=start/rate)


def cached_audio(folder,text,options):
    """Reuse individual utterances across review revisions, including legacy folders."""
    from modules.chinese_dubbing import tts_key
    folder=Path(folder);key=tts_key(text,options)
    for path in [folder/(key+'.json'),*folder.glob('*/'+key+'.json')]:
        value=read_json(path)
        audio=Path(value.get('file') or '__missing__')
        if (value.get('signature')==key and value.get('text')==text and value.get('words')
                and audio.is_file() and audio.resolve().is_relative_to(folder.resolve())):
            return value
    return None


def prepared_clip(runtime, value, folder):
    source=Path(value['file']);source_hash=media.checksum(source)
    target=Path(folder)/('clean-v2-'+source_hash+'.wav')
    metadata=target.with_suffix('.json');saved=read_json(metadata)
    if target.is_file() and saved.get('hash')==media.checksum(target):
        return str(target),saved['seconds'],saved
    pcm=media.run([Path(runtime)/'bin/ffmpeg.exe','-v','error','-i',source,'-ar','32000','-ac','1','-f','s16le','-'],120)
    pcm,info=trim_edges(pcm,32000)
    with wave.open(str(target),'wb') as wav:
        wav.setnchannels(1);wav.setsampwidth(2);wav.setframerate(32000);wav.writeframes(pcm)
    info.update(seconds=len(pcm)/64000,hash=media.checksum(target));write_json(metadata,info)
    return str(target),info['seconds'],info


def timing_row(group,clips):
    pause=(len(clips)-1)*group['pause_ms']/1000
    slot=group['end']-group['start'];spoken=sum(c[2] for c in clips)
    available=slot-pause;tempo=max(1,spoken/max(.001,available))
    return dict(id=group['id'],start=group['start'],end=group['end'],speech=group['speech'],pause_ms=group['pause_ms'],
                slot_seconds=round(slot,3),audio_seconds=round(spoken+pause,3),
                excess_seconds=round(max(0,spoken/1.08+pause-slot),3),tempo=tempo,
                trimmed_seconds=round(sum(c[3]['trimmed_seconds'] for c in clips),3))


def timed_captions(text,words,start,end,tempo=1.,trim_start=0.):
    """Short phrase cues timed against TTS words; never use a whole paragraph as one cue."""
    tokens=list(re.finditer(r'\d+(?:\.\d+)?%?|[A-Za-z0-9]+(?:\.[A-Za-z][A-Za-z0-9]*)+|[A-Za-z0-9]+|[^\s]',text));pieces=[];pending=[]
    def stamp(token,finish=False):
        selected=[w for w in words if w.get('word_begin',-1)<token.end() and w.get('word_end',-1)>token.start()]
        if not selected:raise ValueError('配音逐字时间戳不完整，无法可靠生成短句字幕。')
        value=(selected[-1]['time_end'] if finish else selected[0]['time_begin'])/1000
        return max(start,min(end,start+(value-trim_start)/tempo))
    def emit():
        if not pending:return
        body=caption(text[pending[0].start():pending[-1].end()])
        if body:
            a=stamp(pending[0]);b=stamp(pending[-1],True)
            if b<=a:raise ValueError('配音逐字时间范围无效。')
            pieces.append(dict(start=a,end=b,text=body))
        pending.clear()
    for token in tokens:
        if not caption(token.group()):
            if pending and token.group() in '，。！？；：,.!?;:':emit()
            continue
        if pending:
            body=caption(text[pending[0].start():token.end()])
            if len(body)>18:
                # Keep short names such as OpenAI的Codex together instead of flashing the last word alone.
                pivot=next((i for i in range(len(pending)-1,0,-1) if re.fullmatch('[A-Za-z0-9]+',pending[i].group())),None)
                if pivot and len(caption(text[pending[0].start():pending[pivot-1].end()]))>=5 and len(caption(text[pending[pivot].start():token.end()]))<=18:
                    tail=pending[pivot:];del pending[pivot:];emit();pending.extend(tail)
                else:emit()
            if pending and stamp(token,True)-stamp(pending[0])>3.5:emit()
        pending.append(token)
    emit()
    return pieces


def timing_failure(rows,completed,total):
    row=rows[0]
    return ProductionFailure('narration_timing',
        f'第 {row["id"]} 段配音过长：可用 {row["slot_seconds"]:.2f} 秒，实测 {row["audio_seconds"]:.2f} 秒（含停顿）。'
        f'按最高 1.08 倍语速仍超 {row["excess_seconds"]:.2f} 秒。请修改该段并重新审核；未改句子的配音保留。',
        False,dict(segments=rows,completed=completed,total=total))


def inspect_timing(root,data,runtime,task,folder):
    """Read local audio only. Estimates never masquerade as measured durations."""
    sys.path.insert(0,str(Path(root)/'vendor'))
    from modules.chinese_dubbing import settings
    options=settings(ai.config(data));options.update(voice=task.get('voice_id'),speed=1.)
    rows=[];ready={}
    for group in task.get('speech_plan',[]):
        clips=[];lines=group['speech'].splitlines()
        for line in lines:
            value=cached_audio(folder,line,options)
            if value:
                if line not in ready:ready[line]=prepared_clip(runtime,value,folder)
                clips.append((line,*ready[line]))
        measured=len(clips)==len(lines) and bool(lines)
        if measured:row=timing_row(group,clips)
        else:
            # Screening only; names, numbers and punctuation vary by voice.
            units=len(re.sub(r'\s|[^\w\u4e00-\u9fff]','',group['speech']))
            slot=group['end']-group['start'];estimate=units/4+max(0,len(lines)-1)*group['pause_ms']/1000
            row=dict(id=group['id'],speech=group['speech'],pause_ms=group['pause_ms'],slot_seconds=round(slot,3),audio_seconds=round(estimate,3),tempo=estimate/max(slot,.001))
        row.update(measured=measured,risk=row['tempo']>1.08,cached_lines=len(clips),total_lines=len(lines))
        rows.append(row)
    return dict(version=task['version'],voice_id=task.get('voice_id'),segments=rows,
                measured=sum(r['measured'] for r in rows),total=len(rows),risks=sum(r['risk'] for r in rows))


def synthesize(root,data,runtime,task,folder,progress):
    if not reviewed(task):raise ValueError('讲稿或字幕已变化，请重新审核。')
    if task.get('voice_id') not in dict(VOICES):raise ValueError('请选择本任务音色。')
    sys.path.insert(0,str(Path(root)/'vendor'))
    from modules.chinese_dubbing import synth,settings
    cfg=ai.config(data);options=settings(cfg);options.update(voice=task['voice_id'],speed=1.0)
    folder=Path(folder);folder.mkdir(parents=True,exist_ok=True)
    # Inspect every already-generated group before any new billable request.
    if task.get('narration_auto_repair') and all(g.get('source') for g in task['speech_plan']):
        from .narration_auto import prepare
        prepare(root,data,runtime,task,folder,progress)
    ready={};overruns=[];timings=[];word_maps={}
    for group in task['speech_plan']:
        clips=[]
        for line in group['speech'].splitlines():
            value=cached_audio(folder,line,options)
            if value:
                word_maps[line]=value.get('words',[])
                if line not in ready:ready[line]=prepared_clip(runtime,value,folder)
                clips.append((line,*ready[line]))
        if len(clips)==len(group['speech'].splitlines()):
            row=timing_row(group,clips)
            if row['tempo']>1.08:overruns.append(row)
    total=sum(len(g['speech'].splitlines()) for g in task['speech_plan'])
    if overruns:raise timing_failure(overruns,len(ready),total)
    output=folder/('voice-'+uuid.uuid4().hex+'.wav'); rate=32000; cues=[]; receipt=[]; cursor=0
    total=sum(len(g['speech'].splitlines()) for g in task['speech_plan']);done=0
    with wave.open(str(output),'wb') as wav:
        wav.setnchannels(1);wav.setsampwidth(2);wav.setframerate(rate)
        for group in task['speech_plan']:
            clips=[];lines=group['speech'].splitlines();pause=group['pause_ms']/1000
            for line in lines:
                progress(f'合成审核句块 {done}/{total} 已完成；正在处理下一句')
                if line not in ready:
                    value=synth({'id':done,'text':line},options,ai.ai_budget.speech_config(data,cfg),folder)
                    word_maps[line]=value.get('words',[])
                    ready[line]=prepared_clip(runtime,value,folder)
                clips.append((line,*ready[line]));done+=1
                progress(f'合成审核句块 {done}/{total} 已完成')
            slot=group['end']-group['start'];speech_slot=slot-(len(lines)-1)*pause
            tempo=max(1,sum(c[2] for c in clips)/speech_slot)
            row=timing_row(group,clips);timings.append(row)
            if tempo>1.08:raise timing_failure([row],done,total)
            start=round(group['start']*rate)
            if start>cursor:wav.writeframes(b'\0\0'*(start-cursor));cursor=start
            for index,(line,audio,length,info) in enumerate(clips):
                pcm=media.run([Path(runtime)/'bin/ffmpeg.exe','-v','error','-i',audio,'-af',f'atempo={tempo:.8f}',
                               '-ar',str(rate),'-ac','1','-f','s16le','-'],120)
                begin=cursor/rate;wav.writeframes(pcm);cursor+=len(pcm)//2
                cues.extend(timed_captions(line,word_maps[line],begin,cursor/rate,tempo,info.get('trim_start_seconds',0)))
                receipt.append(dict(text=line,start=begin,end=cursor/rate,pause_ms=group['pause_ms'] if index<len(clips)-1 else 0))
                if index<len(clips)-1:
                    frames=round(pause*rate);wav.writeframes(b'\0\0'*frames);cursor+=frames
            if cursor/rate>group['end']+.02:raise ValueError('配音实际时长超出审核段落，已停止封装。')
    srt=folder/(output.stem+'.zh.srt');ai.write_srt(cues,srt)
    proof=dict(review_hash=task['speech_review_hash'],voice_id=task['voice_id'],chunks=receipt,timing=timings,
               voice_hash=media.checksum(output),subtitle_hash=media.checksum(srt))
    (folder/(output.stem+'.receipt.json')).write_text(json.dumps(proof,ensure_ascii=False,indent=2),encoding='utf-8')
    return dict(voice_path=str(output),translated_subtitles=str(srt),speech_receipt=proof)


def register(app,root,data,runtime,store,archive,record,save_archive,lock):
    def task_for(key,values):
        task=record('tasks',key);flow.editable(task)
        if values.get('version')!=task['version']:raise ValueError('版本已变化，请刷新。')
        if any(p.get('task_id')==key and p.get('status') in ('queued','running','succeeded','needs_check') for p in store.list('publications')):
            raise ValueError('此任务已有投稿记录，请创建独立新版本。')
        return task

    @app.get('/api/narration/voices')
    def voices():return jsonify([dict(id=key,label=label) for key,label in VOICES])

    @app.get('/api/tasks/<key>/speech-timing')
    def speech_timing(key):
        task=record('tasks',key)
        if task.get('production_job',{}).get('status')=='running':raise ValueError('当前任务仍在处理，请完成后检查时长。')
        asset=record('assets',task['asset_id'])
        folder=archive.project(asset)/'03_制作'/key/'voice-cache'
        return jsonify(inspect_timing(root,data,runtime,task,folder))

    @app.post('/api/tasks/<key>/redo')
    def redo(key):
        values=request.get_json() or {}
        try:new_id=uuid.UUID(values.get('request_id','')).hex
        except (ValueError,TypeError,AttributeError):raise ValueError('请刷新页面后重试。') from None
        with lock:
            existing=store.get('tasks',new_id)
            if existing:
                if existing.get('redo_source_task')!=key:raise ValueError('重做请求已失效。')
                return jsonify(existing)
            source=record('tasks',key);flow.editable(source)
            asset=record('assets',source['asset_id'])
            if not Path(asset.get('path') or '__missing__').is_file():raise ValueError('保留的原片缺失，请先恢复素材。')
            clone={field:copy.deepcopy(source[field]) for field in (
                'title','description','tags','mode','start','duration','cover_style','cover_title','cover_category',
                'cover_emphasis','cover_color','cover_frames','cover_selection','cover_crops','source_account','targets') if field in source}
            clone.update(id=new_id,asset_id=asset['id'],version=1,status='draft',confirmed=-1,
                         reviewed_version=None,redo_source_task=key,workflow_stage='content',
                         field_sources={field:'manual' for field in ('title','description','tags') if clone.get(field)})
            return jsonify(store.put('tasks',flow.hydrate(clone,asset))),201

    @app.post('/api/tasks/<key>/cover-review')
    def approve_cover(key):
        values=request.get_json() or {}
        with lock:
            task=task_for(key,values);asset=record('assets',task['asset_id'])
            if task.get('production_job',{}).get('status')=='running':raise ValueError('当前任务正在处理，请勿重复提交。')
            if task.get('cover_signature')!=media.cover_signature(asset,task):raise ValueError('请先生成当前封面。')
            for field in ('cover_landscape_path','cover_portrait_path'):
                if not Path(task.get(field) or '__missing__').is_file() or media.checksum(task[field])!=task.get(field+'_hash'):
                    raise ValueError('封面已变化，请重新生成。')
            task.update(cover_review_hash=task['cover_signature'],workflow_stage='script' if task['mode']=='dub' else 'subtitle_translation' if task['mode']=='subtitles' else 'ready_to_generate')
            return jsonify(store.put('tasks',task))

    @app.post('/api/tasks/<key>/speech-review')
    def approve_script(key):
        values=request.get_json() or {}
        with lock:
            task=task_for(key,values);asset=record('assets',task['asset_id'])
            if task.get('cover_review_hash')!=media.cover_signature(asset,task):raise ValueError('请先确认当前封面。')
            path=Path(task.get('speech_source_subtitles') or task.get('translated_subtitles') or '__missing__')
            if not path.is_file() or media.checksum(path)!=task.get('translated_subtitles_hash'):raise ValueError('字幕已变化，请重新整理。')
            rows=normalize(values.get('rows'),asset['duration'])
            if [(g['start'],g['end']) for g in rows]!=[(g['start'],g['end']) for g in task.get('speech_plan',[])]:
                raise ValueError('请保留段落时间范围，只编辑讲稿和停顿。')
            task.update(speech_plan=rows,version=task['version']+1,reviewed_version=None,status='draft',error='',
                        speech_preparation=dict(status='completed',total=len(rows),completed=len(rows),complete=True,missing_ids=[]))
            task['production_job']=dict(kind='script',status='completed',percent=100,message='讲稿审核已通过')
            task['speech_review_hash']=script_hash(task)
            task['workflow_stage']='voice' if task['mode']=='dub' and task.get('voice_id') not in dict(VOICES) else 'ready_to_generate'
            task.pop('prepared_version',None)
            return jsonify(store.put('tasks',task))

    @app.post('/api/tasks/<key>/speech-draft')
    def save_script_draft(key):
        values=request.get_json() or {}
        with lock:
            task=task_for(key,values);asset=record('assets',task['asset_id'])
            base=task.get('speech_plan',[]);rows=values.get('rows')
            if not base or not isinstance(rows,list) or len(rows)!=len(base):raise ValueError('讲稿段落已变化，请刷新。')
            result=[]
            for i,(old,row) in enumerate(zip(base,rows)):
                if not isinstance(row,dict) or (row.get('start'),row.get('end'))!=(old['start'],old['end']):raise ValueError('请保留段落时间范围。')
                new=dict(old,speech=row.get('speech'),pause_ms=row.get('pause_ms',240))
                # Empty rows remain explicit gaps, never masquerade as approved content.
                if not isinstance(new['speech'],str):raise ValueError('讲稿格式无效。')
                checked=normalize([dict(new,speech=new['speech'].strip() or '待填写')],asset['duration'])[0]
                checked.update(id=i+1,speech=checked['speech'] if new['speech'].strip() else '')
                result.append(checked)
            completed=sum(bool(g['speech']) for g in result)
            task.update(speech_plan=result,version=task['version']+1,reviewed_version=None,status='draft',error='',
                        speech_preparation=dict(status='paused',total=len(result),completed=completed,complete=completed==len(result),
                                                missing_ids=[g['id'] for g in result if not g['speech']]),
                        workflow_stage='script_review' if completed==len(result) else 'script')
            task.pop('speech_review_hash',None);task.pop('prepared_version',None)
            task['production_job']=dict(kind='script',status='paused',message='讲稿草稿已保存，尚未审核')
            return jsonify(store.put('tasks',task))

    @app.post('/api/tasks/<key>/voice-choice')
    def choose_voice(key):
        values=request.get_json() or {}
        with lock:
            task=task_for(key,values)
            if not reviewed(task):raise ValueError('请先审核当前讲稿。')
            if values.get('voice_id') not in dict(VOICES):raise ValueError('请选择列表中的音色。')
            task.update(voice_id=values['voice_id'],workflow_stage='ready_to_generate',version=task['version']+1,reviewed_version=None)
            return jsonify(store.put('tasks',task))

    def launch(key,values,final=False):
        with lock:
            task=task_for(key,values);asset=record('assets',task['asset_id'])
            if task.get('cover_review_hash')!=media.cover_signature(asset,task):raise ValueError('请先确认当前封面。')
            if final:
                if values.get('confirmed') is not True:raise ValueError('请确认当前文案、封面、讲稿和音色后生成。')
                from .chapter_navigation import for_render
                for_render(task)
                if task['mode']=='subtitles':
                    from .subtitle_workspace import workspace
                    from .subtitle_stages import approved
                    if not approved(workspace(archive,asset,task),asset,task,data):
                        raise ValueError('请先完成字幕翻译并确认人工审核，再独立封装。封装不会补译字幕。')
                if task['mode']=='dub' and not reviewed(task):raise ValueError('请先审核当前讲稿。')
                if task['mode']=='dub' and task.get('voice_id') not in dict(VOICES):raise ValueError('请选择音色。')
                checked=copy.deepcopy(task);checked['confirmed']=-1
                for step in range(4):flow.confirm(checked,step,asset)
                store.backup('before-narration-generation')
                if task['mode']=='subtitles' and values.get('reset_translation_attempts') is True:
                    from .translation import reset_attempts
                    from .subtitle_workspace import workspace
                    reset_attempts(workspace(archive,asset,task,True))
            else:
                if task['mode']!='dub':raise ValueError('仅中文配音需要整理讲稿；请直接确认并生成成片。')
                if values.get('rebuild_alignment') is True:
                    original,_=ai.source_cues(asset)
                    if task['mode']!='dub' or not original:raise ValueError('未找到可用于重新对齐的原文字幕，请先补充原文。')
                    store.backup('before-speech-realignment')
                    previous=archive.project(asset)/'03_制作'/key/('speech-before-realignment-'+uuid.uuid4().hex+'.json')
                    write_json(previous,dict(speech_plan=task.get('speech_plan',[]),voice_id=task.get('voice_id'),version=task['version']))
                    task['speech_plan_backup']=str(previous)
                    task.pop('speech_plan',None);task.pop('speech_timing',None)
                if not task.get('speech_plan'):task['speech_alignment_policy']=DRAFT_POLICY
                task.update(version=task['version']+1,reviewed_version=None)
                task.pop('speech_review_hash',None);task.pop('prepared_version',None)
            job_id=uuid.uuid4().hex
            task.update(status='rendering',workflow_stage='subtitle_packaging' if final and task['mode']=='subtitles' else 'generating' if final else 'script',error='')
            task['production_job']=dict(id=job_id,kind='finish' if final else 'script',status='running',percent=0,message='准备生成' if final else '准备中文字幕与讲稿')
            store.put('tasks',task)
        def progress(message):
            with lock:
                latest=record('tasks',key)
                if latest.get('status')=='cancelled' or latest.get('production_job',{}).get('id')!=job_id:raise RuntimeError('任务已停止')
                job=latest['production_job'];job['message']=message
                if message=='正在封装视频与字幕' and task['mode']=='subtitles':
                    from .subtitle_workspace import workspace
                    from .translation import check_pause
                    check_pause(workspace(archive,asset,task))
                    job['stage']='render'
                match=re.search(r'(?:句块|整理|字幕翻译) (\d+)/(\d+)',message)
                if match:job['percent']=min(55,5+int(match[1])/max(1,int(match[2]))*50)
                match=re.search(r'封装进度 (\d+)%',message)
                if match:job['percent']=55+int(match[1])*.43
                latest['progress']=message;store.put('tasks',latest)
        def checkpoint(rows,state):
            task.update(speech_plan=rows,speech_preparation=state)
            with lock:
                latest=record('tasks',key)
                if latest.get('status')=='cancelled' or latest.get('production_job',{}).get('id')!=job_id:raise RuntimeError('任务已停止')
                for field in ('translated_subtitles','speech_source_subtitles','translated_subtitles_hash','subtitle_source','speech_plan','speech_preparation'):
                    if field in task:latest[field]=task[field]
                latest['production_job'].update(stage='script_localization',completed=state['completed'],total=state['total'])
                store.put('tasks',latest)
        @ai.ai_budget.task_scope(key)
        def work():
            try:
                folder=archive.project(asset)/'03_制作'/key/('finish-'+job_id)
                folder.mkdir(parents=True,exist_ok=True)
                if not final:
                    progress('阶段 1/3 · 准备中文字幕')
                    saved=Path(task.get('speech_source_subtitles') or task.get('translated_subtitles') or '__missing__')
                    if saved.is_file() and media.checksum(saved)==task.get('translated_subtitles_hash'):
                        translated=ai.read_cues(saved);source=task.get('subtitle_source') or '已保存中文字幕'
                    else:
                        original,_=ai.source_cues(asset)
                        if task['mode']=='dub' and not original:
                            # Establish the original before drafting; never translate into an intermediate Chinese clock.
                            original,_=ai.transcribe(asset,runtime,progress)
                        if task['mode']=='dub' and original:
                            translated=original;source='原文时间轴（直接生成中文配音译稿）'
                        else:
                            translated,path,source=ai.prepare_subtitles(root,data,asset,runtime,archive.project(asset)/'02_字幕/translation-cache',progress)
                    path=folder/'speech-source.srt';ai.write_srt(translated,path)
                    task.update(translated_subtitles=str(path),speech_source_subtitles=str(path),translated_subtitles_hash=media.checksum(path),subtitle_source=source)
                    checkpoint(task.get('speech_plan',[]),dict(status='running',stage='translation_saved',total=0,completed=0,complete=False))
                    progress('阶段 2/3 · 核对中文语境与断句，复用已完成段落')
                    task['speech_plan']=draft(root,data,asset,task,archive.project(asset)/'03_制作'/key/'script-cache',progress,checkpoint)
                    progress('阶段 3/3 · 检查讲稿完整性与时间轴')
                    task.pop('speech_review_hash',None)
                    task.update(status='draft',workflow_stage='script_review',reviewed_version=None)
                else:
                    if task['mode']=='dub':
                        task['narration_auto_repair']=True
                        voice_cache=archive.project(asset)/'03_制作'/key/'voice-cache'
                        task.update(synthesize(root,data,runtime,task,voice_cache,progress))
                        with lock:
                            latest=record('tasks',key)
                            if latest.get('status')=='cancelled' or latest.get('production_job',{}).get('id')!=job_id:raise RuntimeError('任务已停止')
                            for field in ('speech_plan','speech_review_hash','speech_context_review','speech_auto_revision','speech_measurement','voice_path','translated_subtitles','speech_receipt'):
                                if field in task:latest[field]=task[field]
                            store.put('tasks',latest)
                    elif task['mode']=='subtitles':
                        from .subtitle_workspace import workspace
                        from .translation import check_pause
                        subtitle_folder=workspace(archive,asset,task,True)
                        check_pause(subtitle_folder)
                        # Keep the subtitle clock; dubbing paragraphs and speech budgets do not apply.
                        saved=Path(task['subtitle_approved_path'])
                        if media.checksum(saved)!=task['subtitle_approved_hash']:
                            raise ValueError('审核后的字幕文件已变化，请重新审核。')
                        progress('只封装已审核中文字幕，保留原声；不调用翻译服务')
                        cues=ai.read_cues(saved)
                        from .translation import subtitle_caption
                        cues=[dict(c,text=subtitle_caption(c['text'])) for c in cues]
                        path=folder/'approved.zh.srt';ai.write_srt(cues,path);task['translated_subtitles']=str(path)
                        with lock:
                            latest=record('tasks',key)
                            if latest.get('status')=='cancelled' or latest.get('production_job',{}).get('id')!=job_id:raise RuntimeError('任务已停止')
                            for field in ('translated_subtitles','speech_source_subtitles','translated_subtitles_hash','subtitle_source'):
                                if field in task:latest[field]=task[field]
                            store.put('tasks',latest)
                    if task['mode']=='subtitles':check_pause(subtitle_folder)
                    progress('正在封装视频与字幕')
                    task.update(media.render(root,runtime,asset,task,folder/'video.mp4',progress))
                    task.update(status='approved',workflow_stage='ready',prepared_version=task['version'],reviewed_version=task['version'],
                                confirmed=3,reviewed_at=time.time(),review_basis='confirmed_inputs',progress='待发布')
                    if task['mode']=='subtitles':
                        task.update(status='review',workflow_stage='subtitle_render_review',reviewed_version=None,
                                    review_basis=None,progress='封装完成，请审核成片后再进入发布中心。')
                with lock:
                    latest=record('tasks',key)
                    if latest.get('status')=='cancelled' or latest.get('production_job',{}).get('id')!=job_id:return
                    task['production_job']=dict(id=job_id,kind='finish' if final else 'script',status='completed',percent=100,message='待发布' if final else '讲稿已整理，等待人工审核')
                    if final and task['mode']=='subtitles':task['production_job']['message']='封装完成，等待人工审片。'
                    archive.manifest(asset,[task,*[t for t in store.list('tasks') if t['asset_id']==asset['id'] and t['id']!=key]])
                    store.put('tasks',task)
            except Exception as exc:
                with lock:
                    latest=record('tasks',key)
                    if latest.get('status')=='cancelled' or latest.get('production_job',{}).get('id')!=job_id:return
                    latest.update(status='failed',error=str(exc)[:200],workflow_stage='generation_failed' if final else 'script')
                    if isinstance(exc,ProductionFailure) and exc.code=='narration_timing':
                        latest['speech_timing']=dict(review_hash=task.get('speech_review_hash'),voice_id=task.get('voice_id'),**exc.details)
                    paused=isinstance(exc,ProductionFailure) and exc.code=='subtitle_review'
                    if paused:latest.update(status='draft',workflow_stage='subtitle_review',error='')
                    latest['production_job'].update(status='paused' if paused else 'failed',**public_failure(exc));store.put('tasks',latest)
        threading.Thread(target=work,daemon=True).start()
        return task

    @app.post('/api/tasks/<key>/speech-plan')
    def prepare_script(key):return jsonify(launch(key,request.get_json() or {})),202

    @app.post('/api/tasks/<key>/finish')
    def finish(key):return jsonify(launch(key,request.get_json() or {},True)),202
