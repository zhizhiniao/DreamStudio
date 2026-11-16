"""Explicit per-task choice between original media and configured localization."""
import json
import secrets
from datetime import datetime
from pathlib import Path

MODES = ('standard', 'original', 'original_audio_subtitles')


def keeps_original(task):
    return bool(task and task.get('media_processing_mode') == 'original')


def keeps_original_audio(task):
    return bool(task and task.get('media_processing_mode') == 'original_audio_subtitles')


def delivery_ready(task, config):
    if keeps_original(task):
        return original_ready(task)
    if keeps_original_audio(task):
        from modules.original_audio_subtitles import current
        return current(task, config)
    from modules.chinese_dubbing import enabled, current
    return not enabled(config) or current(task, config)


def original_ready(task):
    """Original mode must point to its recorded, non-empty source file."""
    try:
        source = Path(task.get('source_video_path') or '')
        video = Path(task.get('video_path_local') or '')
        return source.is_file() and source.stat().st_size > 0 and source.resolve() == video.resolve()
    except (OSError, ValueError):
        return False


def source_video(task, downloads_dir):
    """Recover preserved inputs from old editions without using a rendered output."""
    video = Path(task.get('video_path_local') or '')
    candidates = []
    if task.get('source_video_path'):
        candidates = [Path(task['source_video_path'])]
    else:
        manifest = video.parent / 'manifest.json'
        if manifest.is_file():
            saved = json.loads(manifest.read_text(encoding='utf-8'))
            if saved.get('kind') in ('mandarin-dub', 'original-audio-subtitles') and saved.get('source_video'):
                candidates = [Path(saved['source_video'])]
        if not candidates and video.stem.endswith('_with_subtitle'):
            candidates = [video.with_name(video.stem[:-14] + video.suffix),
                          video.parent / 'video.mp4', video.parent.parent / 'video.mp4']
        if not candidates and 'chinese-dub' not in video.parts:
            candidates = [video]
    root = Path(downloads_dir).resolve()
    for candidate in candidates:
        path = candidate.resolve()
        if path.is_relative_to(root) and path.is_file() and path.stat().st_size > 0:
            return str(path)
    raise ValueError('找不到保留的原视频，请先恢复或重新下载原片。')


def set_mode(task_id, mode):
    from modules import multi_publish as mp, task_manager as tm
    if mode not in MODES:
        raise ValueError('请选择有效的视频处理方式。')
    root_id = mp.root_for(task_id)
    with tm.get_db_connection() as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT * FROM tasks WHERE id=?', (root_id,)).fetchone()
        if not row:
            raise ValueError('任务不存在。')
        task = dict(row)
        if mp.submitted(task):
            raise ValueError('此稿件已提交；如需制作另一个版本，请新建任务。')
        state = db.execute('SELECT phase FROM publication_sets WHERE root_id=?', (root_id,)).fetchone()
        children = db.execute('SELECT t.status FROM tasks t JOIN publication_jobs j ON j.task_id=t.id WHERE j.root_id=?', (root_id,)).fetchall()
        if (task['status'] not in mp.SAFE_STATES or
                (state and state['phase'] in ('preparing', 'sending')) or
                any(c['status'] not in mp.SAFE_STATES for c in children)):
            raise ValueError('任务正在处理，请完成后再切换视频处理方式。')
        if task.get('media_processing_mode', 'standard') == mode:
            return root_id
        source = source_video(task, tm.DOWNLOADS_DIR)
        # Always rebuild from the preserved source, never stack captions or retain a dub.
        path = source
        manifest = Path(task.get('video_path_local') or '').parent / 'manifest.json'
        if manifest.is_file():
            saved = json.loads(manifest.read_text(encoding='utf-8'))
            if saved.get('kind') == 'mandarin-dub':
                db.execute('UPDATE tasks SET subtitle_path_original=?,subtitle_path_translated=? WHERE id=?',
                           (saved.get('source_srt'), saved.get('translated_srt'), root_id))
        description = task.get('description_translated')
        if mode == 'original' and path != task['video_path_local'] and description:
            notice = '\n\n中文配音及字幕由 AI 辅助制作。\n原视频：' + str(task.get('youtube_url') or '')
            description = description.replace(notice, '')
        # Only localization errors are cleared; unrelated failures remain visible.
        localized_error = task.get('error_category') in ('dubbing', 'subtitle_required', 'original_audio_subtitles')
        db.execute('UPDATE tasks SET media_processing_mode=?,source_video_path=?,video_path_local=?,description_translated=?,error_message=?,error_category=?,updated_at=? WHERE id=?',
                   (mode, source, path, description, None if localized_error else task.get('error_message'),
                    None if localized_error else task.get('error_category'), datetime.now().strftime('%Y-%m-%d %H:%M:%S'), root_id))
        db.execute('UPDATE publication_sets SET error=NULL WHERE root_id=?', (root_id,))
    return root_id


def register_routes(app, login_required):
    from flask import session, request, abort, flash, redirect, url_for
    from modules.multi_publish import root_for
    def csrf():
        return session.setdefault('media_mode_csrf', secrets.token_urlsafe(32))
    app.jinja_env.globals.update(keeps_original=keeps_original, keeps_original_audio=keeps_original_audio,
                                media_mode_csrf=csrf, media_root_id=root_for)

    @app.post('/tasks/<task_id>/media_mode')
    @login_required
    def task_media_mode(task_id):
        if not secrets.compare_digest(str(request.form.get('csrf_token', '')), csrf()):
            abort(403)
        root_id = root_for(task_id)
        try:
            root_id = set_mode(task_id, request.form.get('media_processing_mode'))
            messages = {'original': '已保留原声与原画面，跳过配音和字幕处理。请准备各平台文案后审核发布。',
                        'original_audio_subtitles': '已选择保留原声＋中文字幕，请在成片审核页生成中文字幕成片。',
                        'standard': '已恢复按全局设置处理，请先生成并审核成片。'}
            flash(messages[request.form.get('media_processing_mode')], 'success')
        except ValueError as exc:
            flash(str(exc), 'warning')
        endpoint = 'publish_task_platforms' if request.form.get('return_to') == 'publish' else 'edit_task'
        return redirect(url_for(endpoint, task_id=root_id))
