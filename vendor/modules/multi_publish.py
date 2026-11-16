"""Explicit platform selection, reviewed drafts and independent submission receipts."""
import hashlib
import json
import secrets
import shutil
import threading
import uuid
from pathlib import Path
from urllib.parse import urlsplit

from modules.publishing_platforms import PLATFORM_LABELS, metadata_limits, normalize_wechat_title, topic_limit
from modules.media_policy import keeps_original, original_ready
from modules.video_summary import needs_summary, ensure_summary, status as summary_status, SummaryError
from modules.description_policy import clean_description, BODY_INSTRUCTION

PLATFORMS = tuple(PLATFORM_LABELS)
SAFE_STATES = ('ready_for_upload', 'awaiting_manual_review', 'failed', 'completed')


def connect():
    from modules.task_manager import get_db_connection
    return get_db_connection()


def init_schema():
    with connect() as db:
        db.execute("""CREATE TABLE IF NOT EXISTS publication_sets (
            root_id TEXT PRIMARY KEY, targets TEXT NOT NULL DEFAULT '[]',
            phase TEXT NOT NULL DEFAULT 'idle', error TEXT)""")
        db.execute("""CREATE TABLE IF NOT EXISTS publication_jobs (
            root_id TEXT NOT NULL, platform TEXT NOT NULL, task_id TEXT UNIQUE NOT NULL,
            source_revision TEXT, PRIMARY KEY(root_id, platform))""")
        # Interrupted HTTP workers never imply that a platform submission failed.
        db.execute("UPDATE publication_sets SET phase='idle',error='服务已重启，请核对各平台记录后继续。' WHERE phase IN ('preparing','sending')")


def selection(values):
    if not values or any(p not in PLATFORMS for p in values):
        raise ValueError('请明确勾选至少一个发布平台。')
    return [p for p in PLATFORMS if p in values]


def root_for(task_id):
    from modules.video_catalog import group_for
    group = group_for(task_id)
    return group['root']['id'] if group else task_id


def remember_selection(task_id, platforms):
    targets = selection(platforms)
    with connect() as db:
        db.execute("INSERT INTO publication_sets(root_id,targets) VALUES(?,?) ON CONFLICT(root_id) DO UPDATE SET targets=excluded.targets",
                   (task_id, json.dumps(targets)))
    from modules.task_manager import publish_task_event
    publish_task_event('task_updated', {'task_id': task_id})


def revision(task):
    data = {k: task.get(k) for k in ('video_path_local', 'subtitle_path_translated', 'video_title_translated',
                                     'description_translated', 'tags_generated', 'cover_path_local',
                                     'media_processing_mode', 'source_video_path')}
    for name in ('video_path_local', 'subtitle_path_translated'):
        path = Path(task.get(name) or '')
        if path.is_file():
            data[name + '_stat'] = (path.stat().st_size, path.stat().st_mtime_ns)
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()


def submitted(task):
    from modules.task_manager import _task_has_upload_response
    return bool(task and (_task_has_upload_response(task) or task['status'] == 'completed'))


def display_targets(task):
    with connect() as db:
        row = db.execute('SELECT targets FROM publication_sets WHERE root_id=?', (task['id'],)).fetchone()
        child = db.execute('SELECT 1 FROM publication_jobs WHERE task_id=? AND root_id<>?', (task['id'], task['id'])).fetchone()
    targets = json.loads(row['targets']) if row else []
    if targets:
        return ' / '.join(PLATFORM_LABELS[p] for p in targets)
    return PLATFORM_LABELS.get(task.get('upload_target'), '待选平台') if child or submitted(task) else '待选平台'


