import pytest

from image_vault import crawler, network


def test_browser_photo_cards_exclude_logos_unrelated_hosts_and_duplicate_images():
    page = '''<img src="https://www.baidu.com/logo.png">
    <a href="https://image.baidu.com/search/detail?word=cat&amp;pn=0">
      <img src="https://img0.baidu.com/it/u=123,456&amp;fm=253" alt="cat &amp; tree">
      <img src="https://img0.baidu.com/it/u=123,456&amp;fm=253"></a>
    <a href="https://image.baidu.com/search/detail?word=cat&amp;pn=1">
      <img src="https://img2.baidu.com/it/u=789,100&amp;fm=253"></a>
    <a href="https://evil.example/search/detail"><img src="https://img0.baidu.com/it/u=1,2"></a>
    <a href="https://image.baidu.com/search/detail"><img src="https://127.0.0.1/a.jpg"></a>'''
    rows = crawler.parse_baidu_page(page)
    assert len(rows) == 2
    assert rows[0]['title'] == 'cat & tree'
    assert rows[0]['source'] == 'baidu'
    assert rows[0]['page_url'] == 'https://image.baidu.com/search/detail?word=cat&pn=0'


def test_baidu_blocked_http_uses_regular_browser_fallback(monkeypatch):
    from image_vault import baidu_browser
    monkeypatch.setattr(crawler, 'fetch_url', lambda *a, **kw: (b'{"antiFlag":1}', 'application/json', ''))
    calls = []

    def collect(keyword, page):
        calls.append((keyword, page))
        return [{'url': 'https://img0.baidu.com/it/u=1,2', 'source': 'baidu'}]

    monkeypatch.setattr(baidu_browser, 'collect', collect)
    assert crawler.collect_candidates('cat', 'baidu', 1)
    assert calls == [('cat', 1)]


def test_baidu_thumbnail_proxy_is_limited_to_image_paths(monkeypatch):
    monkeypatch.setattr(network, 'getproxies', lambda: {'https': 'http://127.0.0.1:7897'})
    assert network.proxy_for_source('https://img0.baidu.com/it/u=123,456&fm=253?w=500&h=500')
    for url in ['https://img0.baidu.com/private', 'https://img0.baidu.com.evil.test/it/u=1,2',
                'https://img0.baidu.com/it/u=../admin', 'https://user@img0.baidu.com/it/u=1,2']:
        assert network.proxy_for_source(url) is None
