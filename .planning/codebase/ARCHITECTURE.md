# Architecture

**Analysis Date:** 2026-03-28

## Pattern Overview

**Overall:** Event-driven pipeline with async service layer

**Key Characteristics:**
- Telegram bot (aiogram 3.x) + FastAPI in one process: bot handles commands/callbacks, FastAPI handles incoming webhooks
- All heavy work runs in FastAPI `BackgroundTasks` — never `asyncio.create_task()` (Cloud Run constraint)
- Blocking I/O (Supabase client, GCS SDK, Drive SDK) wrapped in `asyncio.to_thread()`
- Stateless services: each service class is instantiated per-request in handlers, not shared singletons
- Supabase `content_items` table is the single state store — all pipeline stages write their status there

## Layers

**Bot Layer:**
- Purpose: Handle Telegram interactions — commands, callbacks, progress messages
- Location: `app/bot/handlers.py`
- Contains: Command handlers, callback handlers, inline keyboard logic, progress bar updates, mode detection (talking_head vs storyboard), pipeline orchestration inline
- Depends on: All services
- Used by: aiogram Dispatcher (via `dp.include_router(bot_router)`)

**Webhook Layer:**
- Purpose: Receive external async callbacks (Creatomate render completion)
- Location: `app/webhooks/creatomate_webhook.py`
- Contains: `POST /webhooks/creatomate/{item_id}` — idempotency guard, background delivery task
- Depends on: `supabase_service`, `gcs_service`, `claude_service` (disabled compliance)
- Used by: Creatomate rendering service (external)

**Service Layer:**
- Purpose: Thin wrappers around external APIs with domain logic
- Location: `app/services/`
- Contains: One module per integration or domain concern
- Depends on: `app/config.py` (settings), each other (handlers wire them together)
- Used by: `handlers.py`, `creatomate_webhook.py`

**Config Layer:**
- Purpose: Central env var access
- Location: `app/config.py`
- Contains: `Settings` class, module-level singleton `settings`
- Depends on: `python-dotenv`
- Used by: All services and handlers

**Entry Point:**
- Location: `app/main.py`
- Triggers: `uvicorn app.main:app` (local) or Cloud Run injecting `PORT`
- Responsibilities: FastAPI app + lifespan (Supabase init, polling vs webhook mode), includes bot router + webhook router

## Data Flow

**talking_head pipeline (user uploads speaker video):**

