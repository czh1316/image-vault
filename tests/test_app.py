import io
import zipfile

import pytest
from PIL import Image

from image_vault import create_app
from image_vault.storage import store_image


@pytest.fixture
def app(tmp_path):
    return create_app({'TESTING': True, 'DATA_DIR': str(tmp_path), 'COVERS_DIR': str(tmp_path / 'covers'), 'RUN_WORKERS': False})


def register(client, username='alice'):
    response = client.post('/api/auth/register', json={'username': username, 'password': 'test-password-123'})
    assert response.status_code == 201, response.json
    return response.json['csrf_token'], response.json['user']['id']


def image_bytes(color='red', fmt='PNG'):
    output = io.BytesIO()
    Image.new('RGB', (40, 30), color).save(output, fmt)
    return output.getvalue()


def seed(app, user_id):
    with app.app_context():
        return store_image(user_id, None, '山水', {'url': 'https://example.com/a.png', 'title': '山水', 'source': 'bing'}, image_bytes())


def test_register_login_persist_and_revoke_session(app):
    client = app.test_client()
    token, user_id = register(client)
    assert client.get('/api/auth/me').json['user']['id'] == user_id
    assert client.post('/api/auth/logout', headers={'X-CSRF-Token': token}).status_code == 200
    assert client.get('/api/auth/me').status_code == 401
    assert client.post('/api/auth/login', json={'username': 'alice', 'password': 'incorrect'}).status_code == 401
    assert client.post('/api/auth/login', json={'username': 'alice', 'password': 'test-password-123'}).status_code == 200
    rebuilt = create_app({'TESTING': True, 'DATA_DIR': app.config['DATA_DIR'], 'RUN_WORKERS': False})
    another_client = rebuilt.test_client()
    another_client.set_cookie('vault_session', client.get_cookie('vault_session').value)
    assert another_client.get('/api/auth/me').json['user']['username'] == 'alice'


def test_mutation_requires_csrf_and_disallows_foreign_origin(app):
    client = app.test_client()
    token, _ = register(client)
    assert client.post('/api/jobs', json={'keyword': '山水', 'limit': 5, 'source': 'baidu'}).status_code == 403
    response = client.post('/api/jobs', json={'keyword': '山水', 'limit': 5, 'source': 'baidu'}, headers={'X-CSRF-Token': token, 'Origin': 'https://evil.example'})
    assert response.status_code == 403


def test_images_deduplicate_and_never_cross_accounts(app):
    alice, bob = app.test_client(), app.test_client()
    alice_token, alice_id = register(alice)
    bob_token, bob_id = register(bob, 'bob')
    first = seed(app, alice_id)
    assert first['status'] == 'saved'
    assert seed(app, alice_id)['status'] == 'duplicate'
    assert seed(app, bob_id)['status'] == 'saved'
    items = alice.get('/api/images').json
    assert items['total'] == 1
    image_id = first['id']
    assert alice.get(f'/api/images/{image_id}/file').data == image_bytes()
    assert bob.get(f'/api/images/{image_id}/file').status_code == 404
    assert bob.post('/api/images/delete', json={'ids': [image_id]}, headers={'X-CSRF-Token': bob_token}).status_code == 404
    exported = alice.post('/api/images/export', json={'ids': [image_id]}, headers={'X-CSRF-Token': alice_token})
    assert exported.status_code == 200
    with zipfile.ZipFile(io.BytesIO(exported.data)) as z:
        assert len(z.namelist()) == 1
        assert z.read(z.namelist()[0]) == image_bytes()
    assert alice.post('/api/images/delete', json={'ids': [image_id]}, headers={'X-CSRF-Token': alice_token}).json['deleted'] == 1
    assert alice.get('/api/images').json['total'] == 0
    assert alice.get(f'/api/images/{image_id}/file').status_code == 404
    assert bob.get('/api/images').json['total'] == 1


