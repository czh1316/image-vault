import hashlib
import hmac
import re
import secrets
import sqlite3
import time
import uuid
from datetime import datetime, timezone
from functools import wraps
from urllib.parse import urlsplit

from flask import Blueprint, current_app, g, jsonify, request
from werkzeug.security import check_password_hash, generate_password_hash

from .db import get_db

bp = Blueprint('auth', __name__, url_prefix='/api/auth')
SESSION_SECONDS = 7 * 24 * 3600


def now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def error(message, status=400):
    return jsonify(error=message), status


def same_origin():
    origin = request.headers.get('Origin')
    if not origin:
        return True
    return origin.rstrip('/') == request.host_url.rstrip('/')


def load_user():
    g.user = None
    g.vault_session = None
    raw = request.cookies.get('vault_session', '')
    if not raw or len(raw) > 200:
        return
    row = get_db().execute(
        'SELECT s.*, u.username FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token_hash=? AND s.expires_at>?',
        (hashlib.sha256(raw.encode()).hexdigest(), int(time.time())),
    ).fetchone()
    if row:
        g.user = {'id': row['user_id'], 'username': row['username']}
        g.vault_session = row


def require_user(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        if g.user is None:
            return error('请先登录', 401)
        if request.method not in ('GET', 'HEAD', 'OPTIONS'):
            supplied = request.headers.get('X-CSRF-Token', '')
            if not same_origin() or not hmac.compare_digest(supplied, g.vault_session['csrf_token']):
                return error('请求校验失败，请刷新页面重试', 403)
        return fn(*args, **kwargs)
    return wrapped


def rate_limit():
    db = get_db()
    timestamp = int(time.time())
    address = request.remote_addr or 'unknown'
    db.execute('DELETE FROM auth_attempts WHERE attempted_at<?', (timestamp - 900,))
    count = db.execute('SELECT COUNT(*) FROM auth_attempts WHERE address=?', (address,)).fetchone()[0]
    db.execute('INSERT INTO auth_attempts VALUES (?,?)', (address, timestamp))
    db.commit()
    return count >= current_app.config['AUTH_RATE_LIMIT']


def open_session(user):
    raw = secrets.token_urlsafe(48)
    token_hash = hashlib.sha256(raw.encode()).hexdigest()
    csrf = secrets.token_urlsafe(32)
    db = get_db()
    if g.vault_session:
        db.execute('DELETE FROM sessions WHERE token_hash=?', (g.vault_session['token_hash'],))
    db.execute('DELETE FROM sessions WHERE expires_at<?', (int(time.time()),))
    db.execute('INSERT INTO sessions VALUES (?,?,?,?)', (token_hash, user['id'], csrf, int(time.time()) + SESSION_SECONDS))
    db.commit()
    response = jsonify(user={'id': user['id'], 'username': user['username']}, csrf_token=csrf)
    response.set_cookie('vault_session', raw, max_age=SESSION_SECONDS, httponly=True, samesite='Lax', secure=current_app.config['COOKIE_SECURE'])
    return response


@bp.post('/register')
def register():
    if not same_origin():
        return error('来源校验失败', 403)
    if rate_limit():
        return error('尝试次数过多，请 15 分钟后重试', 429)
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return error('请提交用户名和密码')
    username, password = body.get('username'), body.get('password')
    if not isinstance(username, str) or not re.fullmatch(r'[A-Za-z0-9_一-鿿]{2,32}', username.strip()):
        return error('用户名需为 2–32 位中文、字母、数字或下划线')
    if not isinstance(password, str) or not 8 <= len(password) <= 128:
        return error('密码需要 8–128 个字符')
    username = username.strip()
    user = {'id': uuid.uuid4().hex, 'username': username}
    db = get_db()
    try:
        db.execute('INSERT INTO users VALUES (?,?,?,?,?)', (user['id'], username, username.casefold(), generate_password_hash(password, method='scrypt'), now()))
        db.commit()
    except sqlite3.IntegrityError:
        db.rollback()
        return error('此用户名已被使用', 409)
    return open_session(user), 201


@bp.post('/login')
def login():
    if not same_origin():
        return error('来源校验失败', 403)
    if rate_limit():
        return error('尝试次数过多，请 15 分钟后重试', 429)
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return error('请提交用户名和密码')
    username, password = body.get('username'), body.get('password')
    if not isinstance(username, str) or not isinstance(password, str) or len(username) > 32 or len(password) > 128:
        return error('用户名或密码不正确', 401)
    user = get_db().execute('SELECT * FROM users WHERE username_key=?', (username.strip().casefold(),)).fetchone()
    password_hash = user['password_hash'] if user else current_app.config['DUMMY_PASSWORD_HASH']
    valid = check_password_hash(password_hash, password)
    if not user or not valid:
        return error('用户名或密码不正确', 401)
    return open_session(user)


@bp.get('/me')
@require_user
def me():
    return jsonify(user=g.user, csrf_token=g.vault_session['csrf_token'])


@bp.post('/logout')
@require_user
def logout():
    db = get_db()
    db.execute('DELETE FROM sessions WHERE token_hash=?', (g.vault_session['token_hash'],))
    db.commit()
    response = jsonify(ok=True)
    response.delete_cookie('vault_session', secure=current_app.config['COOKIE_SECURE'], httponly=True, samesite='Lax')
    return response
