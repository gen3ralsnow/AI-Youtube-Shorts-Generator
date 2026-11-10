"""Content-opportunity scoring (0-100).

Each signal is normalised to 0..1. A signal that could not be measured (e.g. no
trends provider) is *excluded* and the remaining weights are renormalised; it is
never treated as zero. The breakdown records which signals were missing.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timezone

from ..enums import Category, Classification

WEIGHTS = {
    "audience_interest": 0.20,
    "relevance": 0.10,
    "novelty": 0.15,
    "discussion_potential": 0.10,
    "entertainment_value": 0.10,
    "search_demand": 0.15,
    "low_saturation": 0.10,
    "timeliness": 0.10,
}

ENTERTAINMENT_BY_CATEGORY = {
    Category.DISCOVERIES: 0.75,
    Category.NEWS: 0.55,
    Category.THEORIES: 0.8,
    Category.ENTERTAINMENT: 0.9,
    Category.VERSUS: 0.7,
}


@dataclass
class Signals:
    views: list[int] = field(default_factory=list)
    likes: list[int] = field(default_factory=list)
    comments: list[int] = field(default_factory=list)
    reddit_scores: list[int] = field(default_factory=list)
    newest: datetime | None = None
    relevance: float = 0.0
    max_similarity_to_past: float = 0.0
    search_interest: float | None = None  # 0-100 from trends provider
    recent_competitor_videos: int | None = None
    source_count: int = 0


def _log_norm(value: float, scale: float) -> float:
    """Map counts onto 0..1 with diminishing returns (scale ~ value that maps to ~0.75)."""
    if value <= 0:
        return 0.0
    return min(1.0, math.log10(1 + value) / math.log10(1 + scale) * 0.75)


def compute(signals: Signals, category: Category, classification: Classification,
            now: datetime | None = None) -> tuple[float, dict]:
    now = now or datetime.now(timezone.utc)
    values: dict[str, float | None] = {}

    interest_parts = []
    if signals.views:
        interest_parts.append(_log_norm(max(signals.views), 500_000))
    if signals.reddit_scores:
        interest_parts.append(_log_norm(max(signals.reddit_scores), 5_000))
    if signals.source_count:
        interest_parts.append(min(1.0, signals.source_count / 6))
    values["audience_interest"] = max(interest_parts) if interest_parts else None

    values["relevance"] = signals.relevance
    values["novelty"] = max(0.0, 1.0 - signals.max_similarity_to_past)

    if signals.comments:
        total_comments = sum(signals.comments)
        base = _log_norm(total_comments, 2_000)
        if signals.views and sum(signals.views):
            ratio = total_comments / max(1, sum(signals.views))
            base = 0.6 * base + 0.4 * min(1.0, ratio / 0.01)  # 1% comment rate is strong
        values["discussion_potential"] = base
    else:
        values["discussion_potential"] = None

    values["entertainment_value"] = ENTERTAINMENT_BY_CATEGORY.get(category, 0.6)
    values["search_demand"] = (
        None if signals.search_interest is None else min(1.0, signals.search_interest / 100)
    )
    values["low_saturation"] = (
        None
        if signals.recent_competitor_videos is None
        else max(0.0, 1.0 - _log_norm(signals.recent_competitor_videos, 400) / 0.75 * 0.9)
    )
    if signals.newest:
        age_h = max(0.0, (now - signals.newest).total_seconds() / 3600)
        values["timeliness"] = math.exp(-age_h / 48)  # half-life ~33h
    else:
        values["timeliness"] = None

    present = {k: v for k, v in values.items() if v is not None}
    weight_sum = sum(WEIGHTS[k] for k in present)
    score = 100 * sum(WEIGHTS[k] * v for k, v in present.items()) / weight_sum if weight_sum else 0.0

    adjustments = []
    if classification == Classification.DEBUNKED:
        score *= 0.6
        adjustments.append("debunked: only usable as a debunking video (x0.6)")
    if signals.relevance < 0.3:
        score *= 0.5
        adjustments.append("weak GTA VI relevance (x0.5)")

    breakdown = {
        "signals": {k: (None if v is None else round(v, 3)) for k, v in values.items()},
        "missing_signals": [k for k, v in values.items() if v is None],
        "weights": WEIGHTS,
        "adjustments": adjustments,
    }
    return round(score, 1), breakdown


def urgency(category: Category, classification: Classification, timeliness: float | None) -> str:
    if timeliness is None:
        return "normal"
    if category == Category.NEWS and classification in (Classification.OFFICIAL, Classification.CREDIBLE):
        return "urgent" if timeliness > 0.6 else "high"
    if timeliness > 0.8:
        return "high"
    return "low" if timeliness < 0.2 else "normal"


def recommended_length(category: Category, source_text_words: int) -> str:
    if category == Category.NEWS and source_text_words < 250:
        return "15-25"
    if category in (Category.THEORIES, Category.ENTERTAINMENT) and source_text_words > 300:
        return "40-60"
    if source_text_words < 120:
        return "15-25"
    return "25-40"
