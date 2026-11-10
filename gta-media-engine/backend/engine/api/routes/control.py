"""Settings, operating mode, pause controls, launch checklist, system health, costs, overview, audit."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from ... import costs, settings_store
from ...config import get_settings
from ...enums import JobStatus, OperatingMode, Platform, PublicationStatus
from ...models import (
    AgentRun,
    AuditLog,
    ContentJob,
    CostEntry,
    MediaAsset,
    MetricSnapshot,
    PlatformConnection,
    Publication,
    Render,
)
from ...providers.llm import get_llm
from ...providers.transcription import get_transcriber
from ...providers.tts import get_tts
from ...publishing import manager, oauth
from ...research.connectors import connector_status
from ...security import CredentialVault
from ...storage import get_storage
from ...video import ffmpeg
from ..deps import admin, db, iso, log_action, viewer

router = APIRouter(prefix="/api")
ACTIVATION_PHRASE = "ACTIVATE AUTONOMOUS MODE"


# ------------------------------------------------------------------ settings


@router.get("/settings")
def get_settings_all(s: Session = Depends(db), _: str = Depends(viewer)):
    return settings_store.get_all(s)


@router.put("/settings/{key}")
def put_setting(key: str, request: Request, value: Any = Body(...), s: Session = Depends(db),
                _: str = Depends(admin)):
    try:
        out = settings_store.put(s, key, value)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    log_action(s, request, "settings.update", key)
    return out


# ------------------------------------------------------------------ pause controls


def _mode(s: Session) -> dict:
    return {"mode": settings_store.get(s, "mode"), "publishing_paused": settings_store.get(s, "publishing_paused"),
            "processing_paused": settings_store.get(s, "processing_paused"),
            "processing_pause_reason": settings_store.get(s, "processing_pause_reason"),
            "autonomous_activated_by": settings_store.get(s, "autonomous_activated_by"),
            "autonomous_activated_at": settings_store.get(s, "autonomous_activated_at")}


@router.get("/control/mode")
def mode(s: Session = Depends(db), _: str = Depends(viewer)):
    return _mode(s)


# ------------------------------------------------------------------ launch checklist & activation


def checklist(s: Session) -> list[dict]:
    settings = get_settings()
    items: list[dict] = []

    def add(key: str, label: str, passed: bool, detail: str, required: bool = True):
        items.append({"key": key, "label": label, "passed": bool(passed), "detail": detail, "required": required})

    add("admin_token", "Admin API token configured", bool(settings.admin_api_token), "ADMIN_API_TOKEN")
    add("encryption_key", "Credential encryption key configured", bool(settings.encryption_key),
        "ENGINE_ENCRYPTION_KEY")
    llm = get_llm()
    add("llm", "Language model provider connected", llm.available,
        f"{settings.llm_provider}" + ("" if llm.available else " (rule-based fallback in use)"), required=False)
    tts = get_tts()
    add("tts", "Text-to-speech provider connected", tts.name != "placeholder" and tts.available,
        f"{settings.tts_provider}")
    research = [c for c in connector_status() if c["configured"]]
    add("research", "At least one research source connected", bool(research),
        ", ".join(c["name"] for c in research) or "none configured")
    verified = s.scalar(select(func.count()).select_from(MediaAsset).where(
        MediaAsset.kind == "footage", MediaAsset.rights_verified.is_(True))) or 0
    add("footage", "At least 3 rights-verified footage assets", verified >= 3, f"{verified} verified")
    enabled = [p for p, c in settings_store.get(s, "schedule")["platforms"].items() if c.get("enabled")]
    for p in enabled:
        conn = s.get(PlatformConnection, p)
        add(f"platform_{p}", f"{p} connected and verified", bool(conn and conn.status == "verified"),
            conn.status if conn else "disconnected")
    publishable = [r for r in s.scalars(select(Render)) if (r.qc or {}).get("passed")]
    add("sample_video", "A sample video passed full QC", bool(publishable), f"{len(publishable)} publishable render(s)")
    tests = s.scalars(select(Publication).where(Publication.idempotency_key.like("%:test"))).all()
    ok_tests = [t for t in tests if t.status in (PublicationStatus.DRAFTED.value, PublicationStatus.PUBLISHED.value)]
    add("test_publish", "Non-public test upload confirmed by a platform", bool(ok_tests),
        f"{len(ok_tests)} confirmed test upload(s)")
    snaps = s.scalar(select(func.count()).select_from(MetricSnapshot)) or 0
    add("analytics", "Analytics retrieval verified", snaps > 0, f"{snaps} metric snapshot(s)", required=False)
    health = system_health(s)
    add("health", "System health OK", health["ok"], "; ".join(f"{k}: {v['detail']}" for k, v in
                                                               health["components"].items() if not v["ok"]) or "ok")
    return items


@router.get("/control/launch-checklist")
def launch_checklist(s: Session = Depends(db), _: str = Depends(viewer)):
    items = checklist(s)
    return {"items": items, "ready": all(i["passed"] for i in items if i["required"]), **_mode(s)}


class Activation(BaseModel):
    confirm: str


@router.post("/control/activate-autonomous")
def activate(body: Activation, request: Request, s: Session = Depends(db), _: str = Depends(admin)):
    if body.confirm != ACTIVATION_PHRASE:
        raise HTTPException(422, f"type exactly: {ACTIVATION_PHRASE}")
    items = checklist(s)
    failing = [i["label"] for i in items if i["required"] and not i["passed"]]
    if failing:
        raise HTTPException(409, {"message": "launch checklist incomplete", "failing": failing})
    actor = f"admin@{request.client.host if request.client else 'unknown'}"
    settings_store.put(s, "mode", OperatingMode.AUTONOMOUS.value, allow_protected=True)
    settings_store.put(s, "autonomous_activated_by", actor, allow_protected=True)
    settings_store.put(s, "autonomous_activated_at", datetime.now(timezone.utc).isoformat(), allow_protected=True)
    released = manager.release_dry_runs(s)
    log_action(s, request, "control.activate_autonomous", released_dry_runs=released)
    # Publishing stays paused until the administrator explicitly resumes it.
    return {**_mode(s), "released_dry_runs": released,
            "note": "AUTONOMOUS mode active. Publishing remains paused until you resume it."}


# ------------------------------------------------------------------ system health


def system_health(s: Session) -> dict:
    settings = get_settings()
    comp: dict[str, dict] = {}
    try:
        s.execute(text("SELECT 1"))
        comp["database"] = {"ok": True, "detail": settings.database_url.split("://")[0]}
    except Exception as exc:  # noqa: BLE001
        comp["database"] = {"ok": False, "detail": type(exc).__name__}
    if settings.redis_url:
        try:
            import redis

            redis.Redis.from_url(settings.redis_url, socket_timeout=2).ping()
            comp["redis"] = {"ok": True, "detail": "reachable"}
        except Exception as exc:  # noqa: BLE001
            comp["redis"] = {"ok": False, "detail": f"unreachable ({type(exc).__name__})"}
    else:
        comp["redis"] = {"ok": True, "detail": "not configured (in-process runner)"}
    ok, detail = get_storage().healthy()
    comp["storage"] = {"ok": ok, "detail": detail}
    comp["ffmpeg"] = {"ok": ffmpeg.available(), "detail": "found" if ffmpeg.available() else "missing"}
    vault = CredentialVault()
    comp["credential_vault"] = {"ok": not (vault.insecure and settings.is_production),
                                "detail": "development key in use" if vault.insecure else "configured"}
    stuck = s.scalar(select(func.count()).select_from(Publication).where(
        Publication.status == PublicationStatus.UPLOADING.value)) or 0
    comp["publishing"] = {"ok": stuck == 0, "detail": f"{stuck} upload(s) in progress"}
    return {"ok": all(c["ok"] for c in comp.values()), "components": comp}


@router.get("/system/health")
def health(s: Session = Depends(db), _: str = Depends(viewer)):
    settings = get_settings()
    return {
        **system_health(s),
        "providers": {
            "llm": {"provider": settings.llm_provider, "available": get_llm().available},
            "tts": {"provider": get_tts().name, "available": get_tts().available,
                    "placeholder": get_tts().name == "placeholder"},
            "transcription": {"provider": settings.transcription_provider, "available": get_transcriber().available},
        },
        "research_sources": connector_status(),
        "platform_apps": {p.value: oauth.client_for(p.value).missing() for p in Platform},
        "budget_alerts": costs.budget_alerts(s),
        "recent_agent_failures": [
            {"agent": r.agent, "job_id": r.job_id, "status": r.status, "error": r.error, "at": iso(r.created_at)}
            for r in s.scalars(select(AgentRun).where(AgentRun.status != "succeeded")
                               .order_by(AgentRun.id.desc()).limit(10))
        ],
    }


# ------------------------------------------------------------------ costs, overview, audit


@router.get("/costs")
def cost_summary(s: Session = Depends(db), _: str = Depends(viewer)):
    recent = s.scalars(select(CostEntry).order_by(CostEntry.id.desc()).limit(100))
    return {**costs.spend_summary(s), "alerts": costs.budget_alerts(s),
            "recent": [{"id": c.id, "at": iso(c.created_at), "category": c.category, "provider": c.provider,
                        "units": c.units, "unit": c.unit, "estimated_usd": c.estimated_usd,
                        "actual_usd": c.actual_usd, "job_id": c.job_id, "note": c.note} for c in recent]}


@router.get("/overview")
def overview(s: Session = Depends(db), _: str = Depends(viewer)):
    def count(model, *where):
        return s.scalar(select(func.count()).select_from(model).where(*where)) or 0

    latest = (select(MetricSnapshot.publication_id, func.max(MetricSnapshot.id).label("mid"))
              .group_by(MetricSnapshot.publication_id).subquery())
    snaps = list(s.scalars(select(MetricSnapshot).join(latest, MetricSnapshot.id == latest.c.mid)))

    def total(field: str):
        vals = [getattr(m, field) for m in snaps if getattr(m, field) is not None]
        return sum(vals) if vals else None

    views = total("views")
    interactions = [x for x in (total("likes"), total("comments"), total("shares"), total("saves")) if x is not None]
    upcoming = s.scalars(select(Publication).where(Publication.status == PublicationStatus.QUEUED.value)
                         .order_by(Publication.scheduled_for).limit(8))
    spend = costs.spend_summary(s)
    return {
        **_mode(s),
        "videos_created": count(Render, Render.profile == "youtube_shorts") or count(ContentJob, ContentJob.status.in_(
            [JobStatus.READY.value, JobStatus.PUBLISHED.value])),
        "jobs_total": count(ContentJob),
        "published_videos": count(Publication, Publication.status == PublicationStatus.PUBLISHED.value,
                                  Publication.idempotency_key.not_like("%:test")),
        "total_views": views,
        "total_interactions": sum(interactions) if interactions else None,
        "engagement_rate": (sum(interactions) / views) if views and interactions else None,
        "jobs_in_production": count(ContentJob, ContentJob.status.in_([JobStatus.RUNNING.value, JobStatus.QUEUED.value])),
        "pending_publications": count(Publication, Publication.status.in_(
            [PublicationStatus.QUEUED.value, PublicationStatus.PROCESSING.value, PublicationStatus.UPLOADING.value])),
        "failed_jobs": count(ContentJob, ContentJob.status.in_([JobStatus.FAILED.value, JobStatus.BLOCKED.value])),
        "failed_publications": count(Publication, Publication.status.in_(
            [PublicationStatus.FAILED.value, PublicationStatus.BLOCKED.value])),
        "api_calls_today": count(CostEntry, CostEntry.created_at >= datetime.now(timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0)),
        "spend": {"today": spend["today"], "month": spend["month"], "budgets": spend["budgets"]},
        "schedule": [{"id": p.id, "platform": p.platform, "job_id": p.job_id, "scheduled_for": iso(p.scheduled_for),
                      "title": p.job.topic.title if p.job and p.job.topic else None} for p in upcoming],
    }


@router.get("/audit")
def audit_log(limit: int = Query(100, le=500), s: Session = Depends(db), _: str = Depends(admin)):
    return [{"id": a.id, "at": iso(a.created_at), "actor": a.actor, "action": a.action, "target": a.target,
             "details": a.details} for a in s.scalars(select(AuditLog).order_by(AuditLog.id.desc()).limit(limit))]


@router.get("/agents/runs")
def agent_runs(limit: int = Query(100, le=500), s: Session = Depends(db), _: str = Depends(viewer)):
    return [{"id": r.id, "agent": r.agent, "job_id": r.job_id, "status": r.status, "attempts": r.attempts,
             "duration_ms": r.duration_ms, "error": r.error, "summary": r.summary, "at": iso(r.created_at)}
            for r in s.scalars(select(AgentRun).order_by(AgentRun.id.desc()).limit(limit))]


# Declared last: FastAPI matches routes in order, so the specific /control/* routes above win.
@router.post("/control/{action}")
def control(action: str, request: Request, s: Session = Depends(db), _: str = Depends(admin)):
    if action == "pause-publishing":
        settings_store.put(s, "publishing_paused", True)
    elif action == "resume-publishing":
        if settings_store.mode(s) != OperatingMode.AUTONOMOUS:
            raise HTTPException(409, "resume publishing only applies in AUTONOMOUS mode")
        settings_store.put(s, "publishing_paused", False)
    elif action == "pause-processing":
        settings_store.put(s, "processing_paused", True)
        settings_store.put(s, "processing_pause_reason", "paused by administrator")
    elif action == "resume-processing":
        settings_store.put(s, "processing_paused", False)
        settings_store.put(s, "processing_pause_reason", "")
    elif action == "deactivate-autonomous":
        settings_store.put(s, "mode", OperatingMode.TEST.value, allow_protected=True)
        settings_store.put(s, "publishing_paused", True)
    else:
        raise HTTPException(404, "unknown control action")
    log_action(s, request, f"control.{action}")
    return _mode(s)
