"""Independent Xiaohongshu copy and cover review, sharing only the finished video."""
import hashlib
import json
import re
import secrets
import threading
import uuid
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageOps
from modules import multi_publish as mp, task_manager as tm


def init_schema():
    with mp.connect() as db:
        db.execute('''CREATE TABLE IF NOT EXISTS xiaohongshu_options (
            task_id TEXT PRIMARY KEY, cover_mode TEXT NOT NULL DEFAULT 'reviewed',
            cover_path TEXT, updated_at TEXT)''')
        db.execute('''CREATE TRIGGER IF NOT EXISTS remove_xiaohongshu_options
            AFTER DELETE ON tasks BEGIN
                DELETE FROM xiaohongshu_options WHERE task_id=OLD.id;
            END''')


def options(task):
    with mp.connect() as db:
        row = db.execute('SELECT * FROM xiaohongshu_options WHERE task_id=?', (task['id'],)).fetchone()
    return dict(row) if row else {'cover_mode': 'reviewed', 'cover_path': None}


def cover_info(task, settings=None):
    settings = settings or options(task)
    result = {'mode': settings['cover_mode'], 'ready': False, 'path': '', 'width': 0, 'height': 0, 'error': ''}
    if settings['cover_mode'] == 'platform_default':
        return {**result, 'ready': True}
    try:
        path = Path(settings.get('cover_path') or task.get('cover_path_local') or '').resolve()
        if not path.is_relative_to(Path(tm.DOWNLOADS_DIR).resolve()) or not path.is_file():
            raise ValueError('请先为小红书选择一张有效封面。')
        if path.stat().st_size > 10 * 1024 * 1024:
            raise ValueError('封面超过 10 MB，请先更换。')
        with Image.open(path) as picture:
            width, height = picture.size
            if picture.format not in ('JPEG', 'PNG', 'WEBP') or width * height > 32_000_000:
                raise ValueError('请使用不超过 3200 万像素的 JPG、PNG 或 WEBP 封面。')
            picture.verify()
        return {**result, 'ready': True, 'path': str(path), 'width': width, 'height': height}
    except (OSError, ValueError, Image.DecompressionBombError) as exc:
        result['error'] = str(exc) if isinstance(exc, ValueError) else '封面文件无法读取，请重新上传图片。'
        return result


def cover_for_upload(task):
    info = cover_info(task)
    if not info['ready']:
        raise ValueError('小红书封面检查未通过：' + info['error'] + ' 请打开“小红书专用流程”处理。')
    return info['path']


def assert_editable(task, root_id, db):
    state = db.execute('SELECT phase FROM publication_sets WHERE root_id=?', (root_id,)).fetchone()
    if not task or mp.submitted(task):
        raise ValueError('小红书已经提交，不能改写或重复发布。')
    if task['status'] not in mp.SAFE_STATES or (state and state['phase'] in ('preparing', 'sending')):
        raise ValueError('当前正在处理，请结束后再修改小红书稿件。')


def save_draft(task, root_id, form, uploaded_file=None):
    from modules.browser_publisher import validate_metadata
    from modules.description_policy import clean_description
    if task.get('upload_target') != 'xiaohongshu':
        raise ValueError('此编辑流程仅用于小红书稿件。')
    if not form.get('title', '').strip():
        raise ValueError('请填写小红书标题。')
    mode = form.get('cover_mode', '')
    if mode not in ('reviewed', 'platform_default'):
        raise ValueError('请选择小红书的封面方式。')
    tags = list(dict.fromkeys(t.strip().lstrip('#') for t in re.split(r'[,，\n\r]', form.get('tags', '')) if t.strip()))
    candidate = {**task, 'video_title_translated': form.get('title', '').strip(),
                 'description_translated': clean_description(form.get('description', ''), task['youtube_url']),
                 'tags_generated': json.dumps(tags, ensure_ascii=False)}
    validate_metadata(candidate)
    current_options = options(task)
    selected_path = current_options.get('cover_path')
    new_path = None
    try:
        if uploaded_file and uploaded_file.filename:
            if mode != 'reviewed':
                raise ValueError('上传图片时，请选择“使用审核封面”。')
            raw = uploaded_file.stream.read(10 * 1024 * 1024 + 1)
            if len(raw) > 10 * 1024 * 1024:
                raise ValueError('封面最多 10 MB。')
            import io
            try:
                with Image.open(io.BytesIO(raw)) as picture:
                    if picture.format not in ('JPEG', 'PNG', 'WEBP') or picture.width * picture.height > 32_000_000:
                        raise ValueError('请上传 JPG、PNG 或 WEBP 图片，最多 3200 万像素。')
                    prepared = ImageOps.exif_transpose(picture).convert('RGB')
            except (OSError, Image.DecompressionBombError) as exc:
                raise ValueError('上传文件不是有效封面图片。') from exc
            directory = Path(tm.DOWNLOADS_DIR) / task['id']
            directory.mkdir(parents=True, exist_ok=True)
            new_path = directory / ('xiaohongshu-cover-' + uuid.uuid4().hex + '.jpg')
            prepared.save(new_path, quality=94)
            selected_path = str(new_path)
        info = cover_info(candidate, {'cover_mode': mode, 'cover_path': selected_path})
        if not info['ready']:
            raise ValueError(info['error'])
        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        with mp.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            actual = db.execute('SELECT * FROM tasks WHERE id=?', (task['id'],)).fetchone()
            assert_editable(dict(actual) if actual else None, root_id, db)
            db.execute('UPDATE tasks SET video_title_translated=?,description_translated=?,tags_generated=?,updated_at=? WHERE id=?',
                       (candidate['video_title_translated'], candidate['description_translated'], candidate['tags_generated'], now, task['id']))
            db.execute('''INSERT INTO xiaohongshu_options(task_id,cover_mode,cover_path,updated_at) VALUES(?,?,?,?)
                ON CONFLICT(task_id) DO UPDATE SET cover_mode=excluded.cover_mode,cover_path=excluded.cover_path,updated_at=excluded.updated_at''',
                       (task['id'], mode, selected_path, now))
            # If XHS itself is the source task, its explicitly reviewed copy is current.
            if task['id'] == root_id:
                job = db.execute("SELECT source_revision FROM publication_jobs WHERE root_id=? AND platform='xiaohongshu'", (root_id,)).fetchone()
                if job and job['source_revision'] == mp.revision(task):
                    db.execute("UPDATE publication_jobs SET source_revision=? WHERE root_id=? AND platform='xiaohongshu'",
                               (mp.revision(candidate), root_id))
    except Exception:
        if new_path:
            new_path.unlink(missing_ok=True)
        raise
    tm.publish_task_event('task_updated', {'task_id': task['id']})


