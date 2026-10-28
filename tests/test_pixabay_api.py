import json
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import parse_qs, urlsplit

import pytest
from flask import Flask

from image_vault import crawler
from image_vault.network import FetchError


@pytest.fixture
def api_env(tmp_path, monkeypatch):
    app = Flask(__name__)
    app.config.update(DATA_DIR=str(tmp_path), **{'PIXABAY_' + 'API_KEY': 'test-private-key'})
    monkeypatch.delenv('PIXABAY_API_KEY', raising=False)
    with app.app_context():
        yield app


def photo(**overrides):
    return dict(type='photo', tags='mountain, lake',
                pageURL='https://pixabay.com/photos/mountain-123/',
                largeImageURL='https://cdn.pixabay.com/photo/mountain_1280.jpg',
                webformatURL='https://cdn.pixabay.com/photo/mountain_640.jpg', **overrides)


def response(url, hits=None, headers=None):
    return (json.dumps({'hits': hits if hits is not None else [photo()]}).encode(),
            'application/json', url, headers or {})


def test_official_photo_api_and_persistent_24_hour_cache(api_env, monkeypatch):
    from image_vault import pixabay
    calls = []
    clock = [1000.0]
    monkeypatch.setattr(pixabay.time, 'time', lambda: clock[0])

    def fetch(url, **kwargs):
        calls.append(url)
        query = parse_qs(urlsplit(url).query)
        assert urlsplit(url).path == '/api/'
        assert query == {'key': ['test-private-key'], 'q': ['山 水'], 'lang': ['zh'],
                         'image_type': ['photo'], 'safesearch': ['true'], 'per_page': ['50'], 'page': ['2']}
        assert kwargs['include_headers'] is True
        return response(url)

    monkeypatch.setattr(pixabay, 'fetch_url', fetch)
    rows = crawler.collect_candidates(' 山 水 ', 'pixabay', 1)
    assert rows == [{'url': 'https://cdn.pixabay.com/photo/mountain_1280.jpg',
                     'title': 'mountain, lake', 'source': 'pixabay',
                     'page_url': 'https://pixabay.com/photos/mountain-123/'}]
    assert crawler.collect_candidates('山 水', 'pixabay', 1) == rows
    assert len(calls) == 1
    # A new Flask app represents a restarted process reading the same cache.
    restarted = Flask('restart')
    restarted.config.update(api_env.config)
    clock[0] += 86399
    with restarted.app_context():
        assert crawler.collect_candidates('山 水', 'pixabay', 1) == rows
    assert len(calls) == 1
    clock[0] += 2
    crawler.collect_candidates('山 水', 'pixabay', 1)
    assert len(calls) == 2


def test_api_filters_non_photos_and_untrusted_urls(api_env, monkeypatch):
    from image_vault import pixabay
    valid = photo()
    hits = [valid, dict(valid), dict(valid, type='vector'),
            dict(valid, type='video'), dict(valid, largeImageURL='http://127.0.0.1/a.jpg', webformatURL=''),
            dict(valid, pageURL='https://evil.example/photos/cat/'),
            dict(valid, largeImageURL='https://cdn.pixabay.com/video/a.mp4', webformatURL='')]
    monkeypatch.setattr(pixabay, 'fetch_url', lambda url, **kw: response(url, hits))
    assert len(crawler.collect_candidates('cat', 'pixabay', 0)) == 1


def test_api_accepts_official_signed_photo_download_urls(api_env, monkeypatch):
    from image_vault import pixabay
    signed = 'https://pixabay.com/get/g' + 'a' * 128 + '_1280.jpg'
    hit = dict(photo(), largeImageURL=signed, webformatURL='')
    monkeypatch.setattr(pixabay, 'fetch_url', lambda url, **kw: response(url, [hit]))
    rows = crawler.collect_candidates('cat', 'pixabay', 0)
    assert rows and rows[0]['url'] == signed


def test_signed_download_proxy_and_photo_cdn_redirect(monkeypatch):
    from image_vault import network
    monkeypatch.setattr(network, 'getproxies', lambda: {'https': 'http://127.0.0.1:7897'})
    signed = 'https://pixabay.com/get/g' + 'a' * 128 + '_1280.jpg'
    assert network.proxy_for_source(signed)
    assert network.proxy_for_source(signed.replace('_1280.jpg', '.mp4')) is None
    calls = []

    def request(url, *args):
        calls.append(url)
        if len(calls) == 1:
            return 302, {'location': 'https://cdn.pixabay.com/photo/cat.jpg'}, b''
        return 200, {'content-type': 'image/jpeg'}, b'photo'

    monkeypatch.setattr(network, '_proxy_request_once', request)
    assert network.fetch_url(signed)[0] == b'photo'
    assert len(calls) == 2


