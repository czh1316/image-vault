"""Live browser acceptance using isolated accounts and the shared API cache."""
import json
import secrets
import sys
import tempfile
import threading
import time
import zipfile
from pathlib import Path

from playwright.sync_api import sync_playwright
from werkzeug.serving import make_server

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from image_vault import create_app
from image_vault.pixabay import settings

BASE = 'http://127.0.0.1:8768'


def main():
    cache_dir, key = settings()
    reports, errors = [], []
    with tempfile.TemporaryDirectory(prefix='vault-live-sources-') as directory:
        app = create_app({'DATA_DIR': directory, 'PIXABAY_API_KEY': key,
                          'PIXABAY_CACHE_DIR': str(cache_dir), 'RUN_WORKERS': True})
        server = make_server('127.0.0.1', 8768, app, threaded=True)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(channel='chrome', headless=True)
                context = browser.new_context(viewport={'width': 1440, 'height': 1100})
                page = context.new_page()
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.goto(BASE, wait_until='networkidle')
                page.locator('#register-tab').click()
                page.locator('#username').fill('live_' + secrets.token_hex(4))
                page.locator('#password').fill(secrets.token_urlsafe(20))
                page.locator('#auth-submit').click()
                page.locator('#app-view').wait_for(state='visible')
                for source, keyword in [('pixabay', 'mountain'), ('baidu', 'cat')]:
                    page.locator('#source').select_option(source)
                    page.locator('#keyword').fill(keyword)
                    page.locator('#limit').fill('3')
                    page.locator('#collect-submit').click()
                    deadline = time.monotonic() + 90
                    while time.monotonic() < deadline:
                        jobs = page.request.get(BASE + '/api/jobs').json()['jobs']
                        if jobs and jobs[0]['source'] == source and jobs[0]['status'] not in ('queued', 'running'):
                            break
                        page.wait_for_timeout(500)
                    assert jobs[0]['source'] == source and jobs[0]['status'] == 'completed', jobs
                    assert jobs[0]['saved'] == 3, jobs
                    reports.append({'source': source, 'saved': jobs[0]['saved'], 'status': jobs[0]['status']})
                    page.locator('#collect-submit').wait_for(state='visible')
                    page.reload(wait_until='networkidle')
                page.locator('.image-card').first.wait_for(state='visible')
                assert page.locator('.image-card').count() == 6
                for img in page.locator('.image-card img').all():
                    assert img.evaluate('(img)=>img.complete && img.naturalWidth>0')
                gallery = page.request.get(BASE + '/api/images').json()
                assert gallery['total'] == 6
                assert key not in json.dumps(gallery)
                for image in gallery['images']:
                    assert page.request.get(BASE + '/api/images/' + image['id'] + '/file').status == 200
                page.locator('.image-button').first.click()
                with page.expect_download() as download:
                    page.locator('#detail-download').click()
                assert Path(download.value.path()).stat().st_size > 100
                page.locator('#close-image').click()
                page.locator('#select-button').click()
                page.locator('#select-all').click()
                with page.expect_download() as download:
                    page.locator('#export-selected').click()
                with zipfile.ZipFile(download.value.path()) as archive:
                    assert len(archive.namelist()) == 6
                other = browser.new_context()
                assert other.request.post(BASE + '/api/auth/register', data={
                    'username': 'other_' + secrets.token_hex(4), 'password': secrets.token_urlsafe(20)}).status == 201
                assert other.request.get(BASE + '/api/images').json()['total'] == 0
                assert other.request.get(BASE + '/api/images/' + gallery['images'][0]['id'] + '/file').status == 404
                other.close()
                page.screenshot(path=str(ROOT / 'analysis/live-sources-desktop.png'), full_page=True)
                page.set_viewport_size({'width': 390, 'height': 844})
                assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
                assert not errors, errors
                browser.close()
            report = {'live': True, 'results': reports, 'page_errors': errors,
                      'checks': ['real API/browser searches', 'real downloads saved to account',
                                 'gallery images display', 'single download', 'ZIP export',
                                 'account isolation', 'no key in gallery response', 'mobile layout']}
            (ROOT / 'analysis/live-sources-browser-verification.json').write_text(
                json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
            print(json.dumps(report))
        finally:
            app.extensions['job_executor'].shutdown(wait=True, cancel_futures=True)
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)


if __name__ == '__main__':
    main()
