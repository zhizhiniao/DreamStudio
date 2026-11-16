"""Non-destructive normalized crops and contact-sheet input for cover renderers."""
import math
import hashlib
from pathlib import Path
from PIL import Image, ImageOps


def prepare(frames, crops, folder):
    if not isinstance(crops, dict):
        raise ValueError('裁剪参数格式无效。')
    folder = Path(folder); folder.mkdir(parents=True, exist_ok=True)
    files, records = [], []
    for frame in frames:
        key = str(frame['id']); path = Path(frame['file'])
        box = crops.get(key, [0, 0, 1, 1])
        if not isinstance(box, list) or len(box) != 4 or any(type(v) not in (int,float) or not math.isfinite(v) for v in box):
            raise ValueError('裁剪范围必须为四个有效数字。')
        x,y,r,b = box
        if not (0 <= x < r <= 1 and 0 <= y < b <= 1):
            raise ValueError('裁剪范围超出图片或为空，请重新裁剪。')
        with Image.open(path) as image:
            image = image.convert('RGB'); w,h = image.size
            bounds = [round(x*w), round(y*h), round(r*w), round(b*h)]
            if bounds[2]-bounds[0] < 2 or bounds[3]-bounds[1] < 2:
                raise ValueError('裁剪区域过小，请至少保留 2×2 像素。')
            output = folder / (key+'.png'); image.crop(bounds).save(output)
        files.append(output)
        records.append({'id':frame['id'],'original':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'crop':box,'pixels':bounds})
    return files, records


def mosaic(files, output):
    cols = math.ceil(math.sqrt(len(files)*16/9)); rows = math.ceil(len(files)/cols)
    cell_w,cell_h = 320,240
    canvas = Image.new('RGB',(cols*cell_w,rows*cell_h),'#18202b')
    for i,file in enumerate(files):
        with Image.open(file) as source:
            image=ImageOps.contain(source.convert('RGB'),(cell_w-8,cell_h-8),Image.Resampling.LANCZOS)
            canvas.paste(image,((i%cols)*cell_w+(cell_w-image.width)//2,(i//cols)*cell_h+(cell_h-image.height)//2))
    canvas.save(output)
    return output
