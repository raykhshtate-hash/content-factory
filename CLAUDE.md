# Content Factory — CLAUDE.md

## What is this
Telegram bot: video upload → AI analysis → Visual Director → Creatomate render → delivery.
Russian-language Instagram Reels for Romina (doctor-cosmetologist, Germany/Israel).

**Repo:** `https://github.com/raykhshtate-hash/content-factory`
**Branch:** `dev` (active) | `v0.1.5` on `main` (stable)

## Architecture
Python 3.12, FastAPI, aiogram 3.x | Claude Opus 4.6 (prod/1080p) / Sonnet 4.6 (dev/720p) — Visual Director | Gemini 2.5 Flash via Vertex AI | Creatomate (dynamic JSON, no templates) | GCS + Cloud Run (europe-west1) | Supabase (PostgreSQL)

**GCP:** `romina-content-factory-489121` | Cloud Run: `content-factory` | URL: `https://content-factory-7ufgsc2feq-ew.a.run.app`
**SA:** `content-factory-sa@romina-content-factory-489121.iam.gserviceaccount.com`
**Drive:** INBOX `1ro3BwV7-u0wKq51PboVRMs2k1aeAs24L` | storyboard `1KLVCb6z-DisAhbtZf55nKACtJzF9Cw5P` | talking_head `1FXC2WR-E2MCFXMfXLy0uCOz0LLp6upLd`

## Key Files
- `handlers.py` — bot logic + render flow | `creatomate_service.py` — render JSON
- `visual_director.py` — Claude picks transitions + stickers | `gemini_service.py` — video analysis
- `audio_processing.py` — voiceover silence removal + speedup (storyboard only)
- `drive_service.py` → `gcs_service.py` → presigned URLs | `creatomate_webhook.py` — delivery

## Content Modes

### talking_head (INBOX/talking_head/)
Video has audio (speaker on camera). Gemini selects best clips with timestamps. Karaoke `transcript_source` = video element name (per-clip). Stickers always needed — same face gets boring. No audio processing.

### storyboard ordered (INBOX/storyboard/, numbered files)
Numbered clips (01.mp4, 02.mp4...) + `voiceover.mp3`. Video `volume: "0%"`. Single karaoke on track 3 with `transcript_source: "voiceover"`. Audio element `id="voiceover"` on track 2. Voiceover processed: silence removal → dynamic speedup (capped 1.5x) → re-speed for transition overlap. Whisper candidate spans for sticker placement. Stickers almost never (max 1).

### storyboard smart (INBOX/storyboard/, unnumbered files)
Unnumbered clips + `voiceover.mp3`. Two-pass Gemini: Pass 1 (Flash) discovers story, Pass 2 (Pro) selects clips. Montage (vo ≤15s, ≤3 segs): spread voiceover across timeline, music loop track 8, ambient 15%. Narrative: per-clip voiceover (reuses hybrid logic). Visual Director: Sonnet for storyboard, Opus for TH/hybrid.
- `/remix` — same clips, fresh creative | `/addclip` — Gemini search + manual fallback → auto-render
- Delivery buttons: "Перемонтаж" + "Добавить кадр" (Cloud Run webhook)

### Hybrid Mode (talking_head + voiceover)
Per-clip voiceover architecture. Each matched broll gets its own audio element.
Pipeline: Whisper voiceover → segments + words | Gemini semantic matching (broll → segment via `matched_voiceover_segment`) | Python dedup (one segment = one broll, `used_segments` set) | `build_source` per-clip audio on track 5 with `trim_start`/`trim_duration` from voiceover segment | Per-clip karaoke: `phrase_time = clip_render_start + (word.start - seg.start)`.
Unmatched broll: volume 70% ambient, no audio element. Speech clips: original audio + speech karaoke.
Ducking: REMOVED for hybrid. Voiceover only plays on matched broll, never overlaps speech.
Supabase: `voiceover_segments` + `voiceover_words` + `voiceover_duration` in `analysis_data`.
Clip dataclass: `matched_voiceover_segment` field added.

### Clip Pre-buffer
**Gap-aware pre-buffer**: 0.5s capped to actual inter-clip gap for same-source clips. Prevents audio replay during transitions.

### Clip Duration Clamp
`_candidates_to_clips` clamps `end = min(end, src_dur - 0.5)` via ffprobe. Prevents black frames from Gemini timestamps beyond source length.

