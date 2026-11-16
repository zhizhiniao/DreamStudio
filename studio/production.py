"""Production confirmations belong to persisted content, not navigation buttons."""
import copy
import math
import re

MODES = ('dub', 'subtitles', 'original')
STEPS = ('title', 'description', 'tags', 'cover')
FIELDS = {'mode': -1, 'title': 0, 'cover_category': 0, 'description': 1, 'tags': 2, 'cover_selection':3, 'cover_crops':3,
          'cover_emphasis': 3, 'cover_color': 3, 'cover_title': 3, 'cover_style': 3, 'start': 3, 'duration': 3, 'subtitle': 3}


def chinese(value):
    return bool(re.search(r'[\u3400-\u9fff]', str(value)))


def linked_cover(title):
    text = re.sub(r'After\s+Effects\s*[:：]?\s*', 'AE ', title, flags=re.I).strip()
    return text if chinese(text) and len(text.replace('\n', '')) <= 30 and len(text.splitlines()) <= 3 else ''


def hydrate(task, asset):
    task = copy.deepcopy(task)
    from .cover_frames import with_original
    task['cover_frames']=with_original(task.get('cover_frames',[]),asset,task)
    task.setdefault('flow_version', 1)
    task.setdefault('workflow_stage','cover_review' if task.get('cover_landscape_path') else 'content')
    task.setdefault('mode', '')
    task.setdefault('confirmed', -1)
    task.setdefault('description', asset.get('description', ''))
    task.setdefault('tags', list(asset.get('tags') or []))
    task.setdefault('cover_style', 'collage')
    task.setdefault('cover_title', '')
    task.setdefault('cover_category', '影像创作')
    task.setdefault('suggestions', {})
    task.setdefault('production_job', {})
    task.setdefault('field_sources', {})
    if chinese(task.get('title', '')) and not chinese(task['cover_title']):
        task['cover_title'] = linked_cover(task['title'])
        task['field_sources']['cover_title'] = 'linked'
    return task


def editable(task):
    if task.get('status') == 'cancelled':
        raise ValueError('任务已取消，请从素材库重新创建任务。')
    if task.get('status') == 'rendering' or task.get('production_job', {}).get('status') == 'running':
        raise ValueError('任务正在处理，请等待完成后修改。')


def change(task, values):
    editable(task)
    if 'version' in values and values['version'] != task['version']:
        raise ValueError('任务已在其他窗口更新，请刷新后再操作。')
    changed = []
    if 'cover_selection' in values or 'cover_crops' in values:
        from .cover_frames import validate
        validate(values.get('cover_selection',task.get('cover_selection',[])),values.get('cover_crops',task.get('cover_crops',{})),task.get('cover_frames',[]))
    for field in FIELDS:
        if field not in values:
            continue
        value = values[field]
        if field in ('cover_selection','cover_crops'):
            value=copy.deepcopy(value)
        elif field == 'cover_emphasis':
            if type(value) is not int or not 0 <= value <= 2:raise ValueError('请选择第 1–3 行着色。')
        elif field in ('start', 'duration'):
            try:value = float(value)
            except (ValueError, TypeError):raise ValueError('片段参数必须是数字。') from None
            if not math.isfinite(value) or value < 0 or (field == 'duration' and value == 0):
                raise ValueError('片段参数无效。')
        elif field == 'tags':
            if not isinstance(value, list) or any(not isinstance(v, str) for v in value):
                raise ValueError('标签格式无效。')
            value = list(dict.fromkeys(v.strip().lstrip('#') for v in value if v.strip().lstrip('#')))
            if len(value) > 12 or any(len(v) > 20 for v in value):
                raise ValueError('最多 12 个内容标签，每项最多 20 字。')
        else:
            if not isinstance(value, str):raise ValueError('文本格式无效。')
            value = value.strip()
            if len(value) > (5000 if field == 'description' else 500):raise ValueError('内容过长。')
        if field == 'cover_color':
            if not re.fullmatch(r'#[0-9a-fA-F]{6}', value):raise ValueError('强调色须为 #RRGGBB 格式。')
            value = value.upper()
        if field == 'cover_category' and (not value or len(value)>6 or '\n' in value):
            raise ValueError('栏目标签请填写 1–6 字，不能换行。')
        if field == 'mode' and value not in MODES:raise ValueError('请选择一种视频处理方式。')
        if field == 'cover_style' and value not in ('original', 'collage'):raise ValueError('封面方式无效。')
        if task.get(field) != value:
            task[field] = value
            changed.append(field)
            task.setdefault('field_sources', {})[field] = 'manual'
    if 'cover_title' in changed:
        title = task['cover_title'].replace('\n', '').strip()
        if task.get('title') != title:
            task['title'] = title
            task['field_sources']['title'] = 'manual'
            changed.append('title')
    elif 'title' in changed:
        task['cover_title'] = task['title']
        task['field_sources']['cover_title'] = 'linked'
        changed.append('cover_title')
    if changed:
        if any(f in changed for f in ('title','cover_title','cover_style','cover_category','cover_selection','cover_crops','cover_emphasis','cover_color')):
            task.pop('cover_review_hash',None)
            task['workflow_stage']='cover'
        if any(f in changed for f in ('mode','start','duration','subtitle')):
            task.pop('speech_review_hash',None)
            task['workflow_stage']='ready_to_generate' if task.get('mode')!='dub' and task.get('cover_review_hash') else 'script'
        task.pop('prepared_version', None)
        content_changes=[FIELDS[f] for f in changed if f!='mode']
        if content_changes:task['confirmed'] = min(task['confirmed'], min(content_changes) - 1)
        task['confirmed'] = max(-1, task['confirmed'])
        task.update(version=task['version']+1, reviewed_version=None, status='draft', error='', progress='')
    return task


def confirm(task, step, asset):
    if not task.get('mode'):raise ValueError('请先选择处理方式。')
    if step not in range(4) or step > task['confirmed'] + 1:raise ValueError('请先完成前面的确认步骤。')
    if step == 0 and (not chinese(task['title']) or len(task['title']) > 100):
        raise ValueError('请填写 100 字以内的中文发布标题。')
    if step == 1 and not task['description'].strip():raise ValueError('请补充内容简介。')
    if step == 2 and not task['tags']:raise ValueError('请至少保留一个内容标签。')
    if step == 3:
        if task['cover_style'] == 'collage':
            text = task['cover_title']
            if not chinese(text) or len(text.replace('\n', '')) > 30 or not 1 <= len(text.splitlines()) <= 3:
                raise ValueError('拼贴封面须填写中文标题：最多 30 字、1–3 行。')
        else:
            from pathlib import Path
            if not Path(task.get('original_thumbnail') or asset.get('original_thumbnail') or '__missing__').is_file():
                raise ValueError('没有原始封面，请先上传原封面，或选择拼贴封面。')
        if task.get('start', 0) + task.get('duration', 0) > asset.get('duration', 0) + .1:
            raise ValueError('片段范围超出原视频时长。')
        if task['mode'] == 'original' and (task.get('start', 0) != 0 or abs(task.get('duration', 0)-asset['duration']) > .1):
            raise ValueError('保留原片不裁剪，请恢复完整时长或选择字幕/配音模式。')
    task['confirmed'] = max(task['confirmed'], step)
    return task
