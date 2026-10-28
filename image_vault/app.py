import hashlib
import io
import os
import tempfile
import zipfile
from pathlib import Path

from flask import Flask, current_app, g, jsonify, request, send_file
from werkzeug.exceptions import HTTPException
from werkzeug.security import generate_password_hash

from . import auth
from .auth import error, require_user
from .db import get_db, init_db
from .jobs import init_jobs
from .storage import image_path

COVER_EXTENSIONS = {'.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.png': 'image/png', '.webp': 'image/webp', '.gif': 'image/gif'}
COVER_ORDER = (
    '9fc7ec93eecc4ee38450ffea6228def6.jpg',
    'f23dadab8d9e4401becb42c4180b3155.jpg',
    '3f5a5912e1064d25986a40bc0aa58c61.jpg',
    'cefde338a5804506b5b2012716224bd2.jpg',
    'ad6f108c6aa846b59a5b797637ec7fcd.jpg',
    '99ba2c9b156f45b7a3f93accd6a228bb.jpg',
    'd456444b003d4914bbb133228b200493.jpg',
    '77b915aed32b45369edb23e28b27526a.jpg',
)
COVER_CAPTIONS = {
    '9fc7ec93eecc4ee38450ffea6228def6.jpg': ('雾中森林', 'FOREST'),
    'f23dadab8d9e4401becb42c4180b3155.jpg': ('湖畔长椅', 'LAKE'),
    '3f5a5912e1064d25986a40bc0aa58c61.jpg': ('山麓牧场', 'ALPINE'),
    'cefde338a5804506b5b2012716224bd2.jpg': ('通往雪线', 'TRAIL'),
    'ad6f108c6aa846b59a5b797637ec7fcd.jpg': ('夏日花野', 'MEADOW'),
    '99ba2c9b156f45b7a3f93accd6a228bb.jpg': ('星河雪峰', 'NIGHT'),
    'd456444b003d4914bbb133228b200493.jpg': ('秋日公路', 'AUTUMN'),
    '77b915aed32b45369edb23e28b27526a.jpg': ('像素花园', 'PIXEL'),
}


def cover_rows():
    directory = Path(current_app.config['COVERS_DIR'])
    try:
        files = [path for path in directory.iterdir() if path.is_file() and path.suffix.lower() in COVER_EXTENSIONS]
    except OSError:
        return []
    order = {name: index for index, name in enumerate(COVER_ORDER)}
    files.sort(key=lambda path: (order.get(path.name, len(order)), path.name.lower()))
    rows = []
    for path in files[:12]:
        title, category = COVER_CAPTIONS.get(path.name, (path.stem[:40], 'COVER'))
        rows.append({'id': hashlib.sha1(path.name.encode('utf-8')).hexdigest()[:16], 'name': path.name, 'title': title, 'category': category})
    return rows


