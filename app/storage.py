import uuid
from pathlib import Path

import boto3
from botocore.client import Config


class Store:
    def __init__(self, data_dir: Path, endpoint: str, bucket: str, access: str, secret: str, public_base: str):
        self.data_dir = data_dir
        self.bucket = bucket
        self.public_base = public_base.rstrip("/")
        self.client = None
        if endpoint and access and secret and bucket:
            self.client = boto3.client(
                "s3",
                endpoint_url=endpoint,
                aws_access_key_id=access,
                aws_secret_access_key=secret,
                config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
            )
            try:
                self.client.create_bucket(Bucket=bucket)
            except Exception:
                pass

    def put(self, username: str, data: bytes, content_type: str) -> str:
        suffix, content_type = _image_type(content_type)
        key = f"{username}/{uuid.uuid4().hex}{suffix}"
        if self.client:
            self.client.put_object(Bucket=self.bucket, Key=key, Body=data, ContentType=content_type)
        else:
            path = self.data_dir / "media" / key
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        return key

    def url(self, key: str | None) -> str | None:
        if not key:
            return None
        if self.public_base:
            return f"{self.public_base}/{key}"
        return f"/media/{key}"

    def read(self, key: str) -> bytes | None:
        if self.client:
            try:
                return self.client.get_object(Bucket=self.bucket, Key=key)["Body"].read()
            except Exception:
                return None
        path = self.data_dir / "media" / key
        return path.read_bytes() if path.is_file() else None

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
