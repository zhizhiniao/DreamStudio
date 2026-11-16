"""SQLite records. Credentials stay outside API/database responses."""
import json
import sqlite3
import time
import uuid
from contextlib import contextmanager,closing
from pathlib import Path

SCHEMA = '''
CREATE TABLE IF NOT EXISTS records(kind TEXT NOT NULL,id TEXT NOT NULL,data TEXT NOT NULL,PRIMARY KEY(kind,id));
CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY AUTOINCREMENT,at REAL NOT NULL,action TEXT NOT NULL,detail TEXT NOT NULL);
'''


class Store:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / 'studio.sqlite'
        if self.path.exists():
            self.backup('startup')
        with self.connect() as db:
            db.executescript(SCHEMA)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.execute('PRAGMA journal_mode=WAL')
        db.execute('PRAGMA busy_timeout=30000')
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def backup(self, label='checkpoint'):
        folder = self.root / 'backups'
        folder.mkdir(exist_ok=True)
        target = folder / f'{label}-{time.time_ns()}.sqlite'
        with closing(sqlite3.connect(self.path)) as source, closing(sqlite3.connect(target)) as dest:
            source.backup(dest)
        return target

    def get(self, kind, key, default=None):
        with self.connect() as db:
            result = db.execute('SELECT data FROM records WHERE kind=? AND id=?', (kind, key)).fetchone()
        return json.loads(result[0]) if result else default

    def list(self, kind):
        with self.connect() as db:
            rows = db.execute('SELECT data FROM records WHERE kind=? ORDER BY rowid DESC', (kind,)).fetchall()
        return [json.loads(row[0]) for row in rows]

    def put(self, kind, data):
        data = dict(data)
        data.setdefault('id', uuid.uuid4().hex)
        at = time.time()
        data['updated_at'] = at
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            previous = db.execute('SELECT data FROM records WHERE kind=? AND id=?', (kind, data['id'])).fetchone()
            from .activity import track
            data = track(kind, data, json.loads(previous[0]) if previous else None, at)
            db.execute('INSERT INTO records VALUES(?,?,?) ON CONFLICT(kind,id) DO UPDATE SET data=excluded.data',
                       (kind, data['id'], json.dumps(data, ensure_ascii=False)))
        return data

    def event(self, action, detail=''):
        with self.connect() as db:
            db.execute('INSERT INTO events(at,action,detail) VALUES(?,?,?)', (time.time(), action, detail))

    def events(self):
        with self.connect() as db:
            rows = db.execute('SELECT id,at,action,detail FROM events ORDER BY id DESC LIMIT 200').fetchall()
        return [dict(zip(('id','at','action','detail'), row)) for row in rows]
