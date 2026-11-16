"""Private lossless optimizer client and immutable, size-bounded disk media cache."""
import hashlib
import os
import shutil
import tempfile
import threading
from contextlib import contextmanager
from pathlib import Path

import httpx
from app.telemetry import operation, PROPAGATOR


class Optimizer:
    def __init__(self, url):
        self.url = url.rstrip('/')
        self.client = httpx.Client(timeout=httpx.Timeout(180, connect=5), trust_env=False,
                                  limits=httpx.Limits(max_connections=None, max_keepalive_connections=20))

    @contextmanager
    def convert(self, source, variant):
        with tempfile.SpooledTemporaryFile(max_size=1024 * 1024) as result:
            with operation('image.optimize.' + variant) as span:
                source.seek(0, 2); size = source.tell(); source.seek(0)
                headers = {'Content-Type': 'application/octet-stream', 'Content-Length': str(size)}
                PROPAGATOR.inject(headers)
                def chunks():
                    while chunk := source.read(1024 * 1024): yield chunk
                with self.client.stream('POST', self.url + '/' + variant, content=chunks(), headers=headers) as response:
                    response.raise_for_status()
                    if response.headers.get('content-type', '').split(';')[0] != 'image/webp':
                        raise ValueError('Invalid optimizer output')
                    for chunk in response.iter_bytes(1024 * 1024): result.write(chunk)
                    try:
                        engine_ms = float(response.headers.get('X-Image-Process-Ms', 'nan'))
                        if 0 <= engine_ms <= 300000: span.set_attribute('image.engine.milliseconds', engine_ms)
                    except ValueError: pass
                from app.storage import _verify_webp
                result.seek(0); _verify_webp(result)
                result.seek(0, 2); span.set_attribute('image.input.bytes', size)
                span.set_attribute('image.output.bytes', result.tell()); result.seek(0)
            # Processing span ends here, BEFORE downstream preview/S3 work.
            yield result


class MediaCache:
    def __init__(self, root, budget=1024 * 1024 * 1024):
        self.root = Path(root); self.root.mkdir(parents=True, exist_ok=True)
        self.budget = budget
        # Striped locks coalesce concurrent reads without an unbounded lock map.
        self.locks = [threading.Lock() for _ in range(64)]
        self.prune_lock = threading.Lock()

    def path(self, key, variant):
        digest = hashlib.sha256((variant + ':' + key).encode()).hexdigest()
        return self.root / digest

    def lock(self, key, variant):
        return self.locks[int(self.path(key, variant).name[:8], 16) % len(self.locks)]

    def save(self, key, variant, stream):
        path = self.path(key, variant)
        fd, temporary = tempfile.mkstemp(prefix='.write-', dir=self.root)
        try:
            with os.fdopen(fd, 'wb') as target:
                if getattr(stream, 'seekable', lambda: False)(): stream.seek(0)
                shutil.copyfileobj(stream, target, length=1024 * 1024)
            os.replace(temporary, path)
        finally:
            Path(temporary).unlink(missing_ok=True)
        self.prune(path)
        return path

    def prune(self, protected):
        if not self.prune_lock.acquire(blocking=False): return
        try:
            files = []
            for path in self.root.iterdir():
                if path.name.startswith('.'): continue
                try:
                    stat = path.stat(); files.append((stat.st_mtime, stat.st_size, path))
                except FileNotFoundError: pass
            total = sum(size for _, size, _ in files)
            for _, size, path in sorted(files):
                if total <= self.budget: break
                if path == protected: continue
                path.unlink(missing_ok=True); total -= size
        finally: self.prune_lock.release()

    def delete(self, key):
        for variant in ('master','preview'): self.path(key, variant).unlink(missing_ok=True)
