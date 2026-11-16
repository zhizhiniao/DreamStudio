"""Compare visible content; never treat upload controls or toasts as proof."""
import io
import re
from PIL import Image, ImageChops, ImageOps, ImageStat


def caption_key(text):
    # Browser rich-text blocks may render CRLF, NBSP or double paragraph breaks.
    # Keep word boundaries, punctuation and every visible character intact.
    return re.sub(r'\s+', ' ', text.replace('\u200b', '').replace('\ufeff', '')).strip()


def cover_matches(path, screenshot):
    with Image.open(path) as source, Image.open(io.BytesIO(screenshot)) as observed:
        source = source.convert('RGB')
        observed = observed.convert('RGB')
        if min(observed.size) < 32:
            return False
        # The platform may contain or crop the image. Compare both at its size.
        candidates = [ImageOps.fit(source, observed.size),
                      ImageOps.pad(source, observed.size, color='black')]
        actual = observed.resize((96, 96))
        errors = [sum(ImageStat.Stat(ImageChops.difference(actual, image.resize((96, 96)))).mean) / 3
                  for image in candidates]
        return min(errors) <= 24
