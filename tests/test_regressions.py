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


def gallery_upload(http, item_id, photo_id=None):
    from uuid import uuid4
    from pathlib import Path
    return http.put(f'/api/items/{item_id}/images/{photo_id or uuid4()}', files={
        'file': ('coffee.webp', Path('static/assets/photos/coffee-1.webp').read_bytes(), 'image/webp')})


def test_gallery_order_removal_cover_and_idempotent_upload(http):
    from uuid import uuid4
    from app.main import store
    item_id = http.post('/api/wishlists/tester/items', json={'title': 'Gallery'}).json()['id']
    first_id = str(uuid4())
    first = gallery_upload(http, item_id, first_id).json()
    assert gallery_upload(http, item_id, first_id).json() == first
    second = gallery_upload(http, item_id).json()
    third = gallery_upload(http, item_id).json()
    path = f'/api/items/{item_id}/images'
    assert http.patch(path, json={'ids': [third['id'], first['id'], second['id']]}).status_code == 200
    item = http.get('/api/wishlists/tester').json()['items'][0]
    assert item['photos'] == [third, first, second]
    assert item['image'] == third['url']
    removed_key = db.execute('SELECT image_key FROM item_images WHERE id = ?', (first_id,)).fetchone()[0]
    assert http.patch(path, json={'ids': [second['id'], third['id']]}).status_code == 200
    assert store.read(removed_key) is None
    assert http.get('/api/wishlists/tester').json()['items'][0]['image'] == second['url']
    assert http.patch(path, json={'ids': []}).json() == {'photos': []}
    item = http.get('/api/wishlists/tester').json()['items'][0]
    assert item['image'] is None and item['photos'] == []


def test_gallery_ownership_and_order_validation(http):
    item_id = http.post('/api/wishlists/tester/items', json={'title': 'One'}).json()['id']
    other_id = http.post('/api/wishlists/tester/items', json={'title': 'Two'}).json()['id']
    first = gallery_upload(http, item_id).json()
    other = gallery_upload(http, other_id).json()
    path = f'/api/items/{item_id}/images'
    assert http.patch(path, json={'ids': [other['id']]}).status_code == 422
    assert http.patch(path, json={'ids': [first['id'], first['id']]}).status_code == 422
    assert gallery_upload(http, other_id, first['id']).status_code == 409
    assert http.get('/api/wishlists/tester').json()['items'][0]['photos'] == [first]
    http.cookies.clear()
    assert gallery_upload(http, item_id).status_code == 401
    assert http.patch(path, json={'ids': []}).status_code == 401


def test_gallery_limit_and_item_deletion_cleanup(http):
    from app.main import store
    item_id = http.post('/api/wishlists/tester/items', json={'title': 'Limit'}).json()['id']
    for _ in range(25):
        assert gallery_upload(http, item_id).status_code == 200
    assert gallery_upload(http, item_id).status_code == 200
    keys = [row[0] for row in db.execute('SELECT image_key FROM item_images WHERE item_id = ?', (item_id,))]
    assert len(keys) == 26
    assert http.delete(f'/api/items/{item_id}').status_code == 204
    assert not db.execute('SELECT 1 FROM item_images').fetchone()
    assert all(store.read(key) is None for key in keys)


def test_legacy_photo_migration_is_idempotent(tmp_path):
    from app.db import connect
    path = tmp_path / 'old.sqlite'
    connection = connect(path)
    connection.execute("INSERT INTO users(id, sub, username) VALUES (1, 'old', 'old')")
    connection.execute("INSERT INTO items(id, owner_id, title, image_key) VALUES (1, 1, 'Old', '1/existing.webp')")
    connection.commit()
    connection.close()
    for _ in range(2):
        connection = connect(path)
        rows = connection.execute('SELECT * FROM item_images').fetchall()
        assert len(rows) == 1 and rows[0]['image_key'] == '1/existing.webp'
        assert connection.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
        assert not connection.execute('PRAGMA foreign_key_check').fetchall()
        connection.close()


def test_gallery_parallel_retries_do_not_duplicate_photos(http):
    from concurrent.futures import ThreadPoolExecutor
    from uuid import uuid4
    item_id = http.post('/api/wishlists/tester/items', json={'title': 'Retry'}).json()['id']
    photo_id = str(uuid4())
    with ThreadPoolExecutor(max_workers=6) as pool:
        responses = list(pool.map(lambda _: gallery_upload(http, item_id, photo_id), range(6)))
    assert all(response.status_code == 200 for response in responses)
    assert len({response.json()['url'] for response in responses}) == 1
    assert db.execute('SELECT COUNT(*) FROM item_images WHERE item_id = ?', (item_id,)).fetchone()[0] == 1


def test_large_photo_stream_does_not_require_full_buffer(tmp_path):
    import io
    from PIL import Image
    output = io.BytesIO()
    Image.new('RGB', (10, 10), 'blue').save(output, 'PNG')
    payload = output.getvalue() + b'\0' * 10_000_000
    class BoundedStream(io.BytesIO):
        def read(self, size=-1):
            assert 0 <= size <= 1024 * 1024, 'Must not buffer the entire large file'
            return super().read(size)
    storage = Store(tmp_path, '', '', '', '', '')
    key = storage.put_file('1', BoundedStream(payload), 'image/png')
    assert (tmp_path / 'media' / key).stat().st_size == len(payload)


def test_large_pixel_dimensions_are_not_rejected(tmp_path):
    import io
    from PIL import Image
    output = io.BytesIO()
    Image.new('1', (6000, 5000)).save(output, 'PNG')
    storage = Store(tmp_path, '', '', '', '', '')
    assert storage.put('1', output.getvalue(), 'image/png')


def test_large_gallery_order_has_no_json_size_or_count_limit(http):
    from uuid import uuid4
    item = http.post('/api/wishlists/tester/items', json={'title': 'Large gallery'}).json()['id']
    ids = [str(uuid4()) for _ in range(7000)]
    db.executemany('INSERT INTO item_images VALUES (?, ?, ?, ?)', [(photo_id, item, '1/' + photo_id + '.png', index) for index, photo_id in enumerate(ids)])
    db.commit()
    response = http.patch(f'/api/items/{item}/images', json={'ids': ids[::-1]})
    assert response.status_code == 200
    assert [photo['id'] for photo in response.json()['photos']] == ids[::-1]


def test_large_s3_upload_uses_managed_stream(tmp_path):
    import io
    from unittest.mock import Mock
    from PIL import Image
    output = io.BytesIO()
    Image.new('RGB', (10, 10)).save(output, 'PNG')
    payload = output.getvalue() + b'\0' * 9_000_000
    storage = Store(tmp_path, 'https://s3.invalid', 'wishlist', 'test', 'test', '')
    transfer = Mock()
    storage.client.upload_fileobj = transfer
    storage.put_file('1', io.BytesIO(payload), 'image/png')
    transfer.assert_called_once()
    args, kwargs = transfer.call_args
    assert hasattr(args[0], 'read') and args[1] == 'wishlist'
    assert kwargs['ExtraArgs'] == {'ContentType': 'image/png'}
    assert kwargs['Config'].use_threads is False