1. User sends `/ready` → `handlers.py` `cmd_ready()`
2. `DriveService.list_talking_head_files()` → finds video files + optional voiceover audio
3. `DriveService.copy_to_gcs()` streams each file Drive → GCS; saves `gcs_uris` to Supabase
4. Whisper (`WhisperService.transcribe_url_with_timestamps()`) transcribes each video to word-level timestamps; `analyze_silence()` builds silence map for `clip_type` labeling
5. `GeminiService.analyze_video()` via Vertex AI (gs:// URIs direct) → `VideoAnalysis` with `ClipCandidate[]`
6. Analysis result + `whisper_words` saved to Supabase `analysis_result` JSONB; status → `ready_for_render`
7. User sees clip candidates, taps quality button → callback → `render_talking_head()`
8. `VisualDirector.get_visual_blueprint()` (Claude Opus/Sonnet) → transitions + sticker overlay plan
9. `CreatomateService.build_source()` assembles dynamic JSON; `apply_visual_blueprint()` injects transitions + stickers
10. `CreatomateService.submit_render()` → Creatomate API → render_id stored in Supabase
11. Creatomate calls `POST /webhooks/creatomate/{item_id}` → video downloaded to `/tmp`, uploaded to GCS, sent via Telegram `bot.send_video()`

**storyboard ordered pipeline:**

1. Drive folder has numbered video clips (01.mp4…) + voiceover.mp3
2. Drive → GCS copy (same as above); audio URI separated (last file)
3. `audio_processing.process_voiceover()`: ffmpeg silence removal → dynamic speedup (cap 1.5x) → re-uploaded to GCS
4. Whisper transcribes processed voiceover for candidate spans
5. `GeminiService.analyze_storyboard()` → `StoryboardAnalysis` with `StoryboardScene[]` (audio timecodes → video clip mapping)
6. Visual Director (same as above) + `build_source()` with `voiceover_url` on track 2, karaoke on track 3
7. Creatomate render → webhook delivery

**storyboard smart pipeline:**
- Unnumbered clips detected → routes through `analyze_and_propose()` (same as talking_head)
- All clips treated as broll; per-clip voiceover architecture if voiceover present

**State Management:**
- Supabase `content_items.status` field drives pipeline state: `idea` → `awaiting_footage` → `analyzing` → `ready_for_render` → `rendering` → `delivering` → `pending_approval` → `approved`
- `analysis_result` JSONB stores Gemini output + whisper_words + voiceover segments
- No in-memory state between requests — everything is re-fetched from Supabase

## Key Abstractions

**`Clip` dataclass:**
- Purpose: Represents one video segment for render
- Location: `app/services/creatomate_service.py`
- Fields: `source` (presigned URL), `trim_start`, `trim_duration`, `clip_type` (speech/broll), `video_index`, `matched_voiceover_segment`

**`VideoAnalysis` / `ClipCandidate` Pydantic models:**
- Purpose: Gemini structured output for talking_head analysis
- Location: `app/services/gemini_service.py`
- Pattern: Pydantic v2 with `Field(description=...)` — descriptions are used as Gemini schema hints

**`StoryboardAnalysis` / `StoryboardScene` Pydantic models:**
- Purpose: Gemini structured output for storyboard audio→video mapping
- Location: `app/services/gemini_service.py`

**Visual Blueprint dict:**
- Purpose: Claude's output — transitions per clip + sticker overlays
- Location: produced by `app/services/visual_director.py:get_visual_blueprint()`
- Schema: `{overall_style, transitions: [{clip_index, type, direction?}], overlays: [{anchor_id, image_prompt, duration_seconds}], font_family, subtitle_color}`

**`QUALITY_PRESETS` dict:**
- Purpose: dev (720p/24fps) vs prod (1080p/60fps) render dimensions
- Location: `app/services/creatomate_service.py`

## Entry Points

**Telegram Webhook (production):**
- Location: `app/main.py` `POST /webhook`
- Triggers: Telegram sends update to Cloud Run URL
- Responsibilities: Parse `Update`, offload to `background_tasks.add_task(dp.feed_update, bot, update)`, return 200 immediately

**Telegram Polling (local dev):**
- Location: `app/main.py` lifespan
- Triggers: `K_SERVICE` env var absent → `asyncio.create_task(dp.start_polling(bot))`
- Note: Uses `create_task` here (acceptable in local dev, not Cloud Run)

**Creatomate Webhook:**
- Location: `app/webhooks/creatomate_webhook.py` `POST /webhooks/creatomate/{item_id}`
- Triggers: Creatomate calls after render completes
- Responsibilities: Idempotency check, status lock (`delivering`), offload `process_creatomate_render` to BackgroundTasks

**Bot Commands:**
- `/start`, `/help`, `/status` — `app/bot/handlers.py`
- `/reels`, `/post`, `/carousel`, `/stories` — format selection
- `/ready` — main pipeline trigger: detects Drive folder content → routes to correct mode

## Error Handling

**Strategy:** Fail-soft with fallback paths; critical errors send Telegram message to user

**Patterns:**
- Gemini failure → fallback to equal-duration clip split (storyboard) or `analysis_failed` status (talking_head) with manual render option
- Audio processing failure → fallback to original unprocessed voiceover
- File copy failure → accumulate `failed_files`, continue with what succeeded; abort if zero succeed
- Creatomate render failure → webhook sets `render_failed`, notifies user via Telegram
- Duplicate webhook delivery → idempotency check on `status` field before processing
- All service calls in handlers wrapped in try/except with `logger.error()` or `logger.warning()`
- Supabase blocking calls wrapped in `asyncio.to_thread()` with lambda to capture closure

## Cross-Cutting Concerns

**Logging:** `logging.getLogger(__name__)` per module; root level INFO, `app.*` loggers at DEBUG (`app/main.py` config). No structured logging.

**Validation:** Pydantic models for Gemini structured output. No request-level validation middleware.

**Authentication:** No user auth — bot is single-user (Romina). All Telegram users can invoke commands.

**Presigned URLs:** All Creatomate-facing video URLs are GCS V4 signed (6hr expiry). Cached per `gs://` URI via `_url_cache` dict in `gcs_service.py` (if implemented) — see CLAUDE.md note.

**ffmpeg dependency:** Required at runtime (installed in Dockerfile). Used by `audio_processing.py` and `whisper_service.py` for audio extraction/manipulation.

---

*Architecture analysis: 2026-03-28*
