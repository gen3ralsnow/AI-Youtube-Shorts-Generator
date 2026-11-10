"""Agent 1 (Research) and Agent 2 (Trend Analyst)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from pydantic import BaseModel, Field
from sqlalchemy import select

from .. import settings_store
from ..enums import Category, Classification, TopicStatus
from ..models import SourceItem, Topic, TopicSource
from ..providers.http import NotConfigured, ProviderError
from ..providers.llm import get_llm, metered_complete
from ..research import classify as cls
from ..research import scoring
from ..research.connectors import (
    Connector,
    RawItem,
    TrendsProvider,
    YouTubeConnector,
    all_connectors,
)
from ..research.text import fingerprint, jaccard, keyset, relevance
from .base import Agent, AgentContext

# --------------------------------------------------------------------------- Agent 1


class ResearchInput(BaseModel):
    query: str = "GTA 6"
    connectors: list[str] | None = None


class ResearchOutput(BaseModel):
    fetched: int = 0
    new_items: int = 0
    irrelevant_skipped: int = 0
    errors: dict[str, str] = Field(default_factory=dict)
    unconfigured: list[str] = Field(default_factory=list)


class ResearchAgent(Agent[ResearchInput, ResearchOutput]):
    name = "research"
    responsibility = "Find current GTA VI topics and supporting sources from permitted APIs and feeds."
    input_model, output_model = ResearchInput, ResearchOutput
    max_attempts = 1  # connectors retry internally; one failing source must not block others

    def __init__(self, connectors: list[Connector] | None = None):
        super().__init__()
        self.connectors = connectors

    def execute(self, ctx: AgentContext, payload: ResearchInput) -> ResearchOutput:
        out = ResearchOutput()
        connectors = self.connectors if self.connectors is not None else all_connectors()
        for connector in connectors:
            if payload.connectors and connector.name not in payload.connectors:
                continue
            if not connector.configured():
                out.unconfigured.append(connector.name)
                continue
            try:
                items = connector.fetch(ctx.session, payload.query)
            except (NotConfigured, ProviderError) as exc:
                out.errors[connector.name] = str(exc)[:300]
                continue
            out.fetched += len(items)
            for item in items:
                if relevance(f"{item.title} {item.summary}") < 0.25:
                    out.irrelevant_skipped += 1
                    continue
                if self._store(ctx, item):
                    out.new_items += 1
        return out

    @staticmethod
    def _store(ctx: AgentContext, item: RawItem) -> bool:
        existing = ctx.session.scalar(
            select(SourceItem).where(
                SourceItem.connector == item.connector, SourceItem.external_id == item.external_id
            )
        )
        credibility = cls.source_credibility(item.domain, item.connector, item.raw.get("channel_id"))
        if existing:
            existing.engagement = item.engagement or existing.engagement
            existing.credibility = credibility
            return False
        ctx.session.add(
            SourceItem(
                connector=item.connector, external_id=item.external_id, url=item.url,
                domain=item.domain, title=item.title, summary=item.summary, author=item.author,
                published_at=item.published_at, credibility=credibility,
                engagement=item.engagement, raw=item.raw,
            )
        )
        ctx.session.flush()
        return True


# --------------------------------------------------------------------------- Agent 2


class TrendInput(BaseModel):
    since_hours: int = 96
    cluster_threshold: float = 0.3
    use_saturation_search: bool = False  # costs 100 YouTube quota units per topic


class TopicReport(BaseModel):
    topic_id: int
    title: str
    score: float
    classification: str
    category: str
    urgency: str
    duplicate_of: int | None = None


class TrendOutput(BaseModel):
    clusters: int = 0
    topics_created: int = 0
    duplicates: int = 0
    reports: list[TopicReport] = Field(default_factory=list)


ANGLE_TEMPLATES = {
    Category.DISCOVERIES: "Walk through the detail with the footage, then explain why it matters for the game.",
    Category.NEWS: "State exactly what was announced or reported, by whom, and what it changes.",
    Category.THEORIES: "Present the theory, the evidence fans cite, and what is still unknown. Label it speculation.",
    Category.ENTERTAINMENT: "Fast-paced list with one strong visual per beat.",
    Category.VERSUS: "Side-by-side comparison on one system, using only verifiable footage of both games.",
}


def hook_candidates(subject: str, category: Category, classification: Classification) -> list[str]:
    """Honest, source-agnostic hook templates. Never asserts confirmation it lacks."""
    subject = subject.strip().rstrip(".?!")
    hooks: list[str] = []
    if classification == Classification.DEBUNKED:
        hooks += [f"That viral GTA 6 claim about {subject}? It's not true.",
                  f"Stop sharing this GTA 6 {subject} claim. Here's why."]
    elif classification == Classification.OFFICIAL:
        hooks += [f"Rockstar just made it official: {subject}.",
                  f"{subject}: here's exactly what Rockstar confirmed."]
    elif classification == Classification.CREDIBLE:
        hooks += [f"New GTA 6 report: {subject}. Here are the facts.",
                  f"{subject}: what's actually being reported."]
    elif classification == Classification.THEORY:
        hooks += [f"There's a GTA 6 theory about {subject}, and the evidence is interesting.",
                  f"Fans think GTA 6 is hiding something about {subject}."]
    else:
        hooks += [f"A GTA 6 rumour about {subject} is spreading. Here's what we actually know.",
                  f"Is the {subject} rumour real? Let's check."]
    if category == Category.DISCOVERIES:
        hooks.append(f"Did you catch this {subject} detail in GTA 6?")
    if category == Category.VERSUS:
        hooks.append(f"GTA 6 vs GTA V: {subject}. The difference is huge.")
    return hooks


def subject_from_title(title: str, max_words: int = 7) -> str:
    import re

    t = re.sub(r"\[[^\]]*\]|\([^)]*\)|#\w+", "", title)
    t = re.sub(r"(?i)\b(gta\s*(6|vi)|grand theft auto (6|vi))\b[:\-\s]*", "", t).strip(" -:|")
    words = t.split()
    return " ".join(words[:max_words]) if words else "GTA 6"


class TrendAnalystAgent(Agent[TrendInput, TrendOutput]):
    name = "trend_analyst"
    responsibility = "Cluster research into topics, classify accuracy, score demand 0-100, reject duplicates."
    input_model, output_model = TrendInput, TrendOutput
    max_attempts = 1

    def execute(self, ctx: AgentContext, payload: TrendInput) -> TrendOutput:
        s = ctx.session
        since = datetime.now(timezone.utc) - timedelta(hours=payload.since_hours)
        assigned = select(TopicSource.source_id)
        items = list(
            s.scalars(
                select(SourceItem).where(
                    SourceItem.fetched_at >= since, SourceItem.id.not_in(assigned)
                )
            )
        )
        clusters = self._cluster(items, payload.cluster_threshold)
        out = TrendOutput(clusters=len(clusters))
        processing = settings_store.get(s, "processing")
        dup_threshold = processing["duplicate_similarity_threshold"]
        past = list(s.scalars(select(Topic).where(Topic.status != TopicStatus.DUPLICATE.value)))
        trends = TrendsProvider()
        yt = YouTubeConnector()
        llm = get_llm()

        for cluster in clusters:
            lead = max(cluster, key=_engagement_rank)
            text = " ".join(f"{i.title}. {i.summary}" for i in cluster)
            keys = keyset(" ".join(i.title for i in cluster))
            similar = max(((jaccard(keys, set(t.fingerprint.split())), t) for t in past),
                          default=(0.0, None), key=lambda x: x[0])
            category = cls.infer_category(text)
            classification = cls.classify([text], [i.credibility for i in cluster])
            category = cls.compatible(category, classification)
            subject = subject_from_title(lead.title)

            signals = scoring.Signals(
                views=[v for i in cluster if (v := i.engagement.get("views")) is not None],
                likes=[v for i in cluster if (v := i.engagement.get("likes")) is not None],
                comments=[v for i in cluster if (v := i.engagement.get("comments")) is not None],
                reddit_scores=[v for i in cluster if (v := i.engagement.get("score")) is not None],
                newest=max((_aware(i.published_at) for i in cluster if i.published_at), default=None),
                relevance=relevance(text),
                max_similarity_to_past=similar[0],
                source_count=len({i.domain or i.connector for i in cluster}),
            )
            try:
                signals.search_interest = trends.interest(s, "GTA 6 " + subject)
            except ProviderError as exc:
                self.log.warning("trends unavailable: %s", exc)
            if payload.use_saturation_search:
                try:
                    signals.recent_competitor_videos = yt.count_recent_videos(s, "GTA 6 " + subject)
                except ProviderError as exc:
                    self.log.warning("saturation search unavailable: %s", exc)

            score, breakdown = scoring.compute(signals, category, classification)
            hooks = hook_candidates(subject, category, classification)
            title, explanation, angle = lead.title.strip(), _explain(cluster), ANGLE_TEMPLATES[category]
            generator = "rule-based"
            if llm.available:
                try:
                    res = metered_complete(
                        s, llm, TOPIC_SYSTEM, _topic_prompt(cluster, classification, category),
                        max_tokens=600, purpose="topic_report",
                    )
                    title = str(res.data.get("title") or title)[:200]
                    explanation = str(res.data.get("explanation") or explanation)[:1200]
                    angle = str(res.data.get("angle") or angle)[:500]
                    hooks = [str(h) for h in res.data.get("hooks", [])][:5] or hooks
                    generator = f"{res.provider}:{res.model}"
                except ProviderError as exc:
                    self.log.warning("LLM topic summary failed, using rule-based: %s", exc)

            is_dup = similar[0] >= dup_threshold and similar[1] is not None
            source_words = sum(len(f"{i.title} {i.summary}".split()) for i in cluster)
            topic = Topic(
                title=title,
                explanation=explanation,
                category=category.value,
                classification=classification.value,
                score=score,
                score_breakdown={**breakdown, "generator": generator,
                                 "similar_topic_id": similar[1].id if similar[1] else None,
                                 "similarity": round(similar[0], 3)},
                angle=angle,
                hook=hooks[0] if hooks else "",
                recommended_length=scoring.recommended_length(category, source_words),
                urgency=scoring.urgency(category, classification, breakdown["signals"]["timeliness"]),
                evergreen=category in (Category.VERSUS, Category.ENTERTAINMENT),
                status=TopicStatus.DUPLICATE.value if is_dup else TopicStatus.NEW.value,
                fingerprint=fingerprint(" ".join(i.title for i in cluster)),
                keywords=sorted(keys)[:20],
            )
            topic.sources = list(cluster)
            s.add(topic)
            s.flush()
            if is_dup:
                out.duplicates += 1
            else:
                out.topics_created += 1
                past.append(topic)
            out.reports.append(TopicReport(
                topic_id=topic.id, title=topic.title, score=topic.score,
                classification=topic.classification, category=topic.category,
                urgency=topic.urgency, duplicate_of=similar[1].id if is_dup and similar[1] else None,
            ))
        return out

    @staticmethod
    def _cluster(items: list[SourceItem], threshold: float) -> list[list[SourceItem]]:
        clusters: list[tuple[set[str], list[SourceItem]]] = []
        for item in sorted(items, key=_engagement_rank, reverse=True):
            keys = keyset(item.title)
            best, best_sim = None, 0.0
            for idx, (ckeys, _) in enumerate(clusters):
                sim = jaccard(keys, ckeys)
                if sim > best_sim:
                    best, best_sim = idx, sim
            if best is not None and best_sim >= threshold:
                clusters[best][0].update(keys)
                clusters[best][1].append(item)
            else:
                clusters.append((set(keys), [item]))
        return [members for _, members in clusters]


TOPIC_SYSTEM = (
    "You are a meticulous gaming-news research editor for a GTA VI channel. "
    "Use ONLY the supplied sources. Never invent features, quotes, dates or announcements. "
    "If sources disagree or are speculative, say so."
)


def _topic_prompt(cluster: list[SourceItem], classification: Classification, category: Category) -> str:
    lines = [f"Accuracy classification (fixed, do not change): {classification.value}",
             f"Content category: {category.value}", "Sources:"]
    for i in cluster[:8]:
        lines.append(f"- [{i.credibility}] {i.title} ({i.url})\n  {i.summary[:600]}")
    lines.append(
        'Return JSON: {"title": str (<=90 chars, factual), "explanation": str (2-3 sentences, '
        'sources only), "angle": str, "hooks": [5 short hooks, honest, no false certainty]}'
    )
    return "\n".join(lines)


def _explain(cluster: list[SourceItem]) -> str:
    parts = []
    for i in cluster[:3]:
        bit = (i.summary or i.title).strip().split("\n")[0][:240]
        if bit:
            parts.append(bit)
    return " ".join(parts)


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _engagement_rank(item: SourceItem) -> float:
    e = item.engagement or {}
    cred = {"official": 3.0, "credible": 2.0}.get(item.credibility, 1.0)
    return cred * 1e9 + (e.get("views") or 0) + 50 * (e.get("score") or 0) + 20 * (e.get("comments") or 0)
