"""Collect metrics for published videos from each connected platform."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..enums import PublicationStatus
from ..logs import get_logger
from ..models import MetricSnapshot, Publication
from ..providers.http import NotConfigured, ProviderError
from ..publishing import oauth

log = get_logger("analytics.collector")

FIELDS = ("views", "likes", "comments", "shares", "saves", "avg_watch_seconds", "avg_view_percentage",
          "followers_gained")


def collect(session: Session, http=None) -> dict:
    out = {"snapshots": 0, "errors": {}, "skipped_unconnected": []}
    pubs = list(session.scalars(select(Publication).where(
        Publication.status == PublicationStatus.PUBLISHED.value, Publication.external_id.is_not(None))))
    tokens: dict[str, str | None] = {}
    for pub in pubs:
        if pub.platform not in tokens:
            try:
                tokens[pub.platform] = oauth.access_token(session, pub.platform, http)
            except NotConfigured:
                tokens[pub.platform] = None
                out["skipped_unconnected"].append(pub.platform)
        token = tokens[pub.platform]
        if token is None:
            continue
        try:
            data = oauth.client_for(pub.platform, http).metrics(token, pub.external_id)
        except ProviderError as exc:
            out["errors"][str(pub.id)] = str(exc)[:200]
            continue
        session.add(MetricSnapshot(publication_id=pub.id, raw=data.get("raw", {}),
                                   **{f: data.get(f) for f in FIELDS}))
        out["snapshots"] += 1
    session.flush()
    return out
