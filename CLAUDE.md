# Content Factory — CLAUDE.md

## What is this
Telegram bot: video upload → AI analysis → Visual Director → Creatomate render → delivery.
Russian-language Instagram Reels for Romina (doctor-cosmetologist, Germany/Israel).

**Repo:** `https://github.com/raykhshtate-hash/content-factory`
**Branch:** `dev` (active) | `v0.1.5` on `main` (stable)

## Architecture
Python 3.12, FastAPI, aiogram 3.x | Claude Sonnet 4.6 (Visual Director) | Gemini 2.5 Flash via Vertex AI | Creatomate (dynamic JSON, no templates) | GCS + Cloud Run (europe-west1) | Supabase (PostgreSQL)

**GCP:** `romina-content-factory-489121` | Cloud Run: `content-factory` | URL: `https://content-factory-7ufgsc2feq-ew.a.run.app`
**SA:** `content-factory-sa@romina-content-factory-489121.iam.gserviceaccount.com`
**Drive:** INBOX `1ro3BwV7-u0wKq51PboVRMs2k1aeAs24L` | storyboard `1KLVCb6z-DisAhbtZf55nKACtJzF9Cw5P` | talking_head `1FXC2WR-E2MCFXMfXLy0uCOz0LLp6upLd`

## Key Files
- `handlers.py` — bot logic + render flow | `creatomate_service.py` — render JSON
- `visual_director.py` — Claude picks transitions + stickers | `gemini_service.py` — video analysis
- `audio_processing.py` — voiceover silence removal + speedup (storyboard only)
- `drive_service.py` → `gcs_service.py` → presigned URLs | `creatomate_webhook.py` — delivery

## Two Modes

### talking_head (INBOX/talking_head/)
Video has audio (speaker on camera). Gemini selects best clips with timestamps. Karaoke `transcript_source` = video element name (per-clip). Stickers always needed — same face gets boring. No audio processing.

### storyboard (INBOX/storyboard/)
Numbered clips (01.mp4, 02.mp4...) + `voiceover.mp3`. Video `volume: "0%"`. Single karaoke on track 3 with `transcript_source: "voiceover"`. Audio element `id="voiceover"` on track 2. Voiceover processed: silence removal → dynamic speedup (capped 1.5x) → re-speed for transition overlap. Stickers almost never (max 1).

### Clip Pre-buffer
**0.5s buffer before `trim_start`**: `adjusted_trim_start = max(0, trim_start - 0.5)`. Prevents cutting off phrase beginnings.

### B-roll Timeline Mapping
`render_time = render_start + (source_time - trim_start)` — Gemini timecodes are in source timeline, not rendered output.

---

## Quality Process
See `~/.claude/CLAUDE.md` for full ДО → ВО ВРЕМЯ → ПОСЛЕ loop and `/reiterate` command.

**Project-specific ДО rule:** Creatomate changes → READ `~/.claude/skills/creatomate/references/pitfalls.md` + `our-working-payload.md` FIRST. New effects → READ `all-animations.md` + verify against official docs.

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

### Gemini
- `temperature=0` for deterministic results. Prompt is sensitive — test changes separately.
- `video_index` REQUIRED in schema.

---

## Visual Director (compact)
Claude Sonnet 4.6, temperature=0, JSON-only API. Fallback on error: clean mode.
Modes: clean (hard cuts) | soft (fade/wipe) | dynamic (varied) | mixed (per-section).
Transitions: `animations[]` with `"transition": true`, 0.5s. Forbidden: scale, spin, flip.
Stickers: timeline-based, provider `openai model=gpt-image-1.5`, `dynamic: true`, `reversed: true` on exit.

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
Ken Burns | Compliance check | Script generation | Old B-roll pipeline (Pexels)
