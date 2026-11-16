"""Durable, account-scoped engagement rules and an at-most-once outbox."""
import hashlib
import json
import re
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path


def matches(rule, text):
    text = text.strip().casefold()
    return any((k.casefold() == text if rule['match_mode'] == 'exact' else k.casefold() in text)
               for k in rule['keywords'])


def validate(data):
    result = {}
    for field, maximum in [('work_id', 40), ('work_title', 5000), ('dm_text', 1000),
                           ('success_reply', 100), ('restricted_reply', 100)]:
        value = data.get(field, '')
        if not isinstance(value, str) or len(value.strip()) > maximum:
            raise ValueError('字段格式或长度不正确：' + field)
        result[field] = value.strip()
    if not result['work_id'].isdigit() or not result['dm_text'] or not result['success_reply']:
        raise ValueError('请选择作品，并填写私信与成功回评文案。')
    keywords = data.get('keywords')
    if not isinstance(keywords, list) or not 1 <= len(keywords) <= 20:
        raise ValueError('请填写 1–20 个关键词。')
    if any(not isinstance(k, str) or not k.strip() or len(k) > 50 for k in keywords):
        raise ValueError('关键词不能为空，每个最多 50 字。')
    result['keywords'] = list(dict.fromkeys(part.strip() for k in keywords for part in re.split(r'[\n,，、]', k) if part.strip()))
    if not 1 <= len(result['keywords']) <= 20:
        raise ValueError('请填写 1–20 个关键词。')
    result['match_mode'] = data.get('match_mode', 'contains')
    if result['match_mode'] not in ('contains', 'exact'):
        raise ValueError('不支持的匹配方式。')
    cap = data.get('daily_cap', 0)
    if type(cap) is not int or not 0 <= cap <= 1000000:
        raise ValueError('人数须为非负整数，0 表示不设本地上限。')
    result['daily_cap'] = cap
    # Reserved extension, never executed by the fixed-template engine.
    result['reply_mode'] = 'template'
    return result


