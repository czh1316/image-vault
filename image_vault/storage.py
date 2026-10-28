import hashlib
import io
import uuid
import warnings
from pathlib import Path

from flask import current_app
from PIL import Image, UnidentifiedImageError

from .auth import now
from .db import get_db

Image.MAX_IMAGE_PIXELS = 40_000_000
FORMATS = {'JPEG': ('jpg', 'image/jpeg'), 'PNG': ('png', 'image/png'), 'WEBP': ('webp', 'image/webp'), 'GIF': ('gif', 'image/gif')}


def image_path(user_id, filename):
    return Path(current_app.config['DATA_DIR']) / 'images' / user_id / filename


def store_image(user_id, job_id, keyword, candidate, data):
    if not data or len(data) > current_app.config['MAX_IMAGE_BYTES']:
        raise ValueError('图片为空或超过单张大小限制')
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as image:
                fmt, (width, height) = image.format, image.size
                if fmt not in FORMATS:
                    raise ValueError('不支持的图片格式')
                image.verify()
    except (UnidentifiedImageError, OSError, SyntaxError, Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise ValueError('下载内容不是有效图片') from exc
    if width <= 0 or height <= 0:
        raise ValueError('图片尺寸无效')
    sha = hashlib.sha256(data).hexdigest()
    ext, mime = FORMATS[fmt]
    db = get_db()
    path = None
    try:
        db.execute('BEGIN IMMEDIATE')
        if job_id:
            job = db.execute('SELECT status FROM jobs WHERE id=? AND user_id=?', (job_id, user_id)).fetchone()
            if not job or job['status'] != 'running':
                db.rollback()
                return {'status': 'cancelled'}
        existing = db.execute('SELECT id FROM images WHERE user_id=? AND sha256=?', (user_id, sha)).fetchone()
        if existing:
            db.rollback()
            return {'status': 'duplicate', 'id': existing['id']}
        used = db.execute('SELECT COALESCE(SUM(size),0) FROM images WHERE user_id=?', (user_id,)).fetchone()[0]
        if used + len(data) > current_app.config['USER_QUOTA_BYTES']:
            raise ValueError('账户存储配额已满，请删除部分图片')
        image_id = uuid.uuid4().hex
        filename = image_id + '.' + ext
        path = image_path(user_id, filename)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        db.execute('INSERT INTO images VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
            (image_id, user_id, job_id, keyword, str(candidate.get('title') or keyword)[:300],
             candidate.get('source', 'unknown'), candidate['url'], str(candidate.get('page_url') or '')[:4000],
             sha, filename, mime, len(data), width, height, now()))
        db.commit()
        return {'status': 'saved', 'id': image_id}
    except Exception:
        db.rollback()
        if path is not None:
            path.unlink(missing_ok=True)
        raise
