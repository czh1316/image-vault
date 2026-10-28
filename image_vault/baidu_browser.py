"""Load the ordinary Baidu results page in Chrome; stop at verification."""
import os
from urllib.parse import urlencode, urlsplit

from .crawler import SourceBlocked, parse_baidu_page


def collect(keyword, page_number):
    if os.environ.get('VAULT_BAIDU_BROWSER', '1') == '0':
        raise SourceBlocked('百度接口要求验证；浏览器采集已关闭，可切换 Pixabay 或必应。')
    try:
        from playwright.sync_api import sync_playwright, Error, TimeoutError
    except ImportError:
        raise SourceBlocked('百度需要浏览器加载，请安装 requirements.txt 中的 Playwright 和 Chrome。') from None
    url = 'https://image.baidu.com/search/index?' + urlencode(
        {'tn': 'baiduimage', 'word': keyword, 'pn': page_number * 30})
    try:
        with sync_playwright() as p:
            # A fresh context never reads cookies or credentials from the user's browser.
            channel = os.environ.get('VAULT_BROWSER_CHANNEL', 'chrome')
            browser = p.chromium.launch(channel=channel or None, headless=True)
            try:
                page = browser.new_page(viewport={'width': 1440, 'height': 1000})
                page.goto(url, wait_until='domcontentloaded', timeout=20000)
                target = urlsplit(page.url)
                if target.hostname != 'image.baidu.com' or '安全验证' in page.title():
                    raise SourceBlocked('百度浏览器页面要求安全验证，请稍后重试或切换 Pixabay、必应。')
                try:
                    page.locator('a[href*="/search/detail"] img').first.wait_for(state='attached', timeout=12000)
                except TimeoutError:
                    raise SourceBlocked('百度未提供可采集的图片，可能需要验证或暂无结果，请更换关键词或来源。') from None
                rows = parse_baidu_page(page.content())
                if not rows:
                    raise SourceBlocked('百度页面没有可识别的图片，请更换关键词或来源。')
                return rows
            finally:
                browser.close()
    except Error:
        raise SourceBlocked('百度浏览器加载失败，请检查 Chrome 安装和网络，或切换 Pixabay、必应。') from None
