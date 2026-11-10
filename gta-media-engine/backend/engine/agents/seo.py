"""Agent 8: SEO - titles, hooks, descriptions, hashtags, keywords and per-platform metadata.

Titles must be attention-grabbing but never misleading: no "official"/"confirmed"
unless the topic is officially confirmed, speculation is labelled, no ALL-CAPS
shouting, no irrelevant hashtag stuffing.
"""

from __future__ import annotations

import re

from pydantic import BaseModel
from sqlalchemy import select

from ..enums import Category, Classification
from ..models import ContentJob, MediaAsset, MetadataPackage, Render
from ..providers.http import ProviderError
from ..providers.llm import get_llm, metered_complete
from .base import Agent, AgentBlocked, AgentContext
from .research import subject_from_title
from .scriptwriter import ENDINGS, latest_script

LIMITS = {
    "youtube": {"title": 100, "description": 5000, "hashtags": 3},
    "tiktok": {"title": 2200, "description": 2200, "hashtags": 5},
    "instagram": {"title": 2200, "description": 2200, "hashtags": 5},
}
CATEGORY_TAGS = {
    Category.DISCOVERIES: ["gta6details", "gta6trailer"],
    Category.NEWS: ["gta6news", "rockstargames"],
    Category.THEORIES: ["gta6theory"],
    Category.ENTERTAINMENT: ["gaming"],
    Category.VERSUS: ["gta5", "gta6vsgta5"],
}
LABEL = {
    Classification.THEORY.value: "Theory",
    Classification.RUMOUR.value: "Rumour",
    Classification.DEBUNKED.value: "Debunked",
}
MISLEADING = re.compile(r"(?i)\b(official(ly)?|confirmed|100% real|proof)\b")


def clean_title(title: str, classification: str, limit: int) -> str:
    t = re.sub(r"\s+", " ", title).strip()
    if classification != Classification.OFFICIAL.value:
        t = MISLEADING.sub("", t)
        t = re.sub(r"\s{2,}", " ", t).strip(" -:|")
    letters = [c for c in t if c.isalpha()]
    if letters and sum(c.isupper() for c in letters) / len(letters) > 0.5:
        t = t.title().replace("Gta", "GTA")
    label = LABEL.get(classification)
    if label and label.lower() not in t.lower():
        t = f"{t} ({label})"
    return t[:limit].rstrip()


def title_variations(subject: str, category: Category, classification: str) -> list[str]:
    base = {
        Category.DISCOVERIES: [f"GTA 6 detail you missed: {subject}", f"Look closer at {subject} in GTA 6",
                               f"{subject}: the GTA 6 detail everyone missed", f"GTA 6's {subject} explained",
                               f"Small GTA 6 detail, big implications: {subject}"],
        Category.NEWS: [f"GTA 6 news: {subject}", f"{subject}: what it means for GTA 6",
                        f"GTA 6 update: {subject}", f"The facts on {subject} (GTA 6)",
                        f"{subject}: GTA 6 in 30 seconds"],
        Category.THEORIES: [f"GTA 6 theory: {subject}", f"Is GTA 6 hiding {subject}?",
                            f"The {subject} theory, explained", f"Fans think {subject} in GTA 6",
                            f"{subject}: evidence vs speculation"],
        Category.ENTERTAINMENT: [f"{subject} (GTA 6)", f"GTA 6: {subject}", f"Wait for it: {subject}",
                                 f"{subject} in GTA 6 is wild", f"The best of {subject}"],
        Category.VERSUS: [f"GTA 6 vs GTA V: {subject}", f"{subject}: GTA 6 vs GTA 5 compared",
                          f"How GTA 6 changes {subject}", f"{subject}, GTA V then and GTA 6 now",
                          f"Side by side: {subject}"],
    }[category]
    return [clean_title(t, classification, 100) for t in base]


def hashtags_for(platform: str, category: Category, keywords: list[str]) -> list[str]:
    tags = ["GTA6"] + CATEGORY_TAGS.get(category, [])
    for k in keywords:
        if len(k) > 3 and k.isalpha() and k.lower() not in {"trailer", "game", "video", "new"}:
            tags.append(k)
        if len(tags) >= 6:
            break
    if platform == "youtube":
        tags = ["Shorts"] + tags
    out, seen = [], set()
    for t in tags:
        key = t.lower()
        if key not in seen:
            seen.add(key)
            out.append("#" + re.sub(r"[^\w]", "", t))
    return out[: LIMITS[platform]["hashtags"]]


class SEOInput(BaseModel):
    job_id: int


class SEOOutput(BaseModel):
    platforms: list[str]
    titles: list[str]
    generator: str = "rule-based"


