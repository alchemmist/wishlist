import base64
from concurrent.futures import ThreadPoolExecutor
import io
import json
import sqlite3

from botocore.stub import Stubber
from fastapi.testclient import TestClient
from itsdangerous import TimestampSigner
from PIL import Image
import pytest

from app.db import connect
from app.main import app, db, store, valid_username
from app.storage import Store


def session(sub):
    payload = base64.b64encode(json.dumps({'sub': sub}).encode())
    return TimestampSigner('test').sign(payload).decode()


@pytest.fixture
def owner():
    db.execute("INSERT INTO users(sub, username) VALUES ('audit', 'AuditOwner')")
    db.execute("INSERT INTO users(sub, username) VALUES ('other', 'OtherOwner')")
    db.commit()
    with TestClient(app) as client:
        client.cookies.set('session', session('audit'), domain='testserver.local')
        yield client


def create(owner):
    response = owner.post('/api/wishlists/auditowner/items', json={'title': 'Gift', 'links': ['https://example.com']})
    assert response.status_code == 200
    return response.json()['id']


def image_bytes(fmt='PNG'):
    output = io.BytesIO()
    Image.new('RGB', (20, 20), 'red').save(output, format=fmt)
    return output.getvalue()


def test_isolated_connections_and_concurrent_reservation(owner):
    item = create(owner)

    def attempt(index):
        with TestClient(app) as client:
            return client.post(f'/api/items/{item}/reserve', json={'name': f'Guest {index}'})

    with ThreadPoolExecutor(max_workers=12) as pool:
        responses = list(pool.map(attempt, range(24)))
    assert [r.status_code for r in responses].count(200) == 1
    assert [r.status_code for r in responses].count(409) == 23
    assert db.execute('SELECT COUNT(*) FROM reservations').fetchone()[0] == 1
    # A rejected reservation must roll back the guest insertion as well.
    assert db.execute('SELECT COUNT(*) FROM guests').fetchone()[0] == 1
    winner = next(r.json()['name'] for r in responses if r.status_code == 200)
    with TestClient(app) as guest_client:
        assert guest_client.post(f'/api/items/{item}/release', json={'name': winner}).status_code == 200


def test_idempotent_same_name_and_deleted_items(owner):
    item = create(owner)
    with ThreadPoolExecutor(max_workers=8) as pool:
        responses = list(pool.map(lambda _: owner.post(f'/api/items/{item}/reserve', json={'name': 'Маша'}), range(12)))
    assert all(response.status_code == 200 for response in responses)
    assert owner.delete(f'/api/items/{item}').status_code == 204
    assert owner.post(f'/api/items/{item}/reserve', json={'name': 'Маша'}).status_code == 404
    assert owner.post(f'/api/items/{item}/release', json={'name': 'Маша'}).status_code == 404
    assert db.execute('SELECT COUNT(*) FROM reservations').fetchone()[0] == 0
    assert db.execute('SELECT COUNT(*) FROM links').fetchone()[0] == 0


def test_owner_checks_use_id_and_unique_unicode_usernames(owner):
    item = create(owner)
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("INSERT INTO users(sub, username) VALUES ('collision', 'AUDITOWNER')")
    db.rollback()
    owner.cookies.clear()
    owner.cookies.set('session', session('other'))
    for method, path, kwargs in [
        ('patch', f'/api/items/{item}', {'json': {'title': 'No'}}),
        ('delete', f'/api/items/{item}', {}),
        ('delete', f'/api/items/{item}/image', {}),
        ('post', '/api/wishlists/AuditOwner/items', {'json': {'title': 'No'}}),
        ('post', f'/api/items/{item}/image', {'files': {'file': ('x.png', image_bytes(), 'image/png')}}),
    ]:
        assert getattr(owner, method)(path, **kwargs).status_code == 403
    owner.cookies.clear()
    assert owner.delete(f'/api/items/{item}').status_code == 401
    owner.cookies.set('session', 'forged')
    assert owner.patch(f'/api/items/{item}', json={'title': 'No'}).status_code == 401


@pytest.mark.parametrize('url', [
    'javascript:alert(1)', 'data:text/html,hi', '//example.com', 'https://',
    'https://user:pass@example.com', 'https://example.com:bad', 'https://example.com:99999',
    'https://exam ple.com', 'https://example.com\\evil', 'https://example.com/' + 'a' * 2048,
])
def test_bad_links_do_not_mutate_item(owner, url):
    item = create(owner)
    assert owner.patch(f'/api/items/{item}', json={'title': 'Changed', 'links': [url]}).status_code == 422
    data = owner.get('/api/wishlists/AuditOwner').json()['items'][0]
    assert data['title'] == 'Gift'
    assert data['links'] == ['https://example.com']


