"""Job state machine.

Each stage runs as a recorded ``StageRun``. Failures are retried up to the
configured limit; safety gates (facts, rights, budget, missing credentials)
block the job with a reason instead of retrying. Regeneration supersedes the
affected stage runs rather than deleting history.
"""

from __future__ import annotations

from collections.abc import Callable

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from .. import costs, settings_store
from ..agents.base import AgentBlocked, AgentContext, AgentFailed
from ..agents.factcheck import FactCheckerAgent
from ..agents.production import VideoProductionAgent
from ..agents.quality import QualityControlAgent
from ..agents.scriptwriter import ScriptwriterAgent, draft_from_row, latest_script, validate_script
from ..agents.seo import SEOAgent
from ..agents.voiceover import VoiceoverAgent
from ..costs import BudgetExceeded
from ..db import utcnow
from ..enums import PIPELINE_ORDER, JobStatus, PublicationStatus, Stage, StageStatus
from ..logs import get_logger
from ..models import ContentJob, CostEntry, Publication, StageRun
from ..providers.http import NotConfigured
from ..publishing import manager

log = get_logger("pipeline")

SUPERSEDED = "superseded"


class StageGate(RuntimeError):
    """Stage completed but its result failed a gate (e.g. low fact confidence)."""


def _latest(session: Session, job_id: int, stage: Stage) -> StageRun | None:
    return session.scalar(select(StageRun).where(StageRun.job_id == job_id, StageRun.stage == stage.value,
                                                 StageRun.status != SUPERSEDED).order_by(StageRun.id.desc()))


def next_stage(session: Session, job: ContentJob) -> Stage | None:
    for stage in PIPELINE_ORDER:
        run = _latest(session, job.id, stage)
        if run is None or run.status != StageStatus.SUCCEEDED.value:
            return stage
    return None


def attempts(session: Session, job_id: int, stage: Stage) -> int:
    return session.scalar(select(func.count()).select_from(StageRun).where(
        StageRun.job_id == job_id, StageRun.stage == stage.value,
        StageRun.status == StageStatus.FAILED.value)) or 0


# ------------------------------------------------------------------ handlers


def _fact_topic(ctx: AgentContext, job: ContentJob) -> dict:
    res = FactCheckerAgent().run(ctx, {"job_id": job.id, "mode": "topic"})
    if not res.passed:
        raise AgentBlocked("topic verification failed: " + "; ".join(res.issues))
    return res.model_dump(mode="json")


def _script(ctx: AgentContext, job: ContentJob) -> dict:
    feedback = []
    last_val = _latest(ctx.session, job.id, Stage.SCRIPT_VALIDATION)
    if last_val is not None and last_val.status == StageStatus.FAILED.value:
        feedback = last_val.output.get("errors", [])
    return ScriptwriterAgent().run(ctx, {"job_id": job.id, "feedback": feedback}).model_dump(mode="json")


def _script_validation(ctx: AgentContext, job: ContentJob) -> dict:
    s = ctx.session
    script = latest_script(s, job.id)
    wpm = float(settings_store.get(s, "processing")["words_per_minute"])
    allowed = {src.id for src in job.topic.sources}
    report = validate_script(draft_from_row(script, job.topic.classification), allowed, wpm)
    script.validation = report
    if not report["passed"]:
        raise StageGate("script validation failed: " + "; ".join(report["errors"]))
    facts = FactCheckerAgent().run(ctx, {"job_id": job.id, "mode": "script"})
    report["fact_check"] = {"confidence": facts.confidence, "threshold": facts.threshold,
                            "issues": facts.issues, "checker": facts.checker}
    if not facts.passed:
        raise AgentBlocked(f"fact confidence {facts.confidence:.2f} below threshold {facts.threshold:.2f}: "
                           + "; ".join(facts.issues[:3]))
    return report


def _voice(ctx, job):
    return VoiceoverAgent().run(ctx, {"job_id": job.id}).model_dump(mode="json")


def _footage(ctx, job):
    return VideoProductionAgent().run(ctx, {"job_id": job.id, "step": "footage"}).detail


def _edit(ctx, job):
    return VideoProductionAgent().run(ctx, {"job_id": job.id, "step": "edit"}).detail


def _qc(ctx, job):
    res = QualityControlAgent().run(ctx, {"job_id": job.id})
    out = res.model_dump(mode="json")
    if not res.technical_passed:
        # Gate applied after the savepoint commits so the QC report stays on the renders.
        failed = [f"{r['profile']}: {', '.join(r['failed'])}" for r in res.renders if not r["technical_passed"]]
        out["_gate"] = "QC rejected export: " + ("; ".join(failed + res.blockers) or "content checks failed")
    return out


def _thumbs(ctx, job):
    return VideoProductionAgent().run(ctx, {"job_id": job.id, "step": "thumbnails"}).detail


def _seo(ctx, job):
    return SEOAgent().run(ctx, {"job_id": job.id}).model_dump(mode="json")


def _queue(ctx, job):
    pubs = manager.enqueue(ctx.session, job)
    return {"publications": [{"id": p.id, "platform": p.platform, "scheduled_for": str(p.scheduled_for)}
                             for p in pubs]}


HANDLERS: dict[Stage, Callable[[AgentContext, ContentJob], dict]] = {
    Stage.FACT_CHECK: _fact_topic,
    Stage.SCRIPT: _script,
    Stage.SCRIPT_VALIDATION: _script_validation,
    Stage.VOICEOVER: _voice,
    Stage.FOOTAGE: _footage,
    Stage.EDIT: _edit,
    Stage.QC: _qc,
    Stage.THUMBNAIL: _thumbs,
    Stage.METADATA: _seo,
    Stage.QUEUE: _queue,
}

