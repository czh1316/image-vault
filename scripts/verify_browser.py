"""Real browser acceptance flow; temporary accounts/data, real public search."""
import io
import json
import os
import secrets
import subprocess
import sys
import tempfile
import time
import urllib.request
import zipfile
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
BASE = 'http://127.0.0.1:8766'


def main():
    errors = []
    console_errors = []
    password = secrets.token_urlsafe(18)
    with tempfile.TemporaryDirectory(prefix='image-vault-browser-') as directory:
        env = dict(os.environ, VAULT_DATA_DIR=directory, VAULT_PORT='8766')
        log = open(ROOT / 'analysis/browser-server.log', 'w', encoding='utf-8')
        proc = subprocess.Popen([sys.executable, '-u', str(ROOT / 'run.py')], cwd=ROOT, env=env,
                                stdout=log, stderr=subprocess.STDOUT,
                                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        try:
            for _ in range(50):
                try:
                    with urllib.request.urlopen(BASE + '/api/health', timeout=1) as response:
                        if response.status == 200:
                            break
                except Exception:
                    time.sleep(.2)
            else:
                raise RuntimeError('Browser test server did not start')
            with sync_playwright() as p:
                browser = p.chromium.launch(channel='chrome', headless=True)
                context = browser.new_context(viewport={'width': 1440, 'height': 1100}, device_scale_factor=1)
                page = context.new_page()
                page.on('pageerror', lambda exc: errors.append(str(exc)))
                page.on('console', lambda message: console_errors.append(message.text) if message.type == 'error' else None)
                page.goto(BASE, wait_until='networkidle')
                assert page.locator('#source option[value="pixabay"]').count() == 1
                assert 'Pixabay' in page.locator('#source').inner_text()
                page.locator('#auth-view').wait_for(state='visible')
                page.screenshot(path=str(ROOT / 'analysis/login-desktop.png'), full_page=True)
                page.locator('#register-tab').click()
                page.locator('#username').fill('browser_' + secrets.token_hex(4))
                page.locator('#password').fill(password)
                page.locator('#auth-submit').click()
                page.locator('#app-view').wait_for(state='visible')
                page.locator('#source').select_option('pixabay')
                assert page.locator('#source').input_value() == 'pixabay'
                page.locator('#source').select_option('auto')
                page.locator('#gallery-empty').wait_for(state='visible')
                page.screenshot(path=str(ROOT / 'analysis/gallery-empty-desktop.png'), full_page=True)
                assert page.locator('#stat-images').inner_text().startswith('0')
                page.locator('#keyword').fill('山水')
                page.locator('#limit').fill('4')
                page.locator('#source').select_option('auto')
                page.locator('#collect-submit').click()
                deadline = time.time() + 100
                jobs = []
                while time.time() < deadline:
                    jobs = page.request.get(BASE + '/api/jobs').json()['jobs']
                    if jobs and jobs[0]['status'] not in ('queued', 'running'):
                        break
                    page.wait_for_timeout(1000)
                assert jobs and jobs[0]['status'] == 'completed', jobs
                assert jobs[0]['saved'] == 4, jobs
                page.reload(wait_until='networkidle')
                page.locator('.image-card').first.wait_for(state='visible')
                assert page.locator('.image-card').count() == 4
                for img in page.locator('.image-card img').all():
                    assert img.evaluate('(img)=>img.complete && img.naturalWidth>0')
                gallery = page.request.get(BASE + '/api/images').json()
                saved_id = gallery['images'][0]['id']
                page.screenshot(path=str(ROOT / 'analysis/gallery-desktop.png'), full_page=True)
                page.locator('.image-button').first.click()
                page.locator('#image-dialog').wait_for(state='visible')
                with page.expect_download() as download:
                    page.locator('#detail-download').click()
                assert Path(download.value.path()).stat().st_size > 100
                page.locator('#close-image').click()
                page.locator('#gallery-search').fill('没有这样的采集关键词12345')
                page.locator('#empty-title').get_by_text('还没有找到匹配的图片').wait_for(state='visible')
                page.locator('#gallery-search').fill('')
                page.locator('.image-card').first.wait_for(state='visible')
                page.locator('#select-button').click()
                page.locator('#select-all').click()
                with page.expect_download() as download:
                    page.locator('#export-selected').click()
                with zipfile.ZipFile(download.value.path()) as z:
                    assert len(z.namelist()) == 4
                page.locator('#delete-selected').click()
                page.locator('#confirm-delete').click()
                page.locator('#gallery-empty').wait_for(state='visible')
                assert page.request.get(BASE + '/api/images').json()['total'] == 0
                page.locator('#cancel-selection').click()
                page.locator('#toast').wait_for(state='hidden')
                page.set_viewport_size({'width': 390, 'height': 844})
                page.screenshot(path=str(ROOT / 'analysis/gallery-mobile.png'), full_page=True)
                assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
                page.locator('#keyword').fill('猫咪')
                page.locator('#limit').fill('200')
                page.locator('#collect-submit').click()
                page.locator('.job-row .text-button').first.wait_for(state='visible')
                page.locator('.job-row .text-button').first.click()
                page.locator('#nav-tasks').click()
                page.locator('.job-badge.cancelled').wait_for(state='visible')
                assert page.request.get(BASE + '/api/jobs').json()['jobs'][0]['status'] == 'cancelled'
                page.locator('#logout').click()
                page.locator('#auth-view').wait_for(state='visible')
                page.screenshot(path=str(ROOT / 'analysis/login-mobile.png'), full_page=True)
                second = browser.new_context(viewport={'width': 1440, 'height': 1000})
                other = second.new_page()
                other.goto(BASE)
                other.locator('#register-tab').click()
                other.locator('#username').fill('other_' + secrets.token_hex(4))
                other.locator('#password').fill(password)
                other.locator('#auth-submit').click()
                other.locator('#app-view').wait_for(state='visible')
                assert other.request.get(BASE + '/api/images').json()['total'] == 0
                assert other.request.get(BASE + '/api/images/' + saved_id + '/file').status == 404
                second.close()
                context.close()
                browser.close()
                assert not errors, errors
                result = {'browser': 'Chrome', 'desktop': '1440x1100', 'mobile': '390x844',
                          'live_job': jobs[0], 'page_errors': errors, 'console_errors': console_errors,
                          'checks': ['Pixabay photo source', 'registration', 'live auto-source collection', 'real image files', 'detail and single download',
                                     'search empty state', 'ZIP export', 'delete files', 'mobile overflow', 'cancel running job',
                                     'logout', 'second account isolation']}
                (ROOT / 'analysis/browser-verification.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
                print(json.dumps(result, ensure_ascii=False, indent=2))
        finally:
            proc.terminate()
            proc.wait(timeout=20)
            log.close()


if __name__ == '__main__':
    main()
