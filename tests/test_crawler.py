import json

import pytest

from image_vault import crawler
from image_vault.crawler import SourceBlocked, parse_baidu, parse_bing
from image_vault.network import FetchError, UnsafeURL, fetch_url, resolve_public_url


def test_baidu_keeps_last_real_image_and_skips_empty_sentinel():
    data = {'data': [{'thumbURL': 'https://img.example/a.jpg', 'fromPageTitleEnc': '\u5c71\u6c34'}, {}, {'thumbURL': 'https://img.example/b.jpg'}]}
    rows = parse_baidu(json.dumps(data))
    assert [x['url'] for x in rows] == ['https://img.example/a.jpg', 'https://img.example/b.jpg']
    assert rows[0]['source'] == 'baidu'


def test_baidu_verification_response_is_not_silent_empty_success():
    with pytest.raises(SourceBlocked):
        parse_baidu('{"antiFlag":1,"message":"verify"}')
    with pytest.raises(SourceBlocked):
        parse_baidu('<html><title>baidu security check</title></html>')


def test_bing_metadata_extracts_actual_images_and_unescapes_titles():
    html = '<a class="iusc" m="{&quot;murl&quot;:&quot;https://example.com/a.jpg&quot;,&quot;turl&quot;:&quot;https://tse1.mm.bing.net/th/id/a&quot;,&quot;purl&quot;:&quot;https://example.com/page&quot;,&quot;t&quot;:&quot;hill &amp; cloud&quot;}"></a>'
    rows = parse_bing(html)
    assert len(rows) == 1
    assert rows[0]['url'] == 'https://tse1.mm.bing.net/th/id/a'
    assert rows[0]['page_url'] == 'https://example.com/page'
    assert rows[0]['source'] == 'bing'


@pytest.mark.parametrize('url', ['file:///etc/passwd', 'http://127.0.0.1/a', 'http://[::1]/a', 'http://169.254.169.254/latest', 'http://10.0.0.1/a', 'http://2130706433/a', 'http://user:pass@example.com/a', 'http://example.com:22/a'])
def test_download_rejects_internal_or_credential_urls(url):
    with pytest.raises(UnsafeURL):
        resolve_public_url(url)


def test_download_revalidates_redirect_before_connecting(monkeypatch):
    import image_vault.network as network
    monkeypatch.setattr(network.socket, 'getaddrinfo', lambda *a, **k: [(2, 1, 6, '', ('93.184.216.34', 443))])
    calls = []

    def request(*args, **kwargs):
        calls.append(args)
        return 302, {'location': 'http://127.0.0.1/private'}, b''

    monkeypatch.setattr(network, '_request_once', request)
    with pytest.raises(UnsafeURL):
        fetch_url('https://example.com/a')
    assert len(calls) == 1


def test_private_dns_is_blocked_even_for_public_looking_hostname(monkeypatch):
    import image_vault.network as network
    monkeypatch.setattr(network.socket, 'getaddrinfo', lambda *a, **k: [(2, 1, 6, '', ('10.2.3.4', 443))])
    with pytest.raises(UnsafeURL):
        resolve_public_url('https://example.com/a')


def test_proxy_is_only_used_for_fixed_search_and_thumbnail_hosts(monkeypatch):
    import image_vault.network as network
    monkeypatch.setattr(network, 'getproxies', lambda: {'https': 'http://127.0.0.1:7897'})
    assert network.proxy_for_source('https://www.bing.com/images/async') == 'http://127.0.0.1:7897'
    assert network.proxy_for_source('https://cn.bing.com/images/async') == 'http://127.0.0.1:7897'
    assert network.proxy_for_source('https://tse1.mm.bing.net/th/id/a') == 'http://127.0.0.1:7897'
    assert network.proxy_for_source('https://ts2.mm.bing.net/th?id=OIP.a') == 'http://127.0.0.1:7897'
    for url in ['https://127.0.0.1/a', 'https://www.bing.com.evil.test/a', 'https://example.com/a', 'https://www.bing.com@10.0.0.1/a']:
        assert network.proxy_for_source(url) is None


