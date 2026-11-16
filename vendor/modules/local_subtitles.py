"""Use the installed full-audio ASR runtime when platform captions are absent."""
import hashlib
import math
from pathlib import Path
from modules import video_summary as vs
from modules.media_policy import source_video


def _stamp(seconds):
    total = round(seconds * 1000)
    return f'{total//3600000:02}:{total//60000%60:02}:{total//1000%60:02},{total%1000:03}'


def transcribe_missing(task, downloads_dir):
    video = Path(source_video(task, downloads_dir))
    root = Path(downloads_dir) / task['id']
    work = root / 'local-subtitles'
    work.mkdir(parents=True, exist_ok=True)
    output = root / f"asr_{task['id']}.srt"
    receipt = work / 'receipt.json'
    key = vs.fingerprint(video)
    saved = vs._read(receipt)
    if (saved.get('source_fingerprint') == key and output.is_file()
            and saved.get('srt_sha256') == hashlib.sha256(output.read_bytes()).hexdigest()):
        return str(output)
    data = vs.transcribe_full(video, work / 'transcription-result.json')
    segments = data.get('segments') or []
    duration = float(data.get('audio_duration') or 0)
    if not data.get('complete') or not segments or not math.isfinite(duration) or duration <= 0:
        raise ValueError('本地语音识别没有获得完整有效的台词；请检查音轨或导入人工字幕。')
    rows, previous = [], -1
    for i, cue in enumerate(segments, 1):
        start, end = float(cue['start']), float(cue['end'])
        text = str(cue.get('text') or '').strip()
        if (not all(map(math.isfinite, (start, end))) or start < 0 or start < previous
                or end <= start or end > duration + .2 or not text):
            raise ValueError('本地识别字幕时间轴不完整或无效，已暂停，原视频保持不变。')
        rows.append(f'{i}\n{_stamp(start)} --> {_stamp(min(end, duration))}\n{text}\n')
        previous = start
    temporary = output.with_suffix('.tmp')
    temporary.write_text('\n'.join(rows), encoding='utf-8')
    temporary.replace(output)
    vs._write(receipt, {'source_fingerprint': key, 'srt_sha256': hashlib.sha256(output.read_bytes()).hexdigest(),
                       'language': data.get('language'), 'audio_duration': duration, 'cues': len(rows),
                       'engine': data.get('engine', 'faster-whisper-small-int8')})
    return str(output)
