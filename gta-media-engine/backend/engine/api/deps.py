"""Auth, rate limiting and serialisation helpers for the API."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_session
from ..security import RateLimiter, audit, resolve_role

_limiter: RateLimiter | None = None


def limiter() -> RateLimiter:
    global _limiter
    if _limiter is None:
        _limiter = RateLimiter(get_settings().rate_limit_per_minute)
    return _limiter


def role(authorization: str | None = Header(default=None)) -> str:
    token = None
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()
    r = resolve_role(token)
    if r is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing or invalid API token",
                            headers={"WWW-Authenticate": "Bearer"})
    return r


def viewer(r: str = Depends(role)) -> str:
    return r


def admin(r: str = Depends(role)) -> str:
    if r != "admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "admin role required")
    return r


def db(session: Session = Depends(get_session)) -> Session:
    return session


def log_action(session: Session, request: Request, action: str, target: str = "", **details: Any) -> None:
    actor = f"admin@{request.client.host if request.client else 'unknown'}"
    audit(session, actor, action, target, **details)


def iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def row(obj, *fields: str, **extra) -> dict:
    out = {}
    for f in fields:
        v = getattr(obj, f)
        out[f] = iso(v) if isinstance(v, datetime) else v
    out.update(extra)
    return out
