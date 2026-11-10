"""Run long operations off the request: Celery when Redis is configured, else a background thread."""

from __future__ import annotations

import threading

from ..config import get_settings
from ..logs import get_logger

log = get_logger("api.background")


def _thread(fn, *args) -> None:
    def target():
        try:
            fn(*args)
        except Exception:  # noqa: BLE001
            log.exception("background task %s failed", getattr(fn, "__name__", fn))

    threading.Thread(target=target, daemon=True).start()


def run_job(job_id: int) -> str:
    if get_settings().redis_url:
        from ..workers import tasks

        tasks.dispatch.delay()  # claims queued jobs within the concurrency cap
        return "queued on worker"
    from ..db import session_scope
    from ..enums import JobStatus
    from ..models import ContentJob
    from ..pipeline import orchestrator

    # Claim synchronously so the caller learns whether the job really started.
    with session_scope() as s:
        if not orchestrator.claim(s, job_id):
            job = s.get(ContentJob, job_id)
            status = job.status if job else "missing"
            log.warning("job %s not started: status is %s", job_id, status)
            return f"not started: job is {status}"

    def work(jid: int) -> None:
        while True:
            with session_scope() as s:
                job = s.get(ContentJob, jid)
                run = orchestrator.run_stage(s, job)
                status = job.status
            if run is None or status != JobStatus.RUNNING.value:
                break

    _thread(work, job_id)
    return "running in background thread"


def run_tick(name: str) -> str:
    from ..pipeline import scheduler

    if get_settings().redis_url:
        from ..workers import tasks

        getattr(tasks, name).delay()
        return "queued on worker"
    fn = {"research": scheduler.research_tick, "plan": scheduler.plan_tick, "publish": scheduler.publish_tick,
          "analytics": scheduler.analytics_tick, "maintenance": scheduler.maintenance_tick}[name]
    _thread(fn)
    return "running in background thread"
