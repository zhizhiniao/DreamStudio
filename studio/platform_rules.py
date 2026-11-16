"""Shared title adaptation and validation for generation, review and submission."""
import re
import unicodedata
from vendor.publisher.caption_rules import KUAISHOU_LIMIT, kuaishou_caption, kuaishou_issues, kuaishou_count

KUAISHOU_DESCRIPTION_TARGET = 400


def compact_description(text, budget, measure=len):
    """Extract complete lead sentences across paragraphs before adding details."""
    if measure(text)<=budget:return text
    paragraphs=[p.strip() for p in re.split(r'\n\s*\n',text) if p.strip()]
    groups=[re.findall(r'[^。！？!?\n]+[。！？!?]?|[^\n]+$',p) for p in paragraphs]
    chosen={}
    def render(parts):
        return '\n\n'.join(''.join(parts[k] for k in sorted(parts) if k[0]==i) for i in range(len(groups)) if any(k[0]==i for k in parts))
    for depth in range(max((len(g) for g in groups),default=0)):
        for i,group in enumerate(groups):
            if depth>=len(group):continue
            sentence=group[depth].strip()
            candidate={**chosen,(i,depth):sentence}
            rendered=render(candidate)
            if measure(rendered)<=budget:chosen=candidate
    if chosen:return render(chosen)
    # A single oversized sentence: leave an explicit ellipsis, never a broken Latin word.
    tokens=re.findall(r'[A-Za-z0-9]+|.',text.replace('\n',' '))
    result=''
    for token in tokens:
        if measure(result+token)>max(0,budget-1):break
        result+=token
    return result.rstrip()+'…' if result else ''


def channels_short_title(title):
    # Keep words and the punctuation explicitly allowed by the form. Emoji and
    # exclamation marks are not accepted in the video-account short-title field.
    allowed='《》「」『』“”‘’\"\'：:+＋?？%％℃'
    text=''.join(c if c.isalnum() or c.isspace() or c in allowed else ' '
                 for c in unicodedata.normalize('NFKC',str(title)))
    return compact_title(re.sub(r'\s+',' ',text).strip(), 16)


LIMITS = {'douyin':30, 'kuaishou':30, 'xiaohongshu':20, 'bilibili':80}


def compact_title(text, limit):
    text = text.strip()
    if len(text)<=limit:return text
    # Remove redundant wording first, retaining the topic and whole Latin words.
    for old,new in [('After Effects','AE'),('最流行','常用'),('专门讲解','讲解'),('这是一个',''),('三个','3个')]:
        text=text.replace(old,new)
    if len(text)<=limit:return text
    # Enumeration introductions are useful standalone summaries; keep the count/topic.
    match=re.match(r'^(.{2,12}?(?:转场|技巧|方法|教程|步骤))',text)
    if match and len(match[1])<=limit:return match[1]
    tokens=re.findall(r'[A-Za-z0-9]+(?:[+.#-][A-Za-z0-9]+)*|.',text)
    result=''
    for token in tokens:
        if len(result+token)>limit:break
        result+=token
    return result.rstrip(' ，、：:;；-')


def adapt_draft(draft, reset_short=False):
    """Mutates only platform copy. Final submission must validate, never rewrite."""
    title=draft.get('title','')
    if not isinstance(title,str):raise ValueError('标题须为文字')
    before=(title,draft.get('short_title'),draft.get('body'),draft.get('tags'))
    if draft['platform']=='wechat_channels':
        if reset_short or 'short_title' not in draft:
            draft['short_title']=channels_short_title(title)
    else:
        draft['title']=compact_title(title,LIMITS.get(draft['platform'],100))
    if draft['platform']=='kuaishou':
        body=draft.get('body','');tags=draft.get('tags',[])
        if not isinstance(body,str) or not isinstance(tags,list) or any(not isinstance(t,str) for t in tags):
            raise ValueError('简介或话题格式无效')
        draft['tags']=tags[:3]
        # Reserve the actual title, hashtags and both separator newlines.
        overhead=kuaishou_count(kuaishou_caption(draft['title'],'',draft['tags']))+1
        budget=min(KUAISHOU_DESCRIPTION_TARGET,max(0,KUAISHOU_LIMIT-overhead))
        if kuaishou_count(body)>budget:
            draft.setdefault('original_body',body)
            draft['body']=compact_description(body,budget,measure=kuaishou_count)
    changed=before!=(draft.get('title'),draft.get('short_title'),draft.get('body'),draft.get('tags'))
    if changed:
        draft.setdefault('original_title',title)
        draft.update(reviewed=False,status='draft',batch_repair_note='已自动适配平台标题，请核对后确认')
        if draft['platform']=='kuaishou':
            draft['batch_repair_note']='已精简快手简介，请重新确认'
    return changed


def title_issues(draft):
    title=draft.get('title','')
    if not isinstance(title,str) or not title.strip():return ['标题不能为空']
    if draft['platform']=='wechat_channels':
        short=draft.get('short_title','')
        if not isinstance(short,str) or not short.strip() or len(short)>16:
            return ['视频号短标题不能为空且最多 16 字']
        if channels_short_title(short)!=short:return ['视频号短标题含不支持的字符，请修改']
        return []
    limit=LIMITS.get(draft['platform'],100)
    errors=[f'标题不能为空且最多 {limit} 字'] if len(title)>limit else []
    if draft['platform']=='kuaishou':
        errors.extend(kuaishou_issues(title,draft.get('body',''),draft.get('tags',[])))
    return errors
