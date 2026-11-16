"""Width-based portrait captions, measured with the same font used by libass."""
import re
from functools import lru_cache
from pathlib import Path
from PIL import ImageFont

VERSION = 2
FONT = Path(__file__).resolve().parent.parent / 'fonts' / 'NotoSansCJKsc-Regular.otf'


def layout(width, height, height_scaled_font):
    portrait = height > width
    font = (max(12, min(round(height_scaled_font * width / height / 1.5), round(width * .075)))
            if portrait else height_scaled_font)
    return {'version': VERSION, 'portrait': portrait, 'font': font,
            'margin_x': round(width * .10) if portrait else 60,
            'margin_v': round(height * (.12 if portrait else .07)),
            'outline': max(1, round(width / 360)) if portrait else 4,
            'shadow': 1, 'width': width, 'height': height}


@lru_cache(maxsize=32)
def font_at(size):
    return ImageFont.truetype(str(FONT), size)


def wrap(text, profile):
    """Explicit ASS breaks do not depend on libass's optional Unicode line breaker."""
    face = font_at(profile['font'])
    # Reserve space for synthetic bold, outlines and renderer rounding.
    available = (profile['width'] - 2 * profile['margin_x'] - 2 * profile['outline']) / 1.08
    tokens = re.findall(r'[A-Za-z0-9]+(?:[._+-][A-Za-z0-9]+)*|\s+|.', text)
    lines, line = [], ''
    for token in tokens:
        token = ' ' if token.isspace() else token
        pieces = list(token) if face.getlength(token) > available else [token]
        for piece in pieces:
            if line and face.getlength(line + piece) > available:
                lines.append(line.rstrip()); line = ''
            line = (line + piece).lstrip()
    if line:
        lines.append(line.rstrip())
    return lines


def events(cue, profile):
    text = re.sub(r'\s+', ' ', cue['text']).strip().replace('{', '').replace('}', '').replace('\\', '')
    if not profile['portrait']:
        return [{**cue, 'text': text}]
    lines = wrap(text, profile)
    pages = [lines[i:i+2] for i in range(0, len(lines), 2)]
    weights = [sum(len(line) for line in page) for page in pages]
    duration = cue['end'] - cue['start']
    if len(pages) > 1 and duration / len(pages) < .5:
        raise ValueError('竖屏字幕在当前时长内过长，请精简或校对该句后重新生成。')
    result, elapsed = [], 0
    for page, weight in zip(pages, weights):
        start = cue['start'] + duration * elapsed / sum(weights)
        elapsed += weight
        result.append({'start': start, 'end': cue['start'] + duration * elapsed / sum(weights),
                       'text': r'\N'.join(page)})
    return result


def current(saved, video):
    resolution = saved.get('resolution')
    if not resolution:
        ass = Path(video).parent / 'dub.zh.ass'
        if ass.is_file():
            text = ass.read_text(encoding='utf-8-sig')
            match = re.search(r'PlayResX:\s*(\d+)\s+PlayResY:\s*(\d+)', text)
            resolution = [int(v) for v in match.groups()] if match else None
    return (not resolution or resolution[1] <= resolution[0]
            or saved.get('caption_layout', {}).get('version') == VERSION)
