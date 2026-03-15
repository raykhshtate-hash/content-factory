# Content Factory — Automated Content Pipeline for Romina

## What is this
Telegram bot that automates Instagram Reels creation for Romina (dermatologist, Germany).
Pipeline: video upload → AI analysis → visual directing → render → delivery.

## Architecture
- **Runtime**: Python 3.12, FastAPI, aiogram 3.x
- **AI**: Claude Sonnet 4.6 (Visual Director), Gemini 2.5 Flash via Vertex AI (video analysis), Whisper (voice transcription only)
- **Render**: Creatomate API (dynamic JSON source, NO templates)
- **Infra**: Google Cloud Run (europe-west1), GCS, Supabase (PostgreSQL)
- **Bot**: Telegram webhook in Cloud Run, polling in local dev

## Key Files
- `app/bot/handlers.py` — all bot logic, command handlers, render flow
- `app/services/creatomate_service.py` — video render via JSON source
- `app/services/gemini_service.py` — Vertex AI video analysis
- `app/services/claude_service.py` — compliance check (DISABLED)
- `app/services/visual_director.py` — Claude picks transitions + sticker overlays
- `app/services/audio_processing.py` — voiceover silence removal + speedup (storyboard only)
- `app/services/video_processing.py` — MOV→MP4 remux utility (reserved, not yet in pipeline)
- `app/services/drive_service.py` — Google Drive folders → GCS transfer
- `app/services/gcs_service.py` — GCS operations + presigned URLs
- `app/services/whisper_service.py` — voice message transcription (NOT used for subtitles)
- `app/services/timeline_utils.py` — B-roll timecode mapping (source → render timeline)
- `app/webhooks/creatomate_webhook.py` — render completion → Telegram delivery

## Content Modes

Two modes, determined by which Drive subfolder contains the files:

### talking_head (INBOX/talking_head/)
One or more raw videos → Gemini analysis → clip selection → Visual Director → render.
- Audio comes from video (speaker talking to camera)
- Gemini analyzes all videos, selects best clips with timestamps
- Visual Director adds transitions + sticker overlays (stickers always needed — same talking head gets boring)
- Karaoke subtitles sync to each clip's audio via `transcript_source`

### storyboard (INBOX/storyboard/)
Pre-shot scene videos (sorted by filename) + separate voiceover audio file.
- No Gemini analysis — scenes used as-is in filename order
- Video audio muted, karaoke syncs to voiceover via `transcript_source: "voiceover"`
- Voiceover processed: silence removal → speedup → re-speed for transition overlap
- Visual Director adds transitions freely; stickers almost never (video already tells the story)
- Gemini `visual_description` passed to Visual Director for storyboard context

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
- **BEFORE making ANY changes** to `creatomate_service.py` or any code that builds Creatomate JSON:
  1. READ `~/.claude/skills/creatomate/references/pitfalls.md`
  2. READ `~/.claude/skills/creatomate/references/our-working-payload.md`
  3. If adding NEW Creatomate features (transitions, effects, new element types) — READ `~/.claude/skills/creatomate/references/all-animations.md` for correct JSON format AND verify against Creatomate documentation. **NEVER guess the format.**
- Flat timeline: each video clip is named `clip-{i}`, each gets its own karaoke text element with `transcript_source`.
- `transcript_effect: "karaoke"` = native Creatomate word-by-word subs. NO Whisper needed for subtitles.
- **Dev/Prod quality**: `quality="dev"` = 720×1280 @24fps, `quality="prod"` = 1080×1920 @60fps.
- **Image elements — use minimal fields**: extra properties (scale animations, shadow, background_color, background_padding) silently break image elements without errors.
- **`border_radius` must be a number** (50), not a string ("50").
- **AI image providers**: only `openai`, `elevenlabs`, `stabilityai` — no Google/Gemini/Nano Banana.
- **Fade animations are safe on image elements**, scale animations are NOT.
- **Storyboard mode**: `voiceover_url` param in `create_render()`. Adds audio element `id="voiceover"` on track 2, single karaoke on track 3 with `transcript_source: "voiceover"`. Video elements get `volume: "0%"`.

### Gemini
- Model: `gemini-2.5-flash` via Vertex AI (NOT Google AI Studio, NOT Gemini 1.5).
- Videos passed as `gs://` URIs directly — no download needed.
- `video_index` in schema is REQUIRED to map clips to correct source video.
- Structured output via `response_schema=VideoAnalysis` — guarantees valid JSON.
- **Prompt is sensitive**: any new instructions change clip selection even for unrelated fields. Test prompt changes separately.

### Photos
- **Do NOT filter out photos permanently** from the Drive→GCS pipeline. They're needed for future posts/carousels.
- Current filter is video-only for Gemini analysis, but photos are preserved in GCS.

