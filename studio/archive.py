"""Project media and human-readable manifests, separate from private sessions."""
import json
import re
from pathlib import Path


class Archive:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def project(self, asset):
        # The stable asset key survives title edits and duplicate source titles.
        existing = asset.get('project_path')
        if existing:
            path = Path(existing).resolve()
            if not path.is_relative_to(self.root.resolve()):
                raise ValueError('项目目录越界')
        else:
            title = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', asset.get('title') or '新项目').strip(' .')[:48]
            path = self.root / f"{title or '新项目'}__{asset['id']}"
        for name in ('01_原始视频', '02_字幕', '03_制作', '04_文案'):
            (path / name).mkdir(parents=True, exist_ok=True)
        asset['project_path'] = str(path)
        return path

    def manifest(self, asset, tasks):
        path = self.project(asset)
        payload = {'title': asset['title'], 'asset_id': asset['id'],
                   'source_url': asset.get('source_url', ''), 'author': asset.get('author', ''),
                   'source_video': asset.get('path', ''), 'tasks': tasks}
        temp = path / 'project.json.tmp'
        temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
        temp.replace(path / 'project.json')
        for task in tasks:
            (path / '04_文案' / (task['id'] + '.txt')).write_text(
                f"标题：{task['title']}\n封面标题：{task.get('cover_title') or task['title']}\n来源：{asset.get('source_url', '')}\n作者：{asset.get('author', '')}\n版本：{task['version']}\n",
                encoding='utf-8')
