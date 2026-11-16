import io
from contextlib import contextmanager
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import httpx
import pytest
from PIL import Image
from app.images import MediaCache, Optimizer
from app.storage import Store


def webp(size=(32, 48)):
    stream = io.BytesIO(); Image.new('RGBA', size, (10, 30, 70, 128)).save(stream, 'WEBP', lossless=True)
    return stream.getvalue()


def test_optimizer_binary_contract_streaming_and_invalid_response():
    optimizer = Optimizer('http://private-optimizer:8081')
    seen = []
    def handle(request):
        seen.append((request.url.path, request.read(), dict(request.headers)))
        return httpx.Response(200, content=webp(), headers={'content-type':'image/webp','X-Image-Process-Ms':'12.3'})
    optimizer.client.close(); optimizer.client = httpx.Client(transport=httpx.MockTransport(handle))
    with optimizer.convert(io.BytesIO(b'original'), 'master') as result:
        assert result.read() == webp()
    assert seen[0][0:2] == ('/master', b'original')
    assert seen[0][2]['content-length'] == '8'
    optimizer.client = httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200,content=b'bad',headers={'content-type':'image/png'})))
    with pytest.raises(ValueError):
        with optimizer.convert(io.BytesIO(b'input'), 'master'): pass


def test_cache_atomic_bounded_and_nonseekable_source(tmp_path):
    cache = MediaCache(tmp_path, budget=12)
    class Body:
        def __init__(self): self.stream = io.BytesIO(b'12345678')
        def read(self, length): return self.stream.read(length)
    first = cache.save('a','master', Body())
    assert first.read_bytes() == b'12345678'
    second = cache.save('b','preview',io.BytesIO(b'abcdefgh'))
    assert second.exists() and not first.exists()
    assert not list(tmp_path.glob('.write-*'))
    class Bad:
        def read(self, length): raise OSError('unavailable')
    with pytest.raises(OSError): cache.save('bad','master',Bad())
    assert not cache.path('bad','master').exists() and not list(tmp_path.glob('.write-*'))


class FakeOptimizer:
    def __init__(self): self.calls = []
    @contextmanager
    def convert(self, source, variant):
        self.calls.append(variant)
        yield io.BytesIO(webp((12, 18) if variant == 'preview' else (32, 48)))


def test_optimized_master_preview_cleanup_and_no_original_persisted(tmp_path):
    store = Store(tmp_path,'','','','',''); store.optimizer = FakeOptimizer()
    original = io.BytesIO(); Image.new('RGB',(32,48),'red').save(original,'PNG')
    key = store.put_file('1',original,'image/png')
    assert key.endswith('.webp') and store.optimizer.calls == ['master','preview']
    assert len(list((tmp_path/'media').rglob('*.png'))) == 0
    assert Image.open(io.BytesIO(store.read(key))).format == 'WEBP'
    assert Image.open(io.BytesIO(store.read(store.preview_key(key)))).size == (12,18)
    assert store.media_file(key,True).read_bytes() == webp((12,18))
    store.delete(key)
    assert store.read(key) is None and store.read(store.preview_key(key)) is None
    assert not store.cache.path(key,'preview').exists()


def test_legacy_preview_cache_coalesces_without_rewriting_original(tmp_path):
    store = Store(tmp_path,'','','','','')
    original = io.BytesIO(); Image.new('RGB',(32,48),'red').save(original,'PNG')
    key = store.put_file('1',original,'image/png'); original_bytes = store.read(key)
    store.optimizer = FakeOptimizer()
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: store.media_file(key,True).read_bytes(),range(8)))
    assert all(result == results[0] for result in results)
    assert store.optimizer.calls == ['preview'] and store.read(key) == original_bytes
    assert store.read(store.preview_key(key)) is None  # Legacy derivatives cache-only.


def test_optimizer_failure_never_silently_stores_unoptimized_input(tmp_path):
    store = Store(tmp_path,'','','','','')
    class Broken:
        @contextmanager
        def convert(self, source, variant):
            raise httpx.ConnectError('unavailable'); yield
    store.optimizer = Broken()
    with pytest.raises(httpx.ConnectError): store.put_file('1',io.BytesIO(webp()),'image/webp')
    assert not list((tmp_path/'media').glob('**/*'))


def test_public_media_immutable_content_length_range_and_failure_recovery(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import app
    store = Store(tmp_path,'','','','','')
    data = webp(); key = store.put('1',data,'image/webp')
    monkeypatch.setattr('app.main.store',store)
    with TestClient(app) as client:
        response = client.get('/media/' + key)
        assert response.status_code == 200 and response.content == data
        assert response.headers['content-length'] == str(len(data))
        assert 'immutable' in response.headers['cache-control'] and 'etag' in response.headers
        assert client.get('/media/' + key,headers={'if-none-match':response.headers['etag']}).status_code == 304
        monkeypatch.setattr(store,'open',lambda _: pytest.fail('Warm response must not reach S3'))
        assert client.get('/media/' + key,headers={'range':'bytes=0-7'}).content == data[:8]
        assert client.get('/media/_previews/' + key).status_code == 404
