"""Agent 3: Content Strategist - selects topics, balances categories, builds the calendar."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone

from pydantic import BaseModel, Field
from sqlalchemy import select

from .. import settings_store
from ..enums import Category, JobStatus, TopicStatus
from ..models import ContentJob, Report, Topic
from .base import Agent, AgentContext

URGENCY_BOOST = {"urgent": 1.3, "high": 1.12, "normal": 1.0, "low": 0.9}


class StrategyInput(BaseModel):
    count: int = Field(1, ge=0, le=50)
    create_jobs: bool = True


class Selection(BaseModel):
    topic_id: int
    job_id: int | None = None
    category: str
    priority: float
    reason: str
    duration_bucket: str


class StrategyOutput(BaseModel):
    selected: list[Selection] = Field(default_factory=list)
    candidates_considered: int = 0
    category_share_7d: dict[str, float] = Field(default_factory=dict)
    performance_multipliers: dict[str, float] = Field(default_factory=dict)


def category_multipliers(session) -> dict[str, float]:
    """Use the latest weekly report, only for categories with a sufficient sample."""
    report = session.scalar(select(Report).where(Report.kind == "weekly").order_by(Report.id.desc()))
    if not report:
        return {}
    out = {}
    for row in report.body.get("by_category", []):
        if row.get("sufficient_sample") and row.get("relative_engagement") is not None:
            out[row["group"]] = max(0.7, min(1.3, float(row["relative_engagement"])))
    return out


class ContentStrategistAgent(Agent[StrategyInput, StrategyOutput]):
    name = "content_strategist"
    responsibility = "Choose what to produce next from scored topics, category balance and real performance."
    input_model, output_model = StrategyInput, StrategyOutput
    max_attempts = 1

    def execute(self, ctx: AgentContext, payload: StrategyInput) -> StrategyOutput:
        s = ctx.session
        cats = settings_store.get(s, "categories")
        processing = settings_store.get(s, "processing")
        enabled = set(cats["enabled"])
        target = cats["target_mix"]

        week_ago = datetime.now(timezone.utc) - timedelta(days=7)
        recent = Counter(s.scalars(select(ContentJob.category).where(ContentJob.created_at >= week_ago)))
        total = sum(recent.values())
        share = {c.value: (recent.get(c.value, 0) / total if total else 0.0) for c in Category}
        multipliers = category_multipliers(s)

        candidates = list(
            s.scalars(
                select(Topic).where(
                    Topic.status == TopicStatus.NEW.value,
                    Topic.score >= processing["min_topic_score"],
                )
            )
        )
        candidates = [t for t in candidates if t.category in enabled and t.sources]
        out = StrategyOutput(candidates_considered=len(candidates), category_share_7d=share,
                             performance_multipliers=multipliers)

        ranked = []
        for t in candidates:
            deficit = target.get(t.category, 0.0) - share.get(t.category, 0.0)
            mult = multipliers.get(t.category, 1.0)
            priority = t.score * (1 + deficit) * mult * URGENCY_BOOST.get(t.urgency, 1.0)
            reason = (f"score {t.score:.0f}, category deficit {deficit:+.2f}, "
                      f"performance x{mult:.2f}, urgency {t.urgency}")
            ranked.append((priority, reason, t))
        ranked.sort(key=lambda r: r[0], reverse=True)

        picked_cats: Counter = Counter()
        chosen = []
        for priority, reason, topic in ranked:
            if len(chosen) >= payload.count:
                break
            # Avoid repeating a category within one batch while alternatives exist.
            if picked_cats[topic.category] and any(
                r[2].category not in picked_cats for r in ranked if r[2] not in [c[2] for c in chosen]
            ):
                continue
            chosen.append((priority, reason, topic))
            picked_cats[topic.category] += 1
        # Fill remaining slots if category diversity left gaps.
        for item in ranked:
            if len(chosen) >= payload.count:
                break
            if item not in chosen:
                chosen.append(item)

        for priority, reason, topic in chosen:
            job_id = None
            if payload.create_jobs:
                job = ContentJob(topic_id=topic.id, category=topic.category,
                                 status=JobStatus.QUEUED.value,
                                 duration_bucket=topic.recommended_length)
                s.add(job)
                topic.status = TopicStatus.SELECTED.value
                s.flush()
                job_id = job.id
            out.selected.append(Selection(topic_id=topic.id, job_id=job_id, category=topic.category,
                                          priority=round(priority, 2), reason=reason,
                                          duration_bucket=topic.recommended_length))
        return out


def build_calendar(session, days: int = 7) -> list[dict]:
    """Planned slots for the next ``days``: ready jobs first, then category placeholders."""
    from ..models import Publication

    sched = settings_store.get(session, "schedule")
    cats = settings_store.get(session, "categories")
    per_day = int(sched["videos_per_day"])
    windows = sched["publishing_windows"] or ["12:00-14:00"]
    target = cats["target_mix"]

    ready = list(session.scalars(
        select(ContentJob).where(ContentJob.status == JobStatus.READY.value).order_by(ContentJob.id)
    ))
    scheduled = {
        p.job_id: p.scheduled_for
        for p in session.scalars(select(Publication).where(Publication.scheduled_for.is_not(None)))
    }
    # Weighted round-robin of categories for unfilled slots
    credit = {c: 0.0 for c in target}
    today = datetime.now(timezone.utc).date()
    slots = []
    for d in range(days):
        day = today + timedelta(days=d)
        for n in range(per_day):
            window = windows[n % len(windows)]
            if ready:
                job = ready.pop(0)
                slots.append({"date": day.isoformat(), "window": window, "category": job.category,
                              "job_id": job.id, "title": job.topic.title if job.topic else None,
                              "scheduled_for": scheduled.get(job.id).isoformat() if scheduled.get(job.id) else None,
                              "status": "ready"})
                credit[job.category] = credit.get(job.category, 0) - 1
                continue
            for c in credit:
                credit[c] += target[c]
            cat = max(credit, key=credit.get)
            credit[cat] -= 1
            slots.append({"date": day.isoformat(), "window": window, "category": cat,
                          "job_id": None, "title": None, "scheduled_for": None, "status": "planned"})
    return slots
