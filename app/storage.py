import io
import logging
import uuid
import warnings
from pathlib import Path
from urllib.parse import quote

import boto3
from botocore.client import Config
from botocore.exceptions import ClientError
from PIL import Image, UnidentifiedImageError

Image.MAX_IMAGE_PIXELS = 25_000_000
logger = logging.getLogger(__name__)


class Store:
    def __init__(self, data_dir: Path, endpoint: str, bucket: str, access: str, secret: str, public_base: str):
        self.data_dir = data_dir
        self.bucket = bucket
        self.public_base = public_base.rstrip("/")
        self.client = None
        if any((endpoint, access, secret)) and not all((endpoint, access, secret, bucket)):
            raise RuntimeError("S3 requires endpoint, bucket, access key and secret key together")
        if endpoint and access and secret and bucket:
            self.client = boto3.client(
                "s3",
                endpoint_url=endpoint,
                aws_access_key_id=access,
                aws_secret_access_key=secret,
                config=Config(signature_version="s3v4", s3={"addressing_style": "path"}, connect_timeout=5, read_timeout=15, retries={"max_attempts": 2, "mode": "standard"}),
            )
            # Bucket provisioning belongs to infrastructure, not app startup.

    def put(self, username: str, data: bytes, content_type: str) -> str:
        suffix, content_type = _image_type(content_type)
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(io.BytesIO(data)) as image:
                    actual = Image.MIME.get(image.format)
                    if actual != content_type or image.width * image.height > Image.MAX_IMAGE_PIXELS:
                        raise ValueError("Unsupported image or image exceeds 25 megapixels")
                    image.verify()
                with Image.open(io.BytesIO(data)) as image:
                    image.load()
        except (UnidentifiedImageError, OSError, SyntaxError, Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
            raise ValueError("Choose a valid JPEG, PNG, WebP or GIF image") from exc
        if content_type not in {"image/jpeg", "image/png", "image/webp", "image/gif"}:
            raise ValueError("Choose a JPEG, PNG, WebP or GIF image")
        key = f"{username}/{uuid.uuid4().hex}{suffix}"
        if not self._safe_key(key):
            raise ValueError("Invalid image owner")
        if self.client:
            self.client.put_object(Bucket=self.bucket, Key=key, Body=data, ContentType=content_type)
        else:
            root = (self.data_dir / "media").resolve()
            path = (root / key).resolve()
            if not path.is_relative_to(root):
                raise ValueError("Invalid image path")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        return key

    def url(self, key: str | None) -> str | None:
        if not key:
            return None
        if self.public_base:
            return f"{self.public_base}/{quote(key, safe='/')}"
        return f"/media/{quote(key, safe='/')}"

    def read(self, key: str) -> bytes | None:
        if not self._safe_key(key):
            return None
        if self.client:
            try:
                body = self.client.get_object(Bucket=self.bucket, Key=key)["Body"]
                try:
                    return body.read()
                finally:
                    body.close()
            except ClientError as exc:
                if exc.response["Error"]["Code"] in {"NoSuchKey", "404", "NotFound"}:
                    return None
                raise
        root = (self.data_dir / "media").resolve()
        path = (root / key).resolve()
        if not path.is_relative_to(root):
            return None
        return path.read_bytes() if path.is_file() else None

    @staticmethod
    def _safe_key(key: str) -> bool:
        return (bool(key) and len(key.encode("utf-8")) <= 1024
                and not key.startswith("/") and "\\" not in key
                and not any(ord(char) < 32 or ord(char) == 127 for char in key)
                and all(part not in {"", ".", ".."} for part in key.split("/")))

    def delete(self, key: str) -> None:
        if not self._safe_key(key):
            return
        try:
            if self.client:
                self.client.delete_object(Bucket=self.bucket, Key=key)
            else:
                root = (self.data_dir / "media").resolve()
                path = (root / key).resolve()
                if path.is_relative_to(root):
                    path.unlink(missing_ok=True)
        except Exception:
            # A cleanup failure must not turn a successful save into a retry/duplicate.
            logger.exception("Could not delete obsolete image %s", key)

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
