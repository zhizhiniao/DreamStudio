"""Burn Chinese captions while copying the source audio streams unchanged."""
import hashlib
import json
import re
import threading
from pathlib import Path

from modules import chinese_dubbing as media

LOCK = threading.Lock()
VERSION = 1


def normalize_source_captions(task, downloads_dir):
    """Unroll native YouTube automatic captions before translation; retain the raw file."""
    folder = Path(downloads_dir) / task['id']
    candidates = sorted(folder.glob('video.*-orig.srt'))
    if not candidates:
        return None
    source = candidates[0]
    text = source.read_text(encoding='utf-8-sig').replace('\r\n', '\n')
    stamp = r'\d+:\d+:\d+[,.]\d+'
    pattern = rf'(?m)^\s*\d+\s*\n({stamp})\s*-->\s*({stamp})[^\n]*\n(.*?)(?=^\s*\d+\s*\n{stamp}|\Z)'
    def seconds(value):
        h, m, s, ms = map(int, re.split('[:,.]', value))
        return h * 3600 + m * 60 + s + ms / 1000
    rows = []
    for match in re.finditer(pattern, text, re.DOTALL):
        start, end = map(seconds, match.groups()[:2])
        lines = [re.sub(r'<[^>]+>', '', line).strip() for line in match.group(3).splitlines() if line.strip()]
        if lines and end > start:
            rows.append((start, end, lines))
    # Only native rolling captions have these near-zero-duration carry-over frames.
    if not rows or sum(end - start <= .04 for start, end, _ in rows) < 2:
        return None
    cues, previous_line = [], None
    for start, end, lines in rows:
        contiguous = cues and start - cues[-1]['end'] <= .15
        if contiguous and end - start <= .04 and lines == [previous_line]:
            cues[-1]['end'] = max(cues[-1]['end'], end)
            continue
        body = lines[1:] if contiguous and len(lines) > 1 and lines[0] == previous_line else lines
        cues.append({'start': start, 'end': end, 'text': ' '.join(body)})
        previous_line = lines[-1]
    def timestamp(value):
        value = round(value * 1000); h, value = divmod(value, 3600000); m, value = divmod(value, 60000); s, ms = divmod(value, 1000)
        return f'{h:02}:{m:02}:{s:02},{ms:03}'
    language = source.name.split('.')[1].removesuffix('-orig')
    normalized = folder / f'source-original-audio.{language}.srt'
    normalized.write_text(''.join(f'{i}\n{timestamp(c["start"])} --> {timestamp(c["end"])}\n{c["text"]}\n\n'
                                  for i, c in enumerate(cues, 1)), encoding='utf-8')
    media.atomic_json(folder / 'original-audio-subtitle-source.json', {
        'source': str(source), 'source_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
        'normalized': str(normalized), 'input_cues': len(rows), 'output_cues': len(cues)})
    return str(normalized)


def settings(config):
    # Same large-caption size as the existing dubbing renderer; voice changes do not matter.
    return {'font': media.settings(config)['font']}


def inputs(task):
    from modules.media_policy import source_video
    from modules.task_manager import DOWNLOADS_DIR
    source = Path(source_video(task, DOWNLOADS_DIR))
    translated = Path(task.get('subtitle_path_translated') or '')
    if not translated.is_file() and str(task.get('subtitle_language_detected') or '').lower().startswith('zh'):
        translated = Path(task.get('subtitle_path_original') or '')
    if not translated.is_file() or not translated.stat().st_size:
        raise ValueError('中文字幕尚未生成，请先获取原字幕并完成中文翻译。')
    return source, translated.resolve()


def source_key(video, subtitles):
    return media.digest({'version': VERSION, 'video': str(video), 'bytes': video.stat().st_size,
                         'mtime': video.stat().st_mtime_ns,
                         'subtitles': hashlib.sha256(subtitles.read_bytes()).hexdigest()})


def current(task, config):
    from modules.caption_layout import current as layout_current
    try:
        video = Path(task.get('video_path_local') or '')
        saved = json.loads((video.parent / 'manifest.json').read_text(encoding='utf-8'))
        source, subtitles = inputs(task)
        return (saved.get('kind') == 'original-audio-subtitles' and saved.get('ready') is True
                and saved.get('source_key') == source_key(source, subtitles)
                and saved.get('settings') == settings(config)
                and layout_current(saved, video)
                and video.name == 'video.zh-original-audio.mp4'
                and video.is_file() and video.stat().st_size == saved.get('video_bytes')
                and Path(saved.get('captions', '')).is_file())
    except (OSError, ValueError, TypeError, KeyError):
        return False


