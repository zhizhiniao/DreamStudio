"""Task-owned chapter overlays for the normal FFmpeg render and frame preview."""
import math
import re
from pathlib import Path
from PIL import ImageFont

STYLES = ('dash', 'ribbon', 'focus', 'off')
FONT = 'Noto Sans CJK SC'


def basis(task):
    return [task.get('asset_id'), task['mode'], task.get('start', 0), task.get('duration'),
            task.get('subtitle_review_key') if task['mode'] == 'subtitles' else task.get('speech_review_hash')]


def seconds(value):
    try:
        if isinstance(value, bool):
            raise ValueError()
        parts = str(value).strip().split(':')
        if len(parts) > 3 or any(not re.fullmatch(r'\d+(?:\.\d+)?', p) for p in parts):
            raise ValueError()
        if any(float(p) >= 60 for p in parts[1:]):
            raise ValueError()
        t = sum(float(p) * 60**i for i, p in enumerate(reversed(parts)))
        if not math.isfinite(t):
            raise ValueError()
        return round(t, 2)
    except (ValueError, TypeError, OverflowError):
        raise ValueError('章节起点请输入秒数或 分:秒。') from None


def checked(task, values):
    style = values.get('style')
    if style not in STYLES:
        raise ValueError('请选择一种顶部章节样式。')
    if style == 'off':
        return {'style':style}
    rows = values.get('chapters')
    if not isinstance(rows, list) or not 1 <= len(rows) <= 8:
        raise ValueError('请输入 1–8 章，或选择“不显示”。')
    chapters = []
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get('title'),str):
            raise ValueError('每章需要起点和标题。')
        title = row['title'].strip()
        if not 1 <= len(title) <= 24 or re.search(r'[\\{}\x00-\x1f]',title):
            raise ValueError('章节标题限 1–24 字，不支持反斜杠、花括号或换行。')
        start = seconds(row.get('start'))
        if (not chapters and start != 0) or (chapters and start <= chapters[-1]['start']) or start >= task['duration']:
            raise ValueError('首章从 00:00 开始，其余起点须依次递增并早于片尾。')
        chapters.append(dict(start=start,title=title))
    return {'style':style, 'chapters':chapters, 'basis':basis(task)}


def for_render(task):
    config = task.get('chapter_navigation', {})
    if task.get('mode') == 'original' or config.get('style','off') == 'off':
        return None
    if config.get('basis') != basis(task):
        raise ValueError('片段或审核内容已改变，请在章节导航中复核并保存。')
    return checked(task, config)


def stamp(t):
    total = round(t * 100)
    h, rem = divmod(total, 360000)
    m, rem = divmod(rem, 6000)
    return f'{h}:{m:02}:{rem//100:02}.{rem%100:02}'


def filter_path(path):
    return str(path).replace('\\','/').replace(':',r'\:').replace("'",r"\'")


