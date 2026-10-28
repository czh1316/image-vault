"""Bounded downloads with DNS validation, pinned connections, and safe redirects."""
import http.client
import ipaddress
import re
import socket
import ssl
from urllib.parse import parse_qs, quote, urljoin, urlsplit
from urllib.request import getproxies

USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36'


class UnsafeURL(ValueError):
    pass


class FetchError(ValueError):
    def __init__(self, message, status=None, headers=None):
        super().__init__(message)
        self.status = status
        self.headers = headers or {}


def is_baidu_thumbnail(parsed):
    try:
        return (parsed.scheme == 'https' and not parsed.username and not parsed.password
                and parsed.port in (None, 443)
                and parsed.hostname in {f'img{i}.baidu.com' for i in range(4)}
                and bool(re.fullmatch(r'/it/u=\d+,\d+(?:&[A-Za-z0-9_=,.-]+)*', parsed.path)))
    except ValueError:
        return False


def is_pixabay_signed_photo(parsed):
    try:
        return (parsed.scheme == 'https' and parsed.hostname == 'pixabay.com'
                and not parsed.username and not parsed.password and parsed.port in (None, 443)
                and not parsed.query
                and bool(re.fullmatch(r'/get/g[0-9a-f]{32,256}_(?:640|1280)\.(?:jpe?g|png|webp)', parsed.path)))
    except ValueError:
        return False


def _is_pixabay_endpoint(parsed):
    try:
        if parsed.scheme != 'https' or parsed.username or parsed.password or parsed.port not in (None, 443):
            return False
        if parsed.hostname == 'pixabay.com':
            path = parsed.path
            if is_pixabay_signed_photo(parsed):
                return True
            if path == '/api/':
                query = parse_qs(parsed.query)
                required = {'key', 'q', 'lang', 'image_type', 'safesearch', 'per_page', 'page'}
                return (set(query) == required and all(len(v) == 1 for v in query.values())
                        and 1 <= len(query['key'][0]) <= 200 and 1 <= len(query['q'][0]) <= 100
                        and query['image_type'] == ['photo'] and query['lang'] == ['zh']
                        and query['safesearch'] == ['true'] and query['per_page'] == ['50']
                        and query['page'][0].isdigit() and 1 <= int(query['page'][0]) <= 10)
            if re.search(r'%(?:2e|2f|5c)', path, re.I) or not re.fullmatch(r'/(?:zh/)?photos/search/[^/]+/?', path):
                return False
            query = parse_qs(parsed.query)
            pagi = query.get('pagi', [])
            return (not query or
                    (set(query) == {'pagi'} and len(pagi) == 1 and pagi[0].isdigit()
                     and 1 <= int(pagi[0]) <= 20))
        if parsed.hostname == 'cdn.pixabay.com':
            if re.search(r'%(?:2e|2f|5c)', parsed.path, re.I):
                return False
            parts = parsed.path.split('/')
            return (len(parts) >= 3 and parts[1] == 'photo'
                    and all(part not in ('.', '..') for part in parts)
                    and '\\' not in parsed.path
                    and bool(re.search(r'\.(?:jpe?g|png|webp)$', parsed.path, re.I)))
        return False
    except ValueError:
        return False


def resolve_public_url(url):
    if not isinstance(url, str) or len(url) > 8192 or any(ord(c) < 32 for c in url):
        raise UnsafeURL('图片地址无效')
    try:
        parsed = urlsplit(url)
        if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password:
            raise UnsafeURL('仅允许无认证的 HTTP/HTTPS 公网地址')
        port = parsed.port or (443 if parsed.scheme == 'https' else 80)
        if port not in (80, 443):
            raise UnsafeURL('仅允许标准网页端口')
        hostname = parsed.hostname.encode('idna').decode('ascii')
        try:
            literal = ipaddress.ip_address(hostname)
        except ValueError:
            literal = None
        if literal is not None and not literal.is_global:
            raise UnsafeURL('已阻止访问内网或特殊地址')
        addresses = socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
    except (ValueError, UnicodeError, socket.gaierror) as exc:
        raise UnsafeURL('无法解析图片公网地址') from exc
    if not addresses:
        raise UnsafeURL('图片地址没有可用记录')
    for item in addresses:
        ip = ipaddress.ip_address(item[4][0].split('%')[0])
        if not ip.is_global or ip.is_multicast or ip.is_reserved:
            raise UnsafeURL('已阻止访问内网或特殊地址')
    return parsed, hostname, port, addresses[0][4][0]


def proxy_for_source(url):
    """Honor the operator's proxy only for fixed search/CDN endpoints.

    Some Windows proxies return synthetic benchmark DNS addresses. Arbitrary
    source URLs must still pass public-address validation and pinned download.
    """
    try:
        parsed = urlsplit(url)
        if parsed.scheme != 'https' or parsed.username or parsed.password or parsed.port not in (None, 443):
            return None
        host = parsed.hostname
        allowed = ((host in ('www.bing.com', 'cn.bing.com') and parsed.path == '/images/async')
                   or (host == 'image.baidu.com' and parsed.path == '/search/acjson')
                   or (host in {f'{prefix}{i}.mm.bing.net' for prefix in ('tse', 'ts') for i in range(1, 5)}
                       and (parsed.path == '/th' or parsed.path.startswith('/th/')))
                   or is_baidu_thumbnail(parsed) or _is_pixabay_endpoint(parsed))
        proxy = getproxies().get('https')
        if not allowed or not proxy:
            return None
        config = urlsplit(proxy)
        if config.scheme != 'http' or not config.hostname or config.username or config.password:
            return None
        return proxy
    except ValueError:
        return None