### Pipeline Shortcuts
- **GCS reuse**: `/ready` compares video filenames with prev item → skip upload if match (+ voiceover_gcs_uri).
- **Stale auto-cancel**: items in processing_video/analyzing >10 min → cancelled on next `/ready`.
- **`/ready` without `/reels`**: auto-creates item if no `awaiting_footage`.
- **Music loops**: track 8, Splice library, `duration: total_duration`, `audio_fade_out: 2.0s`.
- **Spread voiceover**: montage mode, per-segment audio elements evenly across timeline.

### B-roll Timeline Mapping
`render_time = render_start + (source_time - trim_start)` — Gemini timecodes are in source timeline, not rendered output.

---

## Quality Process
See `~/.claude/CLAUDE.md` for full ДО → ВО ВРЕМЯ → ПОСЛЕ loop and `/reiterate` command.

**Project-specific ДО rule:** Creatomate changes → READ `~/.claude/skills/creatomate/references/pitfalls.md` + `our-working-payload.md` FIRST. New effects → READ `all-animations.md` + verify against official docs.

### Auto-reiterate rule
**Before editing `creatomate_service.py` or `visual_director.py`**, run `/reiterate` mentally (shadow check, fallback masking, multi-site, mode safety, track collision). These two files are where every production bug has lived.

### Pasted instructions rule
When the user pastes implementation or architecture instructions (from Antigravity, Gemini, GPT, Claude chat, or any external source), do NOT blindly execute. First:
1. **Verify** each explicit instruction against the actual codebase — does the referenced code/line/function exist as described?
2. **Check** whether the suggested approach could break existing behavior (mode safety, fallback paths, variable shadowing).
3. **Improve** if you see a better way — simpler, fewer touch points, reuses existing patterns.
4. Only then implement, noting any deviations from the original instructions.

---

## Critical Rules — NEVER violate

### Cloud Run
- **NEVER `asyncio.create_task()`** — use `BackgroundTasks` (FastAPI). Cloud Run kills orphaned tasks.
- Webhook uses `background_tasks.add_task(dp.feed_update, ...)` — don't change.

### Supabase
- **`model_dump()` for JSONB**, NOT `model_dump_json()` (string vs dict — silent failure).

### GCS
- **Always presigned URLs** for Creatomate (bucket not public). 6hr expiry, 360min minimum.
- Cache signed URLs per `gs://` URI.

### Telegram
- `allowed_updates` must include `callback_query` in webhook.
- Idempotency: set "rendering" + remove keyboard on first callback click.

### Creatomate
- **ALWAYS read pitfalls.md + our-working-payload.md before ANY change.**
- `animations[]` array for transitions — never property keyframes (breaks transcript_source).
- Ken Burns (scale, scope: element) — REMOVED, causes darkening.
- `border_radius`: number (50), not string ("50%").
- `"transition": true` in animations[] — separate `"transition"` property doesn't work.
- Image elements: minimal fields only — extras silently break without errors.
- Fade animations safe on images, scale animations NOT.
- Exit animation: MUST have `"reversed": true`.
- Same source URL can't have multiple `transcript_source` — use `transcribed_sources` set.
- Storyboard: omit explicit `duration` when voiceover present.
- AI providers: only `openai`, `elevenlabs`, `stabilityai`.
- `background_color: "transparent"` required for AI sticker image elements.
- Decoupled audio: talking_head + transitions → video muted track 1, audio track 2 clean cuts.
- **Track map**: 1=video, 2=TH audio/storyboard voiceover, 3=karaoke, 4=stickers, 5=hybrid per-clip audio, 6=SFX, 7=text popups, 8=music loop.

### Gemini
- `temperature=0` for deterministic results. Prompt is sensitive — test changes separately.
- `video_index` REQUIRED in schema.

---

## Visual Director (compact)
Quality-based: Opus 4.6 (prod) / Sonnet 4.6 (dev), temperature=0, JSON-only API. Fallback on error: clean mode.
Modes: clean (hard cuts) | soft (fade/wipe) | dynamic (varied) | mixed (per-section).
Transitions: `animations[]` with `"transition": true`, 0.5s. Forbidden: scale, spin, flip.
Stickers: timeline-based, provider `openai model=gpt-image-1.5 background=transparent`, `dynamic: true`, `reversed: true` on exit.

