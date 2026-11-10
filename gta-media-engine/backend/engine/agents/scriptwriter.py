"""Agent 4: Scriptwriter - short-form scripts with hook variants, scenes and sourced claims.

Two generators share one validator:
* model-backed (Anthropic/OpenAI) with a strict JSON contract, and
* rule-based (offline), which only re-uses sentences from the attached sources
  with attribution, so it cannot invent facts.
"""

from __future__ import annotations

import re
from collections import Counter

from pydantic import BaseModel, Field
from sqlalchemy import select

from .. import settings_store
from ..enums import Category, Classification
from ..models import ContentJob, Script, SourceItem, Topic
from ..providers.http import ProviderError
from ..providers.llm import get_llm, metered_complete
from ..research.classify import DISCLAIMERS
from ..research.text import keyset, normalize, sentences, word_count
from .base import Agent, AgentBlocked, AgentContext
from .research import hook_candidates, subject_from_title

BUCKETS = {"15-25": (15, 25), "25-40": (25, 40), "40-60": (40, 60)}

GENERIC_PHRASES = [
    "in today's video", "in this video", "let's dive in", "dive into", "without further ado",
    "buckle up", "game-changer", "game changer", "in conclusion", "it's important to note",
    "delve", "tapestry", "in the ever-evolving", "whether you're a", "stay tuned",
    "hey guys", "what's up guys", "smash that like", "don't forget to like",
]
CERTAINTY_WORDS = ["confirmed", "officially", "it's official", "rockstar announced", "rockstar revealed"]
NEGATIONS = ["not ", "n't", "unconfirmed", "no official", "yet to", "never"]
CURIOSITY = ["missed", "hidden", "secret", "actually", "nobody", "why", "truth", "detail", "real"]

ENDINGS = {
    Category.DISCOVERIES: "Did you spot this before? Tell me in the comments.",
    Category.NEWS: "Does this change what you expect from GTA 6?",
    Category.THEORIES: "Do you buy this theory? Tell me why in the comments.",
    Category.ENTERTAINMENT: "Which one surprised you most?",
    Category.VERSUS: "Which game does it better? Comment below.",
}
CTA = "Follow for daily GTA 6 coverage."
FACTUAL = {Category.NEWS, Category.DISCOVERIES, Category.VERSUS}


class Claim(BaseModel):
    text: str
    source_ids: list[int] = Field(default_factory=list)


class Visual(BaseModel):
    keywords: list[str] = Field(default_factory=list)
    description: str = ""
    prefer_official: bool = False


class Scene(BaseModel):
    index: int
    section: str  # hook | intro | main | ending
    narration: str
    caption: str = ""
    visual: Visual = Field(default_factory=Visual)
    sfx: str | None = None
    transition: str = "fade"
    start: float = 0.0
    end: float = 0.0


class ScriptDraft(BaseModel):
    title: str
    category: str
    classification: str
    hooks: list[str]
    chosen_hook: str
    sections: dict
    scenes: list[Scene]
    voiceover_text: str
    captions: list[str]
    claims: list[Claim]
    sources: list[dict]
    call_to_action: str
    estimated_seconds: float
    duration_bucket: str
    generator: str


class ScriptInput(BaseModel):
    job_id: int
    feedback: list[str] = Field(default_factory=list)


class ScriptOutput(BaseModel):
    script_id: int
    version: int
    generator: str
    estimated_seconds: float
    duration_bucket: str
    hook: str


# ------------------------------------------------------------------ hook selection


def score_hook(hook: str, classification: str) -> float:
    words = word_count(hook)
    low = normalize(hook)
    score = 0.0
    score += 3 if 5 <= words <= 11 else (1 if words <= 15 else -3)
    score += 1 if "?" in hook else 0
    score += 1 if re.search(r"\d", hook) or re.search(r"\s[A-Z][a-z]+", hook) else 0
    score += sum(0.5 for w in CURIOSITY if w in low)
    score -= sum(3 for p in GENERIC_PHRASES if p in low)
    if classification != Classification.OFFICIAL.value and _asserts_certainty(hook):
        score -= 100  # disqualified: false certainty
    return score


def choose_hook(hooks: list[str], classification: str) -> str:
    return max(hooks, key=lambda h: score_hook(h, classification))