def test_pixabay_extracts_photo_cards_lazy_images_and_largest_srcset():
    page = '''<html><title>Photos - Pixabay</title>
      <a href="/zh/photos/mountain-123/"><picture>
        <source srcset="https://cdn.pixabay.com/photo/2024/01/01/mountain_640.jpg 640w,
                        https://cdn.pixabay.com/photo/2024/01/01/mountain_1280.jpg 1280w">
        <img src="data:image/gif;base64,a" alt="&#23665; &amp; &#20113;"></picture></a>
      <a href="/photos/lake-456/"><img data-lazy="//cdn.pixabay.com/photo/2024/01/01/lake_640.png" alt="&#28246;"></a>
      <a href="/photos/lake-456/"><img data-src="https://cdn.pixabay.com/photo/2024/01/01/lake_640.png"></a>
      </html>'''
    rows = crawler.parse_pixabay(page)
    assert rows == [
        {'url': 'https://cdn.pixabay.com/photo/2024/01/01/mountain_1280.jpg', 'title': '\u5c71 & \u4e91',
         'source': 'pixabay', 'page_url': 'https://pixabay.com/zh/photos/mountain-123/'},
        {'url': 'https://cdn.pixabay.com/photo/2024/01/01/lake_640.png', 'title': '\u6e56',
         'source': 'pixabay', 'page_url': 'https://pixabay.com/photos/lake-456/'},
    ]


def test_pixabay_excludes_videos_avatars_ads_and_non_photo_links():
    page = '''<title>Pixabay</title>
      <a href="/videos/cat-123/"><img src="https://cdn.pixabay.com/photo/cat.jpg"></a>
      <a href="/illustrations/cat-123/"><img src="https://cdn.pixabay.com/photo/cat.jpg"></a>
      <a href="https://ads.example/photos/cat-123/"><img src="https://cdn.pixabay.com/photo/cat.jpg"></a>
      <img src="https://cdn.pixabay.com/photo/unlinked.jpg">
      <a href="/photos/cat-123/"><video><source src="https://cdn.pixabay.com/video/cat.mp4"></video>
        <img src="https://cdn.pixabay.com/user/avatar.jpg">
        <img src="https://cdn.pixabay.com.evil.test/photo/cat.jpg">
        <img src="https://cdn.pixabay.com/photo/cat.mp4">
        <img data-src="https://cdn.pixabay.com/photo/cat.jpg" alt="cat"></a>'''
    rows = crawler.parse_pixabay(page)
    assert len(rows) == 1
    assert rows[0]['url'] == 'https://cdn.pixabay.com/photo/cat.jpg'


def test_pixabay_rejects_credentialed_detail_and_image_urls():
    page = '''<a href="https://user@pixabay.com/photos/credentialed-1/">
      <img src="https://cdn.pixabay.com/photo/hidden.jpg"></a>
      <a href="/photos/credentialed-2/"><img src="https://user@cdn.pixabay.com/photo/hidden.jpg"></a>
      <p>No results found</p>'''
    assert crawler.parse_pixabay(page) == []


@pytest.mark.parametrize('page', [
    '<title>Just a moment...</title><script src="/cdn-cgi/challenge-platform/a"></script>',
    '<title>Pixabay</title><p>Verify you are human</p>',
    '<title>Pixabay</title><p>Changed markup without identifiable results</p>',
])
def test_pixabay_verification_or_unrecognized_markup_is_reported(page):
    with pytest.raises(SourceBlocked, match='Pixabay'):
        crawler.parse_pixabay(page)


def test_pixabay_explicit_empty_results_are_allowed():
    assert crawler.parse_pixabay('<title>Pixabay</title><p>No results found</p>') == []


def test_pixabay_keyword_and_pagination_use_official_api(monkeypatch):
    from image_vault import pixabay

    def search(keyword, page):
        assert keyword == '\u5c71 \u6c34'
        assert page == 1
        return []

    monkeypatch.setattr(pixabay, 'search_photos', search)
    assert crawler.collect_candidates('\u5c71 \u6c34', 'pixabay', 1) == []


