"""Platform connections, publishing queue and analytics."""

from __future__ import annotations

import html
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...analytics import analyst
from ...config import get_settings
from ...enums import Platform
from ...models import ContentJob, MetricSnapshot, Publication, Report
from ...providers.http import NotConfigured, ProviderError
from ...publishing import manager, oauth
from .. import background
from .. import serializers as ser
from ..deps import admin, db, log_action, viewer

router = APIRouter(prefix="/api")


# ------------------------------------------------------------------ platforms


@router.get("/platforms")
def platforms(s: Session = Depends(db), _: str = Depends(viewer)):
    return [ser.connection(oauth.connection(s, p.value), oauth.client_for(p.value).missing()) for p in Platform]


@router.post("/platforms/{platform}/connect")
def connect(platform: Platform, request: Request, s: Session = Depends(db), _: str = Depends(admin)):
    try:
        url = oauth.start(s, platform.value)
    except NotConfigured as exc:
        raise HTTPException(412, str(exc)) from exc
    log_action(s, request, "platform.connect_start", platform.value)
    return {"authorize_url": url, "redirect_uri": oauth.redirect_uri(platform.value)}


@router.get("/platforms/{platform}/callback", response_class=HTMLResponse)
def callback(platform: Platform, request: Request, code: str = "", state: str = "", error: str = "",
             s: Session = Depends(db)):
    """OAuth redirect target. Authenticated by the single-use state value, not the API token."""
    back = html.escape(get_settings().dashboard_origin.rstrip("/") + "/platforms", quote=True)

    def page(message: str, code_: int) -> HTMLResponse:
        return HTMLResponse(f"<!doctype html><meta charset=utf-8><p>{html.escape(message)}</p>"
                            f"<a href=\"{back}\">Back to the dashboard</a>", status_code=code_)

    if error or not code:
        return page(f"Connection cancelled: {error or 'no authorisation code'}", 400)
    try:
        conn = oauth.finish(s, platform.value, code, state)
    except PermissionError as exc:
        return page(str(exc), 403)
    except (ProviderError, NotConfigured) as exc:
        s.commit()
        return page(f"Connection failed: {str(exc)[:300]}", 502)
    log_action(s, request, "platform.connected", platform.value, account=conn.account_name)
    return page(f"Connected {platform.value} as {conn.account_name}.", 200)


@router.post("/platforms/{platform}/verify")
def verify(platform: Platform, request: Request, s: Session = Depends(db), _: str = Depends(admin)):
    try:
        conn = oauth.verify(s, platform.value)
    except NotConfigured as exc:
        raise HTTPException(412, str(exc)) from exc
    except ProviderError as exc:
        s.commit()
        raise HTTPException(502, f"verification failed: {exc}") from exc
    log_action(s, request, "platform.verify", platform.value)
    return ser.connection(conn, [])


@router.post("/platforms/{platform}/disconnect")
def disconnect(platform: Platform, request: Request, s: Session = Depends(db), _: str = Depends(admin)):
    oauth.disconnect(s, platform.value)
    log_action(s, request, "platform.disconnect", platform.value)
    return {"platform": platform.value, "status": "disconnected"}


# ------------------------------------------------------------------ publishing


@router.get("/publications")
def publications(status: str | None = None, limit: int = Query(200, le=1000), s: Session = Depends(db),
                 _: str = Depends(viewer)):
    q = select(Publication).order_by(Publication.id.desc()).limit(limit)
    if status:
        q = q.where(Publication.status == status)
    return [ser.publication(p) for p in s.scalars(q)]


@router.post("/publications/process")
def process(request: Request, s: Session = Depends(db), _: str = Depends(admin)):
    log_action(s, request, "publishing.process")
    return {"status": background.run_tick("publish")}


@router.post("/publications/{pub_id}/retry")
def retry(pub_id: int, request: Request, s: Session = Depends(db), _: str = Depends(admin)):
    p = s.get(Publication, pub_id) or _404()
    try:
        manager.requeue(s, p)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    log_action(s, request, "publication.retry", f"publication:{pub_id}")
    return ser.publication(p)


