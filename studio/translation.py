"""ID-aligned subtitles; durable per-cue attempt budgets, no nested network retries."""
import hashlib
import json
import re
import threading
import time
from pathlib import Path
from .production import chinese
from .production_errors import ProductionFailure
from .youtube import read_json, write_json

MAX_ATTEMPTS = 3

def cue_key(cue,model):
    return hashlib.sha256(json.dumps([cue,model],ensure_ascii=False,sort_keys=True).encode()).hexdigest()

def human_text(source,text,keep=False):
    return (isinstance(text,str) and bool(text.strip()) and len(text)<=2000
            and '\ufffd' not in text and not re.search(r'<[^>]+>|\{\\|```',text)
            and (valid_text(source,text,'proper_name') or (keep and text.strip()==source.strip())))

def check_pause(folder):
    if (Path(folder)/'pause.json').exists():
        raise ProductionFailure('subtitle_review','字幕已暂停，请在中英对照表中检查，保存后继续。',False,{})
_LOCKS = {}
_LOCK_GUARD = threading.Lock()
TOOLS = {'After Effects','Adobe After Effects','Premiere Pro','Adobe Premiere Pro','DaVinci Resolve',
         'Motion Dock','Nothi Toolkit','Blender','Cinema 4D','Photoshop','Illustrator','Figma',
         'Claude','Claude Motion','Cloud Motion','Opus','JavaScript','Python','Remotion','YouTube',
         'Node.js','C++','C#','Sploosh','AEbar','Petals','Suno','ComfyUI','CapCut','FFmpeg','ffprobe','yt-dlp','tldraw','GSAP'}
TOOLS_FOLDED={name.casefold() for name in TOOLS}


def folder_lock(folder):
    with _LOCK_GUARD:
        return _LOCKS.setdefault(str(Path(folder).resolve()),threading.RLock())


def standalone_url(text):
    return isinstance(text,str) and bool(re.fullmatch(
        r'(?:https?://)?(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z]{2,63}(?::[0-9]{1,5})?(?:[/#?][^\s<>\ufffd]*)?[.。!?！？，,;；]?',text.strip()))


