# Content Factory — Automated Content Pipeline for Romina

## What is this
Telegram bot that automates Instagram Reels creation for Romina (dermatologist, Germany).
Pipeline: idea → script → video upload → AI analysis → render → delivery.

## Architecture
- **Runtime**: Python 3.12, FastAPI, aiogram 3.x
- **AI**: Claude Sonnet 4.6 (scripts), Gemini 2.5 Flash via Vertex AI (video analysis), Whisper (voice transcription only)
- **Render**: Creatomate API (dynamic JSON source, NO templates)
- **Infra**: Google Cloud Run (europe-west1), GCS, Supabase (PostgreSQL)
- **Bot**: Telegram webhook in Cloud Run, polling in local dev

## Key Files
- `app/bot/handlers.py` — all bot logic, command handlers, render flow
- `app/services/creatomate_service.py` — video render via JSON source
- `app/services/gemini_service.py` — Vertex AI video analysis
- `app/services/claude_service.py` — script generation + compliance
- `app/services/drive_service.py` — Google Drive INBOX → GCS transfer
- `app/services/gcs_service.py` — GCS operations + presigned URLs
- `app/services/whisper_service.py` — voice message transcription (NOT used for subtitles)
- `app/webhooks/creatomate_webhook.py` — render completion → Telegram delivery

## Critical Rules — NEVER violate these

### Cloud Run
- **NEVER use `asyncio.create_task()`** — Cloud Run kills CPU after response. Use `await` directly or FastAPI `BackgroundTasks`.
- Webhook handler (`/webhook`) uses `background_tasks.add_task(dp.feed_update, ...)` — this is correct, don't change.
- All long operations (Gemini analysis, render) run inside this BackgroundTask chain.

### Supabase
- **Use `model_dump()` for JSONB columns**, NOT `model_dump_json()` — the latter produces a string, causing silent failures.
- `analysis_result` column stores Gemini output as JSONB dict.

### GCS & URLs
- **Always use presigned URLs** for Creatomate — GCS bucket is NOT public.
- `gcs_service.generate_presigned_url()` creates V4 signed URLs (6 hour expiry).
- Cache signed URLs per unique `gs://` URI to avoid re-signing the same file.

### Creatomate
- We use **dynamic JSON source** (not templates). All render logic is in `creatomate_service.py`.
- Flat timeline: each video clip is named `clip-{i}`, each gets its own karaoke text element with `transcript_source`.
- `transcript_effect: "karaoke"` = native Creatomate word-by-word subs. NO Whisper needed for subtitles.
- **Zoom/pan (Ken Burns)**: Each clip alternates zoom-in (100%→110%) and zoom-out (110%→100%) via `animations` array for dynamic feel.
- **Dev/Prod quality**: `quality="dev"` = 720×1280 @24fps, `quality="prod"` = 1080×1920 @60fps.

### Gemini
- Model: `gemini-2.5-flash` via Vertex AI (NOT Google AI Studio, NOT Gemini 1.5).
- Videos passed as `gs://` URIs directly — no download needed.
- `video_index` in schema is REQUIRED to map clips to correct source video.
- Structured output via `response_schema=VideoAnalysis` — guarantees valid JSON.

### Photos
- **Do NOT filter out photos permanently** from the Drive→GCS pipeline. They're needed for future posts/carousels.
- Current filter is video-only for Gemini analysis, but photos are preserved in GCS.

### Telegram
- `allowed_updates` must include `callback_query` in `setWebhook` (set in `deploy.sh`).
- Always respond 200 OK immediately to Telegram webhooks to prevent duplicate deliveries.

## Development Workflow
- **Diagnose before fixing** — always identify root cause first, don't just try random fixes.
- **One change at a time** — deploy and test each change separately.
- Check Cloud Run logs: `gcloud run services logs read content-factory --region europe-west1 --limit 50`
- Local dev: `python -m app.main` (auto-detects local mode via missing `K_SERVICE` env var).

## Deploy
```bash
sh scripts/deploy.sh
```

## GCP Details
- Project: `romina-content-factory-489121`
- Region: `europe-west1`
- Cloud Run URL: `https://content-factory-7ufgsc2feq-ew.a.run.app`
- GCS Bucket: configured via `GCS_BUCKET` env var
- Drive INBOX Folder: `1ro3BwV7-u0wKq51PboVRMs2k1aeAs24L`
