"""Shape ORM rows for the dashboard. Never includes tokens or secrets."""

from __future__ import annotations

from ..models import (
    ContentJob,
    MediaAsset,
    MetadataPackage,
    PlatformConnection,
    Publication,
    Render,
    Script,
    SourceItem,
    StageRun,
    Thumbnail,
    Topic,
    Voiceover,
)
from .deps import row


def source(s: SourceItem) -> dict:
    return row(s, "id", "connector", "url", "domain", "title", "summary", "author", "published_at",
               "credibility", "engagement")


def topic(t: Topic, with_sources: bool = False) -> dict:
    out = row(t, "id", "title", "explanation", "category", "classification", "score", "score_breakdown",
              "angle", "hook", "recommended_length", "urgency", "evergreen", "status", "keywords", "created_at")
    out["source_count"] = len(t.sources)
    if with_sources:
        out["sources"] = [source(s) for s in t.sources]
    return out


def stage(r: StageRun) -> dict:
    return row(r, "id", "stage", "status", "attempt", "started_at", "finished_at", "error", "output")


def job(j: ContentJob) -> dict:
    return row(j, "id", "topic_id", "category", "status", "current_stage", "duration_bucket", "error",
               "blocked_reason", "estimated_cost_usd", "actual_cost_usd", "title_override", "created_at",
               "updated_at", topic_title=j.topic.title if j.topic else None,
               classification=j.topic.classification if j.topic else None)


def script(s: Script) -> dict:
    return row(s, "id", "job_id", "version", "title", "category", "hooks", "chosen_hook", "sections", "scenes",
               "voiceover_text", "captions", "claims", "sources", "call_to_action", "estimated_seconds",
               "validation", "fact_check", "confidence", "generator", "created_at")


def voiceover(v: Voiceover) -> dict:
    return row(v, "id", "job_id", "provider", "is_placeholder", "voice_settings", "raw_duration", "duration",
               "created_at", word_count=len(v.word_timings or []),
               raw_url=f"/api/files/voiceover/{v.id}?kind=raw",
               processed_url=f"/api/files/voiceover/{v.id}?kind=processed")


def render(r: Render) -> dict:
    return row(r, "id", "job_id", "profile", "width", "height", "fps", "duration", "size_bytes", "qc",
               "qc_passed", "asset_ids", "created_at", url=f"/api/files/render/{r.id}")


def thumbnail(t: Thumbnail) -> dict:
    return row(t, "id", "job_id", "variant", "platform", "selected", "concept", url=f"/api/files/thumbnail/{t.id}")


def metadata(m: MetadataPackage) -> dict:
    return row(m, "id", "job_id", "platform", "title", "title_variations", "hook_variations", "description",
               "hashtags", "keywords", "pinned_comment", "engagement_question", "extra")


def publication(p: Publication) -> dict:
    return row(p, "id", "job_id", "platform", "idempotency_key", "status", "privacy", "scheduled_for",
               "external_id", "url", "attempts", "last_error", "next_attempt_at", "consent", "published_at",
               "created_at", "updated_at", is_test=p.idempotency_key.endswith(":test"),
               job_title=p.job.topic.title if p.job and p.job.topic else None)


def asset(a: MediaAsset) -> dict:
    return row(a, "id", "kind", "path", "original_source", "owner", "licence", "usage_permissions",
               "attribution_required", "allowed_platforms", "monetization_allowed", "is_official_material",
               "expires_at", "restrictions", "rights_verified", "verified_by", "tags", "description", "duration",
               "width", "height", "fps", "has_audio", "times_used", "created_at", scene_count=len(a.scenes or []))


def connection(c: PlatformConnection, missing: list[str]) -> dict:
    return row(c, "platform", "status", "account_id", "account_name", "scopes", "expires_at", "verified_at",
               "capabilities", "last_error", "updated_at", app_credentials_missing=missing)
