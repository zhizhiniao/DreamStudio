"""Deterministic torn-paper cover candidate; never generates or rewrites source imagery."""
import hashlib
import random
import math
import re
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageOps
from modules.video_summary import _write

STYLE = 'torn-paper-v1'
ROOT = Path(__file__).resolve().parent.parent
FONT = ROOT / 'static/cover-styles/fonts/smiley-sans/SmileySans-Oblique.ttf'
FONT_SHA = 'b447d7e781f08bc95c4c9f23ba71ed2b8ebb639aa7184485c71c4ca5afcd25c4'
COLORS = {'blue': '#0754D8', 'paper': '#F4F0E5', 'black': '#101010', 'red': '#E52620', 'yellow': '#FFD928'}


def face(size):
    if not FONT.is_file() or hashlib.sha256(FONT.read_bytes()).hexdigest() != FONT_SHA:
        raise ValueError('得意黑字体缺失或版本不一致，请修复字体后重试。')
    return ImageFont.truetype(str(FONT), size)


def check_glyphs(text):
    font = face(64)
    missing = bytes(font.getmask(chr(0x10ffff)))
    bad = sorted({c for c in text if not c.isspace() and bytes(font.getmask(c)) == missing})
    if bad:
        raise ValueError('得意黑字体缺少字符：' + ''.join(bad) + '，请调整文字。')


