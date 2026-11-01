"""Object storage layer. Keys are generated server-side; files never live in a web-served directory."""
from __future__ import annotations

import os
import re
import secrets
import time
from pathlib import Path

from .config import settings

_KEY_RE = re.compile(r"^[a-z0-9][a-z0-9_\-]*(/[a-z0-9][a-z0-9_\-.]*)*$")


def new_key(prefix: str, ext: str) -> str:
    ext = re.sub(r"[^a-z0-9]", "", ext.lower())[:8] or "bin"
    return f"{prefix}/{secrets.token_hex(2)}/{secrets.token_hex(16)}.{ext}"


def _check(key: str) -> str:
    if not key or len(key) > 300 or ".." in key or not _KEY_RE.match(key):
        raise ValueError("invalid storage key")
    return key


class LocalStorage:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        p = (self.root / _check(key)).resolve()
        if self.root not in p.parents:
            raise ValueError("invalid storage key")
        return p

    def put(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> None:
        """Atomic per object: write a unique temporary file, fsync it, then rename over the final name.
        A reader sees either nothing or the complete object; a failed write leaves no temporary file."""
        p = self._path(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(f"{p.name}.{secrets.token_hex(4)}.tmp")
        try:
            with open(tmp, "wb") as fh:
                fh.write(data)
                fh.flush()
                os.fsync(fh.fileno())
            tmp.replace(p)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise

    def get(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    def exists(self, key: str) -> bool:
        try:
            return self._path(key).is_file()
        except ValueError:
            return False

    def delete(self, key: str) -> None:
        try:
            self._path(key).unlink(missing_ok=True)
        except ValueError:
            pass

    def sweep_temp(self, older_than_s: float) -> int:
        """Remove temporary files left by a process that died mid-write (never a committed object)."""
        n, cutoff = 0, time.time() - older_than_s
        for t in self.root.rglob("*.tmp"):
            try:
                if t.is_file() and t.stat().st_mtime < cutoff:
                    t.unlink()
                    n += 1
            except OSError:
                pass
        return n


class S3Storage:  # pragma: no cover - requires an S3/MinIO endpoint
    def __init__(self) -> None:
        import boto3

        self.bucket = settings.storage_bucket
        self.client = boto3.client(
            "s3", endpoint_url=settings.storage_endpoint or None,
            aws_access_key_id=settings.storage_access_key or None, aws_secret_access_key=settings.storage_secret_key or None,
        )
        try:
            self.client.head_bucket(Bucket=self.bucket)
        except Exception:
            self.client.create_bucket(Bucket=self.bucket)

    def put(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> None:
        self.client.put_object(Bucket=self.bucket, Key=_check(key), Body=data, ContentType=content_type)

    def get(self, key: str) -> bytes:
        # callers treat FileNotFoundError as "object missing" (integrity failure) and anything else as "store unavailable",
        # exactly as with LocalStorage; botocore reports a missing key as a ClientError
        try:
            return self.client.get_object(Bucket=self.bucket, Key=_check(key))["Body"].read()
        except Exception as ex:
            code = str(getattr(ex, "response", {}).get("Error", {}).get("Code", ""))
            if code in ("NoSuchKey", "404", "NotFound"):
                raise FileNotFoundError(key) from None
            raise

    def exists(self, key: str) -> bool:
        try:
            self.client.head_object(Bucket=self.bucket, Key=_check(key))
            return True
        except Exception:
            return False

    def delete(self, key: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=_check(key))

    def sweep_temp(self, older_than_s: float) -> int:
        return 0   # S3 PUTs are atomic; there are no temporary objects


storage = S3Storage() if settings.storage_backend == "s3" else LocalStorage(settings.data_dir / "objects")
model_storage = storage if settings.storage_backend == "s3" else LocalStorage(settings.model_storage_path)