def view(task):
    from modules.video_catalog import group_for
    from modules.config_manager import load_config
    config = load_config()
    group = group_for(task['id'])
    root_id = group['root']['id'] if group else task['id']
    with connect() as db:
        root = dict(db.execute('SELECT * FROM tasks WHERE id=?', (root_id,)).fetchone())
        state = db.execute('SELECT * FROM publication_sets WHERE root_id=?', (root_id,)).fetchone()
        state = dict(state) if state else {'targets': '[]', 'phase': 'idle', 'error': None}
        cards = []
        for platform in PLATFORMS:
            job = db.execute('SELECT * FROM publication_jobs WHERE root_id=? AND platform=?', (root_id, platform)).fetchone()
            row = db.execute('SELECT * FROM tasks WHERE id=?', (job['task_id'],)).fetchone() if job else None
            rows = [t for t in (group['members'] if group else [root]) if t.get('upload_target') == platform]
            # A legacy receipt also prevents a duplicate, even when its URL spelling differs.
            existing_submission = next((r for r in reversed(rows) if submitted(r)), None)
            if existing_submission:
                row = existing_submission
            elif row is None:
                row = next((r for r in rows if r['id'] == root_id), rows[-1] if rows else None)
            child = dict(row) if row else None
            cards.append({'platform': platform, 'label': PLATFORM_LABELS[platform], 'task': child,
                          'tags': preview_tags(child, platform, config),
                          'submitted': submitted(child), 'limits': metadata_limits(platform),
                          'prepared': bool(job and child and job['task_id'] == child['id'] and job['source_revision'] == revision(root) and not needs_summary(root))})
    return root, state, cards


def preview_tags(task, platform, config):
    """Show the tags used by the existing platform upload path, without writing drafts."""
    if not task:
        return []
    if platform == 'bilibili':
        from modules.tag_presets import resolve_upload_tags
        tags = resolve_upload_tags(config, task.get('tags_generated'), platform)
        if config.get('YOUTUBE_UPLOADER_AS_FIRST_TAG'):
            try:
                metadata = json.loads(Path(task.get('metadata_json_path_local') or '').read_text(encoding='utf-8'))
                author = str(metadata.get('uploader') or '').strip()
                if author:
                    tags = ([author] + [t for t in tags if t.lower() != author.lower()])[:12]
            except (OSError, ValueError, TypeError):
                pass
        return tags
    try:
        tags = json.loads(task.get('tags_generated') or '[]')
        return [t.strip() for t in tags if isinstance(t, str) and t.strip()] if isinstance(tags, list) else []
    except (ValueError, TypeError):
        return []


def publication_progress(state, cards):
    """Report actual task/receipt state, not a timer-based upload percentage."""
    targets = json.loads(state.get('targets') or '[]')
    items = []
    for card in cards:
        if card['platform'] not in targets:
            continue
        task = card.get('task') or {}
        error = task.get('error_message')
        if card['submitted']:
            code, label = 'submitted', '已提交'
        elif task.get('status') == 'uploading':
            code, label = 'sending', '正在上传／提交'
        elif error or task.get('status') == 'failed':
            code, label = 'failed', '未提交成功'
        elif state.get('phase') == 'sending':
            code, label = 'queued', '等待发布'
        elif state.get('phase') == 'preparing':
            code, label = 'preparing', '正在准备文案'
        elif state.get('error'):
            code, label = 'check', '未确认提交，请核对'
        else:
            code, label = 'pending', '待发布'
        items.append(dict(platform=card['platform'], label=card['label'], state=code,
                          status=label, progress=str(task.get('upload_progress') or '') if code == 'sending' else '',
                          error=error if code == 'failed' else None))
    done = sum(i['state'] == 'submitted' for i in items)
    return dict(items=items, submitted=done, total=len(items), phase=state.get('phase'),
                error=state.get('error'), percent=round(done * 100 / len(items)) if items else 0)


def reserve(root_id, targets, phase):
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT phase FROM publication_sets WHERE root_id=?', (root_id,)).fetchone()
        if row and row['phase'] in ('preparing', 'sending'):
            raise ValueError('当前正在处理，请等待完成，避免重复提交。')
        db.execute("INSERT INTO publication_sets(root_id,targets,phase) VALUES(?,?,?) ON CONFLICT(root_id) DO UPDATE SET targets=excluded.targets,phase=excluded.phase,error=NULL",
                   (root_id, json.dumps(targets), phase))
    from modules.task_manager import publish_task_event
    publish_task_event('task_updated', {'task_id': root_id})


def finish(root_id, error=None):
    with connect() as db:
        db.execute("UPDATE publication_sets SET phase='idle',error=? WHERE root_id=?", (error, root_id))
    from modules.task_manager import publish_task_event
    publish_task_event('task_updated', {'task_id': root_id})


