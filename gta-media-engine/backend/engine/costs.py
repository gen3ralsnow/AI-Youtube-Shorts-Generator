"""Cost ledger and budget guard.

Every paid call is estimated *before* it runs; the guard refuses it if it would
exceed the per-video, daily or monthly limit and pauses processing. After the
call, the provider-reported usage is recorded as the actual cost.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import settings_store
from .config import get_settings
from .logs import get_logger
from .models import CostEntry

log = get_logger("costs")


class BudgetExceeded(RuntimeError):
    pass


def _period_total(session: Session, since: datetime, job_id: int | None = None) -> float:
    # Use actual where known, otherwise the estimate (conservative).
    expr = func.coalesce(CostEntry.actual_usd, CostEntry.estimated_usd)
    stmt = select(func.coalesce(func.sum(expr), 0.0)).where(CostEntry.created_at >= since)
    if job_id is not None:
        stmt = select(func.coalesce(func.sum(expr), 0.0)).where(CostEntry.job_id == job_id)
    return float(session.scalar(stmt) or 0.0)


def spend_summary(session: Session) -> dict:
    now = datetime.now(timezone.utc)
    day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    month = day.replace(day=1)
    budgets = settings_store.get(session, "budgets")

    def split(since: datetime) -> dict:
        est = session.scalar(
            select(func.coalesce(func.sum(CostEntry.estimated_usd), 0.0)).where(
                CostEntry.created_at >= since
            )
        )
        act = session.scalar(
            select(func.coalesce(func.sum(CostEntry.actual_usd), 0.0)).where(
                CostEntry.created_at >= since, CostEntry.actual_usd.is_not(None)
            )
        )
        return {"estimated_usd": round(float(est or 0), 4), "actual_usd": round(float(act or 0), 4)}

    by_cat = session.execute(
        select(
            CostEntry.category,
            func.sum(CostEntry.estimated_usd),
            func.sum(func.coalesce(CostEntry.actual_usd, 0.0)),
            func.count(),
        )
        .where(CostEntry.created_at >= month)
        .group_by(CostEntry.category)
    ).all()
    return {
        "today": split(day),
        "month": split(month),
        "budgets": budgets,
        "by_category_month": [
            {"category": c, "estimated_usd": round(e or 0, 4), "actual_usd": round(a or 0, 4), "calls": n}
            for c, e, a, n in by_cat
        ],
        "processing_paused": settings_store.get(session, "processing_paused"),
        "processing_pause_reason": settings_store.get(session, "processing_pause_reason"),
    }


def check_budget(session: Session, estimate_usd: float, job_id: int | None = None) -> None:
    budgets = settings_store.get(session, "budgets")
    now = datetime.now(timezone.utc)
    day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    month = day.replace(day=1)
    checks = [
        ("daily", _period_total(session, day), budgets["daily_usd"]),
        ("monthly", _period_total(session, month), budgets["monthly_usd"]),
    ]
    if job_id is not None:
        checks.append(("per-video", _period_total(session, day, job_id), budgets["per_video_usd"]))
    for name, spent, limit in checks:
        if spent + estimate_usd > limit:
            reason = f"{name} budget reached: spent ${spent:.4f} + est ${estimate_usd:.4f} > ${limit:.2f}"
            if name != "per-video":
                settings_store.put(session, "processing_paused", True)
                settings_store.put(session, "processing_pause_reason", reason)
            log.warning(reason)
            raise BudgetExceeded(reason)


def budget_alerts(session: Session) -> list[str]:
    budgets = settings_store.get(session, "budgets")
    ratio = budgets.get("alert_ratio", 0.8)
    now = datetime.now(timezone.utc)
    day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    alerts = []
    for name, since, limit in (
        ("daily", day, budgets["daily_usd"]),
        ("monthly", day.replace(day=1), budgets["monthly_usd"]),
    ):
        spent = _period_total(session, since)
        if limit and spent >= ratio * limit:
            alerts.append(f"{name} spend ${spent:.2f} is {spent / limit:.0%} of ${limit:.2f}")
    return alerts


def record(
    session: Session,
    *,
    category: str,
    provider: str,
    units: float,
    unit: str,
    estimated_usd: float,
    actual_usd: float | None,
    job_id: int | None = None,
    note: str = "",
) -> CostEntry:
    entry = CostEntry(
        category=category,
        provider=provider,
        units=units,
        unit=unit,
        estimated_usd=round(estimated_usd, 6),
        actual_usd=None if actual_usd is None else round(actual_usd, 6),
        job_id=job_id,
        note=note,
    )
    session.add(entry)
    session.flush()
    return entry


# --- Estimators -------------------------------------------------------------


def estimate_llm(input_tokens: int, output_tokens: int) -> float:
    s = get_settings()
    return (input_tokens * s.price_llm_input_per_mtok + output_tokens * s.price_llm_output_per_mtok) / 1e6


def estimate_tts(chars: int) -> float:
    return chars / 1000 * get_settings().price_tts_per_kchar


def estimate_transcription(seconds: float) -> float:
    return seconds / 60 * get_settings().price_transcription_per_min


def estimate_render(seconds: float) -> float:
    return seconds / 60 * get_settings().price_render_per_min


def rough_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def recent_window(days: int) -> datetime:
    return datetime.now(timezone.utc) - timedelta(days=days)
