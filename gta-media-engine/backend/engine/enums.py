"""Shared enumerations. Values are stored as strings in the database."""

from enum import Enum


class StrEnum(str, Enum):
    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.value


class OperatingMode(StrEnum):
    TEST = "test"  # render and prepare, never publish
    AUTONOMOUS = "autonomous"  # publish eligible content on schedule


class Classification(StrEnum):
    OFFICIAL = "officially_confirmed"
    CREDIBLE = "credibly_reported"
    THEORY = "community_theory"
    RUMOUR = "unverified_rumour"
    DEBUNKED = "false_or_debunked"


class Category(StrEnum):
    DISCOVERIES = "A_discoveries"
    NEWS = "B_news"
    THEORIES = "C_theories"
    ENTERTAINMENT = "D_entertainment"
    VERSUS = "E_gta6_vs_gtav"


class TopicStatus(StrEnum):
    NEW = "new"
    SELECTED = "selected"
    USED = "used"
    REJECTED = "rejected"
    DUPLICATE = "duplicate"


class Stage(StrEnum):
    FACT_CHECK = "fact_verification"
    SCRIPT = "script_generation"
    SCRIPT_VALIDATION = "script_validation"
    VOICEOVER = "voiceover_generation"
    FOOTAGE = "footage_selection"
    EDIT = "video_editing"
    QC = "quality_control"
    THUMBNAIL = "thumbnail_generation"
    METADATA = "metadata_generation"
    QUEUE = "publishing_queue"


PIPELINE_ORDER: list[Stage] = [
    Stage.FACT_CHECK,
    Stage.SCRIPT,
    Stage.SCRIPT_VALIDATION,
    Stage.VOICEOVER,
    Stage.FOOTAGE,
    Stage.EDIT,
    Stage.QC,
    Stage.THUMBNAIL,
    Stage.METADATA,
    Stage.QUEUE,
]


class StageStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    READY = "ready"  # rendered, QC passed, queued for publishing
    BLOCKED = "blocked"  # stopped by a safety gate (facts, rights, budget)
    FAILED = "failed"  # exhausted retries
    PUBLISHED = "published"
    CANCELLED = "cancelled"


class Platform(StrEnum):
    YOUTUBE = "youtube"
    TIKTOK = "tiktok"
    INSTAGRAM = "instagram"


class PublicationStatus(StrEnum):
    QUEUED = "queued"
    DRY_RUN = "dry_run"  # TEST mode: prepared, not sent
    UPLOADING = "uploading"
    PROCESSING = "processing"  # sent, awaiting platform confirmation
    PUBLISHED = "published"  # platform confirmed
    DRAFTED = "drafted"  # sent to the creator's drafts/inbox (TikTok inbox)
    FAILED = "failed"
    BLOCKED = "blocked"  # not eligible (paused, unconnected, limit)


class AssetKind(StrEnum):
    FOOTAGE = "footage"
    MUSIC = "music"
    SFX = "sfx"
    IMAGE = "image"
