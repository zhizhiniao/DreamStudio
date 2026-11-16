"""Read-only presentation of production state; never infer readiness from percent."""
import math
import re
from pathlib import Path

from modules.media_policy import delivery_ready

PHASES = {
    'pending': '等待后台开始处理', 'fetching_info': '正在读取视频信息',
    'info_fetched': '视频信息已获取，等待处理', 'downloading': '正在下载原视频',
    'downloaded': '原视频已下载，正在准备后续处理',
    'asr_transcribing': '正在识别完整原音轨',
    'translating_subtitle': '正在翻译并校验中文字幕',
    'encoding_video': '正在制作成片／烧录字幕',
    'translating': '正在翻译视频信息', 'tagging': '正在生成标签',
    'partitioning': '正在准备发布分类', 'moderating': '正在检查内容',
}


def snapshot(task, config):
    state = task.get('status', '')
    busy = state in PHASES
    # A previous output may still pass its manifest while a replacement is rendering.
    ready = state in ('ready_for_upload', 'awaiting_manual_review', 'completed', 'failed', 'uploading') and bool(task.get('video_path_local')) and Path(task['video_path_local']).is_file() and delivery_ready(task, config)
    raw = str(task.get('upload_progress') or '').strip()
    match = re.fullmatch(r'(\d+(?:\.\d+)?)\s*%?', raw)
    percent = float(match[1]) if match and busy and state in ('downloading', 'encoding_video', 'asr_transcribing', 'translating_subtitle') else None
    if percent is not None:
        percent = min(99, max(0, percent)) if math.isfinite(percent) else None
    error = task.get('error_message') if not busy and not ready else None
    phase = PHASES.get(state, '成片已就绪' if ready else '尚未开始制作，或设置变更后需要重新生成')
    if error:
        phase = '制作未完成，请处理下方问题'
    return dict(busy=busy, ready=bool(ready), phase=phase, state=state,
                percent=100 if ready else percent, progress=task.get('upload_progress'),
                updated_at=task.get('updated_at'), error=error,
                original=bool(task.get('subtitle_path_original') and Path(task['subtitle_path_original']).is_file()),
                translated=bool(task.get('subtitle_path_translated') and Path(task['subtitle_path_translated']).is_file()))