VIDEO_STAGES = {Stage.FOOTAGE, Stage.EDIT, Stage.QC, Stage.THUMBNAIL}


# ------------------------------------------------------------------ execution


def claim(session: Session, job_id: int) -> bool:
    """Atomically move a queued job to running (safe with several workers)."""
    res = session.execute(update(ContentJob).where(ContentJob.id == job_id,
                                                   ContentJob.status == JobStatus.QUEUED.value)
                          .values(status=JobStatus.RUNNING.value))
    return res.rowcount == 1


def run_stage(session: Session, job: ContentJob) -> StageRun | None:
    """Run the job's next stage once. Returns the StageRun (or None when finished)."""
    stage = next_stage(session, job)
    if stage is None:
        return None
    if settings_store.get(session, "processing_paused"):
        job.status = JobStatus.QUEUED.value
        job.blocked_reason = "processing paused: " + settings_store.get(session, "processing_pause_reason")
        return None
    limit = int(settings_store.get(session, "processing")["stage_retry_limit"])
    run = StageRun(job_id=job.id, stage=stage.value, status=StageStatus.RUNNING.value,
                   attempt=attempts(session, job.id, stage) + 1, started_at=utcnow())
    session.add(run)
    job.current_stage, job.status = stage.value, JobStatus.RUNNING.value
    session.flush()
    ctx = AgentContext(session=session, job_id=job.id)
    try:
        with session.begin_nested():
            output = HANDLERS[stage](ctx, job) or {}
        gate = output.pop("_gate", None)
        run.output = output
        if gate:
            raise StageGate(gate)
        run.status = StageStatus.SUCCEEDED.value
        job.error = job.blocked_reason = None
    except (AgentBlocked, NotConfigured, BudgetExceeded) as exc:
        run.status, run.error = StageStatus.FAILED.value, f"{type(exc).__name__}: {exc}"[:2000]
        job.status, job.blocked_reason = JobStatus.BLOCKED.value, str(exc)[:2000]
    except (AgentFailed, StageGate, Exception) as exc:  # noqa: BLE001 - recorded on the stage
        log.warning("job %s stage %s failed: %s", job.id, stage.value, exc)
        run.status, run.error = StageStatus.FAILED.value, f"{type(exc).__name__}: {exc}"[:2000]
        if isinstance(exc, StageGate) and stage == Stage.SCRIPT_VALIDATION:
            run.output = {"errors": str(exc).split(": ", 1)[-1].split("; ")}
            # Invalidate the script so the next attempt rewrites it with this feedback.
            sup = _latest(session, job.id, Stage.SCRIPT)
            if sup:
                sup.status = SUPERSEDED
        if run.attempt >= limit:
            job.status, job.error = JobStatus.FAILED.value, f"{stage.value}: {exc}"[:2000]
        else:
            job.status, job.error = JobStatus.QUEUED.value, f"retrying {stage.value}: {exc}"[:2000]
    run.finished_at = utcnow()
    _update_costs(session, job)
    if run.status == StageStatus.SUCCEEDED.value and next_stage(session, job) is None:
        job.status, job.current_stage = JobStatus.READY.value, None
    elif run.status == StageStatus.SUCCEEDED.value:
        job.status = JobStatus.RUNNING.value
    session.flush()
    return run


def advance(session: Session, job_id: int, max_stages: int = 20) -> ContentJob:
    """Run stages until the job is ready, blocked, failed or waiting for a retry."""
    job = session.get(ContentJob, job_id)
    for _ in range(max_stages):
        run = run_stage(session, job)
        if run is None or run.status != StageStatus.SUCCEEDED.value:
            break
        if job.status in (JobStatus.READY.value, JobStatus.BLOCKED.value, JobStatus.FAILED.value):
            break
    return job


def _update_costs(session: Session, job: ContentJob) -> None:
    est, act = session.execute(select(func.coalesce(func.sum(CostEntry.estimated_usd), 0.0),
                                      func.coalesce(func.sum(CostEntry.actual_usd), 0.0))
                               .where(CostEntry.job_id == job.id)).one()
    job.estimated_cost_usd, job.actual_cost_usd = round(est, 4), round(act, 4)


# ------------------------------------------------------------------ admin operations


def invalidate(session: Session, job: ContentJob, from_stage: Stage) -> None:
    active = session.scalar(select(Publication).where(
        Publication.job_id == job.id, Publication.idempotency_key.not_like("%:test"),
        Publication.status.in_(manager.ACTIVE)))
    if active:
        raise ValueError("job already sent to a platform; create a new job instead of regenerating")
    idx = PIPELINE_ORDER.index(from_stage)
    for stage in PIPELINE_ORDER[idx:]:
        for run in session.scalars(select(StageRun).where(StageRun.job_id == job.id, StageRun.stage == stage.value)):
            run.status = SUPERSEDED
    for pub in session.scalars(select(Publication).where(Publication.job_id == job.id)):
        if pub.status in (PublicationStatus.QUEUED.value, PublicationStatus.DRY_RUN.value,
                          PublicationStatus.BLOCKED.value, PublicationStatus.FAILED.value):
            session.delete(pub)
    job.status, job.error, job.blocked_reason = JobStatus.QUEUED.value, None, None


def retry(session: Session, job: ContentJob) -> None:
    """Retry a failed/blocked job from its failed stage with a fresh attempt budget."""
    stage = next_stage(session, job)
    if stage is not None:
        for run in session.scalars(select(StageRun).where(StageRun.job_id == job.id, StageRun.stage == stage.value,
                                                          StageRun.status == StageStatus.FAILED.value)):
            run.status = SUPERSEDED
    job.status, job.error, job.blocked_reason = JobStatus.QUEUED.value, None, None


def spend_check(session: Session) -> list[str]:
    return costs.budget_alerts(session)
