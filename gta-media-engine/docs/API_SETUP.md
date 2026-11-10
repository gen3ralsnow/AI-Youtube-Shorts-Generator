# API and credential setup

Every integration below is implemented and tested against mocked endpoints. **None has been exercised against a live account yet**, and the system reports each one as "awaiting credentials" until you configure it and the dashboard's **Verify** call succeeds.

All secrets go in `.env` on the server (see `.env.example`). The browser never receives them: the dashboard's server-side proxy holds the admin token, and OAuth tokens are stored encrypted (Fernet) in PostgreSQL.

OAuth redirect URI for every platform: `{PUBLIC_BASE_URL}/api/platforms/{youtube|tiktok|instagram}/callback`.

## Research sources

| Source | Variables | Where | Notes |
|--------|-----------|-------|-------|
| YouTube Data API v3 | `YOUTUBE_API_KEY` | Google Cloud console → APIs → YouTube Data API v3 → Credentials → API key | `search.list` costs 100 quota units of the default 10,000/day. Research is capped at 80% of the quota, leaving the rest for uploads and analytics. |
| Reddit | `REDDIT_CLIENT_ID`, `REDDIT_CLIENT_SECRET`, `REDDIT_USER_AGENT` | reddit.com/prefs/apps → create app | App-only OAuth (client credentials). Use a descriptive user agent that includes your Reddit username. Check that your use fits Reddit's Data API terms, including commercial-use rules. |
| RSS/Atom | `RSS_FEEDS` | Feeds you are allowed to use | robots.txt is checked before each feed. Add Rockstar Newswire or publisher feeds whose terms you have reviewed. |
| Trends | `SERPAPI_API_KEY` | serpapi.com | Paid. Without it, "search demand" is excluded from scoring (not zeroed). |
| Official channels | `OFFICIAL_DOMAINS`, `OFFICIAL_YOUTUBE_CHANNEL_IDS` | Verify the channel IDs yourself | Drives the "officially confirmed" classification. Leave empty rather than guess. |

## AI providers

| Purpose | Variables | Notes |
|---------|-----------|-------|
| Language model | `LLM_PROVIDER=anthropic` + `ANTHROPIC_API_KEY` (+ `ANTHROPIC_MODEL`), or `LLM_PROVIDER=openai` + `OPENAI_API_KEY` | Without a model the system writes rule-based scripts that only reuse sentences from the sources, with attribution. They are honest but plain. |
| Text-to-speech | `TTS_PROVIDER=elevenlabs` + `ELEVENLABS_API_KEY` + a voice ID in **AI configuration → Voice**; or `TTS_PROVIDER=openai` | Use only stock or designed voices you are licensed to use. Cloning an identifiable person's voice requires their explicit permission and is not supported. ElevenLabs returns character timestamps, which give exact subtitle timing. |
| Transcription | `TRANSCRIPTION_PROVIDER=openai`, or `local` (after `pip install faster-whisper`) | Optional: tightens subtitle timing for providers without alignment. |

Costs: set the `PRICE_*` variables to your providers' current prices. The cost ledger is only as accurate as these numbers.

## YouTube (Shorts)

1. In Google Cloud, enable **YouTube Data API v3** and **YouTube Analytics API**.
2. Configure the OAuth consent screen. Scopes: `youtube.upload`, `youtube.readonly`, `yt-analytics.readonly`.
3. Create an OAuth client of type **Web application** with the redirect URI above. Set `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET`.
4. Dashboard → **Platform connections → Connect YouTube**, then **Verify**.

Limits you should know:
- Uploads from API projects that have not passed Google's **API compliance audit** are locked to **private**. Request the audit before going autonomous.
- An upload costs about 1,600 quota units; the default 10,000/day allows about 6 uploads. Request a quota increase if needed.
- Custom thumbnails need a verified channel. The upload still succeeds without one.
- The API cannot pin comments. The pinned comment is generated for you to pin by hand.

## TikTok

1. In TikTok for Developers, create an app and add **Login Kit** and **Content Posting API** (Direct Post if you need it). Request the scopes `user.info.basic`, `video.upload`, `video.publish`, `video.list`.
2. Register the redirect URI and set `TIKTOK_CLIENT_KEY` and `TIKTOK_CLIENT_SECRET`.
3. Connect and verify from the dashboard.

Compliance model built into the system:
- Default mode is `inbox_draft`: videos arrive in the creator's TikTok inbox and a person finishes posting in the app. This mode works without audit.
- `direct_post` is used only when that publication has a **per-post consent record** from **Publishing manager → TikTok consent**: a privacy level chosen by a person, Music Usage Confirmation, actor and timestamp. Without it, the post is blocked.
- Until TikTok audits your app, direct posts can only be `SELF_ONLY` (private).

## Instagram (Reels)

1. The account must be a **Business or Creator** account.
2. Create a Meta app with **Instagram API with Instagram Login**. Permissions: `instagram_business_basic`, `instagram_business_content_publish`, `instagram_business_manage_insights`. These need Meta App Review for accounts you don't manage as a tester.
3. Set `META_APP_ID`, `META_APP_SECRET` and optionally `META_GRAPH_VERSION`. Register the redirect URI.
4. Connect and verify. Verification reads the account type and the 24-hour publishing limit.

Limits:
- There is no private or draft publishing through the API, so TEST mode never uploads to Instagram.
- The publishing limit (quota) is checked before each upload.
- Long-lived tokens last about 60 days and are refreshed automatically when close to expiry.

## Storage

`STORAGE_BACKEND=s3` with `S3_*` variables works with AWS S3, Cloudflare R2 or MinIO (`docker compose --profile minio up -d`). Create the bucket first, for example with `mc mb local/gta-media-engine` for MinIO. Final renders, thumbnails and audio are copied there. Local working files are cleaned after `TEMP_RETENTION_HOURS`.
