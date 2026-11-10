"""Platform client interface shared by YouTube, TikTok and Instagram."""

from __future__ import annotations

import base64
import hashlib
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx


@dataclass
class TokenSet:
    access_token: str
    refresh_token: str = ""
    expires_in: int | None = None
    scopes: list[str] = field(default_factory=list)
    account_id: str = ""

    @property
    def expires_at(self) -> datetime | None:
        if self.expires_in is None:
            return None
        return datetime.now(timezone.utc) + timedelta(seconds=int(self.expires_in))


@dataclass
class UploadOptions:
    privacy: str = "private"  # platform-specific value
    publish_at: datetime | None = None
    tiktok_mode: str = "inbox_draft"  # inbox_draft | direct_post
    consent: dict = field(default_factory=dict)
    cover_timestamp_ms: int = 0
    thumbnail_path: Path | None = None
    ai_disclosure: bool = True


@dataclass
class UploadResult:
    status: str  # processing | published | drafted | failed
    external_id: str | None = None
    tracking_id: str | None = None
    url: str | None = None
    detail: str = ""


class PlatformClient:
    platform = "base"
    scopes: list[str] = []

    def __init__(self, client: httpx.Client | None = None):
        self.http = client

    # App-level configuration ------------------------------------------------
    def configured(self) -> bool:
        return not self.missing()

    def missing(self) -> list[str]:
        raise NotImplementedError

    # OAuth --------------------------------------------------------------------
    def authorize_url(self, state: str, redirect_uri: str, code_challenge: str) -> str:
        raise NotImplementedError

    def exchange_code(self, code: str, redirect_uri: str, code_verifier: str) -> TokenSet:
        raise NotImplementedError

    def refresh(self, refresh_token: str) -> TokenSet:
        raise NotImplementedError

    def account_info(self, access_token: str) -> dict:
        raise NotImplementedError

    # Publishing -----------------------------------------------------------------
    def eligibility(self, access_token: str, video: dict, options: UploadOptions) -> list[str]:
        return []

    def upload(self, access_token: str, video_path: Path, metadata: dict, options: UploadOptions) -> UploadResult:
        raise NotImplementedError

    def status(self, access_token: str, external_id: str | None, tracking_id: str | None) -> UploadResult:
        raise NotImplementedError

    def metrics(self, access_token: str, external_id: str) -> dict:
        raise NotImplementedError


def pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)[:96]
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def nullable_int(value) -> int | None:
    try:
        return None if value is None else int(value)
    except (TypeError, ValueError):
        return None


def nullable_float(value) -> float | None:
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None
