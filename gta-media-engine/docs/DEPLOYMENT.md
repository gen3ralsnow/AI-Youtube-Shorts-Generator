# Deployment

## Local development (no Docker)

```bash
cd gta-media-engine/backend
pip install -e ".[dev]"                     # or: pip install -r requirements.txt pytest
export ADMIN_API_TOKEN=dev-token ENGINE_ENCRYPTION_KEY=$(python -m engine.cli gen-key)
python -m engine.cli demo-footage           # synthetic, test-only footage so the pipeline can run
uvicorn engine.api.app:app --reload        # SQLite at ./data/engine.db, API docs at /api/docs

cd ../dashboard
npm ci
ENGINE_API_URL=http://localhost:8000 ENGINE_ADMIN_TOKEN=dev-token npm run dev
```

Without Redis, the API runs jobs in background threads, and `python -m engine.cli loop --minutes 15` runs the whole schedule in one process.

## Single host with Docker Compose

```bash
cd gta-media-engine
cp .env.example .env        # fill in the REQUIRED values
mkdir -p footage            # put authorised footage here (mounted read-only at /footage)
docker compose up -d --build
```

Services:
- `postgres`, `redis`
- `api`: runs `alembic upgrade head` on start
- three worker pools: `worker-ai` (research, ai), `worker-video` (video), `worker-publish` (publishing, analytics)
- `beat`: the scheduler
- `dashboard`
- optional `minio` (`--profile minio`)

## Production (HTTPS)

Requirements: a Linux VM with at least 4 vCPU and 8 GB RAM. Rendering is CPU-bound; add `worker-video` replicas before anything else. You also need two DNS records pointing at the VM: `API_DOMAIN` and `DASHBOARD_DOMAIN`.

```bash
# .env additionally: API_DOMAIN, DASHBOARD_DOMAIN, ACME_EMAIL, PUBLIC_BASE_URL=https://$API_DOMAIN,
#                    DASHBOARD_ORIGIN=https://$DASHBOARD_DOMAIN
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
```

What the overlay adds:
- **Caddy** terminates TLS with automatic Let's Encrypt certificates and HSTS. Only ports 80 and 443 are published.
- The API domain only serves `/health` and the OAuth callbacks. Everything else reaches the API through the dashboard's server-side proxy, on the internal network.
- A **backup** container runs `deploy/backup.sh` daily: `pg_dump` with `BACKUP_RETENTION_DAYS` retention, into `BACKUP_HOST_DIR`. Restore with `gunzip -c FILE | psql "$DATABASE_URL"`. Copy backups off the host (for example to object storage) and test a restore regularly.

### Suggested cloud setups

- **Hetzner / DigitalOcean / AWS Lightsail VM** with Docker Compose and the overlay above. This is the simplest option and is enough for tens of videos a day.
- **AWS**: EC2 (c7i.xlarge for rendering) + RDS PostgreSQL + ElastiCache Redis + S3. Set `DATABASE_URL`, `REDIS_URL` and `STORAGE_BACKEND=s3`, and drop the `postgres`/`redis` services.
- **Kubernetes**: the images are stateless apart from `/data` (working files). Run the API, each worker pool and beat (exactly one replica) as Deployments. Mount a volume for `/data`, or keep it ephemeral when `STORAGE_BACKEND=s3`.

### Operational notes

- Run exactly **one** `beat` instance.
- Workers use `acks_late`, so a task from a crashed worker is redelivered. Stage runs make re-execution visible, and enqueueing is idempotent.
- Logs are structured JSON on stdout with secrets redacted. Ship them to your log platform.
- Health endpoints: `GET /health` (liveness, no auth) and `GET /api/system/health` (components, credentials, failures).
- Upgrades: `docker compose pull && docker compose up -d --build`. Migrations run when the API starts.

## Initial launch procedure

Follow it in order; the system stays in TEST mode throughout.

1. Install: `docker compose up -d --build`.
2. Configure environment variables (`.env`) and restart.
3. Connect AI providers (`LLM_PROVIDER`, `TTS_PROVIDER` and their keys). Set the voice ID under **AI configuration**.
4. Connect authorised footage: copy it into `footage/`, then register each file with its owner, licence and platforms under **Footage rights**, and verify its rights.
5–7. Connect YouTube, TikTok and Instagram under **Platform connections**.
8. Press **Verify** on each platform.
9. Run the automated tests: `cd backend && pytest`.
10. Generate a sample video: produce a topic from **Trending topics** (or submit one in **AI research**), then **Run now**.
11. Test-publish in non-public mode: open the job, go to **Publishing**, and use **Test upload to YouTube (private)** or **Send to TikTok drafts**.
12. Verify analytics retrieval: **Performance analytics → Collect metrics now**.
13. Review **System health**.
14. Activate: **Settings & launch → type `ACTIVATE AUTONOMOUS MODE`**. This is only possible when every required checklist item passes. Publishing then stays paused until you press **Resume publishing**.

**Emergency stop:** the **Emergency pause** button in the header (or `POST /api/control/pause-publishing`) stops publishing at once. Queued items are kept.
