# Necessary repairs report

Status as of this build. Severity scale:
- **CRITICAL**: must be fixed before launch.
- **HIGH**: fix before autonomous operation.
- **MEDIUM**: recommended.
- **LOW**: optional.

## CRITICAL: before launch

| # | Category | Issue | Action |
|---|----------|-------|--------|
| C1 | Incomplete integrations | **No live credentials exist yet.** YouTube, TikTok, Instagram, ElevenLabs/OpenAI TTS, Anthropic/OpenAI LLM, YouTube Data API, Reddit and SerpApi are implemented and tested only against mocked APIs. None is verified operational | Configure credentials (see API_SETUP.md), press **Verify** for each platform, and run a private test upload per platform |
| C2 | Copyright risk | **No authorised GTA VI footage is registered.** The only footage is synthetic test patterns, which QC blocks from publishing | Register footage you hold rights to (own captures of released games, licensed libraries, press-kit material within its terms) and record each licence |
| C3 | Missing dependency | No TTS provider configured, so all narration is a placeholder tone and nothing is publishable | Set `TTS_PROVIDER` + key + licensed voice ID |
| C4 | Publishing restriction | YouTube API projects that have not passed Google's compliance audit can only upload private videos | Apply for the audit early; it can take weeks |
| C5 | Publishing restriction | TikTok direct posting needs app audit and a person's consent per post; unattended public TikTok posting is not possible under TikTok's rules | Use the default inbox-draft mode, or record per-post consent in the dashboard |
| C6 | Publishing restriction | Instagram content publishing needs Meta App Review and a professional account | Submit for review with a screencast of the dashboard flow |
| C7 | Legal | IP and platform-policy review has not been done by counsel | Get the review described in COMPLIANCE_REVIEW.md |

## HIGH: before autonomous operation

| # | Category | Issue | Action |
|---|----------|-------|--------|
| H1 | Security | Single shared admin token, no per-user identity or MFA | Put the dashboard behind SSO / an identity-aware proxy |
| H2 | Incomplete integration | No trends provider means scores exclude search demand; saturation search is off by default (it costs 100 YouTube quota units per topic) | Add `SERPAPI_API_KEY` or an equivalent; enable saturation search if your quota allows |
| H3 | Quality | Without a language model, scripts are rule-based: accurate, but stitched from source sentences and less engaging | Configure `LLM_PROVIDER` |
| H4 | Accuracy | Lexical fact checking can miss subtle misstatements | Configure a model for the second-opinion check; spot-check news videos |
| H5 | Platform limits | YouTube default quota (10,000 units/day) allows about 6 uploads per day once research is included | Request a quota increase or lower `videos_per_day` |
| H6 | Operations | No alerting integration: budget alerts and failures show in the dashboard and logs only | Ship logs to a monitoring stack and alert on `level=ERROR`, budget alerts and `/api/system/health` |
| H7 | Copyright | No automated detection of third-party content inside registered footage (for example a licensed-music radio in gameplay); the system relies on the rights register | Keep game audio muted (default) and register only footage you have reviewed |

## MEDIUM: recommended

| # | Category | Issue | Action |
|---|----------|-------|--------|
| M1 | Performance | QC runs about 5 FFmpeg decode passes per export (3 exports per video). A 25 s video takes about 60–90 s of CPU in total | Share passes across exports (the video stream is identical), or QC the master once plus a quick audio check per export |
| M2 | Performance | Footage analysis (scene and motion detection) runs synchronously at registration | Move it to the video queue for large libraries |
| M3 | Scalability | In-process rate limiter; SQLite only suitable for development | Use PostgreSQL in all shared deployments; add a proxy-level rate limit |
| M4 | Feature gap | Subtitle timing for OpenAI TTS is estimated (no alignment), refined to the speech span only when transcription is configured | Use ElevenLabs (exact timestamps) or enable transcription |
| M5 | Feature gap | Clip selection uses tags, descriptions, transcripts and motion, not visual recognition of what is on screen | Add a vision model to tag shots automatically |
| M6 | Feature gap | YouTube API cannot pin comments; the pinned comment must be posted by hand | None available via the API |
| M7 | Security | Encryption key rotation is manual | Add MultiFernet rotation |
| M8 | Reproducibility | Python requirements use version ranges | Commit a lock file; add dependency scanning in CI |
| M9 | Analytics | Retention curves (per-second audience retention) are not collected; average view duration and percentage are | Add YouTube Analytics `audienceWatchRatio` reports |

## LOW: optional enhancements

| # | Issue |
|---|-------|
| L1 | Thumbnail A/B testing is not automated (YouTube's "Test & Compare" has no public API) |
| L2 | Only English content and voice |
| L3 | Brand name "VI Signal" and palette are placeholders |
| L4 | Dashboard has no per-chart time range selection |
| L5 | Music selection uses least-used rotation, not mood matching |

## Bugs found and fixed during this build

| Bug | Found by | Fix |
|-----|----------|-----|
| `/control/{action}` route swallowed `/control/activate-autonomous` (activation always 404) | API integration test | Declared the generic route last |
| Subtitle chunks overlapped (two caption lines stacked on screen); QC tolerance hid it | Visual inspection of rendered frames | End chunks at the next chunk's start; strict overlap check; regression test |
| Scene headlines overflowed the 1080 px frame | Visual inspection | Enabled libass smart wrapping; captions limited to 4 words / 48 chars |
| Media file endpoints held a DB connection for the whole stream, exhausting the pool and hanging the API | Live dashboard test (py-spy stack dump) | Path lookup in a short session before streaming; regression test |
| Dashboard media URLs double-prefixed (`/api/api/files/...`) | Browser console 404s | `media()` URL helper |
| A job could silently fail to start from the dashboard | Live API test | Claim the job synchronously; API returns 409 when it can't start |
| Mobile layout overflowed horizontally | Playwright overflow check | `min-w-0` on grid cards |
| Celery workers crashed in a clean image (`packaging` missing) | Docker Compose boot | Added the dependency |
| QC publishability ignored content blockers (test-only assets) | Code review | Content blockers folded into each render's QC result |
| Script supersede and QC report were lost to savepoint rollback | Code review | Gate applied after the savepoint commits |
| OAuth callback reflected query text into HTML (XSS) | Code review | HTML-escaped; regression test |
| S3 storage backend was never called | Code review | Final artefacts persisted after render and thumbnails |
| Flaky OAuth test (random base64 could contain the asserted substring) | Repeated test runs | Correct assertion |
