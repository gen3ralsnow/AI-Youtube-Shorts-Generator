# GTA VI Autonomous Media Engine

A self-hosted platform that runs a short-form GTA VI channel end to end:
- discovers and fact-checks topics;
- writes scripts and generates narration;
- edits 9:16 videos from rights-cleared footage;
- writes platform metadata and publishes to YouTube Shorts, TikTok and Instagram Reels;
- learns from real performance data.

Every stage has quality gates, and the system starts in **TEST mode**: it prepares everything and publishes nothing until you complete the launch checklist and switch to AUTONOMOUS mode.

> **Status.** Code, tests, dashboard and deployment are built and verified (see [docs/TEST_REPORT.md](docs/TEST_REPORT.md)). **External integrations are implemented but not yet verified against live accounts**, because they need your credentials, platform approvals and authorised footage. See [docs/REPAIRS_REPORT.md](docs/REPAIRS_REPORT.md) for the critical items before launch.

```
research → topic scoring → fact verification → script → script validation → voiceover →
footage selection → video editing → quality control → thumbnails → metadata →
publishing queue → platform distribution → performance analysis → strategy optimisation
```

## What's inside

| Path | Contents |
|------|----------|
| `backend/engine/agents/` | 11 specialised agents (research, trend analyst, strategist, scriptwriter, fact checker, voiceover, video production, SEO, publishing, analytics, quality control) |
| `backend/engine/research/` | Source connectors, accuracy classification, 0–100 opportunity scoring |
| `backend/engine/video/` | FFmpeg engine: footage rights and selection, assembly, animated subtitles, export profiles, QC, thumbnails |
| `backend/engine/publishing/` | YouTube, TikTok and Instagram clients, OAuth, distribution manager |
| `backend/engine/analytics/` | Metrics collection and statistical analysis |
| `backend/engine/pipeline/` | Job state machine, scheduler ticks |
| `backend/engine/workers/` | Celery app and tasks |
| `backend/engine/api/` | FastAPI REST API |
| `backend/migrations/` | Alembic migrations (PostgreSQL) |
| `backend/tests/` | Unit, integration and end-to-end tests |
| `dashboard/` | Next.js + TypeScript + Tailwind admin dashboard (16 screens) |
| `deploy/` | Caddy (HTTPS) config, backup script |
| `docs/` | Architecture, setup, deployment, security, compliance, test and repair reports |

## Quick start (local, no credentials)

```bash
cd backend
pip install -e ".[dev]"
export ADMIN_API_TOKEN=dev-token ENGINE_ENCRYPTION_KEY=$(python -m engine.cli gen-key)
python -m engine.cli demo-footage      # synthetic test-only footage (blocked from publishing)
uvicorn engine.api.app:app --reload    # http://localhost:8000/api/docs

cd ../dashboard && npm ci
ENGINE_API_URL=http://localhost:8000 ENGINE_ADMIN_TOKEN=dev-token npm run dev   # http://localhost:3000
```

In the dashboard:
1. Go to **AI research → Submit a topic with a source**.
2. Go to **Trending topics → Produce**.
3. Go to **Production queue**, open the job and press **Run now**.

You get a real rendered 1080×1920 video with QC. Without a TTS provider it uses a placeholder voice, and QC marks it not publishable.

## Docker

```bash
cp .env.example .env    # fill in REQUIRED values
docker compose up -d --build                                                     # local stack
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build     # production with HTTPS
```

## Configuration

- Secrets and providers: `.env` (template: [.env.example](.env.example)). Per-integration steps: [docs/API_SETUP.md](docs/API_SETUP.md).
- Operating settings (schedule, windows, budgets, voice, thresholds, categories, brand): dashboard **AI configuration**, **Costs** and **Settings**, stored in the database.

## Testing

```bash
cd backend
pytest                    # all tests (end-to-end tests render real video, about 3 minutes)
pytest -m "not slow"      # fast suite
ruff check engine
cd ../dashboard && npm run typecheck && npm run build
```

## Safety model

- **TEST mode by default**: nothing is sent to a platform. Activation requires every required checklist item plus a typed confirmation phrase, and publishing stays paused until you explicitly resume it.
- **Emergency pause** in the dashboard header. Queued content is kept, never dropped.
- **Facts**: claims must cite attached sources. Confidence below the threshold blocks the job. Theories and rumours are labelled and never filed as news.
- **Rights**: only verified, unexpired, platform-licensed assets are used. Footage audio is muted. Test-only assets can't publish.
- **Spend**: estimated before each paid call. Daily, monthly and per-video caps pause processing automatically.
- **Publishing**: idempotent, duplicate-guarded and rate-limited; "published" only after platform confirmation; TikTok direct posts need a person's consent per post.

## Documentation

- [Architecture and agents](docs/ARCHITECTURE.md), including the system diagram
- [API and credential setup](docs/API_SETUP.md)
- [Deployment and launch procedure](docs/DEPLOYMENT.md)
- [Troubleshooting](docs/TROUBLESHOOTING.md)
- [Security review](docs/SECURITY_REVIEW.md)
- [Copyright and compliance review](docs/COMPLIANCE_REVIEW.md)
- [Test report](docs/TEST_REPORT.md)
- [Necessary repairs report](docs/REPAIRS_REPORT.md)
- [Roadmap and checklist](docs/ROADMAP.md)
- [Final system audit](docs/SYSTEM_AUDIT.md)

Fan-made project. Not affiliated with or endorsed by Rockstar Games or Take-Two Interactive.
