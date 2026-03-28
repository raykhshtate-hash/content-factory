# External Integrations

**Analysis Date:** 2026-03-28

## APIs & External Services

**AI — Language Models:**
- Anthropic Claude — Visual Director (transition/sticker planning) + script/caption generation
  - SDK/Client: `anthropic` (`AsyncAnthropic`)
  - Auth: `ANTHROPIC_API_KEY`
  - Models: `claude-opus-4-6` (prod quality), `claude-sonnet-4-6` (dev quality)
  - Used in: `app/services/visual_director.py`, `app/services/claude_service.py`

**AI — Video Analysis:**
- Gemini 2.5 Flash via Vertex AI — clip selection, semantic broll matching, storyboard scene mapping
  - SDK/Client: `google-genai` (`genai.Client(vertexai=True)`)
  - Auth: GCP service account credentials (`GOOGLE_APPLICATION_CREDENTIALS`)
  - Project: `romina-content-factory-489121`, location: `europe-west1`
  - Model: `gemini-2.5-flash` (default), temperature=0
  - Used in: `app/services/gemini_service.py`
  - Note: videos passed as `gs://` URIs directly — no download to server

**AI — Speech Transcription:**
- OpenAI Whisper — word-level timestamps for karaoke, silence detection, sticker placement
  - SDK/Client: `openai` (`AsyncOpenAI`)
  - Auth: `OPENAI_API_KEY`
  - Model: `whisper-1` with `timestamp_granularities=["word"]`
  - Used in: `app/services/whisper_service.py`

**AI — Image Generation (via Creatomate):**
- OpenAI GPT-Image-1.5 — sticker generation (transparent background AI images)
  - NOT called directly — invoked through Creatomate `ai_image` element type
  - Provider declared: `openai`, model `gpt-image-1.5`, `background: transparent`

**Video Rendering:**
- Creatomate — dynamic JSON-based video rendering (no templates)
  - Client: `httpx` async HTTP to `https://api.creatomate.com/v1/renders`
  - Auth: `CREATOMATE_API_KEY` (Bearer token in `Authorization` header)
  - Used in: `app/services/creatomate_service.py`
  - Callback: Creatomate POSTs render result to `{BASE_URL}/webhooks/creatomate/{item_id}`

**Stock Video (Disabled):**
- Pexels — B-roll stock footage search (feature disabled, service still present)
  - Client: `httpx` sync requests to `https://api.pexels.com`
  - Auth: `PEXELS_API_KEY`
  - File: `app/services/pexels_service.py` (not imported in active pipeline)

## Data Storage

**Databases:**
- Supabase (PostgreSQL) — all persistent state, content pipeline tracking
  - URL: `https://yqqjazejuxqeeucyjufz.supabase.co` (note: `.co` not `.com`)
  - Connection env vars: `SUPABASE_URL`, `SUPABASE_SERVICE_KEY` (fallback: `SUPABASE_KEY`)
  - Client: `supabase-py` v2.x, singleton pattern via `_get_client()` in `app/services/supabase_service.py`
  - Primary table: `content_items` (uuid PK, status, format, analysis_data JSONB, clips, render IDs)
  - JSONB fields use `.model_dump()` (NOT `.model_dump_json()`)
  - All public functions async; blocking calls wrapped in `asyncio.to_thread`

**File Storage:**
- Google Cloud Storage (GCS) — video files, processed audio, rendered output
  - Bucket: `GCS_BUCKET` env var
  - Client: `google-cloud-storage` (`storage.Client`)
  - Auth: service account JSON at `GOOGLE_APPLICATION_CREDENTIALS`
  - File: `app/services/gcs_service.py`
  - All access via V4 presigned URLs (6hr expiry, 360min minimum) — bucket is NOT public
  - Signed URLs cached per `gs://` URI to avoid redundant signing
  - Rendered finals stored at: `renders/{item_id}/final.mp4`

## File Ingestion

