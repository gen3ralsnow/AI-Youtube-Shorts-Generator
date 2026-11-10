"""Agent 5: Fact Checker.

``mode="topic"`` (before writing): the topic has sources, and its classification
permits the planned category.
``mode="script"`` (after writing): every claim is checked against the text of
the sources it cites. Lexical support is computed locally; when a model is
configured it also judges each claim and can only *lower* the verdict for a
claim that has no lexical support. Confidence = supported / total claims.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from .. import settings_store
from ..enums import Category, Classification
from ..models import ContentJob, SourceItem
from ..providers.http import ProviderError
from ..providers.llm import get_llm, metered_complete
from ..research.text import keyset
from .base import Agent, AgentBlocked, AgentContext
from .scriptwriter import latest_script


class FactInput(BaseModel):
    job_id: int
    mode: str = Field("script", pattern="^(topic|script)$")


class ClaimVerdict(BaseModel):
    text: str
    source_ids: list[int]
    lexical_support: float
    verdict: str  # supported | unsupported | contradicted
    model_verdict: str | None = None
    note: str = ""


class FactOutput(BaseModel):
    mode: str
    passed: bool
    confidence: float
    threshold: float
    classification: str
    verdicts: list[ClaimVerdict] = Field(default_factory=list)
    issues: list[str] = Field(default_factory=list)
    checker: str = "lexical"


def lexical_support(claim: str, sources: list[SourceItem]) -> float:
    claim_keys = keyset(claim)
    if not claim_keys:
        return 0.0
    corpus = set()
    for s in sources:
        corpus |= keyset(f"{s.title} {s.summary}")
    return round(len(claim_keys & corpus) / len(claim_keys), 3)


FACT_SYSTEM = (
    "You are a strict fact checker. For each claim decide, using ONLY the quoted sources, whether it is "
    "'supported', 'unsupported' (sources do not say it) or 'contradicted'. Paraphrase is fine; "
    "added specifics (numbers, dates, names) not in the sources make a claim unsupported."
)


class FactCheckerAgent(Agent[FactInput, FactOutput]):
    name = "fact_checker"
    responsibility = "Validate claims against reliable sources; block anything below the confidence threshold."
    input_model, output_model = FactInput, FactOutput
    max_attempts = 2

    def execute(self, ctx: AgentContext, payload: FactInput) -> FactOutput:
        s = ctx.session
        job = s.get(ContentJob, payload.job_id)
        if job is None or job.topic is None:
            raise AgentBlocked("job has no topic")
        topic = job.topic
        threshold = float(settings_store.get(s, "processing")["fact_confidence_threshold"])
        classification = Classification(topic.classification)
        category = Category(job.category)

        if payload.mode == "topic":
            issues = []
            if not topic.sources:
                issues.append("no supporting sources")
            if category == Category.NEWS and classification not in (Classification.OFFICIAL, Classification.CREDIBLE):
                issues.append("news videos require official or credibly reported sources")
            if classification == Classification.DEBUNKED and "debunk" not in (topic.angle or "").lower():
                # only allowed as an explicit debunk; the scriptwriter frames it as such via hooks
                topic.angle = "Debunk the claim using the sources that show it is false."
            confidence = 1.0 if not issues else 0.0
            return FactOutput(mode="topic", passed=not issues, confidence=confidence, threshold=threshold,
                              classification=classification.value, issues=issues)

        script = latest_script(s, job.id)
        if script is None:
            raise AgentBlocked("no script to check")
        by_id = {src.id: src for src in topic.sources}
        verdicts: list[ClaimVerdict] = []
        for c in script.claims:
            cited = [by_id[i] for i in c.get("source_ids", []) if i in by_id]
            support = lexical_support(c["text"], cited) if cited else 0.0
            verdicts.append(ClaimVerdict(
                text=c["text"], source_ids=c.get("source_ids", []), lexical_support=support,
                verdict="supported" if support >= 0.6 else "unsupported",
                note="" if cited else "cites no attached source",
            ))

        checker = "lexical"
        llm = get_llm()
        if llm.available and verdicts:
            prompt = ["Sources:"] + [f"[id={src.id}] {src.title}\n{src.summary[:800]}" for src in topic.sources[:10]]
            prompt.append("Claims:")
            prompt += [f"{i}. {v.text} (cites {v.source_ids})" for i, v in enumerate(verdicts)]
            prompt.append('Return JSON: {"verdicts": [{"index": int, "verdict": "supported|unsupported|contradicted", "note": str}]}')
            try:
                res = metered_complete(s, llm, FACT_SYSTEM, "\n".join(prompt), max_tokens=800,
                                       job_id=job.id, purpose="fact_check")
                checker = f"lexical+{res.provider}:{res.model}"
                for item in res.data.get("verdicts", []):
                    idx = int(item.get("index", -1))
                    if 0 <= idx < len(verdicts):
                        v = verdicts[idx]
                        mv = str(item.get("verdict", "")).lower()
                        v.model_verdict, v.note = mv, str(item.get("note", ""))[:300]
                        if mv == "contradicted":
                            v.verdict = "contradicted"
                        elif mv == "supported" and v.lexical_support >= 0.35:
                            v.verdict = "supported"  # paraphrase with partial overlap
                        elif mv == "unsupported":
                            v.verdict = "unsupported"
            except ProviderError as exc:
                self.log.warning("model fact check unavailable, lexical only: %s", exc)

        total = len(verdicts)
        supported = sum(1 for v in verdicts if v.verdict == "supported")
        issues = [f"{v.verdict}: {v.text[:80]}" for v in verdicts if v.verdict != "supported"]
        if total == 0:
            factual = category in (Category.NEWS, Category.DISCOVERIES, Category.VERSUS)
            confidence = 0.0 if factual else 1.0
            if factual:
                issues.append("no checkable claims in a factual video")
        else:
            confidence = supported / total
        if any(v.verdict == "contradicted" for v in verdicts):
            confidence = min(confidence, threshold - 0.01)
        passed = confidence >= threshold
        script.fact_check = {"verdicts": [v.model_dump() for v in verdicts], "issues": issues, "checker": checker}
        script.confidence = round(confidence, 3)
        return FactOutput(mode="script", passed=passed, confidence=round(confidence, 3), threshold=threshold,
                          classification=classification.value, verdicts=verdicts, issues=issues,
                          checker=checker)
