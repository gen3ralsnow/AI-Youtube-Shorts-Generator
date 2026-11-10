# Security review

Scope: backend API, workers, dashboard, deployment configuration. Reviewed by the implementer, so treat it as a self-review; an independent review is recommended before handling production credentials.

## Controls in place

| Area | Control | Where |
|------|---------|-------|
| Authentication | Bearer tokens with admin and viewer roles, compared in constant time; unauthenticated requests get 401, viewers get 403 on writes | `engine/security.py`, `api/deps.py` |
| Dashboard access | HTTP basic auth on every page and proxy route; production refuses to serve without credentials | `dashboard/src/middleware.ts` |
| Secret exposure | Admin token held only by the dashboard **server** (proxy route), never shipped to the browser; no API keys in frontend code | `dashboard/src/app/api/engine/[...path]/route.ts` |
| Credential storage | OAuth access/refresh tokens and PKCE verifiers encrypted with Fernet (AES-128-CBC + HMAC); production refuses to start the vault without `ENGINE_ENCRYPTION_KEY` | `security.CredentialVault` |
| OAuth | PKCE (S256) and a single-use `state` compared in constant time; mismatches rejected as possible CSRF | `publishing/oauth.py` |
| Logging | JSON logs with redaction of bearer tokens, API keys, `access_token`/`refresh_token`/`client_secret` and known key formats; HTTP error bodies are redacted before logging | `engine/logs.py` |
| Input validation | Pydantic models on every write endpoint; settings validated by type and range; protected keys (`mode`) cannot be set through the settings API | `api/routes/*`, `settings_store.py` |
| Rate limiting | Per-client sliding window on `/api/*` (in-process; front it with Caddy or WAF limits for multi-instance setups) | `security.RateLimiter`, `api/app.py` |
| Audit | Every administrative action is recorded with actor, target and details | `audit_logs` table, `/api/audit` |
| File handling | Uploads: extension allow-list, sanitised file names, 2 GB cap, no overwrite. Registered paths must resolve inside configured footage roots. Served files are looked up by DB id only and must live under the data directory; proxy path segments are validated | `routes/assets.py`, `video/footage.py`, `routes/content.py` |
| XSS | OAuth callback page HTML-escapes all reflected text (regression test included) | `routes/distribution.py` |
| Headers | `X-Content-Type-Options`, `X-Frame-Options: DENY`, `Referrer-Policy` on API and dashboard; HSTS at Caddy | `api/app.py`, `next.config.ts`, `deploy/Caddyfile` |
| Network exposure | Production publishes only 80/443; the API domain exposes only `/health` and OAuth callbacks; Postgres and Redis are internal | `docker-compose.prod.yml`, `Caddyfile` |
| Containers | Non-root users in both images | Dockerfiles |
| Publishing safety | TEST mode by default; publishing paused by default; activation requires the checklist plus a typed confirmation phrase; global emergency pause | `routes/control.py`, `publishing/manager.py` |
| Resource exhaustion | File streams don't hold DB connections (regression test included); bounded retries; budget guard | `routes/content.py`, `orchestrator.py`, `costs.py` |

## Findings and residual risks

| Severity | Finding | Recommendation |
|----------|---------|----------------|
| HIGH | Single shared admin token, no per-user accounts or MFA | Put the dashboard behind SSO (Cloudflare Access, oauth2-proxy, or IAP) for real user identity and MFA; rotate `ADMIN_API_TOKEN` regularly |
| MEDIUM | Rate limiter is per-process memory | For multiple API replicas, add limits at Caddy or the load balancer, or a Redis-backed limiter |
| MEDIUM | `ENGINE_ENCRYPTION_KEY` rotation is not automated (rotating breaks decryption of stored tokens) | Rotation procedure: disconnect platforms, rotate the key, reconnect. Or add MultiFernet support |
| MEDIUM | Development fallback encryption key is derived from the admin token when no key is set (non-production only) | Always set `ENGINE_ENCRYPTION_KEY`; health shows "development key in use" |
| MEDIUM | Audit actor is the client IP, not a person | Solved together with SSO (pass the identity header through the proxy) |
| LOW | Dependencies use ranges in `requirements.txt` | Generate a lock file (`pip-compile` or `uv lock`) for reproducible production builds and scan it (pip-audit, npm audit) in CI |
| LOW | Basic auth credentials travel on every dashboard request | Acceptable only over HTTPS (enforced by Caddy); prefer SSO |
| INFO | LLM prompts include third-party text (feeds, Reddit) | Prompts treat sources as data and ask for JSON only. Outputs are schema-validated and fact-checked against sources, and cannot trigger actions (agents have no tool access). Keep it that way |
