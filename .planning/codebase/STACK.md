# Technology Stack

**Analysis Date:** 2026-03-28

## Languages

**Primary:**
- Python 3.12 — all application code

## Runtime

**Environment:**
- Python 3.12 (CPython)
- Container: `python:3.12-slim` via Docker
- System dependency: `ffmpeg` installed in container (required by `whisper_service.py` and `audio_processing.py`)

**Package Manager:**
- pip
- Lockfile: `requirements.txt` (not pinned to exact versions — `aiogram>=3.0.0` style)

## Frameworks

**Core:**
- FastAPI (unpinned) — HTTP server, webhook endpoints, lifespan management
- uvicorn (unpinned) — ASGI server, started via `CMD uvicorn app.main:app`
- aiogram 3.x — Telegram bot framework, async, router-based handlers

**AI/ML:**
- anthropic (unpinned) — Claude API client (AsyncAnthropic), used in `claude_service.py` and `visual_director.py`
- openai (unpinned) — OpenAI API client (AsyncOpenAI), used in `whisper_service.py` for Whisper-1 transcription
- google-genai (unpinned) — Gemini via Vertex AI, used in `gemini_service.py`
- tenacity (unpinned) — retry logic on Gemini and Creatomate HTTP calls

**Data/Validation:**
- pydantic — structured output schemas for Gemini responses (`ClipCandidate`, `VideoAnalysis`, `StoryboardAnalysis`)
- python-dotenv — `.env` loading via `load_dotenv(override=True)` in `config.py`

**HTTP:**
- httpx (unpinned) — async HTTP client for Creatomate API calls and render downloads
- requests (unpinned) — sync HTTP in `gcs_service.py` upload_from_url

## Key Dependencies

**Critical:**
- `aiogram>=3.0.0` — entire bot flow; router, FSM, callback handling in `app/bot/handlers.py`
- `anthropic` — Visual Director (`visual_director.py`) + script generation (`claude_service.py`)
- `openai` — Whisper speech transcription (`whisper_service.py`, model `whisper-1`)
- `google-genai` — video analysis via Vertex AI (`gemini_service.py`, model `gemini-2.5-flash`)
- `supabase` — all persistent state; content items table (`supabase_service.py`)
- `google-cloud-storage` — GCS blob operations, V4 signed URL generation (`gcs_service.py`)
- `google-api-python-client` — Google Drive v3 API (`drive_service.py`)
- `httpx` — Creatomate render API calls and render result download

**Infrastructure:**
- `google-auth` / `google-oauth2` — service account credential loading from `service-account.json`
- `tenacity` — exponential backoff retry on Gemini 429/5xx and Creatomate HTTP errors

## Configuration

**Environment:**
- All config via env vars, read in `app/config.py` as `Settings` class attributes
- `.env` file loaded at startup with `python-dotenv` (`override=True`)
- Key vars:
  - `TELEGRAM_BOT_TOKEN` (also `BOT_TOKEN` fallback)
  - `SUPABASE_URL`, `SUPABASE_SERVICE_KEY` (also `SUPABASE_KEY` fallback)
  - `ANTHROPIC_API_KEY`
  - `OPENAI_API_KEY`
  - `GEMINI_API_KEY`
  - `CREATOMATE_API_KEY`
  - `GCS_BUCKET`
  - `DRIVE_TALKING_HEAD_FOLDER_ID`, `DRIVE_STORYBOARD_FOLDER_ID`
  - `GOOGLE_APPLICATION_CREDENTIALS` — path to `service-account.json`
  - `BASE_URL` — public Cloud Run URL for webhook callbacks
  - `PEXELS_API_KEY` — disabled feature, still read

**Build:**
- `Dockerfile` — single-stage `python:3.12-slim`, copies `requirements.txt` then `.`
- No build args or multi-stage build

## Runtime Modes

**Local dev:**
- `python -m app.main` — starts uvicorn, detects absence of `K_SERVICE` env var, uses aiogram polling
- `asyncio.create_task(dp.start_polling(bot))` used in local mode only

**Cloud Run (prod):**
- `K_SERVICE` env var present → webhook mode
- Telegram updates arrive at `POST /webhook`, processed via `FastAPI BackgroundTasks`
- `asyncio.create_task()` NOT used in Cloud Run (orphan task risk)

## Quality Presets

**Render quality** controlled by `quality` parameter passed through bot callbacks:
- `"dev"` → 720×1280 @ 24fps, model `claude-sonnet-4-6`
- `"prod"` → 1080×1920 @ 60fps, model `claude-opus-4-6`
- Defined in `app/services/creatomate_service.py` as `QUALITY_PRESETS` dict

## Platform Requirements

**Development:**
- Python 3.12
- `ffmpeg` in PATH (for `audio_processing.py` silence removal and `whisper_service.py` audio extraction)
- GCP service account JSON file at `GOOGLE_APPLICATION_CREDENTIALS` path

**Production:**
- GCP Cloud Run (`europe-west1`)
- GCP project: `romina-content-factory-489121`
- Service account: `content-factory-sa@romina-content-factory-489121.iam.gserviceaccount.com`
- Deploy: `./scripts/deploy.sh`

---

*Stack analysis: 2026-03-28*