def proper_name(text):
    # Model classification is required for unknown names; reject sentence/command shapes.
    words=text.strip().split()
    return (0<len(words)<=6 and len(text)<=100 and
            bool(re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9 .+#&_-]*',text)) and
            not re.search(r'\b(?:I|you|we|they|he|she|it|the|a|an|is|are|was|were|will|can|should|please|open|click|use|create|save|visit|download|install|thank|hello|this|that|and|or|to|for)\b',text,re.I) and
            all(w[0].isupper() or w[0].isdigit() or any(c.isupper() for c in w[1:]) for w in words))


def valid_text(source,text,preserve_as=None):
    if not isinstance(text,str) or not text.strip() or len(text)>2000:return False
    if '\ufffd' in text or re.search(r'<[^>]+>|\{\\|```',text):return False
    if chinese(text):return True
    text=text.strip()
    if text!=source.strip():return False
    if standalone_url(text):return True
    label=text.rstrip('.。!！,，;；:：')
    base=re.sub(r'\s+(?:v?\d+(?:\.\d+)*)$','',label)
    if label.casefold() in TOOLS_FOLDED or base.casefold() in TOOLS_FOLDED:return True
    if re.fullmatch(r'(?:[\d\s.,:%+×/–-]+|[A-Z][A-Z\d.+-]{1,10})',label):return True
    return preserve_as=='proper_name' and proper_name(text)


def subtitle_caption(text):
    # Original-audio subtitles are written text, not a script to pronounce.
    return ' '.join(re.sub(r'<[^>]*>|\{\\[^}]*\}', '', str(text)).split())


def reset_attempts(folder):
    """Only called by an explicit user retry; completed translation files remain intact."""
    with folder_lock(folder):
        from .semantic_subtitles import reset
        reset(folder)
        for path in (Path(folder)/'attempts-v1').glob('*.json'):
            write_json(path,{'attempts':0,'reset_at':time.time()})


def translate_cues(root,data,cues,folder,progress,call,cfg):
    with folder_lock(folder):
        return _translate(root,data,cues,Path(folder),progress,call,cfg)


def _translate(root,data,cues,folder,progress,call,cfg):
    folder.mkdir(parents=True,exist_ok=True)
    cue_folder=folder/'cues-v2';cue_folder.mkdir(exist_ok=True)
    attempts_folder=folder/'attempts-v1';attempts_folder.mkdir(exist_ok=True)
    model=cfg.get('OPENAI_MODEL_NAME')
    keys=[cue_key(c,model) for c in cues]
    interactive=(folder/'interactive.json').exists()
    write_json(folder/'manifest.json',{'cues':cues,'model':model})
    texts={};failures={};attempts={};calls=0;repairs=0
    for i,key in enumerate(keys):
        old=read_json(attempts_folder/(key+'.json'))
        attempts[i]=max(0,int(old.get('attempts',0)))
        if isinstance(old.get('failure'),dict):failures[i]=old['failure']
    def save(i,text,preserve_as=None):
        texts[i]=text.strip();failures.pop(i,None)
        write_json(cue_folder/(keys[i]+'.json'),{'text':texts[i],'preserve_as':preserve_as})
    for offset in range(0,len(cues),16):
        batch=cues[offset:offset+16]
        digest=hashlib.sha256(json.dumps([batch,model],ensure_ascii=False).encode()).hexdigest()
        try:old=json.loads((folder/(digest+'.json')).read_text(encoding='utf-8'))
        except (OSError,ValueError):continue
        if isinstance(old,list) and len(old)==len(batch):
            for n,text in enumerate(old):
                if valid_text(batch[n]['text'],text):save(offset+n,text)
    for i,c in enumerate(cues):
        cached=read_json(cue_folder/(keys[i]+'.json'))
        text=cached.get('text')
        if valid_text(c['text'],text,cached.get('preserve_as')):texts[i]=text.strip()
        elif valid_text(c['text'],c['text']):save(i,c['text'])
        manual=read_json(folder/'manual-v1'/(keys[i]+'.json'))
        if human_text(c['text'],manual.get('text'),manual.get('keep_original')):
            texts[i]=manual['text'].strip();failures.pop(i,None)
    initial_cached=len(texts)
    def failure(i,code,reason,candidate=None):
        failures[i]=dict(id=i,source=cues[i]['text'][:120],code=code,reason=reason,attempts=attempts[i],candidate=candidate)
        write_json(attempts_folder/(keys[i]+'.json'),{'attempts':attempts[i],'failure':failures[i]})
    def details(ids):
        return dict(completed=len(texts),total=len(cues),failed_ids=ids,
                    failures=[failures[i] for i in ids],max_attempts=MAX_ATTEMPTS,
                    retry_requires_confirmation=any(attempts[i]>=MAX_ATTEMPTS for i in ids))
    def report(state='running',failed=None):
        ids=failed or []
        write_json(folder/'progress.json',dict(status=state,total=len(cues),completed=len(texts),reused=initial_cached,
            calls=calls,repairs=repairs,failed_ids=ids,failures=[failures[i] for i in ids if i in failures],at=time.time(),
            rows=[dict(c,id=i,text=texts.get(i,''),source=c['text'],candidate=failures.get(i,{}).get('candidate') or '',
                       reason=failures.get(i,{}).get('reason','') if i not in texts else '',
                       state='completed' if i in texts else 'problem' if i in failures else 'pending') for i,c in enumerate(cues)]))
    def stop(ids,code='translation_incomplete'):
        for i in ids:
            if i not in failures:failure(i,'attempt_limit','上次处理已中断或已达到重试上限')
        report('failed',ids)
        info=details(ids);blocked=info['retry_requires_confirmation']
        explanation='；'.join(f"第 {i+1} 段「{failures[i]['source'][:60]}」：{failures[i]['reason']}（{attempts[i]}/{MAX_ATTEMPTS} 次）" for i in ids[:3])
        if interactive:
            raise ProductionFailure('subtitle_review',f'本轮翻译已处理全文，保存了 {len(texts)}/{len(cues)} 段；{len(ids)} 段待人工处理。已完成译文保留，不自动重试问题段。',False,info)
        raise ProductionFailure(code,f'已保存 {len(texts)}/{len(cues)} 段。'+explanation+
            ('。已达上限，不会自动重试；可导入修正字幕，或排除原因后点击“重新尝试失败字幕（最多3次）”。' if blocked else '。已停止本轮请求，已完成内容保留。'),
            not blocked,info)
    def request(ids,repair=False):
        nonlocal calls,repairs
        check_pause(folder)
        blocked=[i for i in ids if attempts[i]>=MAX_ATTEMPTS]
        if blocked:stop(blocked)
        if repair:
            repairs+=1
            if repairs>12:
                for i in ids:
                    attempts[i]=MAX_ATTEMPTS;failure(i,'repair_limit','本轮自动补全已达总上限')
                stop(ids)
        feedback=[dict(failures[i]) for i in ids if i in failures]
        for i in ids:
            attempts[i]+=1;failure(i,'requesting','正在等待本批翻译返回')
        calls+=1;report()
        progress(f'字幕翻译 {len(texts)}/{len(cues)} · 第 {ids[0]+1} 段请求 {attempts[ids[0]]}/{MAX_ATTEMPTS} 次 · 复用 {initial_cached} 段')
        payload={'operation':'subtitle_translation','previous_failures':feedback,'items':[{'id':i,'text':cues[i]['text']} for i in ids],
                 'context_before':[c['text'] for c in cues[max(0,ids[0]-2):ids[0]]]}
        try:
            value=call(root,data,'逐项翻译为自然简体中文。固定 id，禁止合并、漏项或重复 id。网址、工具/软件/产品专名准确原样保留，不翻译字母或符号。若整项仅为专名，原样返回并标 preserve_as="proper_name"；完整英文句子不得标为专名。参考上下文但不要重复译入。仅返回 {"items":[{"id":原id,"text":"译文","preserve_as":null}]}。',payload)
        except ProductionFailure as exc:
            for i in ids:failure(i,exc.code,str(exc)[:160])
            report('failed',ids)
            if exc.code not in ('ai_format','ai_response'):
                if not exc.retryable:
                    raise ProductionFailure(exc.code,str(exc),False,details(ids)) from None
                raise ProductionFailure(exc.code,str(exc)+'；已停止请求，已完成译文保留。',False,details(ids)) from None
            return
        found={};kinds={}
        if isinstance(value,dict) and isinstance(value.get('items'),list):
            duplicates=set()
            for item in value['items']:
                if not isinstance(item,dict) or type(item.get('id')) is not int or item['id'] not in ids:continue
                i=item['id']
                if i in found:duplicates.add(i)
                found[i]=item.get('text');kinds[i]=item.get('preserve_as')
            for i in duplicates:
                found.pop(i,None);failure(i,'duplicate_id','AI 返回重复段号，无法可靠对应原文')
        elif isinstance(value,dict) and isinstance(value.get('texts'),list) and len(value['texts'])==len(ids):
            found=dict(zip(ids,value['texts']))
        for i,text in found.items():
            if valid_text(cues[i]['text'],text,kinds.get(i)):save(i,text,kinds.get(i))
            else:
                reason=('译文为空或格式不是文字' if not isinstance(text,str) or not text.strip() else
                        '译文超过 2000 字' if len(text)>2000 else '译文含乱码字符' if '\ufffd' in text else
                        '未译成中文，或专名未被明确识别；网址/工具名必须与原文一致')
                failure(i,'invalid_translation',reason,text if isinstance(text,str) else None)
        for i in ids:
            if i not in texts and failures.get(i,{}).get('code')=='requesting':
                failure(i,'missing_item','AI 返回缺少此段，或段号/返回格式不正确')
        report()
        check_pause(folder)
    try:
        for offset in range(0,len(cues),16):
            ids=[i for i in range(offset,min(offset+16,len(cues))) if i not in texts]
            if interactive:
                # Existing failures remain visible for manual review. Continue only untouched rows.
                pending=[i for i in ids if i not in failures and attempts[i]<MAX_ATTEMPTS]
                if pending:request(pending)
                continue
            if not ids:continue
            for attempt in range(MAX_ATTEMPTS):
                missing=[i for i in ids if i not in texts]
                if not missing:break
                request(missing,attempt>0)
            missing=[i for i in ids if i not in texts]
            if missing:stop(missing)
        missing=[i for i in range(len(cues)) if i not in texts]
        if missing:stop(missing)
    except ProductionFailure:
        report('failed',[i for i in range(len(cues)) if i not in texts]);raise
    check_pause(folder)
    report('completed');progress(f'字幕翻译已完成 {len(cues)}/{len(cues)}，正在保存')
    return [{**cue,'text':texts[i]} for i,cue in enumerate(cues)]