@router.post("/publications/{pub_id}/refresh")
def refresh(pub_id: int, s: Session = Depends(db), _: str = Depends(admin)):
    p = s.get(Publication, pub_id) or _404()
    manager.refresh_status(s, p)
    return ser.publication(p)


class TikTokConsent(BaseModel):
    privacy_level: str = Field(pattern="^(PUBLIC_TO_EVERYONE|MUTUAL_FOLLOW_FRIENDS|FOLLOWER_OF_CREATOR|SELF_ONLY)$")
    music_usage_confirmed: bool
    disable_comment: bool = False
    disable_duet: bool = False
    disable_stitch: bool = False


@router.post("/publications/{pub_id}/tiktok-consent")
def tiktok_consent(pub_id: int, body: TikTokConsent, request: Request, s: Session = Depends(db),
                   _: str = Depends(admin)):
    """Per-post consent required by TikTok for direct posting. Recorded with actor and time."""
    p = s.get(Publication, pub_id) or _404()
    if p.platform != "tiktok":
        raise HTTPException(422, "not a TikTok publication")
    if not body.music_usage_confirmed:
        raise HTTPException(422, "TikTok requires agreeing to the Music Usage Confirmation")
    actor = f"admin@{request.client.host if request.client else 'unknown'}"
    p.consent = {**body.model_dump(), "confirmed_by": actor,
                 "confirmed_at": datetime.now(timezone.utc).isoformat()}
    log_action(s, request, "tiktok.consent", f"publication:{pub_id}", privacy=body.privacy_level)
    return ser.publication(p)


class TestUpload(BaseModel):
    platform: Platform


@router.post("/jobs/{job_id}/test-upload")
def test_upload(job_id: int, body: TestUpload, request: Request, s: Session = Depends(db), _: str = Depends(admin)):
    """Non-public test publish: YouTube private / TikTok inbox draft. Instagram has no private mode."""
    job = s.get(ContentJob, job_id) or _404()
    try:
        pub = manager.test_upload(s, job, body.platform.value)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    log_action(s, request, "publication.test_upload", f"job:{job_id}", platform=body.platform.value,
               result=pub.status)
    return ser.publication(pub)


# ------------------------------------------------------------------ analytics


@router.get("/analytics/summary")
def summary(days: int | None = Query(None, ge=1, le=365), s: Session = Depends(db), _: str = Depends(viewer)):
    return analyst.analyse(s, days)


@router.get("/analytics/videos")
def videos(s: Session = Depends(db), _: str = Depends(viewer)):
    return analyst.dataset(s)


@router.get("/analytics/history/{pub_id}")
def history(pub_id: int, s: Session = Depends(db), _: str = Depends(viewer)):
    snaps = s.scalars(select(MetricSnapshot).where(MetricSnapshot.publication_id == pub_id)
                      .order_by(MetricSnapshot.captured_at))
    return [{"captured_at": m.captured_at.isoformat(), "views": m.views, "likes": m.likes,
             "comments": m.comments, "shares": m.shares, "saves": m.saves,
             "avg_watch_seconds": m.avg_watch_seconds} for m in snaps]


@router.post("/analytics/collect")
def collect(request: Request, s: Session = Depends(db), _: str = Depends(admin)):
    log_action(s, request, "analytics.collect")
    return {"status": background.run_tick("analytics")}


@router.post("/analytics/report")
def report(request: Request, s: Session = Depends(db), _: str = Depends(admin)):
    r = analyst.weekly_report(s)
    log_action(s, request, "analytics.weekly_report", f"report:{r.id}")
    return {"id": r.id, "body": r.body}


@router.get("/analytics/reports")
def reports(s: Session = Depends(db), _: str = Depends(viewer)):
    return [{"id": r.id, "kind": r.kind, "period_start": r.period_start.isoformat(),
             "period_end": r.period_end.isoformat(), "recommendations": r.body.get("recommendations", [])}
            for r in s.scalars(select(Report).order_by(Report.id.desc()).limit(20))]


def _404():
    raise HTTPException(404, "not found")