def _proxy_request_once(url, proxy, max_bytes, timeout):
    target, proxy_url = urlsplit(url), urlsplit(proxy)
    if proxy_url.scheme == 'https':
        conn = http.client.HTTPSConnection(proxy_url.hostname, proxy_url.port or 443,
                                           timeout=timeout, context=ssl.create_default_context())
    else:
        conn = http.client.HTTPConnection(proxy_url.hostname, proxy_url.port or 80, timeout=timeout)
    conn.set_tunnel(target.hostname, 443)
    try:
        if proxy_url.scheme == 'http':
            conn.connect()
            conn.sock = ssl.create_default_context().wrap_socket(conn.sock, server_hostname=target.hostname)
        path = quote(target.path or '/', safe='/%:@!$&\'()*+,;=-._~')
        if target.query:
            path += '?' + quote(target.query, safe='=&%/:?@!$\'()*+,;~-._')
        conn.request('GET', path, headers={'User-Agent': USER_AGENT, 'Accept-Encoding': 'identity', 'Referer': 'https://' + target.hostname + '/'})
        response = conn.getresponse()
        headers = {k.lower(): v for k, v in response.getheaders()}
        if response.status in (301, 302, 303, 307, 308):
            return response.status, headers, b''
        if response.status != 200:
            raise FetchError(f'远程服务器返回 HTTP {response.status}', response.status, headers)
        if headers.get('content-length', '').isdigit() and int(headers['content-length']) > max_bytes:
            raise FetchError('下载内容超过大小限制')
        data = response.read(max_bytes + 1)
        if len(data) > max_bytes:
            raise FetchError('下载内容超过大小限制')
        return response.status, headers, data
    except (OSError, http.client.HTTPException) as exc:
        raise FetchError('系统代理连接失败，请检查代理是否运行') from exc
    finally:
        conn.close()


def _request_once(parsed, hostname, port, address, max_bytes, timeout):
    conn = http.client.HTTPConnection(hostname, port, timeout=timeout)
    try:
        sock = socket.create_connection((address, port), timeout=timeout)
        if parsed.scheme == 'https':
            try:
                sock = ssl.create_default_context().wrap_socket(sock, server_hostname=hostname)
            except Exception:
                sock.close()
                raise
        conn.sock = sock
        path = quote(parsed.path or '/', safe='/%:@!$&\'()*+,;=-._~')
        if parsed.query:
            path += '?' + quote(parsed.query, safe='=&%/:?@!$\'()*+,;~-._')
        referer = ('https://pixabay.com/' if hostname.endswith('pixabay.com') else
                   'https://image.baidu.com/' if hostname.endswith('.baidu.com') else 'https://www.bing.com/')
        conn.request('GET', path, headers={
            'User-Agent': USER_AGENT, 'Accept': '*/*', 'Accept-Encoding': 'identity',
            'Referer': referer,
        })
        response = conn.getresponse()
        headers = {k.lower(): v for k, v in response.getheaders()}
        if response.status in (301, 302, 303, 307, 308):
            return response.status, headers, b''
        if response.status != 200:
            raise FetchError(f'远程服务器返回 HTTP {response.status}', response.status, headers)
        if headers.get('content-encoding', 'identity').lower() not in ('identity', ''):
            raise FetchError('来源返回了不支持的压缩内容')
        declared = headers.get('content-length', '')
        if declared.isdigit() and int(declared) > max_bytes:
            raise FetchError('下载内容超过大小限制')
        data = response.read(max_bytes + 1)
        if len(data) > max_bytes:
            raise FetchError('下载内容超过大小限制')
        return response.status, headers, data
    except (OSError, http.client.HTTPException) as exc:
        raise FetchError('连接超时或图片来源暂时无法访问') from exc
    finally:
        conn.close()


def fetch_url(url, max_bytes=12 * 1024 * 1024, timeout=12, include_headers=False):
    original_host = urlsplit(url).hostname
    pixabay_source = original_host in ('pixabay.com', 'cdn.pixabay.com')
    pixabay_api = original_host == 'pixabay.com' and urlsplit(url).path == '/api/'
    for _ in range(5):
        proxy = proxy_for_source(url)
        if proxy:
            status, headers, data = _proxy_request_once(url, proxy, max_bytes, timeout)
        else:
            parsed, hostname, port, address = resolve_public_url(url)
            status, headers, data = _request_once(parsed, hostname, port, address, max_bytes, timeout)
        if status in (301, 302, 303, 307, 308):
            if pixabay_api:
                raise UnsafeURL('Pixabay API 重定向已阻止，请检查官方接口地址。')
            location = headers.get('location')
            if not location:
                raise FetchError('重定向地址缺失')
            redirected = urljoin(url, location)
            if pixabay_source and not _is_pixabay_endpoint(urlsplit(redirected)):
                raise UnsafeURL('Pixabay redirected outside photo-search or photo-image paths')
            url = redirected
            continue
        result = (data, headers.get('content-type', ''), url)
        return (*result, headers) if include_headers else result
    raise FetchError('重定向次数过多')