def media_ready(root, config):
    from modules.media_policy import delivery_ready
    return (Path(root.get('video_path_local') or '').is_file() and
            delivery_ready(root, config))


def register_routes(app, login_required):
    from flask import request, session, abort, redirect, url_for, render_template, flash, send_file
    from modules.config_manager import load_config
    init_schema()

    @app.get('/tasks/<task_id>/xiaohongshu-cover')
    @login_required
    def xiaohongshu_cover(task_id):
        task = tm.get_task(task_id)
        if not task or task.get('upload_target') != 'xiaohongshu':
            abort(404)
        info = cover_info(task)
        if not info['path']:
            abort(404)
        return send_file(info['path'], max_age=0)

    @app.route('/tasks/<task_id>/xiaohongshu', methods=['GET', 'POST'])
    @login_required
    def xiaohongshu_workflow(task_id):
        task = tm.get_task(task_id)
        if not task:
            abort(404)
        root, state, cards = mp.view(task)
        card = next(c for c in cards if c['platform'] == 'xiaohongshu')
        child = card['task']
        config = load_config()
        token = session.setdefault('xiaohongshu_csrf', secrets.token_urlsafe(32))
        if request.method == 'POST':
            if not secrets.compare_digest(str(request.form.get('csrf_token', '')), token):
                abort(403)
            try:
                action = request.form.get('action')
                if action == 'prepare':
                    if card['submitted']:
                        raise ValueError('小红书已有提交记录，不会重新准备或投稿。')
                    if root['status'] not in mp.SAFE_STATES or not media_ready(root, config):
                        raise ValueError('请先在成片审核页完成当前视频，再准备小红书草稿。')
                    mp.reserve(root['id'], ['xiaohongshu'], 'preparing')
                    def prepare():
                        try:
                            mp.prepare_drafts(root['id'], ['xiaohongshu'], config, app.logger)
                            mp.finish(root['id'])
                        except Exception:
                            mp.finish(root['id'], '小红书草稿准备失败，请检查文案服务后重试。')
                    threading.Thread(target=prepare, daemon=True).start()
                elif action in ('save', 'publish'):
                    if not child:
                        raise ValueError('请先准备小红书草稿。')
                    save_draft(child, root['id'], request.form, request.files.get('cover_file'))
                    if action == 'publish':
                        root, state, cards = mp.view(tm.get_task(root['id']))
                        card = next(c for c in cards if c['platform'] == 'xiaohongshu')
                        if not card['prepared'] or not media_ready(root, config):
                            raise ValueError('当前成片或来源文案有更新，请先重新准备小红书草稿。')
                        cover_for_upload(card['task'])
                        mp.reserve(root['id'], ['xiaohongshu'], 'sending')
                        def submit():
                            try:
                                mp.run_submissions(root['id'], [card['task']], config, app.logger)
                            except Exception:
                                mp.finish(root['id'], '小红书结果尚未确认，请先核对创作者中心，勿重复提交。')
                        threading.Thread(target=submit, daemon=True).start()
                        flash('仅向小红书提交当前审核稿。', 'info')
                    else:
                        flash('小红书文案和封面方式已单独保存。', 'success')
                else:
                    raise ValueError('无效操作。')
            except ValueError as exc:
                flash(str(exc), 'warning')
            return redirect(url_for('xiaohongshu_workflow', task_id=root['id']))
        busy = state['phase'] in ('preparing', 'sending') or bool(child and child['status'] not in mp.SAFE_STATES)
        return render_template('xiaohongshu_workflow.html', task=root, child=child, card=card, state=state,
                               busy=busy, media_ready=media_ready(root, config), csrf_token=token,
                               cover=cover_info(child) if child else None,
                               tags=json.loads(child.get('tags_generated') or '[]') if child else [],
                               publisher_job_id=hashlib.sha256(('xiaohongshu:' + child['id']).encode()).hexdigest() if child else None)
