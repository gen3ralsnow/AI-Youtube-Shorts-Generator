"""Object storage abstraction: local filesystem or S3-compatible (AWS S3, MinIO, R2).

The pipeline works on local paths (FFmpeg needs files); ``persist`` copies final
artefacts to the configured backend and ``cleanup_temp`` enforces retention.
"""

from __future__ import annotations

import shutil
import time
from pathlib import Path

from .config import get_settings
from .logs import get_logger

log = get_logger("storage")


class Storage:
    def persist(self, local_path: Path, key: str) -> str:
        raise NotImplementedError

    def healthy(self) -> tuple[bool, str]:
        raise NotImplementedError


class LocalStorage(Storage):
    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def persist(self, local_path: Path, key: str) -> str:
        dest = (self.root / "objects" / key).resolve()
        if self.root.resolve() not in dest.parents:
            raise ValueError("Storage key escapes the storage root")
        dest.parent.mkdir(parents=True, exist_ok=True)
        if local_path.resolve() != dest:
            shutil.copy2(local_path, dest)
        return f"file://{dest}"

    def healthy(self) -> tuple[bool, str]:
        probe = self.root / ".healthcheck"
        try:
            probe.write_text(str(time.time()))
            probe.unlink()
            return True, f"local storage writable at {self.root}"
        except OSError as exc:
            return False, f"local storage not writable: {exc}"


class S3Storage(Storage):
    def __init__(self):
        import boto3  # optional dependency in dev

        s = get_settings()
        self.bucket = s.s3_bucket
        self.client = boto3.client(
            "s3",
            endpoint_url=s.s3_endpoint_url or None,
            aws_access_key_id=s.s3_access_key or None,
            aws_secret_access_key=s.s3_secret_key or None,
            region_name=s.s3_region,
        )

    def persist(self, local_path: Path, key: str) -> str:
        self.client.upload_file(str(local_path), self.bucket, key)
        return f"s3://{self.bucket}/{key}"

    def presigned_url(self, key: str, expires: int = 3600) -> str:
        return self.client.generate_presigned_url(
            "get_object", Params={"Bucket": self.bucket, "Key": key}, ExpiresIn=expires
        )

    def healthy(self) -> tuple[bool, str]:
        try:
            self.client.head_bucket(Bucket=self.bucket)
            return True, f"s3 bucket {self.bucket} reachable"
        except Exception as exc:  # noqa: BLE001
            return False, f"s3 bucket {self.bucket} unreachable: {type(exc).__name__}"


def get_storage() -> Storage:
    s = get_settings()
    if s.storage_backend == "s3":
        return S3Storage()
    return LocalStorage(s.data_dir)


def safe_join(root: Path, *parts: str) -> Path:
    """Join untrusted path parts under root, refusing traversal."""
    candidate = root.joinpath(*parts).resolve()
    root = root.resolve()
    if candidate != root and root not in candidate.parents:
        raise ValueError("Path escapes the allowed directory")
    return candidate


def cleanup_temp(max_age_hours: int | None = None) -> int:
    s = get_settings()
    hours = max_age_hours if max_age_hours is not None else s.temp_retention_hours
    cutoff = time.time() - hours * 3600
    tmp = s.data_dir / "tmp"
    removed = 0
    if not tmp.exists():
        return 0
    for path in sorted(tmp.rglob("*"), reverse=True):
        try:
            if path.is_file() and path.stat().st_mtime < cutoff:
                path.unlink()
                removed += 1
            elif path.is_dir() and not any(path.iterdir()):
                path.rmdir()
        except OSError as exc:
            log.warning("temp cleanup skipped %s: %s", path, exc)
    return removed