---

## Development Workflow

### Sandwich Pattern
- **Antigravity (Planning + Gemini Pro 3.1 High):** Architecture only, never code. Russian, specify mode + model.
- **Claude Code (Sonnet):** Simple tasks. **(Opus 4.6 thinking):** Complex multi-file.
- **Sonnet-чат (claude.ai):** Execution questions after architecture settled.

### Local: `python -m app.main` (auto-polling when `K_SERVICE` absent). Use `python3`.
### Deploy: `./scripts/deploy.sh` | Merge: `./scripts/merge.sh`
### Logs: `gcloud run services logs read content-factory --region europe-west1 --limit 50`
### Git: `main` (protected, deployable) + `dev`. Commits/merges/deploys in Antigravity terminal only.

## Do NOT Refactor
- `handlers.py` callback flow | `deploy.sh` webhook setup | presigned URL caching in gcs_service

## Disabled Features
Ken Burns | Compliance check | Script generation | Old B-roll pipeline (Pexels) | Ducking (hybrid mode — replaced by per-clip audio)

<!-- GSD:project-start source:PROJECT.md -->
## Project

**Content Factory — Sprint 1+1.5: Visual Arsenal + Stabilisation**

Telegram bot that transforms raw video into polished Instagram Reels for Romina (doctor-cosmetologist, Germany/Israel). Upload video → AI analysis (Gemini) → Visual Director (Claude) → Creatomate render → delivery. Russian-language content. Currently supports talking_head, storyboard (ordered/smart), and hybrid modes.

This milestone evolves Content Factory from a functional render pipeline into a creative production tool — adding rich visual effects, structured creative briefs, audience feedback loops, and production stability.

**Core Value:** Every reel should feel creatively directed — not just assembled — with feedback-driven iteration and a growing toolkit of visual effects.

### Constraints

- **Cloud Run**: No asyncio.create_task() — BackgroundTasks only. Stateless.
- **Creatomate**: animations[] for transitions (not property keyframes). Read pitfalls.md before any change.
- **GCS**: Always presigned URLs (bucket not public). 6hr expiry minimum.
- **Supabase**: model_dump() for JSONB (not model_dump_json()).
- **AI Models**: Claude Opus 4.6 (prod/1080p) / Sonnet 4.6 (dev/720p) for Visual Director. Gemini 2.5 Flash via Vertex AI. Whisper-1 for transcription.
<!-- GSD:project-end -->

<!-- GSD:stack-start source:codebase/STACK.md -->
## Technology Stack

