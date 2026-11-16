"""Conservative cue normalization and resumable translation drafts."""
import hashlib
import json
import re
from pathlib import Path

_EVENTS = {'music': '音乐', 'applause': '掌声', 'laughter': '笑声',
           'laughing': '笑声', 'silence': '静音', 'cheering': '欢呼', 'sighs': '叹气'}
_TERMS = re.compile(r'\b(?:Adobe After Effects|After Effects|Premiere Pro|DaVinci Resolve|'
                    r'ChatGPT|Photoshop|Illustrator|Blender|SaaS|SAS|GPU|CPU|UI|UX|RGB|PNG|MP4)\b', re.I)


def sound_cue(text, target):
    if not str(target).lower().startswith(('zh', 'chinese')):
        return None
    match = re.fullmatch(r'\s*(?:>>\s*)?\[\s*([a-z]+)\s*\]\s*', text, re.I)
    label = _EVENTS.get(match.group(1).lower()) if match else None
    return f'[{label}]' if label else None


def without_source_terms(source, translated):
    """Discount only known names actually present in the source, never prose."""
    names = {m.group().casefold() for m in _TERMS.finditer(source)}
    return _TERMS.sub(lambda m: '' if m.group().casefold() in names else m.group(), translated)


def localize_sound_events(text, target):
    if not str(target).lower().startswith(('zh', 'chinese')):
        return text
    return re.sub(r'\[\s*([a-z]+)\s*\]',
                  lambda m: f'[{_EVENTS[m.group(1).lower()]}]' if m.group(1).lower() in _EVENTS else m.group(),
                  text, flags=re.I)


def without_sound_events(text):
    labels = set(_EVENTS) | set(_EVENTS.values())
    return re.sub(r'\[\s*([^\]]+)\s*\]',
                  lambda m: '' if m.group(1).strip().lower() in labels else m.group(), text)


class TranslationDraft:
    def __init__(self, items, output, config, model_config):
        signature = [2, [(i.start_time, i.end_time, i.source_text) for i in items],
                     config.target_language, {k: model_config.get(k) for k in
                     ('OPENAI_MODEL_NAME', 'OPENAI_BASE_URL', 'PROMPT_MODE', 'PROMPT_TEXT',
                      'PROMPT_STRICT_MODE', 'PROMPT_STRICT_TEXT')}]
        key = hashlib.sha256(json.dumps(signature, ensure_ascii=False).encode()).hexdigest()
        self.path = Path(output).parent / '.subtitle-translation' / f'{key}.json'
        self.items = items

    def restore(self):
        try:
            data = json.loads(self.path.read_text(encoding='utf-8'))
            rows = data.get('translations', [])
            if len(rows) == len(self.items) and all(isinstance(x, str) for x in rows):
                for item, text in zip(self.items, rows):
                    item.translated_text = text
        except (OSError, ValueError, TypeError, AttributeError):
            pass

    def save(self):
        # Called under the translator's progress lock, or after all workers joined.
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix('.tmp')
        temporary.write_text(json.dumps({'translations': [i.translated_text or '' for i in self.items]},
                                        ensure_ascii=False), encoding='utf-8')
        temporary.replace(self.path)
