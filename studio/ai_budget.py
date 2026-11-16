"""Durable local request budgets. Reservations include failed/unknown billed attempts."""
import contextvars
import functools
import json
import os
import sqlite3
import time
import uuid
from contextlib import closing, contextmanager
from pathlib import Path
import psutil
from .production_errors import ProductionFailure

TASK = contextvars.ContextVar('ai_task', default='manual')
DEFAULTS = {'AI_CALLS_24H':500, 'AI_CALLS_PER_TASK':300, 'AI_SPEECH_CHARS_24H':100000}


def limits(cfg):
    result={k:cfg.get(k,v) for k,v in DEFAULTS.items()}
    if any(type(v) is not int or not 1<=v<=1000000 for v in result.values()):
        raise ProductionFailure('ai_budget_config','调用限额须为 1–1000000 的整数，请在制作服务设置中调整。')
    return result


def task_scope(key):
    def decorate(fn):
        @functools.wraps(fn)
        def wrapped(*args,**kwargs):
            token=TASK.set(str(key))
            try:return fn(*args,**kwargs)
            finally:TASK.reset(token)
        return wrapped
    return decorate


@contextmanager
def connection(data):
    path=Path(data)/'ai-usage.sqlite';path.parent.mkdir(parents=True,exist_ok=True)
    with closing(sqlite3.connect(path,timeout=30)) as db:
        db.execute('CREATE TABLE IF NOT EXISTS requests(id TEXT PRIMARY KEY, at REAL, task TEXT, kind TEXT, chars INTEGER, status TEXT, tokens INTEGER, pid INTEGER, process_created REAL)')
        db.execute('CREATE INDEX IF NOT EXISTS requests_at ON requests(at)')
        db.execute('CREATE INDEX IF NOT EXISTS requests_task ON requests(task)')
        db.commit()
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise


def summary(data):
    with connection(data) as db:
        count,chars,tokens,known=db.execute('SELECT count(*),coalesce(sum(chars),0),coalesce(sum(tokens),0),count(tokens) FROM requests WHERE at>?',(time.time()-86400,)).fetchone()
    return dict(calls_24h=count,speech_chars_24h=chars,reported_tokens_24h=tokens,requests_with_token_usage=known,concurrency=2)


class Meter:
    tokens=None
    def record(self,usage):
        value=usage.get('total_tokens') if isinstance(usage,dict) else getattr(usage,'total_tokens',None)
        if type(value) is int and value>=0:self.tokens=value


@contextmanager
def request(data,cfg,kind='text',characters=0):
    # Running speech jobs must honor a newly lowered ceiling on their next request.
    config_path=Path(data)/'production-ai.json'
    if config_path.is_file():
        saved=json.loads(config_path.read_text(encoding='utf-8'))
        cfg=dict(cfg,**{k:saved[k] for k in DEFAULTS if k in saved})
    ceiling=limits(cfg);now=time.time();key=uuid.uuid4().hex;task=TASK.get()
    with connection(data) as db:
        db.execute('BEGIN IMMEDIATE')
        # Only clear abandoned reservations; a different process still running owns its slot.
        for rid,pid,created in db.execute("SELECT id,pid,process_created FROM requests WHERE status='running'").fetchall():
            try:alive=abs(psutil.Process(pid).create_time()-created)<.1
            except psutil.Error:alive=False
            if not alive:db.execute("UPDATE requests SET status='unknown' WHERE id=?",(rid,))
        count,chars=db.execute('SELECT count(*),coalesce(sum(chars),0) FROM requests WHERE at>?',(now-86400,)).fetchone()
        task_count=db.execute('SELECT count(*) FROM requests WHERE task=?',(task,)).fetchone()[0]
        if count>=ceiling['AI_CALLS_24H'] or task_count>=ceiling['AI_CALLS_PER_TASK'] or chars+characters>ceiling['AI_SPEECH_CHARS_24H']:
            raise ProductionFailure('ai_budget','已达到调用或配音字数限额，已完成内容保留。请在“设置 → 制作服务”查看用量并调整限额后继续。')
        if db.execute("SELECT count(*) FROM requests WHERE status='running'").fetchone()[0]>=2:
            raise ProductionFailure('ai_busy','已有两个 AI 请求正在处理，请稍后从当前断点继续。',True)
        db.execute('INSERT INTO requests VALUES(?,?,?,?,?,?,?,?,?)',(key,now,task,kind,characters,'running',None,os.getpid(),psutil.Process().create_time()))
    meter=Meter()
    try:
        yield meter
    except BaseException:
        with connection(data) as db:db.execute("UPDATE requests SET status='failed',tokens=? WHERE id=?",(meter.tokens,key))
        raise
    else:
        with connection(data) as db:db.execute("UPDATE requests SET status='completed',tokens=? WHERE id=?",(meter.tokens,key))


def speech_config(data,cfg):
    return dict(cfg, _request_budget=lambda text:request(data,cfg,'speech',len(text)))