## Languages
- Python 3.12 — all application code
## Runtime
- Python 3.12 (CPython)
- Container: `python:3.12-slim` via Docker
- System dependency: `ffmpeg` installed in container (required by `whisper_service.py` and `audio_processing.py`)
- pip
- Lockfile: `requirements.txt` (not pinned to exact versions — `aiogram>=3.0.0` style)
## Frameworks
- FastAPI (unpinned) — HTTP server, webhook endpoints, lifespan management
- uvicorn (unpinned) — ASGI server, started via `CMD uvicorn app.main:app`
- aiogram 3.x — Telegram bot framework, async, router-based handlers
- anthropic (unpinned) — Claude API client (AsyncAnthropic), used in `claude_service.py` and `visual_director.py`
- openai (unpinned) — OpenAI API client (AsyncOpenAI), used in `whisper_service.py` for Whisper-1 transcription
- google-genai (unpinned) — Gemini via Vertex AI, used in `gemini_service.py`
- tenacity (unpinned) — retry logic on Gemini and Creatomate HTTP calls
- pydantic — structured output schemas for Gemini responses (`ClipCandidate`, `VideoAnalysis`, `StoryboardAnalysis`)
- python-dotenv — `.env` loading via `load_dotenv(override=True)` in `config.py`
- httpx (unpinned) — async HTTP client for Creatomate API calls and render downloads
- requests (unpinned) — sync HTTP in `gcs_service.py` upload_from_url
## Key Dependencies
- `aiogram>=3.0.0` — entire bot flow; router, FSM, callback handling in `app/bot/handlers.py`
- `anthropic` — Visual Director (`visual_director.py`) + script generation (`claude_service.py`)
- `openai` — Whisper speech transcription (`whisper_service.py`, model `whisper-1`)
- `google-genai` — video analysis via Vertex AI (`gemini_service.py`, model `gemini-2.5-flash`)
- `supabase` — all persistent state; content items table (`supabase_service.py`)
- `google-cloud-storage` — GCS blob operations, V4 signed URL generation (`gcs_service.py`)
- `google-api-python-client` — Google Drive v3 API (`drive_service.py`)
- `httpx` — Creatomate render API calls and render result download
- `google-auth` / `google-oauth2` — service account credential loading from `service-account.json`
- `tenacity` — exponential backoff retry on Gemini 429/5xx and Creatomate HTTP errors
## Configuration
- All config via env vars, read in `app/config.py` as `Settings` class attributes
- `.env` file loaded at startup with `python-dotenv` (`override=True`)
- Key vars:
- `Dockerfile` — single-stage `python:3.12-slim`, copies `requirements.txt` then `.`
- No build args or multi-stage build
## Runtime Modes
- `python -m app.main` — starts uvicorn, detects absence of `K_SERVICE` env var, uses aiogram polling
- `asyncio.create_task(dp.start_polling(bot))` used in local mode only
- `K_SERVICE` env var present → webhook mode
- Telegram updates arrive at `POST /webhook`, processed via `FastAPI BackgroundTasks`
- `asyncio.create_task()` NOT used in Cloud Run (orphan task risk)
## Quality Presets
- `"dev"` → 720×1280 @ 24fps, model `claude-sonnet-4-6`
- `"prod"` → 1080×1920 @ 60fps, model `claude-opus-4-6`
- Defined in `app/services/creatomate_service.py` as `QUALITY_PRESETS` dict
## Platform Requirements
- Python 3.12
- `ffmpeg` in PATH (for `audio_processing.py` silence removal and `whisper_service.py` audio extraction)
- GCP service account JSON file at `GOOGLE_APPLICATION_CREDENTIALS` path
- GCP Cloud Run (`europe-west1`)
- GCP project: `romina-content-factory-489121`
- Service account: `content-factory-sa@romina-content-factory-489121.iam.gserviceaccount.com`
- Deploy: `./scripts/deploy.sh`
<!-- GSD:stack-end -->

<!-- GSD:conventions-start source:CONVENTIONS.md -->
## Conventions

