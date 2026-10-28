import logging
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

from flask import Blueprint, current_app, g, jsonify, request

from .auth import error, now, require_user
from .crawler import SourceBlocked, collect_candidates
from .db import get_db
from .network import FetchError, fetch_url
from .storage import store_image

bp = Blueprint('jobs', __name__, url_prefix='/api/jobs')
log = logging.getLogger(__name__)
_dispatch_lock = threading.Lock()


def public_job(row):
    return {k: row[k] for k in row.keys() if k != 'user_id'}


@bp.get('')
@require_user
def list_jobs():
    rows = get_db().execute('SELECT * FROM jobs WHERE user_id=? ORDER BY created_at DESC LIMIT 30', (g.user['id'],)).fetchall()
    return jsonify(jobs=[public_job(x) for x in rows])


@bp.post('')
@require_user
def create_job():
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return error('请提交采集参数')
    keyword, limit, source = body.get('keyword'), body.get('limit', 30), body.get('source', 'auto')
    if not isinstance(keyword, str) or not 1 <= len(keyword.strip()) <= 80 or any(ord(c) < 32 for c in keyword):
        return error('关键词需要 1–80 个字符')
    if type(limit) is not int or not 1 <= limit <= 200:
        return error('每个任务可采集 1–200 张图片')
    if source not in ('auto', 'baidu', 'bing', 'pixabay'):
        return error('请选择有效的图片来源')
    db = get_db()
    with _dispatch_lock:
        db.execute('BEGIN IMMEDIATE')
        active = db.execute("SELECT COUNT(*) FROM jobs WHERE user_id=? AND status IN ('queued','running')", (g.user['id'],)).fetchone()[0]
        total_active = db.execute("SELECT COUNT(*) FROM jobs WHERE status IN ('queued','running')").fetchone()[0]
        if active or total_active >= 20:
            db.rollback()
            return error('已有采集任务进行中，请等待完成或先停止任务' if active else '任务队列已满，请稍后再试', 409)
        job_id = uuid.uuid4().hex
        timestamp = now()
        db.execute('INSERT INTO jobs(id,user_id,keyword,source,requested,status,message,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?)',
                   (job_id, g.user['id'], keyword.strip(), source, limit, 'queued', '等待采集…', timestamp, timestamp))
        db.commit()
        job = db.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone()
        if current_app.config['RUN_WORKERS']:
            current_app.extensions['job_executor'].submit(run_job, current_app._get_current_object(), job_id)
    return jsonify(job=public_job(job)), 201


@bp.post('/<job_id>/cancel')
@require_user
def cancel_job(job_id):
    db = get_db()
    row = db.execute('SELECT * FROM jobs WHERE id=? AND user_id=?', (job_id, g.user['id'])).fetchone()
    if row is None:
        return error('任务不存在', 404)
    db.execute("UPDATE jobs SET status='cancelled', message='任务已停止，已保存图片仍在图库。', updated_at=? WHERE id=? AND user_id=? AND status IN ('queued','running')", (now(), job_id, g.user['id']))
    db.commit()
    return jsonify(job=public_job(db.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone()))


def update_job(job_id, **fields):
    fields['updated_at'] = now()
    db = get_db()
    columns = ','.join(k + '=?' for k in fields)
    db.execute(f"UPDATE jobs SET {columns} WHERE id=? AND status IN ('queued','running')", (*fields.values(), job_id))
    db.commit()


def still_running(job_id):
    row = get_db().execute('SELECT status FROM jobs WHERE id=?', (job_id,)).fetchone()
    return row is not None and row['status'] == 'running'