def test_rejects_non_images_and_enforces_storage_quota(app):
    client = app.test_client()
    _, user_id = register(client)
    with app.app_context():
        with pytest.raises(ValueError):
            store_image(user_id, None, 'x', {'url': 'https://example.com/a'}, b'<html>captcha</html>')
        app.config['USER_QUOTA_BYTES'] = 10
        with pytest.raises(ValueError, match='配额'):
            store_image(user_id, None, 'x', {'url': 'https://example.com/a'}, image_bytes())
    assert client.get('/api/images').json['total'] == 0


def test_job_validation_active_limit_cancel_and_owner(app):
    alice, bob = app.test_client(), app.test_client()
    token, _ = register(alice)
    bob_token, _ = register(bob, 'bob')
    headers = {'X-CSRF-Token': token}
    for payload in [{'keyword': '', 'limit': 1}, {'keyword': 'x', 'limit': 999}, {'keyword': 'x', 'limit': True}, {'keyword': 'x', 'limit': 2, 'source': 'invalid'}]:
        assert alice.post('/api/jobs', json=payload, headers=headers).status_code == 400
    response = alice.post('/api/jobs', json={'keyword': '山水', 'limit': 3, 'source': 'auto'}, headers=headers)
    assert response.status_code == 201
    job_id = response.json['job']['id']
    assert alice.post('/api/jobs', json={'keyword': '猫', 'limit': 2}, headers=headers).status_code == 409
    assert bob.post(f'/api/jobs/{job_id}/cancel', headers={'X-CSRF-Token': bob_token}).status_code == 404
    assert alice.post(f'/api/jobs/{job_id}/cancel', headers=headers).json['job']['status'] == 'cancelled'
    assert bob.get('/api/jobs').json['jobs'] == []


def test_restart_marks_unfinished_jobs_interrupted(app):
    client = app.test_client()
    token, _ = register(client)
    client.post('/api/jobs', json={'keyword': '山水', 'limit': 3}, headers={'X-CSRF-Token': token})
    rebuilt = create_app({'TESTING': True, 'DATA_DIR': app.config['DATA_DIR'], 'RUN_WORKERS': False})
    other = rebuilt.test_client()
    other.set_cookie('vault_session', client.get_cookie('vault_session').value)
    assert other.get('/api/jobs').json['jobs'][0]['status'] == 'interrupted'


def test_showcase_lists_only_configured_cover_images(app, tmp_path):
    covers = tmp_path / 'covers'
    covers.mkdir()
    (covers / 'mist.png').write_bytes(image_bytes('blue'))
    (covers / 'autumn.jpg').write_bytes(image_bytes('orange', 'JPEG'))
    (covers / 'readme.txt').write_text('not an image', encoding='utf-8')
    client = app.test_client()
    listing = client.get('/api/showcase').json
    assert len(listing['images']) == 2
    assert {image['title'] for image in listing['images']} == {'mist', 'autumn'}
    for image in listing['images']:
        assert set(image) == {'id', 'title', 'category'}
        response = client.get('/api/showcase/' + image['id'] + '/file')
        assert response.status_code == 200
        assert response.mimetype in {'image/png', 'image/jpeg'}
        assert response.data.startswith(b'\x89PNG') or response.data.startswith(b'\xff\xd8')
    assert client.get('/api/showcase/' + '0' * 16 + '/file').status_code == 404
    assert client.get('/api/showcase/not-a-cover/file').status_code == 404


def test_showcase_never_exposes_private_gallery_images(app):
    client = app.test_client()
    _, user_id = register(client)
    with app.app_context():
        landscape = store_image(user_id, None, '风景', {'url': 'https://example.com/landscape.png', 'title': '风景'}, image_bytes('blue'))
    listing = client.get('/api/showcase').json
    assert listing['images'] == []
    assert client.get('/api/showcase/' + landscape['id'] + '/file').status_code == 404


def test_showcase_tolerates_missing_cover_directory(app, tmp_path):
    app.config['COVERS_DIR'] = str(tmp_path / 'absent')
    client = app.test_client()
    assert client.get('/api/showcase').json == {'images': []}
