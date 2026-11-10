"""Celery tasks. Each job stage is its own task, routed to the right worker pool."""

from __future__ import annotations

from ..db import session_scope
from ..enums import JobStatus
from ..logs import configure_logging
from ..models import ContentJob
from ..pipeline import orchestrator, scheduler
from .celery_app import app

configure_logging()


@app.task(name="engine.workers.tasks.research")
def research():
    return scheduler.research_tick()


@app.task(name="engine.workers.tasks.plan")
def plan():
    return scheduler.plan_tick()


@app.task(name="engine.workers.tasks.dispatch")
def dispatch():
    """Claim queued jobs within the concurrency cap and start their next stage."""
    started = []
    for job_id in scheduler.runnable_jobs(limit=8):
        with session_scope() as s:
            if orchestrator.claim(s, job_id):
                started.append(job_id)
        _enqueue_next(job_id)
    return started


def _enqueue_next(job_id: int) -> None:
    with session_scope() as s:
        job = s.get(ContentJob, job_id)
        stage = orchestrator.next_stage(s, job) if job else None
    if stage is None:
        return
    task = video_stage if stage in orchestrator.VIDEO_STAGES else stage_task
    task.delay(job_id)


def _run(job_id: int) -> str:
    with session_scope() as s:
        job = s.get(ContentJob, job_id)
        orchestrator.run_stage(s, job)
        status = job.status
    if status == JobStatus.RUNNING.value:
        _enqueue_next(job_id)
    return status


@app.task(name="engine.workers.tasks.stage")
def stage_task(job_id: int):
    return _run(job_id)


@app.task(name="engine.workers.tasks.video_stage")
def video_stage(job_id: int):
    return _run(job_id)


@app.task(name="engine.workers.tasks.publish")
def publish():
    return scheduler.publish_tick()


@app.task(name="engine.workers.tasks.analytics")
def analytics():
    return scheduler.analytics_tick()


@app.task(name="engine.workers.tasks.maintenance")
def maintenance():
    return scheduler.maintenance_tick()