def run_job(app, job_id):
    with app.app_context():
        row = get_db().execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone()
        if row is None or row['status'] != 'queued':
            return
        job = dict(row)
        update_job(job_id, status='running', message='正在搜索图片…')
        saved = duplicates = failed = checked = 0
        seen = set()
        notice = ''
        last_error = ''
        sources = ['baidu', 'bing', 'pixabay'] if job['source'] == 'auto' else [job['source']]
        try:
            exhausted = True
            for source in sources:
                if not still_running(job_id) or saved >= job['requested']:
                    break
                source_checked = 0
                source_check_limit = job['requested'] * 5 + 30
                update_job(job_id, actual_source=source, message=notice or '正在搜索图片…')
                try:
                    for page in range(min(20, max(3, (job['requested'] + 19) // 20))):
                        if not still_running(job_id) or saved >= job['requested']:
                            break
                        candidates = collect_candidates(job['keyword'], source, page)
                        fresh = [x for x in candidates if x['url'] not in seen]
                        if not fresh:
                            break
                        for candidate in fresh:
                            if not still_running(job_id) or saved >= job['requested'] or source_checked >= source_check_limit:
                                break
                            seen.add(candidate['url'])
                            checked += 1
                            source_checked += 1
                            try:
                                data, _mime, _final_url = fetch_url(candidate['url'], max_bytes=app.config['MAX_IMAGE_BYTES'], timeout=12)
                                result = store_image(job['user_id'], job_id, job['keyword'], candidate, data)
                                if result['status'] == 'saved':
                                    saved += 1
                                elif result['status'] == 'duplicate':
                                    duplicates += 1
                                else:
                                    return
                            except FetchError as exc:
                                if source == 'pixabay' and exc.status in (403, 429):
                                    raise SourceBlocked('Pixabay \u56fe\u7247\u6765\u6e90\u9650\u5236\u8bbf\u95ee\uff0c\u5df2\u505c\u6b62\u91c7\u96c6\uff0c\u8bf7\u7a0d\u540e\u518d\u8bd5\u3002') from exc
                                failed += 1
                                last_error = str(exc)
                            except ValueError as exc:
                                failed += 1
                                last_error = str(exc)
                                if '配额' in last_error:
                                    raise
                            update_job(job_id, saved=saved, duplicates=duplicates, failed=failed, checked=checked,
                                       message=notice + f'已保存 {saved} 张，重复 {duplicates} 张，失败 {failed} 张')
                            if app.config['CRAWL_DELAY']:
                                time.sleep(app.config['CRAWL_DELAY'])
                        if source_checked >= source_check_limit:
                            break
                        if app.config['PAGE_DELAY'] and saved < job['requested']:
                            time.sleep(app.config['PAGE_DELAY'])
                except (SourceBlocked, FetchError) as exc:
                    last_error = str(exc)
                    if job['source'] == 'auto' and source != sources[-1]:
                        next_source = sources[sources.index(source) + 1]
                        source_names = {'baidu': '\u767e\u5ea6', 'bing': '\u5fc5\u5e94', 'pixabay': 'Pixabay'}
                        notice = f"{source_names[source]}\u6765\u6e90\u6682\u4e0d\u53ef\u7528\uff0c\u5df2\u5207\u6362{source_names[next_source]}\u3002"
                        update_job(job_id, message=notice)
                        continue
                    raise
                if saved >= job['requested']:
                    exhausted = False
                    break
                if job['source'] == 'auto' and source != sources[-1]:
                    next_source = sources[sources.index(source) + 1]
                    source_names = {'baidu': '\u767e\u5ea6', 'bing': '\u5fc5\u5e94', 'pixabay': 'Pixabay'}
                    notice = f"{source_names[source]}\u7ed3\u679c\u4e0d\u8db3\uff0c\u5df2\u8865\u5145{source_names[next_source]}\u6765\u6e90\u3002"
            if still_running(job_id):
                if saved:
                    message = notice + f'已保存 {saved} 张图片。'
                    if exhausted:
                        message += '可用结果不足，未达到目标数量。'
                    update_job(job_id, status='completed', message=message)
                else:
                    message = notice + ('结果均已存在于你的图库。' if duplicates and not failed else '未保存新图片。')
                    if last_error:
                        message += ' ' + last_error[:160]
                    update_job(job_id, status='completed' if duplicates and not failed else 'failed', message=message)
        except Exception as exc:
            log.warning('Collection job %s failed: %s', job_id, type(exc).__name__)
            detail = str(exc)[:220] if isinstance(exc, ValueError) else '采集发生异常，请稍后重试。'
            update_job(job_id, status='failed', message=notice + detail, saved=saved, failed=failed, duplicates=duplicates, checked=checked)


def init_jobs(app):
    app.register_blueprint(bp)
    if app.config['RUN_WORKERS']:
        app.extensions['job_executor'] = ThreadPoolExecutor(max_workers=2, thread_name_prefix='image-collector')