class Store:
    def __init__(self, path, backup=False):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Every open of an existing store takes a consistent backup before recovery/migration.
        if backup and self.path.exists():
            dest = self.path.parent / 'backups'
            dest.mkdir(exist_ok=True)
            with sqlite3.connect(self.path) as src, sqlite3.connect(dest / f'automation-{time.time_ns()}.sqlite3') as dst:
                src.backup(dst)
        with self.db() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS rules (
                  id TEXT PRIMARY KEY, account_id TEXT NOT NULL, work_id TEXT NOT NULL,
                  config TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 0,
                  since REAL NOT NULL DEFAULT 0, revision INTEGER NOT NULL DEFAULT 1,
                  UNIQUE(account_id, work_id));
                CREATE TABLE IF NOT EXISTS jobs (
                  id TEXT PRIMARY KEY, rule_id TEXT NOT NULL, account_id TEXT NOT NULL,
                  work_id TEXT NOT NULL, recipient TEXT NOT NULL, payload TEXT NOT NULL,
                  state TEXT NOT NULL, message TEXT NOT NULL DEFAULT '', created REAL NOT NULL,
                  updated REAL NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
                  due REAL NOT NULL DEFAULT 0, sent_at REAL,
                  UNIQUE(account_id, work_id, recipient));
                CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            ''')

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        try:
            db.execute('PRAGMA journal_mode=WAL')
            db.execute('BEGIN IMMEDIATE')
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def recover(self):
        with self.db() as db:
            db.execute("UPDATE jobs SET state='needs_check', message='服务重启时发送结果未确认；不会自动重发。', updated=? WHERE state IN ('dm_sending','reply_sending')", (time.time(),))
            db.execute("UPDATE jobs SET state='queued' WHERE state='preparing'")

    @staticmethod
    def rule(row):
        return dict(json.loads(row['config']), id=row['id'], account_id=row['account_id'],
                    enabled=bool(row['enabled']), since=row['since'], revision=row['revision'])

    def rules(self, account_id=None):
        with self.db() as db:
            rows = db.execute('SELECT * FROM rules' + (' WHERE account_id=?' if account_id else '') + ' ORDER BY rowid',
                              (account_id,) if account_id else ()).fetchall()
        counts = {}
        result = []
        for row in rows:
            uid = row['account_id']
            counts[uid] = counts.get(uid, 0) + 1
            result.append(dict(self.rule(row), name=f'规则 {counts[uid]}'))
        return result

    def due_rule(self, claim=False, now=None):
        now = time.time() if now is None else now
        with self.db() as db:
            rows = db.execute('SELECT * FROM rules WHERE enabled=1 ORDER BY rowid').fetchall()
            candidates = []
            for row in rows:
                key = 'scan:' + row['id']
                saved = db.execute('SELECT value FROM meta WHERE key=?', (key,)).fetchone()
                due = float(saved['value']) if saved else 0
                if due <= now:
                    candidates.append((due, row, key))
            if not candidates:
                return None
            _, row, key = min(candidates, key=lambda x:x[0])
            if claim:
                db.execute('INSERT OR REPLACE INTO meta VALUES(?,?)', (key, str(now+600)))
            return self.rule(row)

    def save(self, account_id, data):
        config = validate(data)
        rid = hashlib.sha256(f'douyin:{account_id}:{config["work_id"]}'.encode()).hexdigest()
        with self.db() as db:
            old = db.execute('SELECT * FROM rules WHERE id=?', (rid,)).fetchone()
            if old and old['enabled']:
                raise ValueError('请先暂停规则，再修改文案。')
            db.execute('''INSERT INTO rules(id, account_id, work_id, config) VALUES(?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET config=excluded.config, revision=rules.revision+1''',
                       (rid, account_id, config['work_id'], json.dumps(config, ensure_ascii=False)))
            if old:
                db.execute("UPDATE jobs SET state='needs_check',message='文案已修改；旧任务已有私信结果，请核对后人工完成回评。' WHERE rule_id=? AND state IN ('dm_sent','dm_restricted')", (rid,))
        return rid

    def toggle(self, account_id, rid, enabled):
        with self.db() as db:
            row = db.execute('SELECT * FROM rules WHERE id=? AND account_id=?', (rid, account_id)).fetchone()
            if not row:
                raise ValueError('规则不存在或属于其他账号。')
            # Re-enable starts fresh; old queued work never adopts edited wording.
            if bool(row['enabled']) != enabled:
                db.execute('UPDATE rules SET enabled=?, since=? WHERE id=?', (int(enabled), time.time(), rid))
                if not enabled:
                    db.execute("UPDATE jobs SET state='cancelled',message='规则暂停，未发送任务已取消。' WHERE rule_id=? AND state IN ('queued','retry_wait','preparing')", (rid,))

    def pause(self, account_id):
        for r in self.rules(account_id):
            self.toggle(account_id, r['id'], False)

    def enqueue(self, rule, comment):
        if comment['created_at'] <= rule['since'] or comment.get('is_author') or not matches(rule, comment['text']):
            return False
        jid = hashlib.sha256(f'douyin:{rule["account_id"]}:{comment["id"]}'.encode()).hexdigest()
        payload = json.dumps({'rule': rule, 'comment': comment}, ensure_ascii=False)
        with self.db() as db:
            live = db.execute('SELECT enabled, revision, since FROM rules WHERE id=?', (rule['id'],)).fetchone()
            if not live or not live['enabled'] or live['revision'] != rule['revision'] or live['since'] != rule['since']:
                return False
            return db.execute('''INSERT OR IGNORE INTO jobs
                (id,rule_id,account_id,work_id,recipient,payload,state,created,updated)
                VALUES(?,?,?,?,?,?,'queued',?,?)''',
                (jid, rule['id'], rule['account_id'], rule['work_id'], comment['recipient'], payload, time.time(), time.time())).rowcount == 1

    def next_job(self):
        with self.db() as db:
            last = db.execute('SELECT MAX(sent_at) FROM jobs').fetchone()[0]
            if last and time.time()-last < 120:
                return None
            row = db.execute('''SELECT j.* FROM jobs j JOIN rules r ON r.id=j.rule_id
                WHERE r.enabled=1 AND j.state IN ('queued','retry_wait','dm_sent','dm_restricted')
                AND j.due<=? ORDER BY j.created LIMIT 1''', (time.time(),)).fetchone()
            if not row:
                return None
            job = dict(row)
            job['payload'] = json.loads(job['payload'])
            return job

    def allowed(self, job, dm=False):
        with self.db() as db:
            current = db.execute('SELECT state FROM jobs WHERE id=?', (job['id'],)).fetchone()
            if not current or current['state'] in ('cancelled', 'needs_check', 'completed'):
                return False
            r = db.execute('SELECT * FROM rules WHERE id=?', (job['rule_id'],)).fetchone()
            if not r or not r['enabled'] or r['revision'] != job['payload']['rule']['revision']:
                return False
            if dm:
                # Rolling 24h cap is deliberately a local safety setting, not a platform quota.
                n = db.execute('SELECT COUNT(*) FROM jobs WHERE rule_id=? AND sent_at>?',
                               (job['rule_id'], time.time()-86400)).fetchone()[0]
                cap = json.loads(r['config'])['daily_cap']
                return cap == 0 or n < cap
            return True

    def update(self, jid, state, message='', **fields):
        if not set(fields) <= {'attempts', 'due', 'sent_at'}:
            raise ValueError('Invalid state fields')
        with self.db() as db:
            columns = ','.join(k+'=?' for k in fields)
            db.execute(f"UPDATE jobs SET state=?,message=?,updated=?{','+columns if columns else ''} WHERE id=?",
                       (state, message, time.time(), *fields.values(), jid))

    def record_scan(self, rid, state, message='', **counts):
        with self.db() as db:
            key = 'scan-result:' + rid
            previous = db.execute('SELECT value FROM meta WHERE key=?',(key,)).fetchone()
            data = json.loads(previous['value']) if previous else {}
            if state == 'running':
                data = {'started_at':time.time(), 'last_success_at':data.get('last_success_at')}
            data.update(state=state, message=message, at=time.time(), **counts)
            if state == 'success':
                data['last_success_at'] = data['at']
            db.execute('INSERT OR REPLACE INTO meta VALUES(?,?)',(key,json.dumps(data,ensure_ascii=False)))

    def record_outcome(self, jid, **values):
        if not set(values) <= {'dm','reply'}:
            raise ValueError('Invalid outcome fields')
        with self.db() as db:
            key = 'outcome:' + jid
            previous = db.execute('SELECT value FROM meta WHERE key=?',(key,)).fetchone()
            data = json.loads(previous['value']) if previous else {}
            data.update(values)
            db.execute('INSERT OR REPLACE INTO meta VALUES(?,?)',(key,json.dumps(data)))

    def status(self, account_id):
        with self.db() as db:
            rows = db.execute('SELECT * FROM jobs WHERE account_id=? ORDER BY updated DESC', (account_id,)).fetchall()
            meta = {r['key']: json.loads(r['value']) for r in db.execute('SELECT * FROM meta')}
        logs, totals = [], {}
        for row in rows:
            p = json.loads(row['payload'])
            outcome = meta.get('outcome:'+row['id'], {})
            # Legacy jobs have no receipt audit; never infer DM success from "completed".
            dm = outcome.get('dm') or ('sent' if row['state']=='dm_sent' else 'restricted' if row['state']=='dm_restricted' else 'not_submitted' if not row['sent_at'] else 'unknown')
            reply = outcome.get('reply','not_confirmed')
            stats = totals.setdefault(row['rule_id'], {'total':0,'dm_sent':0,'reply_sent':0,'attention':0,'pending':0})
            stats['total'] += 1
            stats['dm_sent'] += dm == 'sent'
            stats['reply_sent'] += reply == 'sent'
            stats['attention'] += row['state'] == 'needs_check'
            stats['pending'] += row['state'] in ('queued','preparing','dm_sending','dm_sent','dm_restricted','reply_sending','retry_wait')
            if len(logs)<100:
                entry = {k:row[k] for k in ('id','rule_id','state','message','created','updated','attempts')}
                entry.update(author=p['comment']['author'],comment=p['comment']['text'],work_title=p['rule']['work_title'],work_id=row['work_id'],dm_result=dm,reply_result=reply)
                logs.append(entry)
        rules = self.rules(account_id)
        for r in rules:
            r['stats'] = totals.get(r['id'],dict(total=0,dm_sent=0,reply_sent=0,attention=0,pending=0))
            r['scan'] = meta.get('scan-result:'+r['id'])
            r['next_scan_at'] = meta.get('scan:'+r['id']) if r['enabled'] else None
        return {'rules':rules,'logs':logs,'worker':{'worker':meta.get('worker',{})}}

    def heartbeat(self, state, message):
        with self.db() as db:
            db.execute("INSERT OR REPLACE INTO meta VALUES('worker',?)",
                       (json.dumps({'state': state, 'message': message, 'at': time.time()}),))