def prepare(task, config, logger, update, cancel=lambda: None):
    with LOCK:
        cancel()
        if current(task, config):
            return task
        video, subtitles = inputs(task)
        info = media.probe(video)
        stream = next(s for s in info['streams'] if s['codec_type'] == 'video')
        if not any(s['codec_type'] == 'audio' for s in info['streams']):
            raise ValueError('原视频没有音轨，无法制作保留原声成片。')
        duration = float(info['format']['duration'])
        width, height = stream['width'], stream['height']
        options = settings(config)
        key = source_key(video, subtitles)
        from modules.caption_layout import VERSION as layout_version
        render_key = [key, options, 'portrait-layout', layout_version] if height > width else [key, options]
        out = video.parent / 'original-audio-subtitles' / media.digest(render_key)[:20]
        out.mkdir(parents=True, exist_ok=True)
        cues = media.read_srt(subtitles)
        cues = [dict(c, end=min(c['end'], duration)) for c in cues if c['start'] < duration]
        if not cues:
            raise ValueError('中文字幕没有覆盖视频时间轴。')
        font = round(options['font'] * height / 1080)
        profile = media.write_captions(out, cues, width, height, font)
        # Explicit mapping and stream copy preserve narration, music and effects together.
        base = ['-i', str(video), '-map', '0:v:0', '-map', '0:a',
                '-vf', media.subtitle_filter()]
        output = out / 'video.zh-original-audio.mp4'
        temporary = out / 'rendering.mp4'
        progress_file = out / 'progress.txt'
        tail = ['-c:a', 'copy', '-movflags', '+faststart', '-progress', str(progress_file),
                '-stats_period', '5', str(temporary)]
        last_progress = [10]
        def progress():
            if progress_file.is_file():
                found = re.findall(r'out_time_us=(\d+)', progress_file.read_text(errors='ignore'))
                percent = min(99, round(10 + int(found[-1]) / 1000000 / duration * 89)) if found else 10
                if percent > last_progress[0]:
                    last_progress[0] = percent
                    update('烧录中文字幕，保留全部原声音轨', percent)
        update('烧录中文字幕，保留全部原声音轨', 10)
        try:
            if media.gpu_available(config):
                try:
                    media.run_ffmpeg([*base, '-c:v', 'h264_nvenc', '-preset', 'p5', '-cq', '23', '-b:v', '0', *tail], cancel, progress, cwd=out)
                except media.DubbingError:
                    cancel()
                    media.run_ffmpeg([*base, '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '23', '-threads', '4', *tail], cancel, progress, cwd=out)
            else:
                media.run_ffmpeg([*base, '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '23', '-threads', '4', *tail], cancel, progress, cwd=out)
        except media.DubbingError as exc:
            raise ValueError('中文字幕烧录失败，原片及原声已保留；请检查转码日志后重试。') from exc
        cancel()
        result = media.probe(temporary)
        audio = lambda data: [(s['codec_name'], s.get('channels'), s.get('sample_rate'))
                              for s in data['streams'] if s['codec_type'] == 'audio']
        if abs(float(result['format']['duration']) - duration) > 1 or audio(info) != audio(result):
            raise ValueError('成片时长或原声音轨校验失败，已停止交付。')
        temporary.replace(output)
        media.atomic_json(out / 'manifest.json', {'kind': 'original-audio-subtitles', 'version': VERSION,
            'ready': True, 'source_key': key, 'source_video': str(video), 'translated_srt': str(subtitles),
            'settings': options, 'video_bytes': output.stat().st_size, 'duration': duration,
            'captions': str(out / 'dub.zh.srt'), 'captions_count': len(cues), 'font_size': profile['font'],
            'caption_layout': profile, 'resolution': [width, height],
            'audio_mode': 'stream-copy', 'audio_streams': audio(result)})
        update('中文字幕成片完成，原声保持不变', 100)
        logger.info('保留原声＋中文字幕完成：%s 条字幕，字号 %s', len(cues), profile['font'])
        return {**task, 'video_path_local': str(output), 'source_video_path': str(video)}