### Telegram
- `allowed_updates` must include `callback_query` in `setWebhook` (set in `deploy.sh`).
- Always respond 200 OK immediately to Telegram webhooks to prevent duplicate deliveries.

## Visual Director

- **`app/services/visual_director.py`** — Claude Sonnet 4.6, temperature=0, JSON-only API
- Single Claude call → `visual_blueprint` dict with transitions + overlays
- Blueprint applied via `apply_visual_blueprint()` in `creatomate_service.py`
- **Architecture**: `build_source()` → `apply_visual_blueprint()` → `submit_render()`
- **Response parsing**: strips markdown fences (```` ```json ````) + extracts first `{` to last `}`
- **Fallback**: any error → all hard cuts, no stickers ("clean" mode)

### Mood Modes
- **clean**: hard cuts only, max 1 sticker. For serious/medical/sad content.
- **soft**: fade/wipe transitions + 1-2 stickers. For personal/emotional content.
- **dynamic**: varied transitions + 2-3 stickers. For lifestyle/tips/motivation.
- **mixed**: different styles for different parts (hook=dynamic, story=soft, closing=clean).

### Transitions
- Format: `animations[]` with `"transition": true`, duration 0.5s fixed
- Allowed: fade, slide, wipe, circular-wipe, color-wipe, film-roll, squash, rotate-slide, shift
- **Forbidden**: scale (rendering artifacts), spin, flip (off-brand)
- Direction types (slide, film-roll, squash, rotate-slide, shift, color-wipe): need `direction` field
- Clip index 0 always has `transition: null`
- Clips < 2.5s → no transition; talking_head clips < 3s → no transition

### Sticker Overlays
- **Timeline-based**: `start_second` / `end_second` on final reel timeline (not per-clip)
- Provider: `openai model=gpt-image-1.5` via Creatomate `dynamic: true`
- Exit animation requires `"reversed": true` (see pitfalls.md)
- **talking_head**: max 3 stickers, always needed for visual variety
- **storyboard**: max 1 sticker, almost never needed (only for exotic mismatch between speech and video)
- Rules: not in first/last 2s, min 4s duration, no overlap, prompt ends with "isolated object, sticker style, no background"
- Position alternates x: 25%/75%

## Audio Processing (storyboard only)

- **`app/services/audio_processing.py`** — ffmpeg-based pipeline
- `process_voiceover()`: silence removal (>1s gaps) → speedup to 70% of video duration (min 1.2x, max 1.5x)
- `adjust_voiceover_for_transitions()`: re-speed to compensate transition overlap
- **Guard**: max 1.3x additional speedup for transition compensation — beyond that, skip adjustment
- Downloads via `requests` (ffmpeg can't handle GCS presigned URLs with special chars)

## Clip Pre-buffer

- **0.5s buffer before `trim_start`** of each clip: `adjusted_trim_start = max(0, trim_start - 0.5)`
- Prevents cutting off the beginning of speaker phrases
- `adjusted_duration = trim_duration + buffer` — video, karaoke, and timeline advance use adjusted values

## Gemini scene_label

- Field `scene_label` added to `ClipCandidate` schema (optional)
- **Sorting by `scene_label` DISABLED** — changed Gemini clip selection behavior
- **Instructions in Gemini prompt DISABLED** (commented out in `handlers.py` ~200)
- Approach needs rethinking — not via Gemini prompt, but via post-processing

## Disabled Features

- **Ken Burns**: scale animations caused video darkening. No scene animations on clips.
- **Compliance check** (`claude_service.py`): commented out in `creatomate_webhook.py:80-86`. Caption shows clean "Твое видео готово!" without compliance warnings.
- **Script generation** (`generate_script`): disabled, pipeline uses passthrough (user's script text as-is).
- **Old broll pipeline** (`_build_broll_elements`, `generate_broll_prompts`): commented out in `creatomate_service.py`, replaced by Visual Director sticker overlays.

## Known Bugs

- **Drive delete 403**: after transferring files to GCS, `drive_service.py` tries to delete originals but gets 403 (insufficient permissions). Files stay in Drive. Non-blocking — pipeline continues.

## On the Horizon

- **Enhance mode**: Whisper word-level timestamps → smart cuts (remove filler words, pauses)
- Visual effects (text overlays, lower thirds)
- GIF/animated stickers
- Background music layer
- Multiple render variants (A/B testing different styles)

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
- Drive Talking Head Folder: configured via `DRIVE_TALKING_HEAD_FOLDER_ID` env var
- Drive Storyboard Folder: configured via `DRIVE_STORYBOARD_FOLDER_ID` env var

## Do NOT Refactor
- handlers.py callback flow — tested, complex state machine
- deploy.sh webhook setup — includes allowed_updates
- Presigned URL caching in gcs_service