## Naming Patterns
- `snake_case.py` for all modules: `creatomate_service.py`, `visual_director.py`, `audio_processing.py`
- Suffix `_service.py` for stateful classes with API clients: `gemini_service.py`, `gcs_service.py`
- No suffix for functional modules: `timeline_utils.py`, `audio_processing.py`
- Bot handlers in `app/bot/handlers.py` — single monolithic file
- `snake_case` throughout
- Private helpers prefixed with `_`: `_build_sticker_anim`, `_parse_gs_uri`, `_get_client`, `_now`
- Async functions for anything I/O-bound: `async def process_voiceover(...)`, `async def get_duration(...)`
- Sync functions for pure computation: `def analyze_silence(...)`, `def map_broll_to_render_timeline(...)`
- `snake_case` for all local variables
- Constants in `UPPER_SNAKE_CASE`: `API_URL`, `FILLER_WORDS`, `QUALITY_PRESETS`, `KARAOKE_STYLES`
- Module-level singletons with leading `_`: `_client: Optional[Client] = None`
- `PascalCase`: `GeminiService`, `CreatomateService`, `DriveService`, `GCSService`, `WhisperService`
- Pydantic models: `ClipCandidate`, `VideoAnalysis`, `StoryboardScene`, `StoryboardAnalysis`, `ContentItem`
- `@dataclass` for simple data structs: `Clip` in `app/services/creatomate_service.py`
- FSM state groups in `handlers.py`: `ClipSelection(StatesGroup)`, `ScriptEdit(StatesGroup)`
- Used consistently on all function signatures (Python 3.12 style)
- Modern union syntax: `str | None`, `list[dict] | None`, `int | None` (not `Optional[str]`)
- `Optional` still appears in older code (supabase_service, drive_service) — being phased out
- `dict` preferred over `Dict` (no `from typing import Dict`)
## Code Style
- No enforced formatter (no `.prettierrc`, `pyproject.toml`, or `ruff.toml` found)
- Indentation: 4 spaces
- Line length: generally under 100 chars, prompt strings use `\` line continuation
- Blank lines: 2 between top-level definitions, 1 within classes between methods
- Section comments styled as `# ── Section Name ──` with Unicode box-drawing dashes
- Used heavily in `creatomate_service.py`, `visual_director.py`, `handlers.py`
- Standard library first, then third-party, then `app.*` internal
- No strict enforcement — `handlers.py` has deferred imports mid-file (after initial router setup)
- Deferred imports used to avoid circular dependencies: `from app.services.drive_service import DriveService` at line 70 in `handlers.py`
## Import Organization
- None — all imports use full `app.` prefix
## Error Handling
- Specific exceptions caught and re-raised or returned as `None`:
- `tenacity` `@retry` on all external API calls: Gemini, Whisper, Drive, Creatomate
- Pipeline steps wrapped in `try/except Exception as e` with fallback behavior
- Fallback on Gemini failure: equal-split clips
- Fallback on audio processing failure: use original audio
- Silent `except Exception: pass` used in UI update calls (e.g. `message.edit_text`)
- `raise RuntimeError(...)` used to signal unrecoverable failure to outer handler
- `response.raise_for_status()` on all HTTP calls
- `RuntimeError` raised for missing credentials or config
- `_validate_blueprint()` returns `None` on invalid Claude response
- Caller falls back to `_make_fallback(num_clips)` on `None`
## Logging
- Preferred (services): `logger.info("Video durations: %s (total=%.1fs)", video_durations, total_video_duration)`
- Older/webhook style: `logger.error(f"Creatomate render failed for item {item_id}: {payload}")`
- Newer code (handlers, services) uses `%`-style; webhook and some handlers use f-strings
- `logger.debug(...)` — detailed internal state (blueprint output, b-roll dedup)
- `logger.info(...)` — pipeline milestones, clip counts, render IDs
- `logger.warning(...)` — recoverable failures with fallback applied
- `logger.error(...)` — failures that reach the user or stop the pipeline
## Comments
- Section headers: `# ── Step 1: Group words into raw speech blocks ──`
- Critical rule reminders inline (e.g. safety rules near Creatomate element builders)
- `# TODO:` for disabled/deferred features (kept in codebase, not tracked externally)
## Function Design
- `None` as sentinel for "not found" or "failed" (service layer)
- `dict` for structured data (Supabase rows, Creatomate source JSON)
- `tuple` for multi-value returns: `tuple[str, float, float]` in `process_voiceover`
- Pydantic models for typed Gemini responses (`VideoAnalysis`, `StoryboardAnalysis`)
## Module Design
## Async Patterns
<!-- GSD:conventions-end -->

<!-- GSD:architecture-start source:ARCHITECTURE.md -->
## Architecture

