"""ORM models. Every pipeline artefact is persisted so each stage has a status."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base, utcnow


def _ts() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class Setting(Base):
    __tablename__ = "settings"
    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[Any] = mapped_column(JSON, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class SourceItem(Base):
    """A single piece of research evidence fetched from a connector."""

    __tablename__ = "source_items"
    __table_args__ = (UniqueConstraint("connector", "external_id", name="uq_source_external"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    connector: Mapped[str] = mapped_column(String(40))
    external_id: Mapped[str] = mapped_column(String(255))
    url: Mapped[str] = mapped_column(Text)
    domain: Mapped[str] = mapped_column(String(255), default="")
    title: Mapped[str] = mapped_column(Text)
    summary: Mapped[str] = mapped_column(Text, default="")
    author: Mapped[str] = mapped_column(String(255), default="")
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    fetched_at: Mapped[datetime] = _ts()
    credibility: Mapped[str] = mapped_column(String(40), default="community")
    engagement: Mapped[dict] = mapped_column(JSON, default=dict)
    raw: Mapped[dict] = mapped_column(JSON, default=dict)


class TopicSource(Base):
    __tablename__ = "topic_sources"
    topic_id: Mapped[int] = mapped_column(ForeignKey("topics.id", ondelete="CASCADE"), primary_key=True)
    source_id: Mapped[int] = mapped_column(
        ForeignKey("source_items.id", ondelete="CASCADE"), primary_key=True
    )


class Topic(Base):
    __tablename__ = "topics"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(Text)
    explanation: Mapped[str] = mapped_column(Text, default="")
    category: Mapped[str] = mapped_column(String(40))
    classification: Mapped[str] = mapped_column(String(40))
    score: Mapped[float] = mapped_column(Float, default=0.0)
    score_breakdown: Mapped[dict] = mapped_column(JSON, default=dict)
    angle: Mapped[str] = mapped_column(Text, default="")
    hook: Mapped[str] = mapped_column(Text, default="")
    recommended_length: Mapped[str] = mapped_column(String(20), default="25-40")
    urgency: Mapped[str] = mapped_column(String(20), default="normal")
    evergreen: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(20), default="new", index=True)
    fingerprint: Mapped[str] = mapped_column(Text, default="")
    keywords: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = _ts()
    sources: Mapped[list[SourceItem]] = relationship(secondary="topic_sources", lazy="selectin")


class ContentJob(Base):
    __tablename__ = "content_jobs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    topic_id: Mapped[int | None] = mapped_column(ForeignKey("topics.id", ondelete="SET NULL"))
    category: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(20), default="queued", index=True)
    current_stage: Mapped[str | None] = mapped_column(String(40))
    duration_bucket: Mapped[str] = mapped_column(String(20), default="25-40")
    error: Mapped[str | None] = mapped_column(Text)
    blocked_reason: Mapped[str | None] = mapped_column(Text)
    estimated_cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    actual_cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    title_override: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _ts()
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )
    topic: Mapped[Topic | None] = relationship(lazy="joined")
    stages: Mapped[list["StageRun"]] = relationship(
        back_populates="job", cascade="all, delete-orphan", order_by="StageRun.id"
    )


class StageRun(Base):
    __tablename__ = "stage_runs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("content_jobs.id", ondelete="CASCADE"), index=True)
    stage: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(20), default="pending")
    attempt: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(Text)
    output: Mapped[dict] = mapped_column(JSON, default=dict)
    job: Mapped[ContentJob] = relationship(back_populates="stages")


class Script(Base):
    __tablename__ = "scripts"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("content_jobs.id", ondelete="CASCADE"), index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    title: Mapped[str] = mapped_column(Text)
    category: Mapped[str] = mapped_column(String(40))
    hooks: Mapped[list] = mapped_column(JSON, default=list)
    chosen_hook: Mapped[str] = mapped_column(Text)
    sections: Mapped[dict] = mapped_column(JSON, default=dict)
    scenes: Mapped[list] = mapped_column(JSON, default=list)
    voiceover_text: Mapped[str] = mapped_column(Text)
    captions: Mapped[list] = mapped_column(JSON, default=list)
    claims: Mapped[list] = mapped_column(JSON, default=list)
    sources: Mapped[list] = mapped_column(JSON, default=list)
    call_to_action: Mapped[str] = mapped_column(Text, default="")
    estimated_seconds: Mapped[float] = mapped_column(Float, default=0.0)
    validation: Mapped[dict] = mapped_column(JSON, default=dict)
    fact_check: Mapped[dict] = mapped_column(JSON, default=dict)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    generator: Mapped[str] = mapped_column(String(60), default="")
    created_at: Mapped[datetime] = _ts()


class Voiceover(Base):
    __tablename__ = "voiceovers"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("content_jobs.id", ondelete="CASCADE"), index=True)
    provider: Mapped[str] = mapped_column(String(40))
    is_placeholder: Mapped[bool] = mapped_column(Boolean, default=False)
    voice_settings: Mapped[dict] = mapped_column(JSON, default=dict)
    raw_path: Mapped[str] = mapped_column(Text)
    processed_path: Mapped[str] = mapped_column(Text)
    raw_duration: Mapped[float] = mapped_column(Float, default=0.0)
    duration: Mapped[float] = mapped_column(Float, default=0.0)
    word_timings: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = _ts()


class MediaAsset(Base):
    """Rights register entry. Nothing without verified rights is ever used."""

    __tablename__ = "media_assets"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String(20), default="footage", index=True)
    path: Mapped[str] = mapped_column(Text, unique=True)
    original_source: Mapped[str] = mapped_column(Text)
    owner: Mapped[str] = mapped_column(Text)
    licence: Mapped[str] = mapped_column(Text)
    usage_permissions: Mapped[str] = mapped_column(Text, default="")
    attribution_required: Mapped[str] = mapped_column(Text, default="")
    allowed_platforms: Mapped[list] = mapped_column(JSON, default=list)
    monetization_allowed: Mapped[bool] = mapped_column(Boolean, default=False)
    is_official_material: Mapped[bool] = mapped_column(Boolean, default=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    restrictions: Mapped[str] = mapped_column(Text, default="")
    rights_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    verified_by: Mapped[str] = mapped_column(String(120), default="")
    tags: Mapped[list] = mapped_column(JSON, default=list)
    description: Mapped[str] = mapped_column(Text, default="")
    duration: Mapped[float] = mapped_column(Float, default=0.0)
    width: Mapped[int] = mapped_column(Integer, default=0)
    height: Mapped[int] = mapped_column(Integer, default=0)
    fps: Mapped[float] = mapped_column(Float, default=0.0)
    has_audio: Mapped[bool] = mapped_column(Boolean, default=False)
    scenes: Mapped[list] = mapped_column(JSON, default=list)
    transcript: Mapped[str] = mapped_column(Text, default="")
    times_used: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = _ts()


class Render(Base):
    __tablename__ = "renders"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("content_jobs.id", ondelete="CASCADE"), index=True)
    profile: Mapped[str] = mapped_column(String(40))
    path: Mapped[str] = mapped_column(Text)
    edit_decision_list: Mapped[dict] = mapped_column(JSON, default=dict)
    width: Mapped[int] = mapped_column(Integer, default=0)
    height: Mapped[int] = mapped_column(Integer, default=0)
    fps: Mapped[float] = mapped_column(Float, default=0.0)
    duration: Mapped[float] = mapped_column(Float, default=0.0)
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    qc: Mapped[dict] = mapped_column(JSON, default=dict)
    qc_passed: Mapped[bool] = mapped_column(Boolean, default=False)
    asset_ids: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = _ts()


class Thumbnail(Base):
    __tablename__ = "thumbnails"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("content_jobs.id", ondelete="CASCADE"), index=True)
    variant: Mapped[str] = mapped_column(String(20))
    platform: Mapped[str] = mapped_column(String(20))
    path: Mapped[str] = mapped_column(Text)
    concept: Mapped[dict] = mapped_column(JSON, default=dict)
    selected: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = _ts()


class MetadataPackage(Base):
    __tablename__ = "metadata_packages"
    __table_args__ = (UniqueConstraint("job_id", "platform", name="uq_metadata_job_platform"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("content_jobs.id", ondelete="CASCADE"), index=True)
    platform: Mapped[str] = mapped_column(String(20))
    title: Mapped[str] = mapped_column(Text)
    title_variations: Mapped[list] = mapped_column(JSON, default=list)
    hook_variations: Mapped[list] = mapped_column(JSON, default=list)
    description: Mapped[str] = mapped_column(Text)
    hashtags: Mapped[list] = mapped_column(JSON, default=list)
    keywords: Mapped[list] = mapped_column(JSON, default=list)
    pinned_comment: Mapped[str] = mapped_column(Text, default="")
    engagement_question: Mapped[str] = mapped_column(Text, default="")
    extra: Mapped[dict] = mapped_column(JSON, default=dict)


class Publication(Base):
    __tablename__ = "publications"
    __table_args__ = (UniqueConstraint("idempotency_key", name="uq_publication_idem"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("content_jobs.id", ondelete="CASCADE"), index=True)
    platform: Mapped[str] = mapped_column(String(20), index=True)
    idempotency_key: Mapped[str] = mapped_column(String(120))
    content_hash: Mapped[str] = mapped_column(String(64), default="")
    status: Mapped[str] = mapped_column(String(20), default="queued", index=True)
    privacy: Mapped[str] = mapped_column(String(40), default="private")
    scheduled_for: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    external_id: Mapped[str | None] = mapped_column(String(255))
    remote_tracking_id: Mapped[str | None] = mapped_column(String(255))
    url: Mapped[str | None] = mapped_column(Text)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    consent: Mapped[dict] = mapped_column(JSON, default=dict)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _ts()
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )
    job: Mapped[ContentJob] = relationship(lazy="joined")


class PlatformConnection(Base):
    __tablename__ = "platform_connections"
    platform: Mapped[str] = mapped_column(String(20), primary_key=True)
    status: Mapped[str] = mapped_column(String(20), default="disconnected")
    account_id: Mapped[str] = mapped_column(String(255), default="")
    account_name: Mapped[str] = mapped_column(String(255), default="")
    scopes: Mapped[list] = mapped_column(JSON, default=list)
    access_token_enc: Mapped[str] = mapped_column(Text, default="")
    refresh_token_enc: Mapped[str] = mapped_column(Text, default="")
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    oauth_state: Mapped[str] = mapped_column(String(255), default="")
    oauth_verifier_enc: Mapped[str] = mapped_column(Text, default="")
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    capabilities: Mapped[dict] = mapped_column(JSON, default=dict)
    last_error: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class MetricSnapshot(Base):
    """Metrics are nullable: a metric the platform did not return stays NULL, never 0."""

    __tablename__ = "metric_snapshots"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    publication_id: Mapped[int] = mapped_column(
        ForeignKey("publications.id", ondelete="CASCADE"), index=True
    )
    captured_at: Mapped[datetime] = _ts()
    views: Mapped[int | None] = mapped_column(Integer)
    likes: Mapped[int | None] = mapped_column(Integer)
    comments: Mapped[int | None] = mapped_column(Integer)
    shares: Mapped[int | None] = mapped_column(Integer)
    saves: Mapped[int | None] = mapped_column(Integer)
    avg_watch_seconds: Mapped[float | None] = mapped_column(Float)
    avg_view_percentage: Mapped[float | None] = mapped_column(Float)
    followers_gained: Mapped[int | None] = mapped_column(Integer)
    raw: Mapped[dict] = mapped_column(JSON, default=dict)


class CostEntry(Base):
    __tablename__ = "cost_entries"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[datetime] = _ts()
    category: Mapped[str] = mapped_column(String(40), index=True)
    provider: Mapped[str] = mapped_column(String(60))
    units: Mapped[float] = mapped_column(Float, default=0.0)
    unit: Mapped[str] = mapped_column(String(30), default="")
    estimated_usd: Mapped[float] = mapped_column(Float, default=0.0)
    actual_usd: Mapped[float | None] = mapped_column(Float)
    job_id: Mapped[int | None] = mapped_column(Integer, index=True)
    note: Mapped[str] = mapped_column(Text, default="")


class AuditLog(Base):
    __tablename__ = "audit_logs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[datetime] = _ts()
    actor: Mapped[str] = mapped_column(String(80))
    action: Mapped[str] = mapped_column(String(120))
    target: Mapped[str] = mapped_column(String(255), default="")
    details: Mapped[dict] = mapped_column(JSON, default=dict)


class AgentRun(Base):
    __tablename__ = "agent_runs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    agent: Mapped[str] = mapped_column(String(60), index=True)
    job_id: Mapped[int | None] = mapped_column(Integer, index=True)
    status: Mapped[str] = mapped_column(String(20))
    attempts: Mapped[int] = mapped_column(Integer, default=1)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text)
    summary: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = _ts()


class Report(Base):
    __tablename__ = "reports"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String(40))
    period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    body: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = _ts()
