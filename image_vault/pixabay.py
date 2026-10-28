"""Official photo search, persistent 24-hour caching and a shared key budget."""
import hashlib
import json
import math
import os
import sqlite3
import threading
import time
from contextlib import closing
from pathlib import Path
from urllib.parse import urlencode, urlsplit

from flask import current_app, has_app_context

from .network import FetchError, fetch_url, is_pixabay_signed_photo

_search_lock = threading.Lock()
CACHE_SECONDS = 24 * 60 * 60


class PixabayError(ValueError):
    pass


def settings():
    default_dir = Path(__file__).resolve().parent.parent / 'data'
    config = current_app.config if has_app_context() else {}
    directory = Path(config.get('DATA_DIR', os.environ.get('VAULT_DATA_DIR', str(default_dir))))
    key = config.get('PIXABAY_API_KEY', os.environ.get('PIXABAY_API_KEY', '')).strip()
    if not key and 'PIXABAY_API_KEY' not in config:
        path = directory / 'private-settings.json'
        if path.exists():
            try:
                key = str(json.loads(path.read_text(encoding='utf-8')).get('PIXABAY_API_KEY', '')).strip()
            except (OSError, ValueError, TypeError, AttributeError):
                raise PixabayError('Pixabay 后端配置无法读取，请检查 private-settings.json。') from None
    if not key:
        raise PixabayError('请在后端配置 Pixabay API Key 后重试。')
    return directory, key


def parse_photos(payload):
    # Keep URL validation shared with the legacy HTML parser; never accept videos.
    from .crawler import _pixabay_detail_url, _pixabay_image_url

    if not isinstance(payload, dict) or not isinstance(payload.get('hits'), list):
        raise PixabayError('Pixabay API 返回了无法识别的数据，请稍后重试。')
    rows, seen = [], set()
    def image_url(value):
        if isinstance(value, str) and is_pixabay_signed_photo(urlsplit(value)):
            return value
        return _pixabay_image_url(value)

    for hit in payload['hits']:
        if not isinstance(hit, dict) or hit.get('type') != 'photo':
            continue
        url = image_url(hit.get('largeImageURL')) or image_url(hit.get('webformatURL'))
        page_url = _pixabay_detail_url(hit.get('pageURL'))
        if not url or not page_url or url in seen:
            continue
        seen.add(url)
        rows.append({'url': url, 'title': str(hit.get('tags') or '')[:500],
                     'source': 'pixabay', 'page_url': page_url})
    return rows


def _integer_header(headers, name, default):
    try:
        return max(0, min(86400, int(headers.get(name, default))))
    except (TypeError, ValueError):
        return default


def _record_headers(db, key_hash, headers, limited=False):
    headers = {k.lower(): v for k, v in headers.items()}
    if limited or _integer_header(headers, 'x-ratelimit-remaining', 100) == 0:
        delay = max(1, _integer_header(headers, 'x-ratelimit-reset',
                                     _integer_header(headers, 'retry-after', 60)))
        db.execute('INSERT INTO cooldown(key_hash,until_at) VALUES (?,?) '
                   'ON CONFLICT(key_hash) DO UPDATE SET until_at=MAX(until_at,excluded.until_at)',
                   (key_hash, time.time() + delay))
        db.commit()


def _reserve_request(db, key_hash, stamp):
    db.execute('BEGIN IMMEDIATE')
    db.execute('DELETE FROM requests WHERE requested_at <= ?', (stamp - 60,))
    cooldown = db.execute('SELECT until_at FROM cooldown WHERE key_hash=?', (key_hash,)).fetchone()
    count, oldest = db.execute('SELECT COUNT(*), MIN(requested_at) FROM requests WHERE key_hash=?',
                               (key_hash,)).fetchone()
    wait = max(0, cooldown[0] - stamp) if cooldown else 0
    if count >= 100:
        wait = max(wait, oldest + 60 - stamp)
    if wait > 0:
        db.rollback()
        raise PixabayError(f'Pixabay API 请求额度暂时用尽，请在 {math.ceil(wait)} 秒后重试；缓存结果仍可使用。')
    db.execute('INSERT INTO requests(key_hash,requested_at) VALUES (?,?)', (key_hash, stamp))
    db.commit()


def search_photos(keyword, page):
    directory, key = settings()
    if has_app_context():
        directory = Path(current_app.config.get('PIXABAY_CACHE_DIR', directory))
    # The official API exposes at most 500 results per query, 50 per page here.
    if not 0 <= page < 10:
        return []
    query = {'q': keyword.strip(), 'lang': 'zh', 'image_type': 'photo',
             'safesearch': 'true', 'per_page': 50, 'page': page + 1}
    key_hash = hashlib.sha256(key.encode()).hexdigest()
    cache_id = hashlib.sha256(('signed-photos-v1:' + key_hash + urlencode(query)).encode()).hexdigest()
    directory.mkdir(parents=True, exist_ok=True)
    # Serialize cache misses across the application's workers. SQLite reservations
    # survive restarts and prevent each account from getting its own API budget.
    with _search_lock, closing(sqlite3.connect(directory / 'pixabay-cache.sqlite3', timeout=20)) as db:
        db.executescript('''
            CREATE TABLE IF NOT EXISTS cache (
                id TEXT PRIMARY KEY, expires_at REAL NOT NULL, rows_json TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS requests (key_hash TEXT NOT NULL, requested_at REAL NOT NULL);
            CREATE INDEX IF NOT EXISTS requests_key ON requests(key_hash,requested_at);
            CREATE TABLE IF NOT EXISTS cooldown (key_hash TEXT PRIMARY KEY, until_at REAL NOT NULL);
        ''')
        stamp = time.time()
        cached = db.execute('SELECT rows_json FROM cache WHERE id=? AND expires_at>?', (cache_id, stamp)).fetchone()
        if cached:
            return json.loads(cached[0])
        _reserve_request(db, key_hash, stamp)
        url = 'https://pixabay.com/api/?' + urlencode(dict(key=key, **query))
        try:
            data, _mime, _final, headers = fetch_url(url, max_bytes=4 * 1024 * 1024,
                                                    timeout=15, include_headers=True)
        except FetchError as exc:
            _record_headers(db, key_hash, exc.headers, limited=exc.status == 429)
            if exc.status in (400, 401):
                message = f'Pixabay API HTTP {exc.status}：请检查后端 API Key 是否有效及搜索参数。'
            elif exc.status == 429:
                delay = _integer_header(exc.headers, 'x-ratelimit-reset', 60)
                message = f'Pixabay API HTTP 429：请求达到额度，请在 {max(1, delay)} 秒后重试。'
            elif exc.status is not None:
                message = f'Pixabay API HTTP {exc.status}：来源拒绝访问，请稍后重试或检查网络。'
            else:
                message = 'Pixabay API 连接失败，请检查网络后重试。'
            raise PixabayError(message) from None
        _record_headers(db, key_hash, headers)
        try:
            rows = parse_photos(json.loads(data))
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise PixabayError('Pixabay API 返回了非 JSON 数据，请检查网络后重试。') from None
        stamp = time.time()
        db.execute('DELETE FROM cache WHERE expires_at<=?', (stamp,))
        db.execute('INSERT OR REPLACE INTO cache(id,expires_at,rows_json) VALUES (?,?,?)',
                   (cache_id, stamp + CACHE_SECONDS, json.dumps(rows, ensure_ascii=False)))
        db.commit()
        return rows