## Pattern Overview
- Telegram bot (aiogram 3.x) + FastAPI in one process: bot handles commands/callbacks, FastAPI handles incoming webhooks
- All heavy work runs in FastAPI `BackgroundTasks` — never `asyncio.create_task()` (Cloud Run constraint)
- Blocking I/O (Supabase client, GCS SDK, Drive SDK) wrapped in `asyncio.to_thread()`
- Stateless services: each service class is instantiated per-request in handlers, not shared singletons
- Supabase `content_items` table is the single state store — all pipeline stages write their status there
## Layers
- Purpose: Handle Telegram interactions — commands, callbacks, progress messages
- Location: `app/bot/handlers.py`
- Contains: Command handlers, callback handlers, inline keyboard logic, progress bar updates, mode detection (talking_head vs storyboard), pipeline orchestration inline
- Depends on: All services
- Used by: aiogram Dispatcher (via `dp.include_router(bot_router)`)
- Purpose: Receive external async callbacks (Creatomate render completion)
- Location: `app/webhooks/creatomate_webhook.py`
- Contains: `POST /webhooks/creatomate/{item_id}` — idempotency guard, background delivery task
- Depends on: `supabase_service`, `gcs_service`, `claude_service` (disabled compliance)
- Used by: Creatomate rendering service (external)
- Purpose: Thin wrappers around external APIs with domain logic
- Location: `app/services/`
- Contains: One module per integration or domain concern
- Depends on: `app/config.py` (settings), each other (handlers wire them together)
- Used by: `handlers.py`, `creatomate_webhook.py`
- Purpose: Central env var access
- Location: `app/config.py`
- Contains: `Settings` class, module-level singleton `settings`
- Depends on: `python-dotenv`
- Used by: All services and handlers
- Location: `app/main.py`
- Triggers: `uvicorn app.main:app` (local) or Cloud Run injecting `PORT`
- Responsibilities: FastAPI app + lifespan (Supabase init, polling vs webhook mode), includes bot router + webhook router
## Data Flow
- Unnumbered clips detected → routes through `analyze_and_propose()` (same as talking_head)
- All clips treated as broll; per-clip voiceover architecture if voiceover present
- Supabase `content_items.status` field drives pipeline state: `idea` → `awaiting_footage` → `analyzing` → `ready_for_render` → `rendering` → `delivering` → `pending_approval` → `approved`
- `analysis_result` JSONB stores Gemini output + whisper_words + voiceover segments
- No in-memory state between requests — everything is re-fetched from Supabase
## Key Abstractions
- Purpose: Represents one video segment for render
- Location: `app/services/creatomate_service.py`
- Fields: `source` (presigned URL), `trim_start`, `trim_duration`, `clip_type` (speech/broll), `video_index`, `matched_voiceover_segment`
- Purpose: Gemini structured output for talking_head analysis
- Location: `app/services/gemini_service.py`
- Pattern: Pydantic v2 with `Field(description=...)` — descriptions are used as Gemini schema hints
- Purpose: Gemini structured output for storyboard audio→video mapping
- Location: `app/services/gemini_service.py`
- Purpose: Claude's output — transitions per clip + sticker overlays
- Location: produced by `app/services/visual_director.py:get_visual_blueprint()`
- Schema: `{overall_style, transitions: [{clip_index, type, direction?}], overlays: [{anchor_id, image_prompt, duration_seconds}], font_family, subtitle_color}`
- Purpose: dev (720p/24fps) vs prod (1080p/60fps) render dimensions
- Location: `app/services/creatomate_service.py`
## Entry Points
- Location: `app/main.py` `POST /webhook`
- Triggers: Telegram sends update to Cloud Run URL
- Responsibilities: Parse `Update`, offload to `background_tasks.add_task(dp.feed_update, bot, update)`, return 200 immediately
- Location: `app/main.py` lifespan
- Triggers: `K_SERVICE` env var absent → `asyncio.create_task(dp.start_polling(bot))`
- Note: Uses `create_task` here (acceptable in local dev, not Cloud Run)
- Location: `app/webhooks/creatomate_webhook.py` `POST /webhooks/creatomate/{item_id}`
- Triggers: Creatomate calls after render completes
- Responsibilities: Idempotency check, status lock (`delivering`), offload `process_creatomate_render` to BackgroundTasks
- `/start`, `/help`, `/status` — `app/bot/handlers.py`
- `/reels`, `/post`, `/carousel`, `/stories` — format selection
- `/ready` — main pipeline trigger: detects Drive folder content → routes to correct mode
## Error Handling
- Gemini failure → fallback to equal-duration clip split (storyboard) or `analysis_failed` status (talking_head) with manual render option
- Audio processing failure → fallback to original unprocessed voiceover
- File copy failure → accumulate `failed_files`, continue with what succeeded; abort if zero succeed
- Creatomate render failure → webhook sets `render_failed`, notifies user via Telegram
- Duplicate webhook delivery → idempotency check on `status` field before processing
- All service calls in handlers wrapped in try/except with `logger.error()` or `logger.warning()`
- Supabase blocking calls wrapped in `asyncio.to_thread()` with lambda to capture closure
## Cross-Cutting Concerns
<!-- GSD:architecture-end -->

<!-- GSD:workflow-start source:GSD defaults -->
## GSD Workflow Enforcement

Before using Edit, Write, or other file-changing tools, start work through a GSD command so planning artifacts and execution context stay in sync.

Use these entry points:
- `/gsd:quick` for small fixes, doc updates, and ad-hoc tasks
- `/gsd:debug` for investigation and bug fixing
- `/gsd:execute-phase` for planned phase work

Do not make direct repo edits outside a GSD workflow unless the user explicitly asks to bypass it.
<!-- GSD:workflow-end -->

<!-- GSD:profile-start -->
## Developer Profile

> Profile not yet configured. Run `/gsd:profile-user` to generate your developer profile.
> This section is managed by `generate-claude-profile` -- do not edit manually.
<!-- GSD:profile-end -->
