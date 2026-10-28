import html
import json
import re
from html.parser import HTMLParser
from urllib.parse import quote, urlencode, urljoin, urlsplit

from .network import FetchError, fetch_url, is_baidu_thumbnail


class SourceBlocked(ValueError):
    pass


class BaiduPageParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.rows, self.seen, self.page_url = [], set(), ''

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'a':
            self.page_url = ''
            try:
                page = urlsplit(urljoin('https://image.baidu.com/', attrs.get('href', '')))
                if (page.scheme == 'https' and page.hostname == 'image.baidu.com'
                        and not page.username and not page.password and page.port in (None, 443)
                        and page.path == '/search/detail'):
                    self.page_url = page.geturl()
            except ValueError:
                pass
        elif tag == 'img' and self.page_url:
            for field in ('data-thumbnail-url', 'src', 'data-src'):
                url = attrs.get(field, '')
                if is_baidu_thumbnail(urlsplit(url)) and url not in self.seen:
                    self.seen.add(url)
                    self.rows.append({'url': url, 'title': attrs.get('alt', ''),
                                      'source': 'baidu', 'page_url': self.page_url})
                    break

    def handle_endtag(self, tag):
        if tag == 'a':
            self.page_url = ''


def parse_baidu_page(text):
    parser = BaiduPageParser()
    parser.feed(text)
    return parser.rows


def parse_baidu(text):
    try:
        data = json.loads(text)
    except (ValueError, TypeError) as exc:
        raise SourceBlocked('\u767e\u5ea6\u8fd4\u56de\u9a8c\u8bc1\u9875\u9762\u6216\u975e JSON \u5185\u5bb9\uff0c\u8bf7\u7a0d\u540e\u518d\u8bd5\u6216\u5207\u6362\u5fc5\u5e94\u6765\u6e90\u3002') from exc
    if not isinstance(data, dict) or not isinstance(data.get('data'), list):
        raise SourceBlocked('\u767e\u5ea6\u89e6\u53d1\u5b89\u5168\u9a8c\u8bc1\u6216\u63a5\u53e3\u6682\u4e0d\u53ef\u7528\uff0c\u8bf7\u7a0d\u540e\u518d\u8bd5\u6216\u5207\u6362\u5fc5\u5e94\u6765\u6e90\u3002')
    results = []
    for row in data['data']:
        if isinstance(row, dict) and isinstance(row.get('thumbURL'), str) and row['thumbURL'].startswith(('http://', 'https://')):
            results.append({'url': row['thumbURL'], 'title': html.unescape(row.get('fromPageTitleEnc') or row.get('fromPageTitle') or ''),
                            'source': 'baidu', 'page_url': row.get('fromURLHost') or ''})
    return results


class BingParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.rows = []

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        metadata = attributes.get('m')
        if not metadata:
            return
        try:
            row = json.loads(metadata)
        except (ValueError, TypeError):
            return
        if not isinstance(row, dict):
            return
        url = row.get('turl') or row.get('murl')
        if isinstance(url, str) and url.startswith(('https://', 'http://')):
            self.rows.append({'url': url, 'title': html.unescape(str(row.get('t') or row.get('desc') or '')),
                              'source': 'bing', 'page_url': row.get('purl') or ''})


def parse_bing(text):
    if any(word in text.lower() for word in ('verify you are human', 'our systems have detected unusual traffic', 'id="b_captcha"')):
        raise SourceBlocked('\u5fc5\u5e94\u8981\u6c42\u9a8c\u8bc1\uff0c\u8bf7\u7a0d\u540e\u518d\u8bd5\u3002')
    parser = BingParser()
    parser.feed(text)
    return parser.rows


def _pixabay_detail_url(value):
    if not isinstance(value, str):
        return ''
    try:
        parsed = urlsplit(urljoin('https://pixabay.com/', value))
        if (parsed.scheme != 'https' or parsed.hostname != 'pixabay.com' or parsed.username or parsed.password
                or parsed.port not in (None, 443)):
            return ''
    except ValueError:
        return ''
    if re.fullmatch(r'/(?:zh/)?photos/[^/?#]+/?', parsed.path):
        return parsed._replace(query='', fragment='').geturl()
    return ''


def _pixabay_image_url(value):
    if not isinstance(value, str):
        return ''
    try:
        parsed = urlsplit(urljoin('https://cdn.pixabay.com/', value.strip()))
        if (parsed.scheme != 'https' or parsed.hostname != 'cdn.pixabay.com' or parsed.username or parsed.password
                or parsed.port not in (None, 443)):
            return ''
    except ValueError:
        return ''
    if re.search(r'%(?:2e|2f|5c)', parsed.path, re.I):
        return ''
    parts = parsed.path.split('/')
    if len(parts) < 3 or parts[1] != 'photo' or any(part in ('.', '..') for part in parts):
        return ''
    if parsed.path.rsplit('.', 1)[-1].lower() not in ('jpg', 'jpeg', 'png', 'webp'):
        return ''
    return parsed._replace(fragment='').geturl()


