import sqlite3
from pathlib import Path

from flask import current_app, g

SCHEMA = '''
CREATE TABLE IF NOT EXISTS users (
 id TEXT PRIMARY KEY, username TEXT NOT NULL, username_key TEXT NOT NULL UNIQUE,
 password_hash TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
 token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 csrf_token TEXT NOT NULL, expires_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS jobs (
 id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id), keyword TEXT NOT NULL,
 source TEXT NOT NULL, actual_source TEXT, requested INTEGER NOT NULL,
 status TEXT NOT NULL, saved INTEGER NOT NULL DEFAULT 0,
 duplicates INTEGER NOT NULL DEFAULT 0, failed INTEGER NOT NULL DEFAULT 0,
 checked INTEGER NOT NULL DEFAULT 0, message TEXT NOT NULL DEFAULT '',
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS jobs_owner ON jobs(user_id, created_at);
CREATE TABLE IF NOT EXISTS images (
 id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id),
 job_id TEXT REFERENCES jobs(id), keyword TEXT NOT NULL, title TEXT NOT NULL,
 source TEXT NOT NULL, source_url TEXT NOT NULL, page_url TEXT NOT NULL DEFAULT '',
 sha256 TEXT NOT NULL, filename TEXT NOT NULL, mime TEXT NOT NULL,
 size INTEGER NOT NULL, width INTEGER NOT NULL, height INTEGER NOT NULL,
 created_at TEXT NOT NULL, UNIQUE(user_id, sha256)
);
CREATE INDEX IF NOT EXISTS images_owner ON images(user_id, created_at);
CREATE TABLE IF NOT EXISTS auth_attempts (
 address TEXT NOT NULL, attempted_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS auth_rate ON auth_attempts(address, attempted_at);
'''


def get_db():
    if 'db' not in g:
        db = sqlite3.connect(Path(current_app.config['DATA_DIR']) / 'vault.sqlite3', timeout=15)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        db.execute('PRAGMA busy_timeout=15000')
        g.db = db
    return g.db


def close_db(_error=None):
    db = g.pop('db', None)
    if db is not None:
        db.close()


def init_db(app):
    Path(app.config['DATA_DIR']).mkdir(parents=True, exist_ok=True)
    app.teardown_appcontext(close_db)
    with app.app_context():
        db = get_db()
        db.execute('PRAGMA journal_mode=WAL')
        db.executescript(SCHEMA)
        db.execute("UPDATE jobs SET status='interrupted', message='服务已重启，任务中断；已保存图片仍在图库。' WHERE status IN ('queued','running')")
        db.commit()
