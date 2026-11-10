"""OAuth connection lifecycle: authorise (PKCE + state), callback, encrypted storage, refresh, verify."""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from ..config import get_settings
from ..enums import Platform
from ..models import PlatformConnection
from ..providers.http import NotConfigured, ProviderError
from ..security import CredentialVault
from .base import PlatformClient, TokenSet, pkce_pair
from .instagram import InstagramClient
from .tiktok import TikTokClient
from .youtube import YouTubeClient

CLIENTS: dict[str, type[PlatformClient]] = {
    Platform.YOUTUBE.value: YouTubeClient,
    Platform.TIKTOK.value: TikTokClient,
    Platform.INSTAGRAM.value: InstagramClient,
}


def client_for(platform: str, http=None) -> PlatformClient:
    if platform not in CLIENTS:
        raise ValueError(f"unknown platform {platform}")
    return CLIENTS[platform](http)


def redirect_uri(platform: str) -> str:
    return f"{get_settings().public_base_url.rstrip('/')}/api/platforms/{platform}/callback"


def connection(session: Session, platform: str) -> PlatformConnection:
    conn = session.get(PlatformConnection, platform)
    if conn is None:
        conn = PlatformConnection(platform=platform, status="disconnected")
        session.add(conn)
        session.flush()
    return conn


def start(session: Session, platform: str) -> str:
    client = client_for(platform)
    if not client.configured():
        raise NotConfigured(f"{platform} app credentials missing: {', '.join(client.missing())}")
    verifier, challenge = pkce_pair()
    state = secrets.token_urlsafe(32)
    conn = connection(session, platform)
    conn.oauth_state = state
    conn.oauth_verifier_enc = CredentialVault().encrypt(verifier)
    conn.status = "authorizing"
    return client.authorize_url(state, redirect_uri(platform), challenge)


def finish(session: Session, platform: str, code: str, state: str, http=None) -> PlatformConnection:
    conn = connection(session, platform)
    if not conn.oauth_state or not secrets.compare_digest(conn.oauth_state, state):
        raise PermissionError("OAuth state mismatch (possible CSRF); start the connection again")
    vault = CredentialVault()
    verifier = vault.decrypt(conn.oauth_verifier_enc)
    client = client_for(platform, http)
    tokens = client.exchange_code(code, redirect_uri(platform), verifier)
    _store(conn, tokens, vault)
    conn.oauth_state, conn.oauth_verifier_enc = "", ""
    verify(session, platform, http=http)
    return conn


def _store(conn: PlatformConnection, tokens: TokenSet, vault: CredentialVault) -> None:
    conn.access_token_enc = vault.encrypt(tokens.access_token)
    if tokens.refresh_token:
        conn.refresh_token_enc = vault.encrypt(tokens.refresh_token)
    conn.expires_at = tokens.expires_at
    if tokens.scopes:
        conn.scopes = [s for s in tokens.scopes if s]
    if tokens.account_id:
        conn.account_id = tokens.account_id
    conn.status = "connected"
    conn.last_error = None


def access_token(session: Session, platform: str, http=None) -> str:
    conn = session.get(PlatformConnection, platform)
    if conn is None or conn.status not in ("connected", "verified") or not conn.access_token_enc:
        raise NotConfigured(f"{platform} account is not connected")
    vault = CredentialVault()
    expires = conn.expires_at
    if expires is not None and expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    if expires and expires - datetime.now(timezone.utc) < timedelta(minutes=5):
        refresh = vault.decrypt(conn.refresh_token_enc)
        if not refresh:
            conn.status, conn.last_error = "expired", "access token expired and no refresh token"
            raise NotConfigured(f"{platform} token expired; reconnect the account")
        try:
            tokens = client_for(platform, http).refresh(refresh)
        except ProviderError as exc:
            conn.status, conn.last_error = "expired", f"refresh failed: {exc}"[:500]
            raise NotConfigured(f"{platform} token refresh failed; reconnect the account") from exc
        _store(conn, tokens, vault)
        conn.status = "verified" if conn.verified_at else "connected"
    return vault.decrypt(conn.access_token_enc)


def verify(session: Session, platform: str, http=None) -> PlatformConnection:
    """Call the platform with the stored token to prove it works and record capabilities."""
    conn = connection(session, platform)
    token = access_token(session, platform, http)
    try:
        info = client_for(platform, http).account_info(token)
    except ProviderError as exc:
        conn.status, conn.last_error = "error", str(exc)[:500]
        raise
    conn.account_id = info.get("account_id") or conn.account_id
    conn.account_name = info.get("account_name", "")
    conn.capabilities = info.get("capabilities", {})
    conn.status, conn.verified_at, conn.last_error = "verified", datetime.now(timezone.utc), None
    return conn


def disconnect(session: Session, platform: str) -> None:
    conn = connection(session, platform)
    conn.access_token_enc = conn.refresh_token_enc = ""
    conn.status, conn.verified_at, conn.expires_at = "disconnected", None, None
    conn.capabilities, conn.scopes = {}, []
