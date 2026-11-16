import io
import logging
import os
from contextlib import closing
import shutil
import struct
import uuid
from pathlib import Path
from urllib.parse import quote

import boto3
from botocore.client import Config
from botocore.exceptions import ClientError
from PIL import Image, UnidentifiedImageError
from app.telemetry import measured, operation

Image.MAX_IMAGE_PIXELS = None
logger = logging.getLogger(__name__)


class Store:
    def __init__(self, data_dir: Path, endpoint: str, bucket: str, access: str, secret: str, public_base: str):
        self.data_dir = data_dir
        self.bucket = bucket
        self.public_base = public_base.rstrip("/")
        self.client = None
        from app.images import Optimizer, MediaCache
        optimizer_url = os.environ.get('IMAGE_OPTIMIZER_URL', '')
        self.optimizer = Optimizer(optimizer_url) if optimizer_url else None
        self.cache = MediaCache(data_dir / 'media-cache')
        if any((endpoint, access, secret)) and not all((endpoint, access, secret, bucket)):
            raise RuntimeError("S3 requires endpoint, bucket, access key and secret key together")
        if endpoint and access and secret and bucket:
            self.client = boto3.client(
                "s3",
                endpoint_url=endpoint,
                aws_access_key_id=access,
                aws_secret_access_key=secret,
                config=Config(signature_version="s3v4", s3={"addressing_style": "path"}, connect_timeout=5, read_timeout=15, max_pool_connections=64, retries={"max_attempts": 2, "mode": "standard"}),
            )
            # Bucket provisioning belongs to infrastructure, not app startup.

    def put(self, username: str, data: bytes, content_type: str) -> str:
        return self.put_file(username, io.BytesIO(data), content_type)

    @measured("storage.put")
    def put_file(self, username: str, stream, content_type: str) -> str:
        """Validate a spooled file and transfer without buffering the full photo in RAM."""
        suffix, content_type = _image_type(content_type)
        with operation("image.validate"):
            try:
                stream.seek(0)
                if content_type == "image/webp":
                    _verify_webp(stream)
                else:
                    with Image.open(stream) as image:
                        if Image.MIME.get(image.format) != content_type:
                            raise ValueError("Choose a valid JPEG, PNG, WebP or GIF image")
                        # Verify the container without decoding an unbounded pixel array.
                        image.verify()
            except (UnidentifiedImageError, OSError, SyntaxError) as exc:
                raise ValueError("Choose a valid JPEG, PNG, WebP or GIF image") from exc
        if content_type not in {"image/jpeg", "image/png", "image/webp", "image/gif"}:
            raise ValueError("Choose a JPEG, PNG, WebP or GIF image")
        if self.optimizer:
            with self.optimizer.convert(stream, 'master') as master, self.optimizer.convert(master, 'preview') as preview:
                key = self._persist(username, master, '.webp', 'image/webp')
                try:
                    self._persist(username, preview, '.webp', 'image/webp', key=self.preview_key(key))
                except Exception:
                    self.delete(key)
                    raise
                for variant, source in [('master', master), ('preview', preview)]:
                    try: self.cache.save(key, variant, source)
                    except OSError: logger.warning('Media cache write failed')
                return key
        return self._persist(username, stream, suffix, content_type)

    def _persist(self, username, stream, suffix, content_type, key=None):
        stream.seek(0, 2)
        size = stream.tell()
        stream.seek(0)
        key = key or f"{username}/{uuid.uuid4().hex}{suffix}"
        if not self._safe_key(key):
            raise ValueError("Invalid image owner")
        if self.client:
            with operation("s3.put") as span:
                span.set_attribute("storage.upload.bytes", size)
                if size < 8 * 1024 * 1024:
                    self.client.put_object(Bucket=self.bucket, Key=key, Body=stream, ContentType=content_type)
                else:
                    from boto3.s3.transfer import TransferConfig
                    self.client.upload_fileobj(stream, self.bucket, key, ExtraArgs={"ContentType": content_type},
                                               Config=TransferConfig(use_threads=False, multipart_chunksize=5 * 1024 * 1024))
        else:
            root = (self.data_dir / "media").resolve()
            path = (root / key).resolve()
            if not path.is_relative_to(root):
                raise ValueError("Invalid image path")
            path.parent.mkdir(parents=True, exist_ok=True)
            try:
                with path.open("wb") as target:
                    shutil.copyfileobj(stream, target, length=1024 * 1024)
            except Exception:
                path.unlink(missing_ok=True)
                raise
        return key

    @staticmethod
    def preview_key(key):
        return '_previews/v2/' + key + '.webp'

    def preview_url(self, key):
        # Always same-origin: no public bucket bypass of private preview logic.
        return '/media/' + quote(key, safe='/') + '?preview=1&v=2' if key and self.optimizer else self.url(key)

    @measured('media.prepare')
    def media_file(self, key, preview=False):
        if not self._safe_key(key): return None
        variant = 'preview' if preview and self.optimizer else 'master'
        with self.cache.lock(key, variant), operation('media.cache') as span:
            path = self.cache.path(key, variant)
            span.set_attribute('media.cache.hit', path.is_file())
            if path.is_file(): return path
            source_key = self.preview_key(key) if variant == 'preview' else key
            body = self.open(source_key)
            if body is not None:
                with closing(body), operation('s3.download') as download:
                    path = self.cache.save(key, variant, body)
                    download.set_attribute('storage.download.bytes', path.stat().st_size)
                    return path
            if variant != 'preview': return None
            # Legacy originals are never deleted/re-written. Derive/cache a
            # preview on demand and reuse the downloaded original for zoom.
            original = self.cache.path(key, 'master')
            if not original.is_file():
                body = self.open(key)
                if body is None: return None
                with closing(body), operation('s3.download') as download:
                    original = self.cache.save(key, 'master', body)
                    download.set_attribute('storage.download.bytes', original.stat().st_size)
            with original.open('rb') as source, self.optimizer.convert(source, 'preview') as result:
                return self.cache.save(key, 'preview', result)

    def url(self, key: str | None) -> str | None:
        if not key:
            return None
        if self.public_base:
            return f"{self.public_base}/{quote(key, safe='/')}"
        return f"/media/{quote(key, safe='/')}"

    @measured("storage.open")
    def open(self, key: str):
        if not self._safe_key(key):
            return None
        if self.client:
            try:
                return self.client.get_object(Bucket=self.bucket, Key=key)["Body"]
            except ClientError as exc:
                if exc.response["Error"]["Code"] in {"NoSuchKey", "404", "NotFound"}:
                    return None
                raise
        root = (self.data_dir / "media").resolve()
        path = (root / key).resolve()
        if not path.is_relative_to(root):
            return None
        return path.open('rb') if path.is_file() else None

    def read(self, key: str) -> bytes | None:
        body = self.open(key)
        if body is None:
            return None
        try:
            return body.read()
        finally:
            body.close()

    @staticmethod
    def _safe_key(key: str) -> bool:
        return (bool(key) and len(key.encode("utf-8")) <= 1024
                and not key.startswith("/") and "\\" not in key
                and not any(ord(char) < 32 or ord(char) == 127 for char in key)
                and all(part not in {"", ".", ".."} for part in key.split("/")))

    @measured("storage.delete")
    def delete(self, key: str) -> None:
        if not self._safe_key(key):
            return
        self.cache.delete(key)
        if self.optimizer and not key.startswith('_previews/'):
            self.delete(self.preview_key(key))
            self.delete('_previews/' + key + '.webp')  # Previous preview version; safe if absent.
        try:
            if self.client:
                self.client.delete_object(Bucket=self.bucket, Key=key)
            else:
                root = (self.data_dir / "media").resolve()
                path = (root / key).resolve()
                if path.is_relative_to(root):
                    path.unlink(missing_ok=True)
        except Exception as exc:
            # A cleanup failure must not turn a successful save into a retry/duplicate.
            logger.warning("Photo cleanup failed error_type=%s", type(exc).__name__)

    def content_type(self, key: str) -> str:
        suffix = Path(key).suffix.lower()
        return next((value for ext, value in _TYPES.values() if ext == suffix), "application/octet-stream")


