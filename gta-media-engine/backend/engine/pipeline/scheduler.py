"""Periodic ticks used by Celery beat (production) and the in-process runner (development)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from .. import settings_store, storage
from ..agents.base import AgentContext
from ..agents.distribution import AnalyticsAgent, PublishingAgent
from ..agents.research import ResearchAgent, TrendAnalystAgent
from ..agents.strategist import ContentStrategistAgent
from ..db import session_scope
from ..enums import JobStatus
from ..logs import get_logger
from ..models import ContentJob, Report
from . import orchestrator

log = get_logger("scheduler")


def research_tick() -> dict:
    with session_scope() as s:
        if settings_store.get(s, "processing_paused"):
            return {"skipped": "processing paused"}
        ctx = AgentContext(s)
        r = ResearchAgent().run(ctx, {"query": "GTA 6"})
        t = TrendAnalystAgent().run(ctx, {})
        return {"research": r.model_dump(), "trends": {"topics_created": t.topics_created,
                                                       "duplicates": t.duplicates}}


def plan_tick() -> dict:
    """Create today's jobs up to videos_per_day."""
    with session_scope() as s:
        if settings_store.get(s, "processing_paused"):
            return {"skipped": "processing paused"}
        per_day = int(settings_store.get(s, "schedule")["videos_per_day"])
        day = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        made_today = s.scalar(select(func.count()).select_from(ContentJob).where(ContentJob.created_at >= day)) or 0
        need = max(0, per_day - made_today)
        if not need:
            return {"created": 0, "reason": "daily quota already planned"}
        out = ContentStrategistAgent().run(AgentContext(s), {"count": need})
        return {"created": len(out.selected), "candidates": out.candidates_considered}


def runnable_jobs(limit: int) -> list[int]:
    with session_scope() as s:
        running = s.scalar(select(func.count()).select_from(ContentJob)
                           .where(ContentJob.status == JobStatus.RUNNING.value)) or 0
        cap = int(settings_store.get(s, "processing")["max_concurrent_jobs"])
        free = max(0, min(limit, cap - running))
        return list(s.scalars(select(ContentJob.id).where(ContentJob.status == JobStatus.QUEUED.value)
                              .order_by(ContentJob.id).limit(free)))


def production_tick(limit: int = 4) -> dict:
    done = {}
    for job_id in runnable_jobs(limit):
        with session_scope() as s:
            if not orchestrator.claim(s, job_id):
                continue
        # one transaction per stage so progress is visible on the dashboard
        while True:
            with session_scope() as s:
                job = s.get(ContentJob, job_id)
                run = orchestrator.run_stage(s, job)
                status = job.status
            if run is None or status != JobStatus.RUNNING.value:
                break
        done[job_id] = status
    return done


def publish_tick(http=None) -> dict:
    with session_scope() as s:
        return PublishingAgent(http).run(AgentContext(s), {}).model_dump()


def analytics_tick(http=None) -> dict:
    with session_scope() as s:
        last = s.scalar(select(Report.created_at).where(Report.kind == "weekly").order_by(Report.id.desc()))
        if last is not None and last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        weekly = last is None or datetime.now(timezone.utc) - last > timedelta(days=7)
        return AnalyticsAgent(http).run(AgentContext(s), {"collect": True, "weekly_report": weekly}).model_dump()


def maintenance_tick() -> dict:
    removed = storage.cleanup_temp()
    with session_scope() as s:
        alerts = orchestrator.spend_check(s)
    for a in alerts:
        log.warning("budget alert: %s", a)
    return {"temp_files_removed": removed, "budget_alerts": alerts}


def full_cycle() -> dict:
    """One pass of the whole operation (used by `python -m engine.cli cycle`)."""
    return {
        "research": research_tick(),
        "plan": plan_tick(),
        "production": production_tick(),
        "publish": publish_tick(),
        "analytics": analytics_tick(),
        "maintenance": maintenance_tick(),
    }