def _asserts_certainty(text: str) -> bool:
    low = text.lower()
    for sent in sentences(low) or [low]:
        if any(w in sent for w in CERTAINTY_WORDS) and not any(n in sent for n in NEGATIONS):
            return True
    return False


# ------------------------------------------------------------------ validation


def estimate_seconds(text: str, wpm: float) -> float:
    return round(word_count(text) / wpm * 60 + 0.6, 2)


def fit_bucket(seconds: float) -> str | None:
    for name, (lo, hi) in BUCKETS.items():
        if seconds <= hi:
            return name
    return None


def validate_script(draft: ScriptDraft, allowed_source_ids: set[int], wpm: float) -> dict:
    errors: list[str] = []
    warnings: list[str] = []
    text = draft.voiceover_text
    low = normalize(text)

    for phrase in GENERIC_PHRASES:
        if phrase in low:
            errors.append(f"generic phrasing: '{phrase}'")

    sents = sentences(text)
    for s in sents:
        if word_count(s) > 28:
            errors.append(f"sentence too long ({word_count(s)} words): '{s[:60]}...'")
    if sents and sum(word_count(s) for s in sents) / len(sents) > 18:
        warnings.append("average sentence length above 18 words")
    dupes = [s for s, n in Counter(x.lower() for x in sents).items() if n > 1]
    if dupes:
        errors.append(f"repeated sentence: '{dupes[0][:60]}'")
    trigrams = Counter(zip(low.split(), low.split()[1:], low.split()[2:]))
    if any(n > 2 for n in trigrams.values()):
        warnings.append("repetitive phrasing (a 3-word sequence appears 3+ times)")

    hook_words = word_count(draft.chosen_hook)
    if hook_words > 16:
        errors.append(f"hook too long for 0-3s ({hook_words} words)")
    elif hook_words > 10:
        warnings.append(f"hook is {hook_words} words; may exceed 3 seconds")

    seconds = estimate_seconds(text, wpm)
    bucket = fit_bucket(seconds)
    if seconds < 12:
        errors.append(f"script too thin ({seconds}s); not enough sourced material")
    if bucket is None:
        errors.append(f"script too long ({seconds}s > 60s)")

    disclaimer = DISCLAIMERS.get(Classification(draft.classification), "")
    if disclaimer and not any(k in low for k in ("theory", "rumour", "rumor", "not confirmed",
                                                  "unconfirmed", "false", "isn't true", "not true",
                                                  "speculation")):
        errors.append("missing speculation/rumour/debunk disclaimer")
    if draft.classification != Classification.OFFICIAL.value and _asserts_certainty(text):
        errors.append("asserts official confirmation without an official source")

    for claim in draft.claims:
        if not claim.source_ids:
            errors.append(f"claim without source: '{claim.text[:60]}'")
        elif not set(claim.source_ids) <= allowed_source_ids:
            errors.append(f"claim cites unknown source ids {claim.source_ids}")
    if Category(draft.category) in FACTUAL and not draft.claims:
        errors.append("factual video with no sourced claims")
    if not draft.sources:
        errors.append("no source references attached")

    return {"passed": not errors, "errors": errors, "warnings": warnings,
            "estimated_seconds": seconds, "fitted_bucket": bucket}


# ------------------------------------------------------------------ generators


def _scene_keywords(text: str, topic: Topic) -> list[str]:
    keys = keyset(text) | set(topic.keywords or [])
    return sorted(keys)[:12]


