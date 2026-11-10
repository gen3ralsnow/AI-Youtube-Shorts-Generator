# Development roadmap and project checklist

Legend: ✅ done and tested · 🔑 implemented, awaiting credentials for live verification · ⏭ next

## Phase 0: audit
- ✅ Inspected all 15 workspace repositories. None is a media platform. `AI-Youtube-Shorts-Generator` is the closest fit and hosts the engine as a self-contained `gta-media-engine/` subproject; its existing `shorts_generator` package is untouched.
- ✅ Problems raised:
  - the existing downloader pulls arbitrary YouTube videos, which conflicts with the rights rules, so it is not used;
  - TikTok rules require a person's consent per direct post;
  - no credentials exist in the build environment.
- ✅ Tooling check: Python 3.13, FFmpeg with libass/loudnorm/blackdetect, Node 22, PostgreSQL 16, Redis, Docker.

## Phase 1: infrastructure and architecture
- ✅ FastAPI + SQLAlchemy 2 + Alembic (migration verified up, down and up on PostgreSQL 16)
- ✅ Settings store (TEST mode and publishing pause by default), cost ledger and budget guard, credential vault, audit log, structured logging with redaction
- ✅ Local and S3-compatible storage with temp-file retention
- ✅ Celery app with research/ai, video and publishing/analytics queues, plus beat; in-process runner for development

## Phase 2: research and content discovery
- 🔑 YouTube Data API, Reddit OAuth and RSS connectors (robots.txt honoured), SerpApi trends; quota tracking
- ✅ Clustering, five-level accuracy classification, 0–100 scoring with missing-signal renormalisation, duplicate detection, topic report (angle, hook, length, urgency)
- ✅ Content strategist with category balancing, urgency and performance feedback; content calendar

## Phase 3: scriptwriting and narration
- ✅ Scriptwriter: hook variants and selection, scenes, captions, SFX, transitions, CTA, sourced claims; rule-based offline mode; 🔑 Anthropic/OpenAI mode
- ✅ Script validator; fact checker (lexical; 🔑 model second opinion)
- ✅ Voiceover pipeline: pronunciation map, processing, separate raw/processed storage, word timings, duration validation; 🔑 ElevenLabs / OpenAI TTS

## Phase 4: automated video production
- ✅ Rights register with verification, expiry, platform licensing and test-only marking
- ✅ Scene and motion analysis, script-aware clip ranking
- ✅ FFmpeg render: 9:16 cover crop with pan, transitions, animated subtitles with keyword accents, ducked music, SFX, -14 LUFS
- ✅ Platform export profiles; automated QC (14+ checks); thumbnails (3 variants × 2 sizes)
- ✅ SEO: 5 titles, 3 hooks, platform descriptions, hashtags, keywords, pinned comment, question

## Phase 5: social publishing
- 🔑 YouTube resumable upload, status and analytics
- 🔑 TikTok inbox/direct post, status and metrics
- 🔑 Instagram container, resumable upload, publish and insights
- ✅ OAuth (PKCE, state), encrypted tokens, refresh
- ✅ Distribution manager: modes, pause, eligibility, idempotency, duplicate guard, retry and backoff, confirmed-only "published", non-public test uploads

## Phase 6: analytics and optimisation
- ✅ Collector (missing metrics stay null), statistical analyst (minimum samples, medians, bootstrap CIs), weekly reports, learning loop into the strategist

## Phase 7: testing, deployment and monitoring
- ✅ 83 automated tests (unit, integration, end-to-end with real rendering)
- ✅ Next.js admin dashboard (16 screens), browser-tested on desktop and mobile, with button actions verified
- ✅ Dockerfiles; Compose stack booted and a job run through Celery on PostgreSQL; production overlay with Caddy HTTPS; backup and restore verified
- ⏭ Monitoring/alerting integration (H6 in REPAIRS_REPORT.md)

## Next steps (in order)
1. Credentials and authorised footage (C1–C3), then a private test upload per platform.
2. YouTube audit, TikTok audit, Meta App Review (C4–C6), with legal review in parallel (C7).
3. SSO in front of the dashboard (H1), alerting (H6).
4. Run in TEST mode for at least a week, review every generated video, then activate AUTONOMOUS mode with a low `videos_per_day`.
