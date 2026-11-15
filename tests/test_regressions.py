import base64
import json

import pytest
from itsdangerous import TimestampSigner
from fastapi.testclient import TestClient
from app.main import app, db
from app.storage import Store


@pytest.fixture
def http():
    db.execute("INSERT OR IGNORE INTO users(sub, username, display_name) VALUES ('regression', 'tester', 'Tester')")
    db.commit()
    with TestClient(app) as client:
        payload = base64.b64encode(json.dumps({'sub': 'regression'}).encode())
        client.cookies.set('session', TimestampSigner('test').sign(payload).decode())
        yield client


def test_owner_crud_validation_and_upload(http):
    response = http.post('/api/wishlists/tester/items', json={'title': ' Test ', 'price': '100', 'links': ['https://example.com']})
    assert response.status_code == 200
    item = response.json()
    assert item['title'] == 'Test'
    assert item['price'] == '100'
    path = f"/api/items/{item['id']}"
    assert http.patch(path, json={'title': 'Changed', 'links': ['javascript:alert(1)']}).status_code == 422
    assert http.get('/api/wishlists/tester').json()['items'][-1]['title'] == 'Test'
    assert http.post(path + '/image', files={'file': ('x.svg', b'<svg/>', 'image/svg+xml')}).status_code == 415
    assert http.post(path + '/image', files={'file': ('x.png', b'not a png', 'image/png')}).status_code == 415
    assert http.post('/api/wishlists/tester/items', json={'title': 'x' * 121}).status_code == 422
    http.cookies.clear()
    assert http.patch(path, json={'title': 'Intruder'}).status_code == 401


def test_unicode_reservations(http):
    item = http.post('/api/wishlists/tester/items', json={'title': 'Unicode'}).json()
    path = f"/api/items/{item['id']}"
    http.cookies.clear()
    assert http.post(path + '/reserve', json={'name': '  Маша  Иванова '}).status_code == 200
    assert http.post(path + '/reserve', json={'name': 'МАША ИВАНОВА'}).status_code == 200
    assert http.post(path + '/reserve', json={'name': 'Другой'}).status_code == 409
    assert http.post(path + '/release', json={'name': 'маша иванова'}).status_code == 200


def test_local_storage_confines_paths(tmp_path):
    store = Store(tmp_path, '', '', '', '', '')
    (tmp_path / 'secret').write_text('secret')
    assert store.read('../secret') is None
    assert store.read('bad\x00key') is None
    assert store.read('x' * 1025) is None
    assert store.read(str(tmp_path / 'secret')) is None
    with pytest.raises(ValueError):
        store.put('tester', b'html', 'text/html')


def test_authenticated_reservations_use_session_username(http):
    item = http.post('/api/wishlists/tester/items', json={'title': 'Signed-in gift'}).json()
    path = f"/api/items/{item['id']}"
    assert http.post(path + '/reserve', json={'name': 'Impersonated'}).json() == {'reserved': True, 'name': 'tester'}
    assert http.post(path + '/release', json={'name': 'Different'}).status_code == 200
    assert http.post(path + '/reserve').json() == {'reserved': True, 'name': 'tester'}
    assert http.post(path + '/release').status_code == 200
    assert not db.execute("SELECT 1 FROM guests WHERE name = 'Impersonated'").fetchone()


def test_authenticated_user_cannot_release_another_name(http):
    item = http.post('/api/wishlists/tester/items', json={'title': 'Someone else'}).json()
    path = f"/api/items/{item['id']}"
    with TestClient(app) as guest_client:
        assert guest_client.post(path + '/reserve', json={'name': 'Another guest'}).status_code == 200
    assert http.post(path + '/release', json={'name': 'Another guest'}).status_code == 403
    assert http.post(path + '/reserve').status_code == 409


def test_anonymous_reservations_still_require_a_name(http):
    item = http.post('/api/wishlists/tester/items', json={'title': 'Guest gift'}).json()
    path = f"/api/items/{item['id']}"
    http.cookies.clear()
    assert http.post(path + '/reserve').status_code == 422
    assert http.post(path + '/release').status_code == 422