@pytest.mark.parametrize('status', [403, 429])
def test_pixabay_http_block_is_labeled_as_source_unavailable(monkeypatch, status):
    from image_vault import pixabay

    def blocked(*args, **kwargs):
        raise pixabay.PixabayError(f'Pixabay API HTTP {status}')

    monkeypatch.setattr(pixabay, 'search_photos', blocked)
    with pytest.raises(SourceBlocked, match='Pixabay'):
        crawler.collect_candidates('\u5c71\u6c34', 'pixabay', 0)


def test_pixabay_proxy_allowlist_and_redirect_validation(monkeypatch):
    import image_vault.network as network
    monkeypatch.setattr(network, 'getproxies', lambda: {'https': 'http://127.0.0.1:7897'})
    for url in ['https://pixabay.com/zh/photos/search/cat/?pagi=1', 'https://cdn.pixabay.com/photo/cat.jpg']:
        assert network.proxy_for_source(url) == 'http://127.0.0.1:7897'
    for url in ['https://pixabay.com/zh/videos/', 'https://pixabay.com/api/',
                'https://cdn.pixabay.com/video/cat.mp4', 'https://cdn.pixabay.com/user/avatar.jpg',
                'https://pixabay.com.evil.test/zh/photos/search/cat/',
                'https://pixabay.com/zh/photos/search/../videos/',
                'https://cdn.pixabay.com/photo/../video/cat.mp4']:
        assert network.proxy_for_source(url) is None
    monkeypatch.setattr(network, '_proxy_request_once', lambda *args: (302, {'location': 'http://127.0.0.1/private'}, b''))
    with pytest.raises(UnsafeURL):
        fetch_url('https://cdn.pixabay.com/photo/cat.jpg')


def test_http_proxy_establishes_connect_then_wraps_target_tls(monkeypatch):
    import image_vault.network as network
    calls = []

    class Response:
        status = 200

        def getheaders(self):
            return [('content-type', 'text/html')]

        def read(self, limit):
            return b'pixabay'

    class Connection:
        def __init__(self, host, port, timeout):
            calls.append(('connection', host, port, timeout))
            self.sock = object()

        def set_tunnel(self, host, port):
            calls.append(('tunnel', host, port))

        def connect(self):
            calls.append(('connect',))

        def request(self, method, path, headers):
            calls.append(('request', method, path, headers, self.sock))

        def getresponse(self):
            return Response()

        def close(self):
            calls.append(('close',))

    class Context:
        def wrap_socket(self, sock, server_hostname):
            calls.append(('tls', server_hostname, sock))
            return 'wrapped tls socket'

    monkeypatch.setattr(network.http.client, 'HTTPConnection', Connection)
    monkeypatch.setattr(network.ssl, 'create_default_context', Context)
    status, headers, body = network._proxy_request_once(
        'https://pixabay.com/zh/photos/search/cat/?pagi=1', 'http://127.0.0.1:7897', 4096, 5)
    request = next(call for call in calls if call[0] == 'request')
    assert status == 200
    assert headers['content-type'] == 'text/html'
    assert body == b'pixabay'
    assert calls[:3] == [('connection', '127.0.0.1', 7897, 5), ('tunnel', 'pixabay.com', 443), ('connect',)]
    assert calls[3][0:2] == ('tls', 'pixabay.com')
    assert request[1:3] == ('GET', '/zh/photos/search/cat/?pagi=1')
    assert request[3]['Referer'] == 'https://pixabay.com/'
    assert request[4] == 'wrapped tls socket'


def test_pixabay_redirect_cannot_leave_photo_search_or_image_paths(monkeypatch):
    import image_vault.network as network
    monkeypatch.setattr(network, 'getproxies', lambda: {})
    monkeypatch.setattr(network.socket, 'getaddrinfo', lambda *a, **k: [(2, 1, 6, '', ('93.184.216.34', 443))])
    calls = []

    def request(*args, **kwargs):
        calls.append(args)
        return 302, {'location': 'https://cdn.pixabay.com/video/cat.mp4'}, b''

    monkeypatch.setattr(network, '_request_once', request)
    with pytest.raises(UnsafeURL):
        fetch_url('https://pixabay.com/zh/photos/search/cat/?pagi=1')
    assert len(calls) == 1