**Google Drive — Input Videos:**
- Google Drive v3 API — source videos uploaded by user to watched folders
  - SDK/Client: `google-api-python-client` (`build("drive", "v3")`)
  - Auth: service account credentials (same JSON as GCS)
  - Scopes: `drive` + `cloud-platform`
  - INBOX talking_head folder ID: `1FXC2WR-E2MCFXMfXLy0uCOz0LLp6upLd` (env: `DRIVE_TALKING_HEAD_FOLDER_ID`)
  - INBOX storyboard folder ID: `1ro3BwV7-u0wKq51PboVRMs2k1aeAs24L` (env: `DRIVE_STORYBOARD_FOLDER_ID`)
  - Storyboard output folder: `1KLVCb6z-DisAhbtZf55nKACtJzF9Cw5P`
  - File: `app/services/drive_service.py`
  - Drive → GCS streaming: chunked 10MB, no full-file buffering

## Telegram Bot

**Telegram Bot API:**
- aiogram 3.x — bot framework
  - Auth: `TELEGRAM_BOT_TOKEN` (fallback: `BOT_TOKEN`)
  - Local dev: long polling (`dp.start_polling(bot)`)
  - Production: webhook at `POST /webhook`, updates dispatched via `FastAPI BackgroundTasks`
  - `allowed_updates` must include `callback_query`
  - File: `app/bot/handlers.py`, `app/main.py`

## Audio/Video Processing

**ffmpeg (system binary):**
- Silence removal from voiceover (`audio_processing.py`)
- Dynamic speedup with `atempo` filter (capped at 1.5x)
- Audio extraction for Whisper transcription (`whisper_service.py`)
- Duration probing via `ffprobe`
- Installed in Docker image via `apt-get install ffmpeg`

## Monitoring & Observability

**Error Tracking:** None (no Sentry or equivalent)

**Logs:**
- `logging` stdlib — root level INFO, `app.*` loggers at DEBUG
- Log format: `%(levelname)s %(name)s: %(message)s`
- Cloud Run logs accessible via: `gcloud run services logs read content-factory --region europe-west1`

## CI/CD & Deployment

**Hosting:**
- Google Cloud Run, region `europe-west1`
- Service name: `content-factory`
- Public URL: `https://content-factory-7ufgsc2feq-ew.a.run.app`
- Service account: `content-factory-sa@romina-content-factory-489121.iam.gserviceaccount.com`

**CI Pipeline:** None (manual deploy only)

**Deploy:**
- `./scripts/deploy.sh` — builds Docker image, pushes to GCR, deploys to Cloud Run
- `./scripts/merge.sh` — merges `dev` → `main`

## Webhooks & Callbacks

**Incoming webhooks:**
- `POST /webhook` — Telegram bot updates (Cloud Run mode only)
- `POST /webhooks/creatomate/{item_id}` — Creatomate render result delivery
  - Handler: `app/webhooks/creatomate_webhook.py`
  - On success: downloads render, uploads to GCS, sends video to Telegram user
  - On failure: updates Supabase status to `render_failed`, notifies user

**Outgoing webhooks:**
- Creatomate render requests include `webhook_url = f"{settings.BASE_URL}/webhooks/creatomate/{item_id}"` as callback

## Environment Configuration

**Required env vars:**
- `TELEGRAM_BOT_TOKEN` — Telegram bot authentication
- `SUPABASE_URL` — Supabase project URL
- `SUPABASE_SERVICE_KEY` — Supabase service role key
- `ANTHROPIC_API_KEY` — Claude API
- `OPENAI_API_KEY` — OpenAI Whisper API
- `GEMINI_API_KEY` — Gemini API key (used as fallback; primary auth is via service account for Vertex AI)
- `CREATOMATE_API_KEY` — Creatomate render API
- `GCS_BUCKET` — GCS bucket name
- `DRIVE_TALKING_HEAD_FOLDER_ID` — Google Drive inbox folder
- `DRIVE_STORYBOARD_FOLDER_ID` — Google Drive storyboard folder
- `GOOGLE_APPLICATION_CREDENTIALS` — path to service account JSON file
- `BASE_URL` — public HTTPS URL for webhook registration (Cloud Run URL)

**Secrets location:**
- `.env` file (local dev, never committed)
- Cloud Run environment variables (production)
- `service-account.json` at project root (path set via `GOOGLE_APPLICATION_CREDENTIALS`)

---

*Integration audit: 2026-03-28*