def _srcset_items(value):
    items = []
    for order, entry in enumerate(re.split(r'\s*,\s*', value or '')):
        parts = entry.strip().split()
        if not parts:
            continue
        weight = 0
        if len(parts) > 1:
            descriptor = parts[1].lower()
            try:
                if descriptor.endswith('w'):
                    weight = float(descriptor[:-1])
                elif descriptor.endswith('x'):
                    weight = float(descriptor[:-1]) * 1_000_000
            except ValueError:
                pass
        items.append((weight, -order, parts[0]))
    return items


class PixabayParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.rows = []
        self.anchor_stack = []

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == 'a':
            if self.anchor_stack:
                self.anchor_stack.pop()
            row = {'page_url': _pixabay_detail_url(attributes.get('href')), 'images': [], 'title': ''}
            self.anchor_stack.append(row)
            if row['page_url']:
                self.rows.append(row)
            return
        if not self.anchor_stack or tag not in ('img', 'source'):
            return
        row = self.anchor_stack[-1]
        if not row['page_url']:
            return
        if tag == 'img':
            row['title'] = row['title'] or html.unescape(str(attributes.get('alt') or attributes.get('title') or '')).strip()
        for attr in ('srcset', 'data-srcset'):
            row['images'].extend(_srcset_items(attributes.get(attr)))
        for attr in ('src', 'data-src', 'data-lazy', 'data-lazy-src', 'data-original'):
            value = attributes.get(attr)
            if value:
                row['images'].append((0, 0, value))

    def handle_endtag(self, tag):
        if tag == 'a' and self.anchor_stack:
            self.anchor_stack.pop()


def parse_pixabay(text):
    lowered = text.lower()
    if any(marker in lowered for marker in ('verify you are human', 'just a moment', '/cdn-cgi/challenge-platform/',
                                             'cf-chl-', 'attention required')):
        raise SourceBlocked('Pixabay \u8981\u6c42\u4eba\u673a\u9a8c\u8bc1\uff0c\u8bf7\u7a0d\u540e\u518d\u8bd5\u3002')
    parser = PixabayParser()
    parser.feed(text)
    results, seen = [], set()
    for row in parser.rows:
        images = sorted(row['images'], reverse=True)
        image_url = next((url for _weight, _order, candidate in images if (url := _pixabay_image_url(candidate))), '')
        if not image_url or image_url in seen:
            continue
        seen.add(image_url)
        results.append({'url': image_url, 'title': row['title'], 'source': 'pixabay', 'page_url': row['page_url']})
    if results:
        return results
    if re.search(r'no (?:results|photos|images) found|nothing found|no assets found|\u672a\u627e\u5230.*\u7ed3\u679c|\u6ca1\u6709\u627e\u5230.*\u7ed3\u679c', lowered):
        return []
    raise SourceBlocked('Pixabay \u9875\u9762\u7ed3\u6784\u65e0\u6cd5\u8bc6\u522b\uff0c\u8bf7\u7a0d\u540e\u518d\u8bd5\u3002')


def collect_candidates(keyword, source, page):
    if source == 'pixabay':
        from .pixabay import PixabayError, search_photos
        try:
            return search_photos(keyword, page)
        except PixabayError as exc:
            raise SourceBlocked(str(exc)) from None
    elif source == 'baidu':
        url = 'https://image.baidu.com/search/acjson?' + urlencode({
            'tn': 'resultjson_com', 'ipn': 'rj', 'ct': '201326592', 'fp': 'result',
            'word': keyword, 'queryWord': keyword, 'pn': page * 30, 'rn': 30,
            'ie': 'utf-8', 'oe': 'utf-8', 'cl': 2, 'lm': -1,
        })
        parser = parse_baidu
    else:
        url = 'https://www.bing.com/images/async?' + urlencode({
            'q': keyword, 'first': page * 35 + 1, 'count': 35, 'adlt': 'strict', 'qft': '',
        })
        parser = parse_bing
    try:
        data, _content_type, _final = fetch_url(url, max_bytes=4 * 1024 * 1024, timeout=15)
    except FetchError as exc:
        if exc.status is not None:
            label = {'baidu': '\u767e\u5ea6', 'bing': '\u5fc5\u5e94', 'pixabay': 'Pixabay'}[source]
            raise SourceBlocked(f'{label}\u6682\u65f6\u9650\u5236\u8bbf\u95ee\uff08HTTP {exc.status}\uff09\uff0c\u8bf7\u7a0d\u540e\u91cd\u8bd5\u3002') from exc
        raise
    try:
        return parser(data.decode('utf-8', errors='replace'))
    except SourceBlocked:
        if source != 'baidu':
            raise
        from .baidu_browser import collect
        return collect(keyword, page)