def video_filter(folder, root, config, width, height, duration):
    """Use true clip time for progress. Reserve enough width for each visible title."""
    rows = config['chapters']
    style = config['style']
    starts = [row['start'] for row in rows]
    ends = starts[1:] + [duration]
    font = max(13, round(min(width * .023, height * .036)))
    face = ImageFont.truetype(str(Path(root)/'vendor/fonts/NotoSansCJKsc-Regular.otf'), font)
    margin = max(8,round(width*.027))
    gap = max(2,round(width*.006))
    top = max(5,round(height*.022))
    h = round(font*1.65)
    groups = []
    if style != 'focus':
        group, used = [], 0
        for i,row in enumerate(rows):
            need = math.ceil(face.getlength(row['title'])*1.16) + gap*2
            if need > width-2*margin:
                raise ValueError(f'第 {i+1} 章标题对当前画幅太长，请精简。')
            if group and used + gap + need > width-2*margin:
                groups.append(group)
                group, used = [], 0
            group.append((i,need))
            used += need + gap
        groups.append(group)
        if top + len(groups)*h > height*.35:
            raise ValueError('章节标题占用画面过高，请减少或精简标题。')
    header = f'''[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 2
ScaledBorderAndShadow: yes
[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Nav,{FONT},{font},&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1
[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
'''
    lines = [header]

    def event(layer, a, b, text):
        if stamp(a) != stamp(b):
            lines.append(f'Dialogue: {layer},{stamp(a)},{stamp(b)},Nav,,0,0,0,,{text}\n')

    def rect(x,y,w,h,color,alpha='00',extra=''):
        return (rf'{{\an7\pos(0,0)\bord0\shad0\1c&H{color}&\1a&H{alpha}&{extra}\p1}}'
                f'm {x} {y} l {x+w} {y} {x+w} {y+h} {x} {y+h}' + r'{\p0}')

    def label(x,y,text,color,bold=False,center=False):
        align = 8 if center else 7
        return rf'{{\an{align}\pos({x},{y})\bord0\shad0\b{int(bold)}\1c&H{color}&}}{text}'

    def track(i,x,y,w,thickness,color):
        a,b = starts[i],ends[i]
        if style != 'ribbon':
            event(2,0,duration,rect(x,y,w,thickness,'736A60','55'))
        ms = max(1, round((b-a)*1000)-30)
        clip = rf'\clip({x},{y},{x},{y+thickness})\t(0,{ms},\clip({x},{y},{x+w},{y+thickness}))'
        event(3,a,b,rect(x,y,w,thickness,color,extra=clip))
        if b < duration:
            event(3,b,duration,rect(x,y,w,thickness,color))

    if style == 'focus':
        label_width = math.ceil(max(face.getlength(row['title']) for row in rows)*1.14)
        box = max(round(width*.26),label_width+font*5)
        if box > width-2*margin:
            raise ValueError('当前章名超过浮签宽度，请精简标题。')
        x1,y1,x2,y2 = margin,top,margin+box,top+2*h
        radius = max(4, round(font*.45))
        shape = (f'm {x1+radius} {y1} l {x2-radius} {y1} b {x2} {y1} {x2} {y1} {x2} {y1+radius} '
                 f'l {x2} {y2-radius} b {x2} {y2} {x2} {y2} {x2-radius} {y2} l {x1+radius} {y2} '
                 f'b {x1} {y2} {x1} {y2} {x1} {y2-radius} l {x1} {y1+radius} b {x1} {y1} {x1} {y1} {x1+radius} {y1}')
        event(0,0,duration,r'{\an7\pos(0,0)\bord1\3c&H726E64&\3a&H88&\1c&H171B18&\1a&H32&\p1}'+shape+r'{\p0}')
        x = margin+gap*2
        for i,row in enumerate(rows):
            event(4,starts[i],ends[i],label(x,top+gap,f'{i+1:02}/{len(rows):02}   '+row['title'],'FFFFFF',True))
        free = box-4*gap-(len(rows)-1)*gap
        for i in range(len(rows)):
            w = max(1,round(free*(ends[i]-starts[i])/duration))
            track(i,x,top+h+gap,w,max(2,font//8),'8EB3E3')
            x += w+gap
    else:
        if style == 'dash':
            shade = top + len(groups)*h + gap*2
            for y in range(0,shade,2):
                alpha = round(62 + 191*(y/shade)**1.7)
                event(0,0,duration,rect(0,y,width,2,'100E0D',f'{alpha:02X}'))
        for band, group in enumerate(groups):
            if style == 'ribbon':
                x, pad, y = 0, 1, band*h
            else:
                x, pad, y = margin, gap, top+band*h
            span = (width if style=='ribbon' else width-2*margin)-pad*(len(group)-1)
            extra = span-sum(w for _,w in group)
            weight = sum(ends[i]-starts[i] for i,_ in group)
            last = (width if style=='ribbon' else width-margin)
            for j,(i,need) in enumerate(group):
                w = last-x if j==len(group)-1 else need+int(extra*(ends[i]-starts[i])/weight)
                ribbon = style=='ribbon'
                if ribbon:
                    event(1,0,duration,rect(x,y,w,h,'D4E4ED'))
                    track(i,x,y,w,h,'7090C0')
                else:
                    track(i,x,y+h-gap,w,max(2,font//9),'8EB3E3')
                tx = x+w//2 if ribbon else x
                ty = y+max(0,(h-font)//3)
                event(4,0,duration,label(tx,ty,rows[i]['title'],'26364D' if ribbon else 'C7C7C7',center=ribbon))
                event(5,starts[i],ends[i],label(tx,ty,rows[i]['title'],'182130' if ribbon else 'FFFFFF',True,ribbon))
                x += w+pad
    path = Path(folder)/'chapters.ass'
    path.write_text(''.join(lines),encoding='utf-8-sig')
    return f"ass=filename='{filter_path(path)}':fontsdir='{filter_path(Path(root)/'vendor/fonts')}'"