@pytest.mark.parametrize('fmt,mime,suffix', [('JPEG','image/jpeg','.jpg'), ('PNG','image/png','.png'), ('WEBP','image/webp','.webp'), ('GIF','image/gif','.gif')])
def test_images_round_trip_replace_and_cleanup(owner, fmt, mime, suffix):
    item = create(owner)
    path = f'/api/items/{item}/image'
    first = owner.post(path, files={'file': ('image' + suffix, image_bytes(fmt), mime)})
    assert first.status_code == 200
    old_url = first.json()['image']
    served = owner.get(old_url)
    assert served.status_code == 200
    assert served.headers['content-type'] == mime
    assert served.headers['x-content-type-options'] == 'nosniff'
    second = owner.post(path, files={'file': ('image' + suffix, image_bytes(fmt), mime)})
    assert second.status_code == 200
    assert owner.get(old_url).status_code == 404
    new_url = second.json()['image']
    assert owner.delete(path).status_code == 200
    assert owner.get(new_url).status_code == 404
    assert owner.get('/api/wishlists/AuditOwner').json()['items'][0]['image'] is None
    third = owner.post(path, files={'file': ('image' + suffix, image_bytes(fmt), mime)})
    assert owner.delete(f'/api/items/{item}').status_code == 204
    assert owner.get(third.json()['image']).status_code == 404


def test_upload_limits_and_storage_failure(owner, monkeypatch):
    item = create(owner)
    path = f'/api/items/{item}/image'
    assert owner.post(path, files={'file': ('big.png', image_bytes() + b'\0' * 9_000_001, 'image/png')}).status_code == 200
    assert owner.post(path, files={'file': ('empty.png', b'', 'image/png')}).status_code == 400
    assert owner.post(path, files={'file': ('mismatch.jpg', image_bytes(), 'image/jpeg')}).status_code == 415
    assert owner.post(path, files={'file': ('truncated.png', b'\x89PNG\r\n\x1a\n', 'image/png')}).status_code == 415
    def fail(*args):
        raise OSError('storage offline')
    monkeypatch.setattr(store, 'put_file', fail)
    assert owner.post(path, files={'file': ('valid.png', image_bytes(), 'image/png')}).status_code == 503
    monkeypatch.setattr(store, 'open', fail)
    assert owner.get('/media/1/file.png').status_code == 503


def test_security_headers_csrf_and_logout(owner):
    item = create(owner)
    response = owner.get('/api/me')
    assert response.headers['cache-control'] == 'no-store'
    assert "frame-ancestors 'none'" in response.headers['content-security-policy']
    assert owner.post('/logout', headers={'origin': 'https://evil.example'}).status_code == 403
    assert owner.get('/api/me').json()['user']['username'] == 'AuditOwner'
    assert owner.post(f'/api/items/{item}/reserve', json={'name': 'No'}, headers={'sec-fetch-site': 'cross-site'}).status_code == 403
    assert owner.post('/logout', headers={'origin': 'http://localhost'}).status_code == 200
    assert owner.get('/api/me').json() == {'user': None}


@pytest.mark.parametrize('username', ['', '.', '..', 'API', 'login', 'logout', 'static', 'docs', 'a/b', 'a\\b', 'a?b', 'a#b', 'a%b', 'two words', 'a\nb', 'a' * 151])
def test_invalid_usernames(username):
    assert not valid_username(username)


def test_legacy_guest_migration_preserves_reservations(tmp_path):
    path = tmp_path / 'old.sqlite'
    old = connect(path)
    old.execute('DROP INDEX guests_name_unicode')
    old.execute("INSERT INTO users(sub, username) VALUES ('u', 'u')")
    for guest_id, name in [(1, ' Маша '), (2, 'МАША'), (3, 'Masha '), (4, 'Masha')]:
        old.execute('INSERT INTO guests(id, name) VALUES (?, ?)', (guest_id, name))
        old.execute('INSERT INTO items(id, owner_id, title) VALUES (?, 1, ?)', (guest_id, name))
        old.execute('INSERT INTO reservations(item_id, guest_id) VALUES (?, ?)', (guest_id, guest_id))
    old.commit(); old.close()
    migrated = connect(path)
    assert [r['name'] for r in migrated.execute('SELECT name FROM guests ORDER BY id')] == ['Маша', 'Masha']
    assert [r[0] for r in migrated.execute('SELECT guest_id FROM reservations ORDER BY item_id')] == [1, 1, 3, 3]
    assert migrated.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
    assert migrated.execute('PRAGMA foreign_key_check').fetchall() == []
    migrated.close()
    connect(path).close()  # Migration is repeatable.


