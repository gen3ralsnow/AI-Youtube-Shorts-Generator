"""Credential encryption, API authentication, rate limiting and audit logging."""

from __future__ import annotations

import base64
import hashlib
import hmac
import threading
import time
from collections import defaultdict, deque
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy.orm import Session

from .config import get_settings
from .models import AuditLog


class CredentialVault:
    """Encrypts OAuth tokens at rest with Fernet (AES-128-CBC + HMAC-SHA256)."""

    def __init__(self, key: str | None = None):
        settings = get_settings()
        key = key if key is not None else settings.encryption_key
        if not key:
            if settings.is_production:
                raise RuntimeError("ENGINE_ENCRYPTION_KEY must be set in production")
            # Development fallback derived from the admin token; logged as a warning
            # by the health check so it is never silently used in production.
            seed = (settings.admin_api_token or "dev-insecure-key").encode()
            key = base64.urlsafe_b64encode(hashlib.sha256(seed).digest()).decode()
            self.insecure = True
        else:
            self.insecure = False
        self._fernet = Fernet(key.encode() if isinstance(key, str) else key)

    def encrypt(self, plaintext: str) -> str:
        if not plaintext:
            return ""
        return self._fernet.encrypt(plaintext.encode()).decode()

    def decrypt(self, token: str) -> str:
        if not token:
            return ""
        try:
            return self._fernet.decrypt(token.encode()).decode()
        except InvalidToken as exc:
            raise RuntimeError("Stored credential cannot be decrypted (key rotated?)") from exc


def generate_encryption_key() -> str:
    return Fernet.generate_key().decode()


def resolve_role(token: str | None) -> str | None:
    """Return 'admin', 'viewer' or None for a presented bearer token."""
    if not token:
        return None
    settings = get_settings()
    if settings.admin_api_token and hmac.compare_digest(token, settings.admin_api_token):
        return "admin"
    if settings.viewer_api_token and hmac.compare_digest(token, settings.viewer_api_token):
        return "viewer"
    return None


class RateLimiter:
    """Sliding-window limiter per client key. In-process; front with a proxy limit in prod."""

    def __init__(self, per_minute: int):
        self.per_minute = per_minute
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        with self._lock:
            hits = self._hits[key]
            while hits and now - hits[0] > 60:
                hits.popleft()
            if len(hits) >= self.per_minute:
                return False
            hits.append(now)
            return True


def audit(session: Session, actor: str, action: str, target: str = "", **details: Any) -> None:
    session.add(AuditLog(actor=actor, action=action, target=target, details=details))
