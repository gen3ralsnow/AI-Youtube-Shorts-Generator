"""Celery application: durable Redis-backed queues with separate workers per workload.

Queues: research, ai (scripts/voice/metadata), video (FFmpeg), publishing, analytics.
Start workers (see deploy/docker-compose.yml):
    celery -A engine.workers.celery_app worker -Q research,ai -c 2
    celery -A engine.workers.celery_app worker -Q video -c 1
    celery -A engine.workers.celery_app worker -Q publishing,analytics -c 1
    celery -A engine.workers.celery_app beat
"""

from __future__ import annotations

from celery import Celery

from ..config import get_settings

settings = get_settings()
broker = settings.redis_url or "redis://localhost:6379/0"

app = Celery("gta_media_engine", broker=broker, backend=broker, include=["engine.workers.tasks"])
app.conf.update(
    task_acks_late=True,  # a crashed worker's task is redelivered
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    task_time_limit=60 * 60,
    task_soft_time_limit=55 * 60,
    timezone="UTC",
    task_routes={
        "engine.workers.tasks.research": {"queue": "research"},
        "engine.workers.tasks.plan": {"queue": "ai"},
        "engine.workers.tasks.stage": {"queue": "ai"},
        "engine.workers.tasks.video_stage": {"queue": "video"},
        "engine.workers.tasks.dispatch": {"queue": "ai"},
        "engine.workers.tasks.publish": {"queue": "publishing"},
        "engine.workers.tasks.analytics": {"queue": "analytics"},
        "engine.workers.tasks.maintenance": {"queue": "analytics"},
    },
    beat_schedule={
        "research": {"task": "engine.workers.tasks.research", "schedule": 60 * 60},
        "plan": {"task": "engine.workers.tasks.plan", "schedule": 30 * 60},
        "dispatch": {"task": "engine.workers.tasks.dispatch", "schedule": 60},
        "publish": {"task": "engine.workers.tasks.publish", "schedule": 5 * 60},
        "analytics": {"task": "engine.workers.tasks.analytics", "schedule": 3 * 60 * 60},
        "maintenance": {"task": "engine.workers.tasks.maintenance", "schedule": 60 * 60},
    },
)
