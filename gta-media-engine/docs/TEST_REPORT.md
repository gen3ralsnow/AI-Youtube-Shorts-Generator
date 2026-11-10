# Test report

Environment: Linux, Python 3.13, FFmpeg (libass), PostgreSQL 16, Redis 7, Node 22, Docker 29. No external credentials were available, so every external API was exercised against mocked HTTP endpoints (`httpx.MockTransport`).

## Automated tests: 83 passed, 0 failed

Command: `cd backend && pytest` took 2 min 36 s. The two end-to-end tests render real video.

| Suite | Tests | Covers |
|-------|------:|--------|
| `unit/test_research.py` | 10 | name normalisation, relevance, 5-level classification, news/rumour separation, scoring bounds, missing signals renormalised (not zero), debunk and relevance penalties, timeliness decay, urgency, length |
| `unit/test_script_and_facts.py` | 8 | rule-based scripts only reuse source sentences, theory disclaimers, validator (generic phrasing, false certainty, unknown sources, missing claims, too-thin scripts), duration buckets never stretch, hook selection, lexical support, fact-check blocking, news credibility |
| `unit/test_controls.py` | 17 | safe defaults, protected mode, settings validation, budget guard and auto-pause, per-video budget, estimated vs actual costs, scheduling windows and limits, encryption and tamper detection, roles, rate limiter, log redaction, non-misleading titles, hashtag limits, pronunciation-to-subtitle timing map, subtitle chunking and cue validation, overlap regression |
| `unit/test_analytics_and_publishing.py` | 12 | missing metrics excluded, small samples give no conclusions, clear winners need CIs, weekly report feeds the strategist, category balancing, idempotent enqueue, TEST mode never contacts platforms, pause keeps content queued, QC and connection blocks, duplicate-content guard, re-queue rules |
| `integration/test_connectors_and_providers.py` | 8 | RSS with robots.txt, YouTube quota accounting, Reddit OAuth and filtering, unconfigured sources reported (not faked), clustering and dedupe, Anthropic/OpenAI parsing and metering, JSON errors, ElevenLabs/OpenAI TTS request shapes |
| `integration/test_platforms.py` | 12 | OAuth with PKCE and state (CSRF rejection), unconfigured apps, token refresh, failed refresh marks expired, YouTube resumable upload and status mapping, rejection, TikTok inbox flow and chunk ranges, direct-post consent, API errors, Instagram container → publish only when FINISHED, professional-account check, manager retries transient errors and only marks published after confirmation |
| `integration/test_api.py` | 14 | auth and roles, security headers, null metrics, settings validation and audit, controls and activation gate, classification can't be overridden, rights fields and footage roots, upload types, XSS escaping, missing app credentials, health, rate limit, streaming doesn't hold DB connections |
| `e2e/test_full_cycle.py` | 2 | **TEST-mode cycle:** RSS discovery → scoring → strategy → all 10 stages → 3 real platform exports at 1080×1920 H.264/AAC → technical QC passes → correctly *not publishable* → 6 thumbnails → metadata → 3 publications → dry run only. **AUTONOMOUS cycle:** licensed footage plus a stand-in TTS → publishable → upload → `processing` (not claimed) → platform confirms → `published` → metrics collected, with missing metrics left null → analyst refuses conclusions from a single video |

## Additional verification (manual / scripted)

| Check | Result |
|-------|--------|
| Alembic migration on PostgreSQL 16: upgrade → downgrade → upgrade | ✅ pass |
| Live API (uvicorn) with dashboard: topic → job → ready through the HTTP API | ✅ pass |
| Dashboard production build (`next build`), TypeScript strict | ✅ pass |
| All 16 dashboard screens rendered in Chromium, no console errors | ✅ pass |
| Mobile (390 px): no horizontal overflow on any screen checked | ✅ pass (after fix) |
| Dashboard actions driven in the browser and verified in the backend: pause/resume processing, save settings, activation gating, caption edit → re-render, run job, audit trail | ✅ 8/8 |
| Basic auth: 401 without credentials, 200 with; proxy rejects path traversal | ✅ pass |
| Docker images build (backend on Ubuntu 24.04 with FFmpeg/libass; dashboard standalone) | ✅ pass |
| Docker Compose stack (Postgres, Redis, API, 3 worker pools, beat, dashboard): job routed through Celery AI and video queues to `ready` | ✅ pass |
| Backup script → restore into a fresh database → data present | ✅ pass |
| Production overlay config and Caddyfile validation; only 80/443 published | ✅ pass |
| Rendered frames inspected visually (subtitles, highlight, headline wrap, pan, brand tag, thumbnails) | ✅ pass (after two fixes) |

## Integrations

| Integration | Code | Mock-tested | Live-verified |
|-------------|:----:|:-----------:|:-------------:|
| YouTube Data API (research) | ✅ | ✅ | 🔑 awaiting `YOUTUBE_API_KEY` |
| Reddit API | ✅ | ✅ | 🔑 awaiting `REDDIT_CLIENT_ID/SECRET` |
| RSS feeds | ✅ | ✅ | 🔑 awaiting `RSS_FEEDS` |
| SerpApi Google Trends | ✅ | ⚠️ not mock-tested | 🔑 awaiting `SERPAPI_API_KEY` |
| Anthropic / OpenAI LLM | ✅ | ✅ | 🔑 awaiting API key |
| ElevenLabs / OpenAI TTS | ✅ | ✅ | 🔑 awaiting API key + voice ID |
| OpenAI / local transcription | ✅ | ⚠️ not mock-tested | 🔑 awaiting key / `faster-whisper` |
| YouTube upload + analytics | ✅ | ✅ | 🔑 awaiting Google OAuth client + audit |
| TikTok Content Posting | ✅ | ✅ | 🔑 awaiting app + audit |
| Instagram Graph | ✅ | ✅ | 🔑 awaiting Meta app + review |
| S3-compatible storage | ✅ | ⚠️ not tested against MinIO/S3 | 🔑 awaiting bucket |
| FFmpeg rendering | ✅ | n/a | ✅ verified (real renders) |
| PostgreSQL / Redis / Celery | ✅ | n/a | ✅ verified (Compose stack) |

No integration marked 🔑 should be considered operational until its **Verify** action and a non-public test upload succeed with your accounts.

## Known limitations

See [REPAIRS_REPORT.md](REPAIRS_REPORT.md). The most important: no live credentials, no authorised GTA footage registered, platform audits pending, and lexical fact checking is a floor, not a guarantee.