def queue_preferred_drafts(task_id, config, logger):
    """Preparation may run automatically; publishing always requires a review POST."""
    from modules.task_manager import get_task
    try:
        root, state, cards = view(get_task(task_id))
        targets = json.loads(state['targets'])
        if not targets or state['phase'] != 'idle' or all(c['prepared'] or c['submitted'] for c in cards if c['platform'] in targets):
            return
        reserve(root['id'], targets, 'preparing')
    except Exception:
        return
    def work():
        try:
            from modules.title_cover import automatically_prepare
            automatically_prepare(get_task(root['id']), config, logger)
            prepare_drafts(root['id'], targets, config, logger)
            finish(root['id'])
        except Exception as exc:
            logger.warning('自动准备多平台文案失败：%s', type(exc).__name__)
            finish(root['id'], str(exc) if isinstance(exc, SummaryError) else '自动准备文案未完成，请在此页重新准备。')
    threading.Thread(target=work, daemon=True).start()


def fit_metadata(task, platform, config, logger):
    title = str(task.get('video_title_translated') or task.get('video_title_original') or '').strip()
    description = clean_description(task.get('description_translated') or task.get('description_original'), task.get('youtube_url'))
    limits = metadata_limits(platform)
    if platform == 'wechat_channels':
        # Do not truncate before the model sees the complete meaning.
        title = title.replace('.', '点')
    if len(title) > limits['title_limit'] or len(description) > limits['description_limit']:
        from modules.ai_enhancer import get_openai_client, _request_translated_metadata_fields
        client = get_openai_client({**config, 'OPENAI_TIMEOUT_SECONDS': 60})
        if hasattr(client, 'with_options'):
            client = client.with_options(max_retries=0)
        plain_json = (urlsplit(str(config.get('OPENAI_BASE_URL') or '')).hostname or '') in ('api.minimax.cn', 'api.minimax.io', 'api.minimaxi.com')
        result = _request_translated_metadata_fields(
            client, config.get('OPENAI_MODEL_NAME'),
            '根据已审核文案生成本平台的简体中文标题和简介。忠实压缩，不新增事实。返回 JSON title、description。' + BODY_INSTRUCTION,
            {'title': title, 'description': description},
            thinking_enabled=False, logger=logger, scene_name='multi-platform-draft',
            description_max_blocks=None,
            title_limit=limits['title_limit'], description_limit=limits['description_limit'], prefer_plain_json=plain_json)
        title, description = result.get('title', ''), clean_description(result.get('description'), task.get('youtube_url'))
    if platform == 'wechat_channels':
        title = normalize_wechat_title(title)
    candidate = {**task, 'upload_target': platform, 'video_title_translated': title, 'description_translated': description,
                 'tags_generated': draft_topics(task, platform)}
    from modules.browser_publisher import validate_metadata
    validate_metadata(candidate)
    return title, description


def draft_topics(task, platform):
    """Select topics while creating the visible draft, before human review."""
    tags = json.loads(task.get('tags_generated') or '[]')
    return json.dumps(tags[:topic_limit(platform)], ensure_ascii=False)


