"""Central distribution manager.

1. Receive the final render  2. validate the export  3. attach platform metadata
4. check eligibility  5. upload  6. track status  7. record ids/URLs
8. retry temporary failures with backoff and a limit  9. prevent duplicates.

Publication states only reach ``published`` when the platform confirms it.
TEST mode never sends anything to a platform (``dry_run``) unless an admin
explicitly requests a non-public test upload. Paused or failed items stay in
the queue; nothing is discarded.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import settings_store
from ..enums import JobStatus, OperatingMode, PublicationStatus
from ..logs import get_logger
from ..models import ContentJob, MetadataPackage, Publication, Render, Thumbnail
from ..providers.http import NotConfigured, ProviderError
from ..video import ffmpeg
from ..video.profiles import PLATFORM_PROFILE
from . import oauth
from .base import UploadOptions, file_sha256

log = get_logger("publishing")

ACTIVE = (PublicationStatus.UPLOADING.value, PublicationStatus.PROCESSING.value,
          PublicationStatus.PUBLISHED.value, PublicationStatus.DRAFTED.value)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def next_slot(session: Session, platform: str, after: datetime | None = None) -> datetime:
    """Next time inside a publishing window that respects per-day limits."""
    sched = settings_store.get(session, "schedule")
    windows = [settings_store.parse_window(w) for w in sched["publishing_windows"]] or [(0, 1440)]
    plat_cfg = sched["platforms"].get(platform, {})
    per_day = min(int(sched["videos_per_day"]), int(plat_cfg.get("max_per_day", 99)))
    t = (after or _now()).replace(second=0, microsecond=0)
    for day_offset in range(0, 30):
        day = (t + timedelta(days=day_offset)).replace(hour=0, minute=0)
        day_end = day + timedelta(days=1)
        taken = session.scalar(select(func.count()).select_from(Publication).where(
            Publication.platform == platform, Publication.scheduled_for >= day,
            Publication.scheduled_for < day_end,
            Publication.status.not_in([PublicationStatus.FAILED.value, PublicationStatus.DRY_RUN.value]),
        )) or 0
        if per_day <= 0 or taken >= per_day:
            continue
        for start, end in windows:
            # spread slots inside the window by how many are already taken today
            candidate = day + timedelta(minutes=start + (taken * 37) % max(1, end - start))
            if candidate >= t and candidate < day + timedelta(minutes=end):
                return candidate
            if day_offset == 0 and day + timedelta(minutes=start) <= t < day + timedelta(minutes=end):
                return t
    return t + timedelta(days=1)


def enqueue(session: Session, job: ContentJob) -> list[Publication]:
    """Create one queued Publication per platform for a QC-passed job (idempotent)."""
    created = []
    renders = {r.profile: r for r in session.scalars(select(Render).where(Render.job_id == job.id))}
    for platform, cfg in settings_store.get(session, "schedule")["platforms"].items():
        if not cfg.get("enabled"):
            continue
        render = renders.get(PLATFORM_PROFILE[platform])
        if render is None:
            continue
        key = f"job{job.id}:{platform}"
        existing = session.scalar(select(Publication).where(Publication.idempotency_key == key))
        if existing:
            created.append(existing)
            continue
        pub = Publication(job_id=job.id, platform=platform, idempotency_key=key,
                          content_hash=file_sha256(Path(render.path)), status=PublicationStatus.QUEUED.value,
                          scheduled_for=next_slot(session, platform))
        session.add(pub)
        session.flush()
        created.append(pub)
    return created


def _metadata(session: Session, pub: Publication) -> MetadataPackage | None:
    return session.scalar(select(MetadataPackage).where(MetadataPackage.job_id == pub.job_id,
                                                        MetadataPackage.platform == pub.platform))


def check_eligibility(session: Session, pub: Publication, *, test_upload: bool = False) -> list[str]:
    """Local checks that need no network. Platform-side checks happen in the client."""
    problems = []
    if settings_store.get(session, "publishing_paused") and not test_upload:
        problems.append("publishing is paused (global emergency pause)")
    if settings_store.mode(session) != OperatingMode.AUTONOMOUS and not test_upload:
        problems.append("TEST mode: publishing disabled")
    sched = settings_store.get(session, "schedule")["platforms"].get(pub.platform, {})
    if not sched.get("enabled"):
        problems.append(f"{pub.platform} publishing disabled in settings")
    render = session.scalar(select(Render).where(Render.job_id == pub.job_id,
                                                 Render.profile == PLATFORM_PROFILE[pub.platform]))
    if render is None:
        problems.append("render missing")
    elif not (render.qc or {}).get("passed"):
        problems.append("render has not passed full QC (publishable=false)")
    elif not Path(render.path).is_file():
        problems.append("render file missing")
    elif file_sha256(Path(render.path)) != pub.content_hash:
        problems.append("render changed since queueing; re-run QC and re-queue")
    if _metadata(session, pub) is None:
        problems.append("metadata missing")
    dup = session.scalar(select(Publication).where(
        Publication.platform == pub.platform, Publication.content_hash == pub.content_hash,
        Publication.id != pub.id, Publication.status.in_(ACTIVE),
        Publication.idempotency_key.not_like("%:test")))
    if dup:
        problems.append(f"identical video already sent to {pub.platform} (publication {dup.id})")
    job = session.get(ContentJob, pub.job_id)
    if job and job.status in (JobStatus.BLOCKED.value, JobStatus.CANCELLED.value):
        problems.append(f"job is {job.status}")
    if not test_upload:
        plat_cfg = settings_store.get(session, "schedule")["platforms"].get(pub.platform, {})
        day = _now().replace(hour=0, minute=0, second=0, microsecond=0)
        sent_today = session.scalar(select(func.count()).select_from(Publication).where(
            Publication.platform == pub.platform, Publication.status.in_(ACTIVE),
            Publication.idempotency_key.not_like("%:test"), Publication.updated_at >= day)) or 0
        if sent_today >= int(plat_cfg.get("max_per_day", 0)):
            problems.append(f"{pub.platform} daily publishing limit reached ({sent_today})")
    return problems


def _options(session: Session, pub: Publication, test_upload: bool) -> UploadOptions:
    sched = settings_store.get(session, "schedule")["platforms"].get(pub.platform, {})
    thumb = session.scalar(select(Thumbnail).where(Thumbnail.job_id == pub.job_id, Thumbnail.platform == "youtube",
                                                   Thumbnail.selected.is_(True)))
    opts = UploadOptions(consent=dict(pub.consent or {}),
                         thumbnail_path=Path(thumb.path) if thumb else None)
    if pub.platform == "youtube":
        opts.privacy = "private" if test_upload else sched.get("privacy_when_autonomous", "public")
    if pub.platform == "tiktok":
        consent_ok = bool((pub.consent or {}).get("confirmed_by"))
        opts.tiktok_mode = "direct_post" if (sched.get("mode") == "direct_post" and consent_ok and not test_upload) \
            else "inbox_draft"
    conn = oauth.connection(session, pub.platform)
    if pub.platform == "instagram":
        opts.consent.setdefault("ig_user_id", conn.account_id)
    return opts


def publish(session: Session, pub: Publication, *, test_upload: bool = False, http=None) -> Publication:
    """Attempt one publication. Never raises for platform problems; records them instead."""
    if pub.status in ACTIVE:
        return pub  # idempotent: already sent
    if test_upload and pub.platform == "instagram":
        pub.last_error = "Instagram has no private/draft API publishing; test uploads are skipped"
        return pub
    problems = check_eligibility(session, pub, test_upload=test_upload)
    meta = _metadata(session, pub)
    if problems:
        if settings_store.mode(session) != OperatingMode.AUTONOMOUS and not test_upload:
            pub.status = PublicationStatus.DRY_RUN.value
            pub.last_error = "; ".join(problems)
        else:
            pub.status = PublicationStatus.BLOCKED.value if any(
                p.startswith(("render", "metadata", "identical", "job is")) for p in problems
            ) else PublicationStatus.QUEUED.value
            pub.last_error = "; ".join(problems)
            if pub.status == PublicationStatus.QUEUED.value:
                pub.next_attempt_at = _now() + timedelta(minutes=30)
        return pub

    render = session.scalar(select(Render).where(Render.job_id == pub.job_id,
                                                 Render.profile == PLATFORM_PROFILE[pub.platform]))
    limit = int(settings_store.get(session, "processing")["publish_retry_limit"])
    client = oauth.client_for(pub.platform, http)
    conn = oauth.connection(session, pub.platform)
    if hasattr(client, "account_id"):
        client.account_id = conn.account_id
    try:
        token = oauth.access_token(session, pub.platform, http)
        video = ffmpeg.probe(render.path)
        opts = _options(session, pub, test_upload)
        remote_problems = client.eligibility(token, video, opts)
        if remote_problems:
            pub.status, pub.last_error = PublicationStatus.BLOCKED.value, "; ".join(remote_problems)
            return pub
        pub.status, pub.attempts = PublicationStatus.UPLOADING.value, pub.attempts + 1
        session.flush()
        result = client.upload(token, Path(render.path), {
            "title": meta.title, "description": meta.description, "keywords": meta.keywords,
            "contains_synthetic_media": False,
        }, opts)
        pub.privacy = opts.privacy if pub.platform == "youtube" else (
            opts.tiktok_mode if pub.platform == "tiktok" else "public")
        _apply(pub, result)
    except NotConfigured as exc:
        pub.status, pub.last_error = PublicationStatus.BLOCKED.value, str(exc)
    except ProviderError as exc:
        pub.last_error = str(exc)[:1000]
        if exc.transient and pub.attempts < limit:
            pub.status = PublicationStatus.QUEUED.value
            pub.next_attempt_at = _now() + timedelta(minutes=5 * 2 ** max(0, pub.attempts - 1))
        else:
            pub.status = PublicationStatus.FAILED.value
    return pub


def _apply(pub: Publication, result) -> None:
    pub.status = {"processing": PublicationStatus.PROCESSING, "published": PublicationStatus.PUBLISHED,
                  "drafted": PublicationStatus.DRAFTED, "failed": PublicationStatus.FAILED}[result.status].value
    pub.external_id = result.external_id or pub.external_id
    pub.remote_tracking_id = result.tracking_id or pub.remote_tracking_id
    pub.url = result.url or pub.url
    pub.last_error = result.detail if result.status == "failed" else None
    if result.status == "published" and pub.published_at is None:
        pub.published_at = _now()


def refresh_status(session: Session, pub: Publication, http=None) -> Publication:
    if pub.status not in (PublicationStatus.PROCESSING.value, PublicationStatus.DRAFTED.value):
        return pub
    client = oauth.client_for(pub.platform, http)
    conn = oauth.connection(session, pub.platform)
    if hasattr(client, "account_id"):
        client.account_id = conn.account_id
    try:
        token = oauth.access_token(session, pub.platform, http)
        _apply(pub, client.status(token, pub.external_id, pub.remote_tracking_id))
    except (NotConfigured, ProviderError) as exc:
        pub.last_error = f"status check failed: {exc}"[:1000]
    if pub.status == PublicationStatus.PUBLISHED.value:
        job = session.get(ContentJob, pub.job_id)
        if job:
            job.status = JobStatus.PUBLISHED.value
    return pub


def process_due(session: Session, http=None, limit: int = 20) -> dict:
    now = _now()
    due = list(session.scalars(
        select(Publication).where(Publication.status == PublicationStatus.QUEUED.value)
        .order_by(Publication.scheduled_for).limit(limit * 3)
    ))
    sent = blocked = dry = 0
    for pub in due:
        if _aware(pub.scheduled_for) and _aware(pub.scheduled_for) > now:
            continue
        if _aware(pub.next_attempt_at) and _aware(pub.next_attempt_at) > now:
            continue
        publish(session, pub, http=http)
        if pub.status == PublicationStatus.DRY_RUN.value:
            dry += 1
        elif pub.status in ACTIVE:
            sent += 1
        else:
            blocked += 1
        if sent >= limit:
            break
    for pub in session.scalars(select(Publication).where(Publication.status.in_(
            [PublicationStatus.PROCESSING.value]))):
        refresh_status(session, pub, http)
    return {"sent": sent, "blocked_or_waiting": blocked, "dry_run": dry}


def requeue(session: Session, pub: Publication) -> Publication:
    if pub.status in (PublicationStatus.PUBLISHED.value, PublicationStatus.UPLOADING.value,
                      PublicationStatus.PROCESSING.value):
        raise ValueError(f"publication is {pub.status}; it cannot be re-queued")
    pub.status, pub.next_attempt_at, pub.last_error = PublicationStatus.QUEUED.value, None, None
    pub.scheduled_for = next_slot(session, pub.platform)
    return pub


def test_upload(session: Session, job: ContentJob, platform: str, http=None) -> Publication:
    """Non-public test upload (YouTube private, TikTok inbox draft). Separate from the real
    publication so it never blocks or counts against the real post."""
    render = session.scalar(select(Render).where(Render.job_id == job.id,
                                                 Render.profile == PLATFORM_PROFILE[platform]))
    if render is None:
        raise ValueError("no render for this platform")
    key = f"job{job.id}:{platform}:test"
    pub = session.scalar(select(Publication).where(Publication.idempotency_key == key))
    if pub is None:
        pub = Publication(job_id=job.id, platform=platform, idempotency_key=key,
                          content_hash=file_sha256(Path(render.path)), status=PublicationStatus.QUEUED.value)
        session.add(pub)
        session.flush()
    return publish(session, pub, test_upload=True, http=http)


def release_dry_runs(session: Session) -> int:
    """On activation of AUTONOMOUS mode, re-queue prepared (dry-run) items."""
    n = 0
    for pub in session.scalars(select(Publication).where(Publication.status == PublicationStatus.DRY_RUN.value)):
        pub.status, pub.last_error = PublicationStatus.QUEUED.value, None
        pub.scheduled_for = next_slot(session, pub.platform)
        n += 1
    return n
