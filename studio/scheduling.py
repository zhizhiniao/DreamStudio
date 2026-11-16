"""One server-side schedule contract for edits and every submission entry point."""
import math
import time


def timestamp(value):
    if isinstance(value,bool) or not isinstance(value,(int,float)):
        raise ValueError('计划时间格式无效，请重新选择时间或清除排期。')
    if not 0<=value<=253402300799 or not math.isfinite(value):
        raise ValueError('计划时间超出有效范围，请重新选择时间或清除排期。')
    return value


def issue(draft):
    try:
        value=timestamp(draft.get('planned_at',0))
    except ValueError as exc:
        return str(exc)
    return '未到计划时段；立即发布请先清除计划时间' if value>time.time() else ''