def prepare_drafts(root_id, targets, config, logger, force_summary=False):
    from modules.task_manager import get_task, update_task
    task = get_task(root_id)
    if not submitted(task):
        ensure_summary(task, config, logger, force=force_summary)
    elif force_summary:
        raise SummaryError('已提交的原任务不能重写简介。')
    root, _, cards = view(get_task(root_id))
    for card in cards:
        platform = card['platform']
        if platform not in targets or card['submitted'] or (card['prepared'] and card['task'].get('cover_path_local') and Path(card['task']['cover_path_local']).is_file()):
            continue
        title, description = fit_metadata(root, platform, config, logger)
        child = card['task']
        if child is None:
            child_id = str(uuid.uuid5(uuid.NAMESPACE_URL, 'y2a-publish:' + root_id + ':' + platform))
            clone = {k: v for k, v in root.items() if k not in {
                'id', 'created_at', 'updated_at', 'error_message', 'error_category', 'upload_progress',
                'bilibili_upload_response', 'acfun_upload_response', 'external_upload_response', 'pipeline_checkpoint'}}
            clone.update(id=child_id, upload_target=platform, status='awaiting_manual_review',
                         video_title_translated=title, description_translated=description,
                         tags_generated=draft_topics(root, platform))
            # Keep per-task cover editing independent; the expensive finished video is shared.
            from modules.title_cover import copy_cover
            generated_cover = copy_cover(root, child_id, platform)
            if generated_cover:
                clone['cover_path_local'] = generated_cover
            elif root.get('cover_path_local') and Path(root['cover_path_local']).is_file():
                from modules.task_manager import DOWNLOADS_DIR
                directory = Path(DOWNLOADS_DIR)/child_id
                directory.mkdir(parents=True, exist_ok=True)
                destination = directory/Path(root['cover_path_local']).name
                shutil.copy2(root['cover_path_local'], destination)
                clone['cover_path_local'] = str(destination)
            with connect() as db:
                keys = list(clone)
                db.execute('INSERT OR IGNORE INTO tasks (' + ','.join(keys) + ') VALUES (' + ','.join('?' for _ in keys) + ')',
                           [clone[k] for k in keys])
                db.execute('INSERT OR IGNORE INTO publication_jobs(root_id,platform,task_id) VALUES(?,?,?)',
                           (root_id, platform, child_id))
        else:
            child_id = child['id']
            from modules.title_cover import repair_missing_inherited_cover
            child = repair_missing_inherited_cover(child)
            with connect() as db:
                db.execute('INSERT OR IGNORE INTO publication_jobs(root_id,platform,task_id) VALUES(?,?,?)',
                           (root_id, platform, child_id))
            update_task(child_id, video_title_translated=title, description_translated=description,
                        media_processing_mode=root.get('media_processing_mode') or 'standard',
                        source_video_path=root.get('source_video_path'),
                        video_path_local=root['video_path_local'], subtitle_path_original=root.get('subtitle_path_original'),
                        subtitle_path_translated=root.get('subtitle_path_translated'), tags_generated=draft_topics(root, platform))
            # A manually uploaded child cover takes precedence over generated defaults.
            if child_id != root_id:
                from modules.title_cover import copy_cover, inherited_cover
                if inherited_cover(root, child):
                    generated_cover = copy_cover(root, child_id, platform)
                    if generated_cover:
                        update_task(child_id, cover_path_local=generated_cover)
        # Preserve completed drafts if a later platform's model request fails.
        with connect() as db:
            db.execute('UPDATE publication_jobs SET source_revision=? WHERE root_id=? AND platform=?',
                       (revision(get_task(root_id)), root_id, platform))
    current_revision = revision(get_task(root_id))
    with connect() as db:
        for target in targets:
            db.execute('UPDATE publication_jobs SET source_revision=? WHERE root_id=? AND platform=?',
                       (current_revision, root_id, target))


def validate_selected_drafts(root, cards, targets, form):
    from modules.browser_publisher import validate_metadata
    drafts = []
    for card in cards:
        platform = card['platform']
        if platform not in targets or card['submitted']:
            continue
        child = card['task']
        if not child or not card['prepared']:
            raise ValueError(card['label'] + '文案尚未准备，或原成片／文案已变化，请先准备所选平台草稿。')
        if child['status'] not in SAFE_STATES:
            raise ValueError(card['label'] + '正在处理，不能重复投稿。')
        edited = {**child, 'video_title_translated': form.get('title_' + platform, '').strip(),
                  'description_translated': form.get('description_' + platform, '').strip()}
        validate_metadata(edited)
        if platform == 'wechat_channels' and normalize_wechat_title(edited['video_title_translated']) != edited['video_title_translated']:
            raise ValueError('视频号短标题含不支持的符号，请用中文“点”替代小数点。')
        drafts.append(edited)
    return drafts


def run_submissions(root_id, drafts, config, logger):
    from modules.task_manager import get_task, force_upload_task
    from concurrent.futures import ThreadPoolExecutor
    def submit(draft):
        if submitted(get_task(draft['id'])):
            return None
        try:
            force_upload_task(draft['id'], config)
            if not submitted(get_task(draft['id'])):
                return PLATFORM_LABELS[draft['upload_target']]
        except Exception as exc:
            logger.warning('多平台投稿中断：%s', type(exc).__name__)
            return PLATFORM_LABELS[draft['upload_target']]
        return None
    # The publisher bounds browser concurrency and serializes each account.
    # One platform waiting for a scan must not prevent the others from starting.
    with ThreadPoolExecutor(max_workers=4) as workers:
        errors = [label for label in workers.map(submit, drafts) if label]
    finish(root_id, ('请核对尚未完成的平台：' + '、'.join(errors)) if errors else None)