def paper(size, color, seed):
    w, h = size
    rng = np.random.default_rng(seed)
    rgb = np.array(tuple(bytes.fromhex(color.lstrip('#'))), dtype=np.float32)
    noise = rng.normal(0, 3.0, (h, w, 1))
    coarse = Image.fromarray(rng.integers(0, 255, (max(2,h//24), max(2,w//24)), dtype=np.uint8)).resize(size, Image.Resampling.BICUBIC)
    noise += (np.asarray(coarse, dtype=np.float32)[:,:,None] - 128) / 20
    return Image.fromarray(np.clip(rgb + noise, 0, 255).astype(np.uint8)).convert('RGBA')


def tear(size, seed):
    w, h = size
    rng = random.Random(seed)
    step, depth = max(4, w//95), max(3, min(w,h)//38)
    pts = [(x, rng.randrange(depth)) for x in range(0,w,step)]
    pts += [(w-1-rng.randrange(depth), y) for y in range(0,h,step)]
    pts += [(x, h-1-rng.randrange(depth)) for x in range(w-1,-1,-step)]
    pts += [(rng.randrange(depth), y) for y in range(h-1,-1,-step)]
    mask = Image.new('L',size); ImageDraw.Draw(mask).polygon(pts,fill=255)
    return mask


def paste_paper(canvas, box, color, seed):
    x,y,w,h = map(int,box)
    sheet = paper((w,h),color,seed); mask = tear((w,h),seed); sheet.putalpha(mask)
    shadow = Image.new('RGBA',(w,h),(0,0,0,55)); shadow.putalpha(mask.point(lambda v: v//5))
    canvas.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(4)),(x+4,y+7))
    canvas.alpha_composite(sheet,(x,y))


def text_row(canvas, text, box, color, maximum):
    x,y,w,h = map(int,box)
    draw = ImageDraw.Draw(canvas)
    for size in range(int(maximum), 15, -2):
        font = face(size); stroke = max(1, size//90)
        b = draw.textbbox((0,0),text,font=font,stroke_width=stroke)
        if b[2]-b[0] <= w and b[3]-b[1] <= h:
            top = y+(h-(b[3]-b[1]))//2
            draw.text((x-b[0],top-b[1]),text,font=font,fill=color,stroke_width=stroke,stroke_fill=color)
            return {'text':text,'font_size':size,'bounds':[x,top,x+b[2]-b[0],top+b[3]-b[1]],'color':color}
    raise ValueError('文字无法在 A 风格安全区内清晰排版，请缩短标题。')


def focus_image(source):
    """Conservative white-mat removal; never alter the source file."""
    a=np.asarray(source.convert('RGB')); h,w=a.shape[:2]
    dark=a.max(2)<180
    ys=np.where(dark.mean(1)>.38)[0]
    if not len(ys): return source, [0,0,w,h]
    y,b=int(ys[0]),int(ys[-1]+1)
    xs=np.where(dark[y:b].mean(0)>.5)[0]
    if not len(xs): return source, [0,0,w,h]
    x,r=int(xs[0]),int(xs[-1]+1)
    area=(r-x)*(b-y)/(w*h)
    outside=np.ones((h,w),bool); outside[y:b,x:r]=False
    # Require four actual margins and a nearly white surround, not a light scene.
    if not (.25<area<.85 and min(x/w,(w-r)/w,y/h,(h-b)/h)>.025
            and (a.min(2)[outside]>205).mean()>.94):
        return source,[0,0,w,h]
    pad=max(2,round(min(w,h)*.003))
    box=[max(0,x-pad),max(0,y-pad),min(w,r+pad),min(h,b+pad)]
    return source.crop(box),box


def render(frames, lines, folder, profiles, category='工具拆解', emphasis=1, emphasis_color='#E52620', auto_focus=None):
    if not re.fullmatch(r'#[0-9a-fA-F]{6}', emphasis_color):
        raise ValueError('强调色须为 #RRGGBB 格式。')
    if not frames or not 1<=len(lines)<=3 or not 0<=emphasis<len(lines):
        raise ValueError('A 风格需要素材、1–3 行文字及有效的强调行。')
    if not category.strip() or len(category)>6: raise ValueError('栏目标签请填写 1–6 字。')
    check_glyphs(''.join(lines)+category)
    folder=Path(folder);folder.mkdir(parents=True,exist_ok=True)
    images=[];sources=[]
    for i,path in enumerate(frames):
        with Image.open(path) as im: source=im.convert('RGB')
        focused,box=focus_image(source) if auto_focus is None or auto_focus[i] else (source,[0,0,*source.size])
        images.append(focused)
        sources.append({'file':str(path),'sha256':hashlib.sha256(Path(path).read_bytes()).hexdigest(),'focus_pixels':box})
    files={};layouts={}
    for profile,(w,h) in profiles.items():
        canvas=paper((w,h),COLORS['blue'],120);draw=ImageDraw.Draw(canvas)
        for x in range(0,w,max(10,w//65)):
            for y in range(0,h,max(10,w//65)):
                if x>w*.88 or x<w*.08:
                    r=max(1,w//420);draw.ellipse((x,y,x+r*2,y+r*2),fill=(9,20,43,155))
        portrait=h>w;n=len(images);bounds=[]
        # Two staggered banks surround the foreground title. Stable randomness avoids reshuffling on retry.
        if portrait:
            slots=[(.27,.18,.65,7),(.75,.29,.65,-7),(.24,.60,.65,-5),(.77,.70,.65,6),(.27,.86,.65,8),(.77,.92,.62,-6)]
            tx,tw=w*.035,w*.93; rh=min(h*.14,w*.19)
            title_y=h*.365
        else:
            slots=[(.14,.17,.43,7),(.52,.16,.44,-6),(.89,.19,.43,-8),(.12,.82,.45,-6),(.51,.86,.46,6),(.90,.82,.44,-5)]
            tx,tw=w*.035,w*.93;rh=min(h*.23,w*.14)
            title_y=h*.31
        if n==1: slots=[(.5,.5,1.05,-4)]
        elif n==2: slots=[(.3,.24,.85,6),(.72,.81,.85,-6)]
        elif n==3: slots=[(.22,.20,.72,6),(.8,.24,.67,-7),(.5,.86,.90,4)]
        elif n>6:
            cols=2 if portrait else 3;rows=math.ceil(n/cols)
            rng=random.Random(73)
            slots=[((i%cols+.5)/cols+rng.uniform(-.05,.05),(i//cols+.5)/rows+rng.uniform(-.025,.025),1.35/cols,rng.choice([-8,-5,5,8])) for i in range(n)]
        for i,source in enumerate(images):
            cx,cy,scale,angle=slots[i];cw=max(20,int(w*scale));ch=round(cw*source.height/source.width)
            # Very tall images remain fully visible inside a suitably proportioned card.
            if ch>h*.67: ch=int(h*.67);cw=round(ch*source.width/source.height)
            border=max(3,w//110);card=paper((cw+2*border,ch+2*border),COLORS['paper'],80+i)
            card.paste(source.resize((cw,ch),Image.Resampling.LANCZOS),(border,border))
            card.putalpha(tear(card.size,80+i));card=card.rotate(angle,Image.Resampling.BICUBIC,expand=True)
            x,y=int(cx*w-card.width/2),int(cy*h-card.height/2)
            shadow=Image.new('RGBA',card.size);shadow.putalpha(card.getchannel('A').point(lambda v:v//3))
            canvas.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(6)),(x+4,y+8));canvas.alpha_composite(card,(x,y))
            bounds.append([x,y,x+card.width,y+card.height])
        # Draw the opaque title AFTER every photo, with compact content-driven rows.
        measured=[]
        for i,line in enumerate(lines):
            for size in range(int(rh),15,-2):
                font=face(size);stroke=max(1,size//90)
                b=ImageDraw.Draw(canvas).textbbox((0,0),line,font=font,stroke_width=stroke)
                if b[2]-b[0]<=tw:break
            measured.append((size,b[3]-b[1]))
        gap=max(6,int(h*.009));padding=int(w*.025)
        total=sum(height for _,height in measured)+gap*(len(lines)-1)
        title_h=total+padding*2+14
        title_y=min(title_y,h-title_h-h*.07)
        title_box=(0,title_y,w,title_h)
        paste_paper(canvas,title_box,COLORS['paper'],31)
        rows=[];y=title_y+padding
        for i,(line,(size,height)) in enumerate(zip(lines,measured)):
            rows.append(text_row(canvas,line,(tx,y,tw,height+2),emphasis_color if i==emphasis else COLORS['black'],size))
            y+=height+gap
        draw=ImageDraw.Draw(canvas);draw.line((int(tx),int(y+4),int(tx+tw*.7),int(y)),fill=COLORS['yellow'],width=max(4,w//150))
        labelbox=(tx,title_y-h*.05,w*.24,h*.055)
        paste_paper(canvas,labelbox,COLORS['yellow'],22)
        label=text_row(canvas,category,(tx+w*.01,labelbox[1]+2,labelbox[2]-w*.02,labelbox[3]-4),COLORS['black'],h*.045)
        out=folder/(profile+'.jpg');canvas.convert('RGB').save(out,quality=95,subsampling=0)
        files[profile]=str(out);layouts[profile]={'size':[w,h],'rows':rows,'label':label,'card_bounds':bounds,'title_box':title_box,'layer_order':['photos','title','label']}
    _write(folder/'cover-bundle.json',files)
    _write(folder/'layout.json',{'style':STYLE,'renderer_version':3,'font_family':'Smiley Sans','font_version':'2.0.1','font_sha256':FONT_SHA,'palette':COLORS,'texture_seed':120,'lines':lines,'category':category,'emphasis':emphasis,'emphasis_color':emphasis_color,'sources':sources,'profiles':layouts})
    return files
