"""Performance analysis on real platform data.

Rules: missing metrics stay missing (excluded, never zero); groups below the
minimum sample size are reported but never used for conclusions; medians with
bootstrap confidence intervals are used instead of means (view counts are
heavy-tailed).
"""

from __future__ import annotations

import random
import statistics
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import settings_store
from ..enums import PublicationStatus
from ..models import MetricSnapshot, Publication, Report, Script, Voiceover


def engagement_rate(m: MetricSnapshot) -> float | None:
    if not m.views:
        return None
    parts = [v for v in (m.likes, m.comments, m.shares, m.saves) if v is not None]
    if not parts:
        return None
    return sum(parts) / m.views


def hook_style(hook: str) -> str:
    h = hook.strip()
    if h.endswith("?"):
        return "question"
    if any(ch.isdigit() for ch in h):
        return "number"
    if any(w in h.lower() for w in ("missed", "hidden", "secret", "nobody")):
        return "curiosity_gap"
    return "statement"


def hour_bucket(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    h = dt.hour
    return "night" if h < 6 else "morning" if h < 12 else "afternoon" if h < 18 else "evening"


def bootstrap_ci(values: list[float], iterations: int = 1000, alpha: float = 0.1) -> tuple[float, float]:
    rng = random.Random(42)
    medians = sorted(statistics.median(rng.choices(values, k=len(values))) for _ in range(iterations))
    lo = medians[int(alpha / 2 * iterations)]
    hi = medians[int((1 - alpha / 2) * iterations) - 1]
    return lo, hi


def dataset(session: Session, since: datetime | None = None) -> list[dict]:
    rows = []
    pubs = session.scalars(select(Publication).where(
        Publication.status == PublicationStatus.PUBLISHED.value, Publication.idempotency_key.not_like("%:test")))
    for pub in pubs:
        if since and pub.published_at and _aware(pub.published_at) < since:
            continue
        snap = session.scalar(select(MetricSnapshot).where(MetricSnapshot.publication_id == pub.id)
                              .order_by(MetricSnapshot.captured_at.desc()))
        if snap is None:
            continue
        job = pub.job
        script = session.scalar(select(Script).where(Script.job_id == job.id).order_by(Script.version.desc()))
        voice = session.scalar(select(Voiceover).where(Voiceover.job_id == job.id).order_by(Voiceover.id.desc()))
        rows.append({
            "publication_id": pub.id, "platform": pub.platform, "job_id": job.id,
            "title": script.title if script else "", "category": job.category,
            "duration_bucket": job.duration_bucket,
            "hook_style": hook_style(script.chosen_hook) if script else None,
            "voice_profile": (voice.voice_settings or {}).get("profile") if voice else None,
            "edit_style": "v1",
            "publish_time": hour_bucket(_aware(pub.published_at)),
            "keywords": (job.topic.keywords if job.topic else [])[:8],
            "views": snap.views, "likes": snap.likes, "comments": snap.comments, "shares": snap.shares,
            "saves": snap.saves, "avg_watch_seconds": snap.avg_watch_seconds,
            "avg_view_percentage": snap.avg_view_percentage, "followers_gained": snap.followers_gained,
            "engagement_rate": engagement_rate(snap),
            "comment_rate": (snap.comments / snap.views) if snap.views and snap.comments is not None else None,
        })
    return rows


def compare(rows: list[dict], dimension: str, metric: str, min_n: int) -> list[dict]:
    groups: dict[str, list[float]] = defaultdict(list)
    for r in rows:
        key, value = r.get(dimension), r.get(metric)
        if key is None or value is None:
            continue
        for k in (key if isinstance(key, list) else [key]):
            groups[str(k)].append(float(value))
    all_values = [v for vals in groups.values() for v in vals]
    overall = statistics.median(all_values) if all_values else None
    out = []
    for g, vals in groups.items():
        row = {"group": g, "n": len(vals), "median": round(statistics.median(vals), 5),
               "sufficient_sample": len(vals) >= min_n, "ci90": None, "relative_engagement": None}
        if len(vals) >= min_n:
            lo, hi = bootstrap_ci(vals)
            row["ci90"] = [round(lo, 5), round(hi, 5)]
            if overall:
                row["relative_engagement"] = round(statistics.median(vals) / overall, 3)
        out.append(row)
    out.sort(key=lambda r: (r["sufficient_sample"], r["median"]), reverse=True)
    return out


def _best(groups: list[dict]) -> dict | None:
    """A group is 'best' only if its CI lower bound beats every other sufficient group's median."""
    ok = [g for g in groups if g["sufficient_sample"]]
    if len(ok) < 2:
        return None
    top = ok[0]
    if all(top["ci90"][0] > g["median"] for g in ok[1:]):
        return top
    return None


def analyse(session: Session, days: int | None = None) -> dict:
    min_n = int(settings_store.get(session, "analytics")["min_sample_size"])
    since = datetime.now(timezone.utc) - timedelta(days=days) if days else None
    rows = dataset(session, since)
    result: dict = {"videos_with_metrics": len(rows), "min_sample_size": min_n}
    missing = {m: sum(1 for r in rows if r[m] is None)
               for m in ("views", "likes", "comments", "shares", "saves", "avg_watch_seconds", "followers_gained")}
    result["missing_metric_counts"] = missing
    dims = ("category", "hook_style", "duration_bucket", "voice_profile", "publish_time", "platform", "keywords")
    result["by"] = {d: compare(rows, d, "engagement_rate", min_n) for d in dims}
    result["by_category"] = result["by"]["category"]
    result["discussion_by_category"] = compare(rows, "category", "comment_rate", min_n)
    result["followers_by_category"] = compare(rows, "category", "followers_gained", min_n)
    result["watch_by_duration"] = compare(rows, "duration_bucket", "avg_view_percentage", min_n)

    answers = {}
    for question, groups in (
        ("best_topics", result["by"]["keywords"]),
        ("best_hooks", result["by"]["hook_style"]),
        ("best_durations", result["by"]["duration_bucket"]),
        ("most_discussion", result["discussion_by_category"]),
        ("subscriber_drivers", result["followers_by_category"]),
        ("best_publish_time", result["by"]["publish_time"]),
    ):
        best = _best(groups)
        answers[question] = (
            {"answer": best["group"], "median": best["median"], "ci90": best["ci90"], "n": best["n"]}
            if best else {"answer": None, "reason": "insufficient data or no statistically clear winner"}
        )
    answers["oversaturated_topics"] = oversaturated(rows, min_n)
    answers["next_focus"] = next_focus(answers, result["by"]["category"])
    result["answers"] = answers
    return result


def oversaturated(rows: list[dict], min_n: int) -> list[dict]:
    """Keywords used often whose recent engagement dropped versus their earlier videos."""
    by_kw: dict[str, list[tuple[int, float]]] = defaultdict(list)
    for r in rows:
        if r["engagement_rate"] is None:
            continue
        for k in r["keywords"]:
            by_kw[k].append((r["publication_id"], r["engagement_rate"]))
    out = []
    for k, vals in by_kw.items():
        if len(vals) < 2 * min_n:
            continue
        vals.sort()
        half = len(vals) // 2
        early = statistics.median(v for _, v in vals[:half])
        late = statistics.median(v for _, v in vals[half:])
        if early and late < early * 0.75:
            out.append({"keyword": k, "videos": len(vals), "early_median": round(early, 5),
                        "late_median": round(late, 5)})
    return out


def next_focus(answers: dict, categories: list[dict]) -> str:
    parts = []
    if answers["best_topics"].get("answer"):
        parts.append(f"topics around '{answers['best_topics']['answer']}'")
    best_cat = _best(categories)
    if best_cat:
        parts.append(f"category {best_cat['group']}")
    if answers["best_hooks"].get("answer"):
        parts.append(f"{answers['best_hooks']['answer']} hooks")
    if answers["best_durations"].get("answer"):
        parts.append(f"{answers['best_durations']['answer']}s videos")
    if not parts:
        return ("Not enough published videos with metrics for a data-backed recommendation; "
                "keep the balanced category mix and keep collecting data.")
    return "Prioritise " + ", ".join(parts) + "."


def weekly_report(session: Session) -> Report:
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=7)
    week = analyse(session, days=7)
    all_time = analyse(session)
    rows = dataset(session, start)
    totals = {m: (sum(r[m] for r in rows if r[m] is not None) if any(r[m] is not None for r in rows) else None)
              for m in ("views", "likes", "comments", "shares", "saves", "followers_gained")}
    top = sorted((r for r in rows if r["views"] is not None), key=lambda r: r["views"], reverse=True)[:5]
    recs = []
    if week["videos_with_metrics"] < week["min_sample_size"]:
        recs.append(f"Only {week['videos_with_metrics']} videos with metrics this week; conclusions use all-time data.")
    recs.append(all_time["answers"]["next_focus"])
    for o in all_time["answers"]["oversaturated_topics"]:
        recs.append(f"Rest the '{o['keyword']}' angle: engagement fell from {o['early_median']:.2%} to {o['late_median']:.2%}.")
    body = {"period": {"start": start.isoformat(), "end": end.isoformat()}, "totals_week": totals,
            "top_videos_week": [{k: r[k] for k in ("title", "platform", "views", "engagement_rate")} for r in top],
            "week": week, "all_time": all_time, "by_category": all_time["by_category"],
            "recommendations": recs}
    report = Report(kind="weekly", period_start=start, period_end=end, body=body)
    session.add(report)
    session.flush()
    return report


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