def register_routes(app, login_required):
    from flask import request, render_template, redirect, url_for, flash, session, abort
    from modules.task_manager import get_task, update_task, force_upload_task
    from modules.config_manager import load_config
    init_schema()
    app.jinja_env.globals['publication_targets'] = display_targets
    from modules.media_policy import register_routes as register_media_routes
    register_media_routes(app, login_required)

    @app.get('/tasks/<task_id>/content-transcript')
    @login_required
    def video_content_transcript(task_id):
        from flask import Response
        from modules.video_summary import transcript_text
        task = get_task(root_for(task_id))
        if not task:
            abort(404)
        try:
            return Response(transcript_text(task), content_type='text/plain; charset=utf-8')
        except (OSError, ValueError):
            abort(404)

    @app.route('/tasks/<task_id>/publish', methods=['GET', 'POST'])
    @login_required
    def publish_task_platforms(task_id):
        task = get_task(task_id)
        if not task:
            abort(404)
        root, state, cards = view(task)
        token = session.setdefault('multi_publish_csrf', secrets.token_urlsafe(32))
        config = load_config()
        if request.method == 'POST':
            if not secrets.compare_digest(str(request.form.get('csrf_token', '')), token):
                abort(403)
            try:
                targets = selection(request.form.getlist('platforms'))
                if not root.get('video_path_local') or not Path(root['video_path_local']).is_file():
                    raise ValueError('视频还未下载完成，请先返回任务重试下载。')
                if root['status'] not in SAFE_STATES:
                    raise ValueError('视频正在处理中，请完成成片后再准备投稿。')
                from modules.media_policy import delivery_ready
                if keeps_original(root) and not original_ready(root):
                    raise ValueError('所选原片不存在或文件不一致，请返回成片审核页恢复原片。')
                if not delivery_ready(root, config):
                    raise ValueError('当前所选处理方式的成片尚未完成或已过期，请返回成片审核页生成最新版本。')
                action = request.form.get('action')
                if action in ('prepare', 'summarize'):
                    reserve(root['id'], targets, 'preparing')
                    def work():
                        try:
                            prepare_drafts(root['id'], targets, config, app.logger, force_summary=(action == 'summarize'))
                            finish(root['id'])
                        except Exception as exc:
                            app.logger.warning('多平台草稿生成失败：%s', type(exc).__name__)
                            finish(root['id'], str(exc) if isinstance(exc, SummaryError) else '草稿准备失败，已保留完成部分；请检查 AI 服务和文案限制后重试。')
                    threading.Thread(target=work, daemon=True).start()
                    flash('正在准备所选平台的文案，完成后请审核并点击发布。', 'info')
                elif action == 'publish':
                    drafts = validate_selected_drafts(root, cards, targets, request.form)
                    if not drafts:
                        raise ValueError('所选平台均已有提交记录，不会重复发布。')
                    from modules.browser_publisher import ensure_account_health
                    ensure_account_health([draft['upload_target'] for draft in drafts])
                    reserve(root['id'], targets, 'sending')
                    for draft in drafts:
                        update_task(draft['id'], video_title_translated=draft['video_title_translated'],
                                    description_translated=draft['description_translated'], status='ready_for_upload')
                    def upload():
                        try:
                            run_submissions(root['id'], drafts, config, app.logger)
                        except Exception:
                            finish(root['id'], '投稿中断，请先核对平台作品记录，勿直接重复提交。')
                    threading.Thread(target=upload, daemon=True).start()
                    flash('仅向本次勾选的平台提交；各平台结果独立记录。', 'info')
                else:
                    raise ValueError('无效操作。')
            except ValueError as exc:
                flash(str(exc), 'warning')
            return redirect(url_for('publish_task_platforms', task_id=root['id']))
        from modules.media_policy import delivery_ready
        from modules.media_progress import snapshot
        media_ready = snapshot(root, config)['ready']
        return render_template('publish_platforms.html', task=root, cards=cards, state=state,
                               supplement=request.args.get('supplement') == '1',
                               selected=[] if request.args.get('supplement') == '1' else json.loads(state['targets']), csrf_token=token, media_ready=media_ready,
                               summary_status=summary_status(root), summary_needed=needs_summary(root))