def test_missing_key_fails_before_network(api_env, monkeypatch):
    from image_vault import pixabay
    api_env.config['PIXABAY_API_KEY'] = ''
    monkeypatch.setattr(pixabay, 'fetch_url', lambda *a, **k: pytest.fail('unexpected network'))
    with pytest.raises(crawler.SourceBlocked, match='API Key'):
        crawler.collect_candidates('cat', 'pixabay', 0)


def test_rate_headers_stop_uncached_queries_but_allow_cached_results(api_env, monkeypatch):
    from image_vault import pixabay
    calls = []
    clock = [1000.0]
    monkeypatch.setattr(pixabay.time, 'time', lambda: clock[0])

    def fetch(url, **kwargs):
        calls.append(url)
        return response(url, headers={'x-ratelimit-remaining': '0', 'x-ratelimit-reset': '45'})

    monkeypatch.setattr(pixabay, 'fetch_url', fetch)
    assert crawler.collect_candidates('cat', 'pixabay', 0)
    with pytest.raises(crawler.SourceBlocked, match='45'):
        crawler.collect_candidates('dog', 'pixabay', 0)
    assert crawler.collect_candidates('cat', 'pixabay', 0)
    assert len(calls) == 1
    clock[0] += 46
    assert crawler.collect_candidates('dog', 'pixabay', 0)
    assert len(calls) == 2


def test_local_limit_is_shared_between_accounts_and_persists(api_env, monkeypatch):
    from image_vault import pixabay
    monkeypatch.setattr(pixabay.time, 'time', lambda: 1000.0)
    calls = []

    def fetch(url, **kwargs):
        calls.append(url)
        return response(url, [])

    monkeypatch.setattr(pixabay, 'fetch_url', fetch)
    for i in range(100):
        assert crawler.collect_candidates(f'query{i}', 'pixabay', 0) == []
    with pytest.raises(crawler.SourceBlocked, match='60'):
        crawler.collect_candidates('one-too-many', 'pixabay', 0)
    assert len(calls) == 100


@pytest.mark.parametrize('status', [400, 401, 403, 429])
def test_api_errors_are_actionable_and_do_not_leak_key(api_env, monkeypatch, status):
    from image_vault import pixabay
    calls = []

    def fetch(url, **kwargs):
        calls.append(url)
        raise FetchError('contains secret test-private-key', status,
                         headers={'x-ratelimit-reset': '30'})

    monkeypatch.setattr(pixabay, 'fetch_url', fetch)
    with pytest.raises(crawler.SourceBlocked) as exc:
        crawler.collect_candidates('cat', 'pixabay', 0)
    assert 'test-private-key' not in str(exc.value)
    assert str(status) in str(exc.value)
    if status == 429:
        with pytest.raises(crawler.SourceBlocked):
            crawler.collect_candidates('dog', 'pixabay', 0)
        assert len(calls) == 1


def test_parallel_cache_misses_make_one_request(api_env, monkeypatch):
    from image_vault import pixabay
    calls = []

    def fetch(url, **kwargs):
        calls.append(url)
        return response(url)

    def collect(_):
        with api_env.app_context():
            return crawler.collect_candidates('cat', 'pixabay', 0)

    monkeypatch.setattr(pixabay, 'fetch_url', fetch)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(collect, range(4)))
    assert all(rows == results[0] for rows in results)
    assert len(calls) == 1


def test_api_proxy_allowlist_and_redirect_do_not_leak_key(monkeypatch):
    from image_vault import network
    monkeypatch.setattr(network, 'getproxies', lambda: {'https': 'http://127.0.0.1:7897'})
    url = 'https://pixabay.com/api/?key=test-private-key&q=cat&lang=zh&image_type=photo&safesearch=true&per_page=50&page=1'
    assert network.proxy_for_source(url)
    assert network.proxy_for_source(url.replace('image_type=photo', 'image_type=all')) is None
    assert network.proxy_for_source(url.replace('/api/', '/api/videos/')) is None
    monkeypatch.setattr(network, '_proxy_request_once', lambda *args: (302, {'location': 'https://pixabay.com/zh/photos/search/cat/'}, b''))
    with pytest.raises(network.UnsafeURL):
        network.fetch_url(url)
