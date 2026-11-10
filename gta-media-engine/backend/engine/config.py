"""Process configuration from environment variables.

Secrets come only from the environment (or a secrets manager that populates
it). Runtime-tunable operating settings (schedules, budgets, voice) live in the
database via ``settings_store`` so the dashboard can change them.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def _env_list(name: str, default: str = "") -> list[str]:
    raw = _env(name, default)
    return [item.strip() for item in raw.split(",") if item.strip()]


def _env_float(name: str, default: float) -> float:
    raw = _env(name)
    return float(raw) if raw else default


@dataclass(frozen=True)
class Settings:
    env: str = field(default_factory=lambda: _env("ENGINE_ENV", "development"))
    database_url: str = field(
        default_factory=lambda: _env("DATABASE_URL", "sqlite:///./data/engine.db")
    )
    redis_url: str = field(default_factory=lambda: _env("REDIS_URL", ""))
    data_dir: Path = field(default_factory=lambda: Path(_env("ENGINE_DATA_DIR", "./data")).resolve())
    public_base_url: str = field(
        default_factory=lambda: _env("PUBLIC_BASE_URL", "http://localhost:8000")
    )
    dashboard_origin: str = field(
        default_factory=lambda: _env("DASHBOARD_ORIGIN", "http://localhost:3000")
    )

    # Access control. Tokens are compared in constant time; never logged.
    admin_api_token: str = field(default_factory=lambda: _env("ADMIN_API_TOKEN"))
    viewer_api_token: str = field(default_factory=lambda: _env("VIEWER_API_TOKEN"))
    encryption_key: str = field(default_factory=lambda: _env("ENGINE_ENCRYPTION_KEY"))
    rate_limit_per_minute: int = field(
        default_factory=lambda: int(_env("API_RATE_LIMIT_PER_MINUTE", "120"))
    )

    # Storage
    storage_backend: str = field(default_factory=lambda: _env("STORAGE_BACKEND", "local"))
    s3_endpoint_url: str = field(default_factory=lambda: _env("S3_ENDPOINT_URL"))
    s3_bucket: str = field(default_factory=lambda: _env("S3_BUCKET", "gta-media-engine"))
    s3_access_key: str = field(default_factory=lambda: _env("S3_ACCESS_KEY_ID"))
    s3_secret_key: str = field(default_factory=lambda: _env("S3_SECRET_ACCESS_KEY"))
    s3_region: str = field(default_factory=lambda: _env("S3_REGION", "us-east-1"))
    temp_retention_hours: int = field(
        default_factory=lambda: int(_env("TEMP_RETENTION_HOURS", "48"))
    )
    footage_roots: list[str] = field(default_factory=lambda: _env_list("FOOTAGE_ROOTS"))

    # Language models
    llm_provider: str = field(default_factory=lambda: _env("LLM_PROVIDER", "offline"))
    anthropic_api_key: str = field(default_factory=lambda: _env("ANTHROPIC_API_KEY"))
    anthropic_model: str = field(
        default_factory=lambda: _env("ANTHROPIC_MODEL", "claude-sonnet-5-5")
    )
    openai_api_key: str = field(default_factory=lambda: _env("OPENAI_API_KEY"))
    openai_model: str = field(default_factory=lambda: _env("OPENAI_MODEL", "gpt-4o-mini"))

    # Text-to-speech
    tts_provider: str = field(default_factory=lambda: _env("TTS_PROVIDER", "placeholder"))
    elevenlabs_api_key: str = field(default_factory=lambda: _env("ELEVENLABS_API_KEY"))
    elevenlabs_model: str = field(
        default_factory=lambda: _env("ELEVENLABS_MODEL", "eleven_multilingual_v2")
    )
    openai_tts_model: str = field(
        default_factory=lambda: _env("OPENAI_TTS_MODEL", "gpt-4o-mini-tts")
    )

    # Transcription
    transcription_provider: str = field(
        default_factory=lambda: _env("TRANSCRIPTION_PROVIDER", "none")
    )

    # Research sources
    youtube_api_key: str = field(default_factory=lambda: _env("YOUTUBE_API_KEY"))
    reddit_client_id: str = field(default_factory=lambda: _env("REDDIT_CLIENT_ID"))
    reddit_client_secret: str = field(default_factory=lambda: _env("REDDIT_CLIENT_SECRET"))
    reddit_user_agent: str = field(
        default_factory=lambda: _env("REDDIT_USER_AGENT", "gta-media-engine/0.1 (research bot)")
    )
    reddit_subreddits: list[str] = field(
        default_factory=lambda: _env_list("REDDIT_SUBREDDITS", "GTA6,GTA,rockstar")
    )
    rss_feeds: list[str] = field(default_factory=lambda: _env_list("RSS_FEEDS"))
    serpapi_api_key: str = field(default_factory=lambda: _env("SERPAPI_API_KEY"))
    official_domains: list[str] = field(
        default_factory=lambda: _env_list(
            "OFFICIAL_DOMAINS", "rockstargames.com,take2games.com"
        )
    )
    official_youtube_channel_ids: list[str] = field(
        default_factory=lambda: _env_list("OFFICIAL_YOUTUBE_CHANNEL_IDS")
    )
    credible_domains: list[str] = field(
        default_factory=lambda: _env_list(
            "CREDIBLE_DOMAINS",
            "ign.com,gamespot.com,polygon.com,eurogamer.net,theverge.com,"
            "bloomberg.com,reuters.com,kotaku.com,pcgamer.com,gamesindustry.biz",
        )
    )

    # Publishing (OAuth app credentials; per-account tokens are stored encrypted in DB)
    google_client_id: str = field(default_factory=lambda: _env("GOOGLE_CLIENT_ID"))
    google_client_secret: str = field(default_factory=lambda: _env("GOOGLE_CLIENT_SECRET"))
    tiktok_client_key: str = field(default_factory=lambda: _env("TIKTOK_CLIENT_KEY"))
    tiktok_client_secret: str = field(default_factory=lambda: _env("TIKTOK_CLIENT_SECRET"))
    meta_app_id: str = field(default_factory=lambda: _env("META_APP_ID"))
    meta_app_secret: str = field(default_factory=lambda: _env("META_APP_SECRET"))
    meta_graph_version: str = field(default_factory=lambda: _env("META_GRAPH_VERSION", "v23.0"))

    # Unit prices used for cost estimates. Verify against your provider invoices.
    price_llm_input_per_mtok: float = field(
        default_factory=lambda: _env_float("PRICE_LLM_INPUT_PER_MTOK", 3.0)
    )
    price_llm_output_per_mtok: float = field(
        default_factory=lambda: _env_float("PRICE_LLM_OUTPUT_PER_MTOK", 15.0)
    )
    price_tts_per_kchar: float = field(
        default_factory=lambda: _env_float("PRICE_TTS_PER_KCHAR", 0.30)
    )
    price_transcription_per_min: float = field(
        default_factory=lambda: _env_float("PRICE_TRANSCRIPTION_PER_MIN", 0.006)
    )
    price_render_per_min: float = field(
        default_factory=lambda: _env_float("PRICE_RENDER_PER_MIN", 0.01)
    )
    price_serpapi_per_call: float = field(
        default_factory=lambda: _env_float("PRICE_SERPAPI_PER_CALL", 0.015)
    )

    @property
    def is_production(self) -> bool:
        return self.env == "production"

    def ensure_dirs(self) -> None:
        for sub in ("audio/raw", "audio/processed", "renders", "thumbnails", "tmp", "footage"):
            (self.data_dir / sub).mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


def reset_settings_cache() -> None:
    get_settings.cache_clear()
