"""Pixabay browser flow with synthetic HTTP fixtures; no external site bypass."""
import io
import json
import secrets
import sys
import tempfile
import threading
import time
import zipfile
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from PIL import Image
from playwright.sync_api import sync_playwright
from werkzeug.serving import make_server

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from image_vault import create_app
from image_vault import jobs, pixabay

BASE = 'http://127.0.0.1:8767'
PAGE = json.dumps({'hits': [
    {'type': 'photo', 'tags': 'Mountain fixture', 'pageURL': 'https://pixabay.com/zh/photos/mountain-123/',
     'largeImageURL': 'https://cdn.pixabay.com/photo/mountain_1280.jpg'},
    {'type': 'photo', 'tags': 'Lake fixture', 'pageURL': 'https://pixabay.com/zh/photos/lake-456/',
     'largeImageURL': 'https://cdn.pixabay.com/photo/lake_640.jpg'},
    {'type': 'video', 'largeImageURL': 'https://cdn.pixabay.com/video/movie.mp4'},
]}).encode()


def main():
    downloads = []
    errors = []
    with tempfile.TemporaryDirectory(prefix='vault-pixabay-browser-') as directory:
        app = create_app({'TESTING': True, 'DATA_DIR': directory, 'RUN_WORKERS': True,
                          'CRAWL_DELAY': 0, 'PAGE_DELAY': 0, 'PIXABAY_API_KEY': 'fixture-key'})

        def search(url, **kwargs):
            parsed = urlsplit(url)
            assert parsed.hostname == 'pixabay.com'
            assert parsed.path == '/api/'
            query = parse_qs(parsed.query)
            assert query['q'] == ['mountain'] and query['image_type'] == ['photo']
            assert query['page'] == ['1']
            return PAGE, 'application/json', url, {}

        def photo(url, **kwargs):
            assert url in ('https://cdn.pixabay.com/photo/mountain_1280.jpg',
                           'https://cdn.pixabay.com/photo/lake_640.jpg')
            downloads.append(url)
            stream = io.BytesIO()
            Image.new('RGB', (640, 480), 'teal' if 'mountain' in url else 'navy').save(stream, 'JPEG')
            return stream.getvalue(), 'image/jpeg', url

        server = make_server('127.0.0.1', 8767, app, threaded=True)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with patch.object(pixabay, 'fetch_url', search), patch.object(jobs, 'fetch_url', photo), sync_playwright() as p:
                browser = p.chromium.launch(channel='chrome', headless=True)
                context = browser.new_context(viewport={'width': 1440, 'height': 1100})
                page = context.new_page()
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.goto(BASE, wait_until='networkidle')
                page.locator('#register-tab').click()
                page.locator('#username').fill('pixabay_' + secrets.token_hex(4))
                page.locator('#password').fill(secrets.token_urlsafe(18))
                page.locator('#auth-submit').click()
                page.locator('#app-view').wait_for(state='visible')
                page.locator('#source').select_option('pixabay')
                page.locator('#keyword').fill('mountain')
                page.locator('#limit').fill('2')
                page.locator('#collect-submit').click()
                deadline = time.monotonic() + 15
                while time.monotonic() < deadline:
                    result = page.request.get(BASE + '/api/jobs').json()['jobs']
                    if result and result[0]['status'] not in ('queued', 'running'):
                        break
                    page.wait_for_timeout(100)
                assert result[0]['status'] == 'completed', result
                assert result[0]['saved'] == 2, result
                assert result[0]['actual_source'] == 'pixabay'
                page.reload(wait_until='networkidle')
                page.locator('.image-card').first.wait_for(state='visible')
                assert page.locator('.image-card').count() == 2
                for image in page.locator('.image-card img').all():
                    assert image.evaluate('(img)=>img.complete && img.naturalWidth===640')
                gallery = page.request.get(BASE + '/api/images').json()['images']
                assert all(image['source'] == 'pixabay' for image in gallery)
                page.locator('.image-button').first.click()
                assert 'Pixabay' in page.locator('#detail-meta').inner_text()
                assert page.locator('#detail-source').get_attribute('href').startswith('https://pixabay.com/zh/photos/')
                with page.expect_download() as single:
                    page.locator('#detail-download').click()
                assert Path(single.value.path()).stat().st_size > 100
                page.locator('#close-image').click()
                page.locator('#select-button').click()
                page.locator('#select-all').click()
                with page.expect_download() as export:
                    page.locator('#export-selected').click()
                with zipfile.ZipFile(export.value.path()) as archive:
                    assert len(archive.namelist()) == 2
                second = browser.new_context()
                registration = second.request.post(BASE + '/api/auth/register', data={
                    'username': 'other_' + secrets.token_hex(4), 'password': secrets.token_urlsafe(18)})
                assert registration.status == 201
                assert second.request.get(BASE + '/api/images').json()['total'] == 0
                assert second.request.get(BASE + '/api/images/' + gallery[0]['id'] + '/file').status == 404
                second.close()
                page.set_viewport_size({'width': 390, 'height': 844})
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                assert not errors, errors
                context.close()
                browser.close()
                report = {'data_source': 'synthetic official API response and generated JPEGs; not a live Pixabay success',
                          'saved': 2, 'downloaded_urls': downloads, 'page_errors': errors,
                          'checks': ['Pixabay selected in browser', 'official API photo filtering',
                                     'worker saved files into account', 'photo source detail link',
                                     'single download', 'ZIP export', 'account isolation', 'mobile overflow']}
                (ROOT / 'analysis/pixabay-browser-verification.json').write_text(
                    json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
                print(json.dumps(report))
        finally:
            app.extensions['job_executor'].shutdown(wait=True, cancel_futures=True)
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)


if __name__ == '__main__':
    main()
