"""Per-draft covers: immutable files, human review, no task-wide mutation."""
import io
import uuid
from pathlib import Path
from flask import jsonify, request, send_file
from PIL import Image, ImageOps, UnidentifiedImageError
from . import media

BUSY = ('queued', 'running', 'succeeded', 'needs_check')
SIZES = {'landscape': (1280, 720), 'portrait': (1080, 1440)}


def editable(store, draft):
    if draft.get('status') in ('published', 'submitted', 'succeeded') or any(
        p.get('draft_id') == draft['id'] and p.get('status') in BUSY for p in store.list('publications')
    ):
        raise ValueError('此稿件正在发布、已提交或结果待核实，请先查看回执')


def selected(draft, task, name):
    field = 'cover_' + name + '_path'
    return draft.get(field) or task.get(field)


def validate(draft, task):
    for name in SIZES:
        field = 'cover_' + name + '_path'
        owner = draft if draft.get(field) else task
        path = selected(draft, task, name)
        try:
            with Image.open(path) as image:
                image.verify()
            if owner.get(field + '_hash') and media.checksum(path) != owner[field + '_hash']:
                raise ValueError('封面文件已变化，请重新选择并保存封面')
        except (OSError, TypeError):
            raise ValueError('横版或竖版封面缺失／损坏，请在调整封面中重新选择')


def register(app, store, record, lock):
    @app.get('/api/drafts/<key>/covers/<name>')
    def draft_cover_image(key, name):
        if name not in SIZES:raise ValueError('请选择横版或竖版封面')
        draft=record('drafts',key);task=record('tasks',draft['task_id'])
        path=selected({} if request.args.get('source')=='task' else draft,task,name)
        if not path or not Path(path).is_file():raise ValueError('封面文件不存在，请上传图片')
        return send_file(path, max_age=0)

    @app.post('/api/drafts/<key>/covers/<name>')
    def save_draft_cover(key, name):
        with lock:
            if name not in SIZES:raise ValueError('请选择横版或竖版封面')
            draft=record('drafts',key);editable(store,draft)
            upload=request.files.get('file')
            if not upload:raise ValueError('请选择封面图片')
            raw=upload.stream.read(15*1024*1024+1)
            if len(raw)>15*1024*1024:raise ValueError('封面图片不能超过 15 MB')
            try:
                with Image.open(io.BytesIO(raw)) as image:
                    if image.width*image.height>40_000_000:raise ValueError('图片像素过大')
                    image=ImageOps.exif_transpose(image).convert('RGB')
                    size=(1440,1080) if name=='landscape' and draft.get('platform')=='douyin' else SIZES[name]
                    image=ImageOps.pad(image,size,color='#211c18')
            except (UnidentifiedImageError,OSError,Image.DecompressionBombError):
                raise ValueError('无法读取图片，请选择有效的 JPG、PNG 或 WebP')
            store.backup('before-draft-cover')
            folder=store.root/'draft-covers'/key;folder.mkdir(parents=True,exist_ok=True)
            path=folder/(name+'-'+uuid.uuid4().hex+'.jpg');image.save(path,quality=95)
            field='cover_'+name+'_path'
            draft.update({field:str(path),field+'_hash':media.checksum(path),
                          'reviewed':False,'status':'draft','manually_edited':True})
            return jsonify(store.put('drafts',draft))