class SEOAgent(Agent[SEOInput, SEOOutput]):
    name = "seo"
    responsibility = "Create honest, platform-specific titles, descriptions, hashtags and engagement copy."
    input_model, output_model = SEOInput, SEOOutput
    max_attempts = 2

    def execute(self, ctx: AgentContext, payload: SEOInput) -> SEOOutput:
        s = ctx.session
        job = s.get(ContentJob, payload.job_id)
        script = latest_script(s, payload.job_id)
        if not job or not script or not job.topic:
            raise AgentBlocked("no script/topic for metadata")
        topic = job.topic
        category = Category(job.category)
        classification = topic.classification
        subject = subject_from_title(job.title_override or script.title)
        titles = title_variations(subject, category, classification)
        hooks = (script.hooks or [])[:3]
        generator = "rule-based"

        llm = get_llm()
        if llm.available:
            try:
                res = metered_complete(
                    s, llm,
                    "You write honest, high-CTR short-form video titles for a GTA VI channel. "
                    "Never claim confirmation the sources don't support. No clickbait lies, no ALL CAPS.",
                    f"Topic: {topic.title}\nClassification: {classification}\nHook: {script.chosen_hook}\n"
                    f"Script: {script.voiceover_text}\n"
                    'Return JSON: {"titles": [5 titles <= 70 chars], "hooks": [3 hooks]}',
                    max_tokens=400, job_id=job.id, purpose="seo",
                )
                llm_titles = [clean_title(str(t), classification, 100) for t in res.data.get("titles", [])][:5]
                if len(llm_titles) == 5:
                    titles = llm_titles
                hooks = [str(h) for h in res.data.get("hooks", [])][:3] or hooks
                generator = f"{res.provider}:{res.model}"
            except ProviderError as exc:
                self.log.warning("LLM SEO unavailable, rule-based: %s", exc)
        if job.title_override:
            titles = [clean_title(job.title_override, classification, 100)] + titles[:4]

        question = ENDINGS[category]
        sources = "\n".join(f"- {src['title'][:80]}: {src['url']}" for src in script.sources[:5])
        renders = list(s.scalars(select(Render).where(Render.job_id == job.id)))
        asset_ids = {aid for r in renders for aid in r.asset_ids}
        attributions = sorted({a.attribution_required for a in
                               (s.get(MediaAsset, i) for i in asset_ids) if a and a.attribution_required})
        disclaimer = {
            Classification.THEORY.value: "This video discusses a fan theory. It is not confirmed by Rockstar Games.",
            Classification.RUMOUR.value: "This video discusses an unverified rumour. Rockstar Games has not confirmed it.",
            Classification.DEBUNKED.value: "This video explains why a circulating claim is false.",
        }.get(classification, "")
        fan_note = "Fan-made commentary. Not affiliated with or endorsed by Rockstar Games or Take-Two."
        ai_note = "Narration voiced with AI text-to-speech; script researched from the sources below."
        keywords = sorted(set((topic.keywords or [])[:10]) | {"gta 6", "gta vi", "rockstar games"})
        summary = topic.explanation[:400]

        platforms = []
        for r in renders:
            platform = {"youtube_shorts": "youtube", "tiktok": "tiktok", "instagram_reels": "instagram"}[r.profile]
            tags = hashtags_for(platform, category, topic.keywords or [])
            if platform == "youtube":
                description = "\n\n".join(x for x in [
                    hooks[0] if hooks else script.chosen_hook, summary, disclaimer, f"Sources:\n{sources}",
                    *(["Footage: " + "; ".join(attributions)] if attributions else []),
                    fan_note, ai_note, " ".join(tags)] if x)
                title = titles[0]
            else:
                # TikTok's post title and Instagram's caption carry the copy.
                body = [titles[0], question, disclaimer,
                        *(["Footage: " + "; ".join(attributions)] if attributions else []),
                        "Sources in comments. " + fan_note, " ".join(tags)]
                description = "\n\n".join(x for x in body if x)
                title = titles[0]
            limit = LIMITS[platform]["description"]
            pkg = s.scalar(select(MetadataPackage).where(MetadataPackage.job_id == job.id,
                                                         MetadataPackage.platform == platform))
            if pkg is None:
                pkg = MetadataPackage(job_id=job.id, platform=platform)
                s.add(pkg)
            pkg.title, pkg.title_variations, pkg.hook_variations = title, titles, hooks
            pkg.description = description[:limit]
            pkg.hashtags, pkg.keywords = tags, keywords
            pkg.pinned_comment = f"{question}\n\nSources:\n{sources}"[:limit]
            pkg.engagement_question = question
            pkg.extra = {"ai_disclosure": True, "generator": generator,
                         "attributions": attributions, "classification": classification}
            platforms.append(platform)
        s.flush()
        return SEOOutput(platforms=platforms, titles=titles, generator=generator)
