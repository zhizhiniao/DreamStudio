"""Local visual diversity sampling. Source images are never modified by crops."""
import hashlib
import json
import threading
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import numpy as np
from PIL import Image
from . import media

LOCK=threading.Lock()


def with_original(rows, asset, task=None):
    rows=[r for r in rows if r['id']!='original-cover']
    path=Path((task or {}).get('original_thumbnail') or asset.get('original_thumbnail') or '__missing__')
    if path.is_file():
        rows.insert(0,{'id':'original-cover','kind':'original-cover','seconds':0,'file':str(path),'label':'原始封面'})
    return rows


def defaults(gallery,duration):
    middle=[f for f in gallery if duration*.05<=f['seconds']<=duration*.9]
    return [f['id'] for f in (middle or gallery)[:3]]


def diverse(rows, limit=12):
    features=[]
    for row in rows:
        with Image.open(row['file']) as image:
            pixels=np.asarray(image.convert('RGB').resize((48,27)),dtype=float)/255
        features.append(pixels)
    eligible=[i for i,p in enumerate(features) if .025<float(p.mean())<.975]
    if not eligible:eligible=list(range(len(rows)))
    if not eligible:raise ValueError('视频没有可用的封面帧。')
    first=max(eligible,key=lambda i:float(features[i].std()))
    selected=[first]
    while len(selected)<min(limit,len(eligible)):
        distances={i:min(float(np.abs(features[i]-features[j]).mean()) for j in selected) for i in eligible if i not in selected}
        if not distances:break
        best=max(distances,key=distances.get)
        if distances[best]<.045:break
        selected.append(best)
    return [rows[i] for i in selected]


def ensure(runtime, asset, progress):
    source=Path(asset['path']);stat=source.stat()
    stamp=hashlib.sha256(f'{source.resolve()}:{stat.st_size}:{stat.st_mtime_ns}:v1'.encode()).hexdigest()[:20]
    folder=Path(asset['project_path'])/'03_制作/cover-frames'/stamp
    manifest=folder/'frames.json'
    with LOCK:
        if manifest.is_file():
            rows=json.loads(manifest.read_text(encoding='utf-8'))
            if rows and all(Path(r['file']).is_file() for r in rows):return with_original(rows,asset)
        folder.mkdir(parents=True,exist_ok=True)
        duration=float(asset['duration']);count=min(36,max(2,int(duration*2)))
        end=max(duration*.5,duration-.3)
        times=[duration*.03+(end-duration*.03)*i/(count-1) for i in range(count)]
        def extract(pair):
            i,seconds=pair;path=folder/f'{i:02}.jpg'
            if not path.is_file():
                media.run([Path(runtime)/'bin/ffmpeg.exe','-y','-ss',str(seconds),'-i',source,'-frames:v','1','-vf','scale=1280:-2,format=yuvj420p','-threads','1',path],120)
            return {'id':f'{stamp}-{i:02}','seconds':round(seconds,2),'file':str(path)}
        progress(f'正在采样 {count} 个视频画面，并筛选差异明显的帧')
        with ThreadPoolExecutor(max_workers=3) as pool:rows=list(pool.map(extract,enumerate(times)))
        selected=diverse(rows)
        manifest.write_text(json.dumps(selected,ensure_ascii=False),encoding='utf-8')
        return with_original(selected,asset)


def validate(selection, crops, gallery):
    available={row['id'] for row in gallery}
    if not isinstance(selection,list) or not 1<=len(selection)<=10 or any(not isinstance(i,str) or i not in available for i in selection) or len(set(selection))!=len(selection):
        raise ValueError('请从候选帧中勾选 1–10 张不同画面。')
    if not isinstance(crops,dict):raise ValueError('裁切参数无效。')
    import math
    for key,box in crops.items():
        if key not in available or not isinstance(box,list) or len(box)!=4 or any(type(v) not in (int,float) or not math.isfinite(v) for v in box):raise ValueError('裁切参数无效。')
        x,y,r,b=box
        if not (0<=x<r<=1 and 0<=y<b<=1) or r-x<.01 or b-y<.01:raise ValueError('裁切区域须在画面内，宽高至少保留 1%。')
