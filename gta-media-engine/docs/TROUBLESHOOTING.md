# Troubleshooting

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| Every API call returns 401 | `ADMIN_API_TOKEN` unset, or the dashboard's `ENGINE_ADMIN_TOKEN` doesn't match | Set the same value for both and restart |
| Dashboard shows "ENGINE_ADMIN_TOKEN is not configured" | Dashboard container env missing | Check the `dashboard` service environment |
| Dashboard returns 503 in production | `DASHBOARD_USER` or `DASHBOARD_PASSWORD` unset | Set both; production refuses to serve without them |
| Job **blocked**: "no rights-verified footage available" | No footage asset is verified for every enabled platform | Register footage, set allowed platforms, press **Verify rights**; or disable a platform under Settings |
| Job **blocked**: "fact confidence … below threshold" | Script claims not supported by the attached sources | Add better sources to the topic, or reject it. Do not lower the threshold to force publication |
| Job **failed** at script_validation: "script too thin" | Sources contain too little material for even 15 s | Expected for weak topics. Add a richer source or reject the topic |
| Job **blocked**: "… budget reached" and processing paused | Daily or monthly cap hit | Raise the cap under **Costs** if intended, then **Resume processing** |
| Render QC fails `subtitle_timing` | Overlapping or off-video cues (custom captions) | Edit the captions; the job re-renders |
| Render QC fails `black_frames` | Footage contains black segments | Trim the source footage or re-analyse it; selection prefers high-motion shots |
| QC "technical passed" but **not publishable** | Placeholder voice, test-only assets, or rights problems | Configure a TTS provider; replace demo footage with real authorised footage |
| Publication stays **queued** with "publishing is paused" | Global pause on | **Resume publishing** (AUTONOMOUS mode only) |
| Publication **dry_run** | TEST mode | Expected. Activating AUTONOMOUS mode re-queues prepared items |
| Publication **blocked**: "account is not connected" | OAuth missing or expired | Platform connections → Reconnect → Verify |
| YouTube videos stay private | API project not audited by Google | Request the YouTube API compliance audit |
| TikTok posts land in drafts | Default `inbox_draft` mode, or no per-post consent | Finish in the TikTok app, or record consent per post with `direct_post` mode |
| TikTok `spam_risk_too_many_posts` | Platform rate limit | The manager backs off. Lower `max_per_day` for TikTok |
| Instagram "publishing limit reached" | 24 h API publishing quota | Wait. Items stay queued |
| Topics never appear | No research source configured | **System health** lists what is missing |
| API hangs under load (dev) | SQLite lock contention | Use PostgreSQL (`DATABASE_URL`) for anything beyond local testing |
| `ffmpeg` errors about `ass` filter | FFmpeg built without libass | Use the provided Docker image (Ubuntu ffmpeg includes libass) |
| Workers crash: `No module named 'packaging'` | Old image | Rebuild the backend image (`packaging` is now a pinned dependency) |

Diagnostics:
- `GET /api/system/health`: component status, missing credentials, recent agent failures.
- Each job's **Stages** tab in the dashboard shows every attempt's error and output.
- `python -m engine.cli status`: mode and launch checklist from the shell.
- `docker compose logs -f worker-video`: rendering logs.