_TYPES = {
    "image/jpeg": (".jpg", "image/jpeg"),
    "image/jpg": (".jpg", "image/jpeg"),
    "image/png": (".png", "image/png"),
    "image/webp": (".webp", "image/webp"),
    "image/gif": (".gif", "image/gif"),
}


def _image_type(content_type: str) -> tuple[str, str]:
    return _TYPES.get((content_type or "").split(";", 1)[0].strip().lower(), (".bin", "application/octet-stream"))


def _verify_webp(stream):
    """Validate RIFF/chunk boundaries without Pillow's full-file WebP buffer."""
    stream.seek(0, 2)
    size = stream.tell()
    stream.seek(0)
    header = stream.read(12)
    if len(header) != 12 or header[:4] != b'RIFF' or header[8:] != b'WEBP':
        raise ValueError('Choose a valid WebP image')
    end = struct.unpack('<I', header[4:8])[0] + 8
    if end > size or end < 20:
        raise ValueError('Truncated WebP image')
    image_found = False
    while stream.tell() < end:
        chunk = stream.read(8)
        if len(chunk) != 8:
            raise ValueError('Truncated WebP chunk')
        length = struct.unpack('<I', chunk[4:])[0]
        next_chunk = stream.tell() + length + (length % 2)
        if next_chunk > end:
            raise ValueError('Truncated WebP chunk')
        if chunk[:4] in {b'VP8 ', b'VP8L', b'VP8X'}:
            sample = stream.read(min(10, length))
            valid = (chunk[:4] == b'VP8 ' and len(sample) >= 10 and sample[3:6] == b'\x9d\x01\x2a') or (chunk[:4] == b'VP8L' and len(sample) >= 5 and sample[0] == 47) or (chunk[:4] == b'VP8X' and length == 10)
            if not valid:
                raise ValueError('Invalid WebP image header')
            image_found = True
        stream.seek(next_chunk)
    if not image_found or stream.tell() != end:
        raise ValueError('Invalid WebP image')
