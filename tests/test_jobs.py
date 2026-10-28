import io

import pytest
from PIL import Image

from image_vault import create_app
from image_vault.crawler import SourceBlocked
from image_vault.db import get_db
from image_vault.jobs import run_job
from image_vault.network import FetchError


def setup_job(tmp_path, source='auto', limit=2):
    app = create_app({'TESTING': True, 'DATA_DIR': str(tmp_path), 'RUN_WORKERS': False, 'CRAWL_DELAY': 0, 'PAGE_DELAY': 0})
    client = app.test_client()
    data = client.post('/api/auth/register', json={'username': 'worker_user', 'password': 'safe-password-123'}).json
    headers = {'X-CSRF-Token': data['csrf_token']}
    job = client.post('/api/jobs', json={'keyword': '\u5c71\u6c34', 'limit': limit, 'source': source}, headers=headers).json['job']
    return app, client, headers, job['id']


def png(color):
    stream = io.BytesIO()
    Image.new('RGB', (20, 20), color).save(stream, 'PNG')
    return stream.getvalue()


def test_auto_fallback_persists_real_files_and_reports_selected_source(tmp_path, monkeypatch):
    import image_vault.jobs as jobs
    app, client, _, job_id = setup_job(tmp_path)

    def candidates(keyword, source, page):
        if source == 'baidu':
            raise SourceBlocked('baidu verification')
        return [{'url': 'https://example.com/red', 'source': 'bing'}, {'url': 'https://example.com/blue', 'source': 'bing'}]

    monkeypatch.setattr(jobs, 'collect_candidates', candidates)
    monkeypatch.setattr(jobs, 'fetch_url', lambda url, **kwargs: (png('red' if url.endswith('red') else 'blue'), 'image/png', url))
    run_job(app, job_id)
    job = client.get('/api/jobs').json['jobs'][0]
    assert job['status'] == 'completed'
    assert job['saved'] == 2
    assert job['actual_source'] == 'bing'
    assert '\u5207\u6362' in job['message']
    gallery = client.get('/api/images').json
    assert gallery['total'] == 2
    assert client.get('/api/images/' + gallery['images'][0]['id'] + '/file').status_code == 200


def test_cancel_during_download_prevents_late_image_commit(tmp_path, monkeypatch):
    import image_vault.jobs as jobs
    app, client, _, job_id = setup_job(tmp_path, source='bing', limit=1)
    monkeypatch.setattr(jobs, 'collect_candidates', lambda *a: [{'url': 'https://example.com/a', 'source': 'bing'}])

    def downloaded(*a, **kw):
        db = get_db()
        db.execute("UPDATE jobs SET status='cancelled' WHERE id=?", (job_id,))
        db.commit()
        return png('red'), 'image/png', 'https://example.com/a'

    monkeypatch.setattr(jobs, 'fetch_url', downloaded)
    run_job(app, job_id)
    assert client.get('/api/images').json['total'] == 0
    assert client.get('/api/jobs').json['jobs'][0]['status'] == 'cancelled'


def test_blocked_baidu_job_fails_instead_of_claiming_empty_success(tmp_path, monkeypatch):
    import image_vault.jobs as jobs
    app, client, _, job_id = setup_job(tmp_path, source='baidu')

    def blocked(*a):
        raise SourceBlocked('baidu verification')

    monkeypatch.setattr(jobs, 'collect_candidates', blocked)
    run_job(app, job_id)
    job = client.get('/api/jobs').json['jobs'][0]
    assert job['status'] == 'failed'
    assert job['saved'] == 0
    assert 'verification' in job['message']


@pytest.mark.parametrize('blocked', [True, False])
def test_auto_uses_pixabay_after_baidu_and_bing(tmp_path, monkeypatch, blocked):
    import image_vault.jobs as jobs
    app, client, _, job_id = setup_job(tmp_path, limit=1)
    visited = []

    def candidates(keyword, source, page):
        visited.append(source)
        if source != 'pixabay':
            if blocked:
                raise SourceBlocked(source + ' verification')
            return []
        return [{'url': 'https://cdn.pixabay.com/photo/cat.jpg', 'source': 'pixabay',
                 'title': 'Cat', 'page_url': 'https://pixabay.com/photos/cat-123/'}]

    monkeypatch.setattr(jobs, 'collect_candidates', candidates)
    monkeypatch.setattr(jobs, 'fetch_url', lambda url, **kw: (png('red'), 'image/png', url))
    run_job(app, job_id)
    job = client.get('/api/jobs').json['jobs'][0]
    assert job['status'] == 'completed'
    assert job['saved'] == 1
    assert job['actual_source'] == 'pixabay'
    assert visited == ['baidu', 'bing', 'pixabay']
    gallery = client.get('/api/images').json['images']
    assert gallery[0]['source'] == 'pixabay'
    assert gallery[0]['page_url'] == 'https://pixabay.com/photos/cat-123/'
    assert client.get('/api/images/' + gallery[0]['id'] + '/file').data == png('red')


def test_explicit_pixabay_stops_on_blocked_download_and_saves_no_html(tmp_path, monkeypatch):
    import image_vault.jobs as jobs
    app, client, _, job_id = setup_job(tmp_path, source='pixabay')
    monkeypatch.setattr(jobs, 'collect_candidates', lambda *args: [
        {'url': 'https://cdn.pixabay.com/photo/cat.jpg', 'source': 'pixabay'},
        {'url': 'https://cdn.pixabay.com/photo/dog.jpg', 'source': 'pixabay'},
    ])
    downloads = []

    def blocked(url, **kwargs):
        downloads.append(url)
        raise FetchError('HTTP 403', 403)

    monkeypatch.setattr(jobs, 'fetch_url', blocked)
    run_job(app, job_id)
    job = client.get('/api/jobs').json['jobs'][0]
    assert job['status'] == 'failed'
    assert 'Pixabay' in job['message']
    assert len(downloads) == 1
    assert client.get('/api/images').json['total'] == 0


def test_failed_baidu_and_bing_candidates_do_not_spend_pixabay_budget(tmp_path, monkeypatch):
    import image_vault.jobs as jobs
    app, client, _, job_id = setup_job(tmp_path, limit=1)
    visits = []

    def candidates(keyword, source, page):
        visits.append(source)
        if source == 'pixabay':
            return [{'url': 'https://cdn.pixabay.com/photo/cat.jpg', 'source': 'pixabay',
                     'page_url': 'https://pixabay.com/photos/cat-123/'}]
        return [{'url': f'https://{source}.example/{page}/{i}', 'source': source} for i in range(40)]

    def download(url, **kwargs):
        if 'pixabay.com' in url:
            return png('blue'), 'image/png', url
        return b'not an image', 'application/octet-stream', url

    monkeypatch.setattr(jobs, 'collect_candidates', candidates)
    monkeypatch.setattr(jobs, 'fetch_url', download)
    run_job(app, job_id)
    job = client.get('/api/jobs').json['jobs'][0]
    assert job['status'] == 'completed'
    assert job['saved'] == 1
    assert job['actual_source'] == 'pixabay'
    assert visits == ['baidu', 'bing', 'pixabay']