def test_legacy_conflicting_accounts_fail_without_merging_owners(tmp_path):
    path = tmp_path / 'old.sqlite'
    old = connect(path)
    old.execute('DROP INDEX users_username_unicode')
    old.execute("INSERT INTO users(sub, username) VALUES ('a', 'Маша'), ('b', 'МАША')")
    old.commit(); old.close()
    with pytest.raises(RuntimeError, match='Conflicting usernames'):
        connect(path)
    raw = sqlite3.connect(path)
    assert raw.execute('SELECT COUNT(*) FROM users').fetchone()[0] == 2
    raw.close()


def test_s3_read_delete_and_missing_vs_outage(tmp_path):
    storage = Store(tmp_path, 'https://s3.invalid', 'wishlist', 'test', 'test', '')
    with Stubber(storage.client) as stub:
        stub.add_response('get_object', {'Body': io.BytesIO(b'image')}, {'Bucket': 'wishlist', 'Key': '1/x.png'})
        assert storage.read('1/x.png') == b'image'
        stub.add_client_error('get_object', service_error_code='NoSuchKey', expected_params={'Bucket': 'wishlist', 'Key': '1/missing.png'})
        assert storage.read('1/missing.png') is None
        stub.add_client_error('get_object', service_error_code='AccessDenied', expected_params={'Bucket': 'wishlist', 'Key': '1/denied.png'})
        with pytest.raises(Exception):
            storage.read('1/denied.png')
        stub.add_response('delete_object', {}, {'Bucket': 'wishlist', 'Key': '1/x.png'})
        storage.delete('1/x.png')
        assert storage.read('/absolute') is None
        assert storage.read('../secret') is None
        stub.assert_no_pending_responses()


def test_create_retries_are_idempotent_even_if_response_was_lost(owner):
    from uuid import uuid4
    request_id = str(uuid4())
    body = {'request_id': request_id, 'title': 'Gift', 'links': ['https://example.com']}
    with ThreadPoolExecutor(max_workers=6) as pool:
        responses = list(pool.map(lambda _: owner.post('/api/wishlists/AuditOwner/items', json=body), range(12)))
    assert all(response.status_code == 200 for response in responses)
    assert len({response.json()['id'] for response in responses}) == 1
    assert db.execute('SELECT COUNT(*) FROM items').fetchone()[0] == 1
    assert db.execute('SELECT COUNT(*) FROM links').fetchone()[0] == 1
    retry = owner.post('/api/wishlists/AuditOwner/items', json={**body, 'title': 'Updated on retry'})
    assert retry.json()['title'] == 'Updated on retry'
    assert db.execute('SELECT COUNT(*) FROM items').fetchone()[0] == 1


def test_s3_put_validated_image(tmp_path):
    from botocore.stub import ANY
    storage = Store(tmp_path, 'https://s3.invalid', 'wishlist', 'test', 'test', '')
    data = image_bytes()
    with Stubber(storage.client) as stub:
        stub.add_response('put_object', {}, {'Bucket': 'wishlist', 'Key': ANY, 'Body': data, 'ContentType': 'image/png'})
        key = storage.put('1', data, 'image/png')
        assert key.startswith('1/') and key.endswith('.png')
        stub.assert_no_pending_responses()


def test_request_body_limits_include_chunked_requests(owner):
    assert owner.post('/api/wishlists/AuditOwner/items', content=b'x' * 262145, headers={'content-type': 'application/json'}).status_code == 413
    def chunks():
        yield b'{"title":"Gift","ignored":"'
        for _ in range(30):
            yield b'x' * 10000
        yield b'"}'
    assert owner.post('/api/wishlists/AuditOwner/items', content=chunks(), headers={'content-type': 'application/json'}).status_code == 413
    # Multipart image streams no longer have a byte cap; unauthenticated ones
    # are rejected before consuming an unbounded body.
    owner.cookies.clear()
    assert owner.post('/api/items/1/image', content=b'x' * 9000001, headers={'content-type': 'multipart/form-data; boundary=test'}).status_code == 401


def test_partial_s3_configuration_fails_instead_of_silently_using_disk(tmp_path):
    with pytest.raises(RuntimeError, match='S3 requires'):
        Store(tmp_path, 'https://s3.invalid', 'wishlist', '', '', '')
