"""Local thumbnail language evidence and reproducible full-frame title layout."""
import csv
import hashlib
import io
import itertools
import os
import re
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageOps

from modules.video_summary import _read, _write

STYLE = 'thumbnail-overlay-v1'


def wrap_headline(text, preferred=None):
    """Keep the complete model headline; layout, not a model, owns line lengths."""
    text = text.strip()
    if not 4 <= len(text) <= 20 or '\n' in text:
        raise ValueError('封面标题需要4–20字，请压缩完整标题；不自动截断。')
    if isinstance(preferred, list) and all(isinstance(line,str) for line in preferred):
        prefix = []
        for line in preferred:
            if not text.startswith(''.join(prefix) + line):
                break
            prefix.append(line)
            if ''.join(prefix) == text:
                preferred = prefix
                break
    if (isinstance(preferred, list) and 2 <= len(preferred) <= 3
            and all(isinstance(line, str) and 2 <= len(line) <= 8 for line in preferred)
            and ''.join(preferred) == text):
        return preferred
    cuts = [i for i in range(2, len(text)-1)
            if not (text[i-1].isascii() and text[i-1].isalnum() and text[i].isascii() and text[i].isalnum())]
    candidates = []
    preferred_cuts = []
    if isinstance(preferred, list) and all(isinstance(line,str) for line in preferred) and ''.join(preferred) == text:
        preferred_cuts = [sum(map(len,preferred[:i])) for i in range(1,len(preferred))]
    for count in (2, 3):
        for positions in itertools.combinations(cuts, count-1):
            indexes = (0,) + positions + (len(text),)
            lines = [text[a:b] for a,b in zip(indexes,indexes[1:])]
            if all(2 <= len(line) <= 8 for line in lines):
                score = sum((len(line)-len(text)/count)**2 for line in lines) + count
                score += 10 * sum(cut not in positions for cut in preferred_cuts)
                candidates.append((score, lines))
    if not candidates:
        raise ValueError('封面含过长的英文词，请改用简短中文。')
    return min(candidates, key=lambda candidate: candidate[0])[1]


def classify(tsv):
    words = []
    for row in csv.DictReader(io.StringIO(tsv), delimiter='\t'):
        try:
            if int(row['level']) == 5 and float(row['conf']) >= 60 and row['text'].strip():
                words.append(row['text'].strip())
        except (KeyError, TypeError, ValueError):
            continue
    text = ' '.join(words)
    chinese = len(re.findall(r'[\u3400-\u9fff]', text))
    latin = len(re.findall(r'[A-Za-z]', text))
    # Short product marks (AE, AI, 4K...) alone are not English headline evidence.
    english_words = re.findall(r'\b[A-Za-z]{3,}\b', text)
    if chinese >= 4 or (chinese >= 2 and chinese / max(1, chinese + latin) >= .2):
        language = 'zh'
    elif chinese < 2 and latin >= 10 and len(english_words) >= 2:
        language = 'en'
    else:
        language = 'unknown'
    return {'language': language, 'text': text[:2000], 'engine': 'tesseract-eng-chi_sim-chi_tra'}


def run_tesseract(path, *options):
    # Windows Tesseract's filesystem layer rejects some Unicode absolute paths.
    # Feed the image through stdin and resolve tessdata from the bundled cwd.
    runtime = Path(__file__).resolve().parents[2] / 'runtime/tesseract'
    env = os.environ.copy()
    cwd = None
    if os.name == 'nt':
        env['TESSDATA_PREFIX'] = 'tessdata'
        cwd = runtime
    with Path(path).open('rb') as image:
        return subprocess.run(['tesseract', 'stdin', 'stdout', *options], stdin=image,
                              capture_output=True, text=True, encoding='utf-8',
                              timeout=35, check=True, cwd=cwd, env=env)


def inspect_thumbnail(path, cache):
    path = Path(path)
    key = hashlib.sha256(path.read_bytes()).hexdigest()
    saved = _read(cache)
    if saved.get('sha256') == key and saved.get('version') == 1:
        return saved
    try:
        result = run_tesseract(path, '-l', 'eng+chi_sim+chi_tra', '--psm', '11', 'tsv')
        saved = {**classify(result.stdout), 'sha256': key, 'version': 1}
        _write(cache, saved)
        return saved
    except (OSError, subprocess.SubprocessError):
        # Do not persist transient failures or claim the image is English from its video title.
        return {'language': 'unknown', 'text': '', 'sha256': key, 'error': '封面文字识别暂不可用，可手动生成。'}


def render(frame, lines, folder, profiles, font):
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    files, layouts = {}, {}
    with Image.open(frame) as source:
        source = source.convert('RGB')
        for profile, (w, h) in profiles.items():
            # A full-bleed copy fills other aspect ratios; the source file is never changed.
            canvas = ImageOps.fit(source, (w, h), Image.Resampling.LANCZOS)
            blur = round(min(w, h) * .012, 1)
            canvas = canvas.filter(ImageFilter.GaussianBlur(blur)).convert('RGBA')
            canvas = Image.alpha_composite(canvas, Image.new('RGBA', (w, h), (12, 15, 20, 178)))
            draw = ImageDraw.Draw(canvas)
            margin, max_width, max_height = int(w * .085), int(w * .83), int(h * .65)
            size = min(int(h * .205), int(w * .155))
            while size >= 16:
                face = font(size)
                boxes = [draw.textbbox((0, 0), line, font=face, stroke_width=1) for line in lines]
                gap = round(size * .23)
                block_height = sum(b[3] - b[1] for b in boxes) + gap * (len(lines) - 1)
                if max(b[2] - b[0] for b in boxes) <= max_width and block_height <= max_height:
                    break
                size -= 2
            if size < 16:
                raise ValueError('封面文字过长，请缩短后再排版。')
            y = (h - block_height) // 2
            text_bounds = []
            for i, (line, box) in enumerate(zip(lines, boxes)):
                color = '#ffdf61' if i == len(lines) - 1 else '#ffffff'
                draw.text((margin - box[0], y - box[1]), line, font=face, fill=color,
                          stroke_width=1, stroke_fill=color)
                text_bounds.append([margin, y, margin + box[2] - box[0], y + box[3] - box[1]])
                y += box[3] - box[1] + gap
            output = folder / (profile + '.jpg')
            canvas.convert('RGB').save(output, quality=94, subsampling=0)
            files[profile] = str(output)
            layouts[profile] = {'size': [w, h], 'font_size': size, 'blur_radius': blur,
                                'overlay_alpha': 178, 'text_bounds': text_bounds}
    _write(folder / 'cover-bundle.json', files)
    _write(folder / 'layout.json', {'style': STYLE, 'source_sha256': hashlib.sha256(Path(frame).read_bytes()).hexdigest(),
                                 'lines': lines, 'profiles': layouts})
    return files