def _assemble(topic: Topic, hooks: list[str], intro: str, main: list[str], ending: str,
              claims: list[Claim], generator: str, wpm: float, captions: list[str] | None = None) -> ScriptDraft:
    cat = Category(topic.category)
    chosen = choose_hook(hooks, topic.classification)
    parts = [("hook", chosen), ("intro", intro)] + [("main", m) for m in main] + [("ending", ending)]
    scenes, t = [], 0.0
    for idx, (section, narration) in enumerate(parts):
        dur = estimate_seconds(narration, wpm) - 0.4
        caption = (captions[idx] if captions and idx < len(captions) else _caption(narration))
        scenes.append(Scene(
            index=idx, section=section, narration=narration, caption=caption,
            visual=Visual(keywords=_scene_keywords(narration, topic),
                          description=f"{section} visual for: {narration[:80]}",
                          prefer_official=cat == Category.NEWS),
            sfx={"hook": "impact", "ending": "riser"}.get(section, "swipe" if section == "main" else None),
            transition="cut" if section == "hook" else "fade",
            start=round(t, 2), end=round(t + dur, 2),
        ))
        t += dur
    voiceover = " ".join(n for _, n in parts)
    sources = [{"id": s.id, "title": s.title, "url": s.url, "credibility": s.credibility}
               for s in topic.sources]
    seconds = estimate_seconds(voiceover, wpm)
    return ScriptDraft(
        title=topic.title[:100], category=topic.category, classification=topic.classification,
        hooks=hooks, chosen_hook=chosen,
        sections={"hook": chosen, "intro": intro, "main": main, "ending": ending},
        scenes=scenes, voiceover_text=voiceover, captions=[s.caption for s in scenes],
        claims=claims, sources=sources, call_to_action=CTA, estimated_seconds=seconds,
        duration_bucket=fit_bucket(seconds) or "40-60", generator=generator,
    )


def _caption(text: str, max_words: int = 4) -> str:
    keys = [w for w in re.findall(r"[A-Za-z0-9']+", text) if w.lower() not in {"the", "a", "and", "of", "to", "is"}]
    return " ".join(keys[:max_words]).upper()


def rule_based_draft(topic: Topic, bucket: str, wpm: float) -> ScriptDraft:
    cat, classification = Category(topic.category), Classification(topic.classification)
    subject = subject_from_title(topic.title)
    hooks = hook_candidates(subject, cat, classification)
    topic_keys = keyset(topic.title) | set(topic.keywords or [])
    budget_facts = {"15-25": 2, "25-40": 3, "40-60": 5}.get(bucket, 3)

    facts: list[tuple[str, SourceItem]] = []
    seen: set[str] = set()
    for src in sorted(topic.sources, key=lambda s: {"official": 0, "credible": 1}.get(s.credibility, 2)):
        for sent in sentences(f"{src.title}. {src.summary}"):
            sent = re.sub(r"https?://\S+|#\w+", "", sent).strip()
            wc = word_count(sent)
            if not 6 <= wc <= 24 or sent.lower() in seen:
                continue
            if len(keyset(sent) & topic_keys) < 1:
                continue
            seen.add(sent.lower())
            facts.append((sent if sent[-1] in ".!?" else sent + ".", src))
            if len(facts) >= budget_facts:
                break
        if len(facts) >= budget_facts:
            break

    main, claims = [], []
    for i, (sent, src) in enumerate(facts):
        who = src.author or src.domain or src.connector
        line = f"According to {who}, {sent[0].lower() + sent[1:]}" if i == 0 else sent
        main.append(line)
        claims.append(Claim(text=sent, source_ids=[src.id]))
    disclaimer = DISCLAIMERS.get(classification, "")
    intro = (disclaimer + " Here's what the sources say.").strip() if disclaimer else "Here's what we know so far."
    return _assemble(topic, hooks, intro, main, ENDINGS[cat], claims, "rule-based", wpm)


SCRIPT_SYSTEM = """You write short-form vertical video scripts for a GTA VI news and discovery channel.
Rules:
- Use ONLY facts in the supplied sources. Never invent features, dates, quotes, gameplay or statements.
- Every factual sentence must appear in "claims" with the ids of the sources that support it.
- If the classification is community_theory or unverified_rumour, say so plainly in the intro.
- Never say "confirmed" or "official" unless the classification is officially_confirmed.
- Natural, conversational, short sentences (<= 16 words). No filler, no generic AI phrasing,
  no "in today's video", no "let's dive in". Do not pad to reach a length.
- Structure: hook (<= 10 words, 0-3s), intro (3-8s), main beats, ending question."""


def _script_prompt(topic: Topic, bucket: str, feedback: list[str]) -> str:
    lines = [f"Topic: {topic.title}", f"Explanation: {topic.explanation}",
             f"Category: {topic.category}", f"Classification (fixed): {topic.classification}",
             f"Suggested angle: {topic.angle}", f"Target length: {bucket} seconds (shorter is fine)",
             "Sources:"]
    for s in topic.sources[:10]:
        lines.append(f"[id={s.id}] ({s.credibility}) {s.title}\n{s.summary[:700]}")
    if feedback:
        lines.append("Fix these problems from the previous attempt: " + "; ".join(feedback))
    lines.append(
        'Return JSON: {"hooks": [5 strings], "intro": str, "main": [str], "ending": str, '
        '"captions": [short on-screen text per beat incl. hook,intro,main...,ending], '
        '"claims": [{"text": str, "source_ids": [int]}]}'
    )
    return "\n".join(lines)


