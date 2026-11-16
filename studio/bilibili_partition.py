"""Local content-based partition defaults; explicit selections always win."""
import json
import re
from pathlib import Path


def zones():
    data=json.loads((Path(__file__).resolve().parents[1]/'vendor/modules/bili_sdk/data/video_zone.json').read_text(encoding='utf-8'))
    return [{'tid':int(s['tid']),'name':p['name']+' / '+s['name']} for p in data for s in p.get('sub',[]) if s.get('tid')]


def apply(draft, task=None):
    if draft.get('platform')!='bilibili' or draft.get('bilibili_tid'):return False
    task=task or {}
    choices=zones();valid={z['tid'] for z in choices}
    inherited=task.get('bilibili_tid')
    text=' '.join(str(x) for x in [draft.get('title',''),draft.get('body',''),*draft.get('tags',[])]).lower()
    rules=[(230,r'after effects|\bae\b|photoshop|premiere|blender|剪映|达芬奇|软件|插件|剪辑|特效|调色|motion design|comfyui'),
           (231,r'python|javascript|编程|代码|算法|数据库|linux|docker'),
           (76,r'菜谱|烘焙|做菜|食谱|烹饪'),(244,r'乐理|吉他教学|钢琴教学'),
           (156,r'舞蹈教学|舞蹈教程'),(229,r'设计|摄影|拍摄|构图'),
           (201,r'物理|化学|天文|科学科普'),(228,r'历史|考古'),
           (207,r'财经|经济|商业'),(250,r'旅行|旅游|游记'),(164,r'健身|锻炼')]
    tid=int(inherited) if str(inherited).isdigit() and int(inherited) in valid else next((tid for tid,pattern in rules if tid in valid and re.search(pattern,text)),None)
    if tid is None:
        # Prefer an exact child-category name in the content before the broad daily default.
        tid=next((z['tid'] for z in choices if len(z['name'].split(' / ')[-1])>1 and z['name'].split(' / ')[-1] in text),21)
    draft.update(bilibili_tid=tid,bilibili_partition_source='automatic')
    return True


def label(tid):
    return next((z['name'] for z in zones() if str(z['tid'])==str(tid)),'')