def create_app(config=None):
    root = Path(__file__).resolve().parent.parent
    app = Flask(__name__, static_folder=str(root / 'static'), static_url_path='/static')
    app.config.update(
        DATA_DIR=os.environ.get('VAULT_DATA_DIR', str(root / 'data')),
        COVERS_DIR=os.environ.get('VAULT_COVERS_DIR', str(root / 'image')),
        RUN_WORKERS=True, MAX_IMAGE_BYTES=12 * 1024 * 1024,
        USER_QUOTA_BYTES=int(os.environ.get('VAULT_QUOTA_MB', '500')) * 1024 * 1024,
        MAX_CONTENT_LENGTH=64 * 1024, COOKIE_SECURE=os.environ.get('VAULT_COOKIE_SECURE', '0') == '1',
        AUTH_RATE_LIMIT=40, CRAWL_DELAY=0.35, PAGE_DELAY=1.0,
        DUMMY_PASSWORD_HASH=generate_password_hash('not-a-real-account-password', method='scrypt'),
    )
    if config:
        app.config.update(config)
    init_db(app)
    app.before_request(auth.load_user)
    app.register_blueprint(auth.bp)
    init_jobs(app)

    @app.after_request
    def security_headers(response):
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['X-Frame-Options'] = 'DENY'
        response.headers['Referrer-Policy'] = 'no-referrer'
        response.headers['Content-Security-Policy'] = "default-src 'self'; img-src 'self' blob:; style-src 'self'; script-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'self'; frame-ancestors 'none'; form-action 'self'"
        response.headers['Permissions-Policy'] = 'camera=(), microphone=(), geolocation=()'
        if request.path.startswith('/api/showcase/') and request.path.endswith('/file'):
            response.headers['Cache-Control'] = 'public, max-age=86400'
        elif request.path.startswith('/api/'):
            response.headers['Cache-Control'] = 'no-store'
        return response

    @app.errorhandler(HTTPException)
    def http_error(exc):
        if request.path.startswith('/api/'):
            return error('请求不存在或格式不正确' if exc.code != 413 else '请求内容过大', exc.code)
        return exc

    @app.get('/')
    def index():
        return app.send_static_file('index.html')

    @app.get('/api/health')
    def health():
        return jsonify(ok=True)

    @app.get('/api/showcase')
    def showcase():
        rows = cover_rows()
        return jsonify(images=[{'id': row['id'], 'title': row['title'], 'category': row['category']} for row in rows])

    @app.get('/api/showcase/<cover_id>/file')
    def showcase_file(cover_id):
        row = next((item for item in cover_rows() if item['id'] == cover_id), None)
        if row is None:
            return error('封面不存在', 404)
        path = Path(app.config['COVERS_DIR']) / row['name']
        if not path.is_file():
            return error('封面不存在', 404)
        return send_file(path, mimetype=COVER_EXTENSIONS[path.suffix.lower()], conditional=True, max_age=86400)

    @app.get('/api/stats')
    @require_user
    def stats():
        db = get_db()
        row = db.execute('SELECT COUNT(*) AS images, COALESCE(SUM(size),0) AS bytes FROM images WHERE user_id=?', (g.user['id'],)).fetchone()
        active = db.execute("SELECT COUNT(*) FROM jobs WHERE user_id=? AND status IN ('queued','running')", (g.user['id'],)).fetchone()[0]
        tasks = db.execute('SELECT COUNT(*) FROM jobs WHERE user_id=?', (g.user['id'],)).fetchone()[0]
        keywords = [x[0] for x in db.execute('SELECT DISTINCT keyword FROM images WHERE user_id=? ORDER BY keyword', (g.user['id'],))]
        return jsonify(images=row['images'], bytes=row['bytes'], quota_bytes=app.config['USER_QUOTA_BYTES'], active_jobs=active, jobs=tasks, keywords=keywords)

    @app.get('/api/images')
    @require_user
    def list_images():
        try:
            page = max(1, min(100000, int(request.args.get('page', '1'))))
        except ValueError:
            return error('页码无效')
        clauses, params = ['user_id=?'], [g.user['id']]
        q = request.args.get('q', '').strip()[:80]
        keyword = request.args.get('keyword', '').strip()[:80]
        if q:
            clauses.append('(instr(lower(title),lower(?))>0 OR instr(lower(keyword),lower(?))>0)')
            params.extend((q, q))
        if keyword:
            clauses.append('keyword=?')
            params.append(keyword)
        condition = ' AND '.join(clauses)
        db = get_db()
        total = db.execute('SELECT COUNT(*) FROM images WHERE ' + condition, params).fetchone()[0]
        order = 'created_at ASC,id ASC' if request.args.get('sort') == 'oldest' else 'created_at DESC,id DESC'
        rows = db.execute('SELECT * FROM images WHERE ' + condition + ' ORDER BY ' + order + ' LIMIT 36 OFFSET ?', (*params, (page - 1) * 36)).fetchall()
        items = [{k: row[k] for k in ('id','keyword','title','source','page_url','mime','size','width','height','created_at')} for row in rows]
        return jsonify(images=items, total=total, page=page, pages=max(1, (total + 35) // 36))

    def owned_image(image_id):
        return get_db().execute('SELECT * FROM images WHERE id=? AND user_id=?', (image_id, g.user['id'])).fetchone()

    @app.get('/api/images/<image_id>/file')
    @require_user
    def get_image(image_id):
        row = owned_image(image_id)
        if row is None:
            return error('图片不存在', 404)
        path = image_path(g.user['id'], row['filename'])
        if not path.is_file():
            return error('图片文件已丢失，请从图库删除后重新采集', 404)
        return send_file(path, mimetype=row['mime'], as_attachment=request.args.get('download') == '1', download_name=row['filename'])

    def selected_images():
        body = request.get_json(silent=True)
        ids = body.get('ids') if isinstance(body, dict) else None
        if not isinstance(ids, list) or not 1 <= len(ids) <= 200 or any(not isinstance(x, str) or len(x) != 32 for x in ids):
            return None, error('请选择 1–200 张图片')
        ids = list(dict.fromkeys(ids))
        placeholders = ','.join('?' for _ in ids)
        rows = get_db().execute('SELECT * FROM images WHERE user_id=? AND id IN (' + placeholders + ')', (g.user['id'], *ids)).fetchall()
        if len(rows) != len(ids):
            return None, error('部分图片不存在或不属于当前账户', 404)
        return rows, None

    @app.post('/api/images/delete')
    @require_user
    def delete_images():
        db = get_db()
        db.execute('BEGIN IMMEDIATE')
        rows, failure = selected_images()
        if failure:
            db.rollback()
            return failure
        try:
            for row in rows:
                image_path(g.user['id'], row['filename']).unlink(missing_ok=True)
            db.executemany('DELETE FROM images WHERE id=? AND user_id=?', [(row['id'], g.user['id']) for row in rows])
            db.commit()
        except OSError:
            db.rollback()
            return error('文件正在被使用，请稍后重试', 409)
        return jsonify(deleted=len(rows))

    @app.post('/api/images/export')
    @require_user
    def export_images():
        rows, failure = selected_images()
        if failure:
            return failure
        if sum(row['size'] for row in rows) > 200 * 1024 * 1024:
            return error('单次导出最多 200 MiB，请分批下载')
        archive = tempfile.SpooledTemporaryFile(max_size=8 * 1024 * 1024)
        try:
            with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_STORED) as z:
                for row in rows:
                    path = image_path(g.user['id'], row['filename'])
                    if not path.is_file():
                        archive.close()
                        return error('所选图片文件已丢失，请刷新图库', 404)
                    z.write(path, row['filename'])
            archive.seek(0)
            response = send_file(archive, mimetype='application/zip', as_attachment=True, download_name='image-vault.zip')
            response.call_on_close(archive.close)
            return response
        except Exception:
            archive.close()
            raise

    return app