class ScriptwriterAgent(Agent[ScriptInput, ScriptOutput]):
    name = "scriptwriter"
    responsibility = "Write retention-focused short scripts with hook variants, scenes and sourced claims."
    input_model, output_model = ScriptInput, ScriptOutput
    max_attempts = 2

    def execute(self, ctx: AgentContext, payload: ScriptInput) -> ScriptOutput:
        s = ctx.session
        job = s.get(ContentJob, payload.job_id)
        if job is None or job.topic is None:
            raise AgentBlocked("job has no topic")
        topic = job.topic
        if not topic.sources:
            raise AgentBlocked("topic has no sources; refusing to write a factual script")
        wpm = float(settings_store.get(s, "processing")["words_per_minute"])
        llm = get_llm()
        allowed = {src.id for src in topic.sources}

        draft: ScriptDraft
        if llm.available:
            feedback = list(payload.feedback)
            draft = None  # type: ignore[assignment]
            for _ in range(2):
                res = metered_complete(s, llm, SCRIPT_SYSTEM, _script_prompt(topic, job.duration_bucket, feedback),
                                       max_tokens=1500, job_id=job.id, purpose="script")
                d = res.data
                try:
                    hooks = [str(h) for h in d.get("hooks", [])][:5] or hook_candidates(
                        subject_from_title(topic.title), Category(topic.category),
                        Classification(topic.classification))
                    claims = [Claim.model_validate(c) for c in d.get("claims", [])]
                    draft = _assemble(topic, hooks, str(d["intro"]), [str(m) for m in d["main"]],
                                      str(d["ending"]), claims, f"{res.provider}:{res.model}", wpm,
                                      captions=[str(c) for c in d.get("captions", [])] or None)
                except (KeyError, TypeError, ValueError) as exc:
                    raise ProviderError(f"script JSON missing fields: {exc}", transient=True) from exc
                check = validate_script(draft, allowed, wpm)
                if check["passed"]:
                    break
                feedback = check["errors"]
        else:
            draft = rule_based_draft(topic, job.duration_bucket, wpm)

        version = (s.scalar(select(Script.version).where(Script.job_id == job.id)
                            .order_by(Script.version.desc())) or 0) + 1
        row = Script(
            job_id=job.id, version=version, title=job.title_override or draft.title,
            category=draft.category, hooks=draft.hooks, chosen_hook=draft.chosen_hook,
            sections=draft.sections, scenes=[sc.model_dump() for sc in draft.scenes],
            voiceover_text=draft.voiceover_text, captions=draft.captions,
            claims=[c.model_dump() for c in draft.claims], sources=draft.sources,
            call_to_action=draft.call_to_action, estimated_seconds=draft.estimated_seconds,
            generator=draft.generator,
        )
        s.add(row)
        job.duration_bucket = draft.duration_bucket
        s.flush()
        return ScriptOutput(script_id=row.id, version=version, generator=draft.generator,
                            estimated_seconds=draft.estimated_seconds,
                            duration_bucket=draft.duration_bucket, hook=draft.chosen_hook)


def latest_script(session, job_id: int) -> Script | None:
    return session.scalar(select(Script).where(Script.job_id == job_id).order_by(Script.version.desc()))


def draft_from_row(row: Script, classification: str) -> ScriptDraft:
    return ScriptDraft(
        title=row.title, category=row.category, classification=classification, hooks=row.hooks,
        chosen_hook=row.chosen_hook, sections=row.sections,
        scenes=[Scene.model_validate(x) for x in row.scenes], voiceover_text=row.voiceover_text,
        captions=row.captions, claims=[Claim.model_validate(c) for c in row.claims],
        sources=row.sources, call_to_action=row.call_to_action,
        estimated_seconds=row.estimated_seconds, duration_bucket=fit_bucket(row.estimated_seconds) or "40-60",
        generator=row.generator,
    )
