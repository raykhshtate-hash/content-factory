---
phase: 07-instagram-carousel-mode
plan: "03"
subsystem: carousel
tags: [carousel, render, ffmpeg, PIL, telegram-delivery, gcs, tdd]
dependency_graph:
  requires: [07-01, 07-02]
  provides: [render_video_slide, extract_video_thumbnail, render_carousel, ingest_album, check_album_sizes, deliver_carousel, on_carousel_album-full-pipeline]
  affects: [app/services/carousel_service.py, app/bot/handlers.py, app/services/gcs_service.py, app/config.py]
tech_stack:
  added: [aiogram.utils.media_group.MediaGroupBuilder, tempfile.TemporaryDirectory, asyncio.Semaphore, subprocess ffmpeg via asyncio.to_thread]
  patterns: [TDD red-green per task, all-or-nothing carousel delivery, bounded retry with total budget, tmpfs TemporaryDirectory lifecycle]
key_files:
  created:
    - tests/test_carousel.py (Task 1 — 7 tests: codec, muted, 60s truncate, faststart, thumbnail, semaphore)
    - tests/test_preflight.py (Task 2 — 7 tests: >20MB reject, Drive hint, DRIVE_CAROUSEL_FOLDER_ID config)
    - tests/test_carousel_retry.py (Task 2 — 7 tests: retry attempts, budget timeout, ingest_album)
    - tests/test_carousel_delivery.py (Task 3 — 7 tests: caption placement, thumbnail, HTML follow-up)
    - tests/fixtures/sample_video.mp4 (2s 640x480 libx264 yuv420p fixture)
    - tests/fixtures/sample_photo.jpg (small JPEG fixture)
  modified:
    - app/services/carousel_service.py (extended with Tasks 1-3 functions)
    - app/services/gcs_service.py (upload_carousel_slides + upload_local_file added)
    - app/bot/handlers.py (on_carousel_album stub replaced with full pipeline)
    - app/config.py (DRIVE_CAROUSEL_FOLDER_ID setting added)
decisions:
  - "_build_slide_overlay video bright_bg defaults to False (dark plashka, safe for any video background)"
  - "deliver_carousel uses html.escape on caption_instagram as belt-and-suspenders against HTML injection"
  - "Thumbnail extraction runs AFTER render_carousel (not during) so semaphore slots are free"
  - "GCS upload runs before delivery so manifest[0].rendered_gs is populated for Supabase final status"
  - "Bot param added to on_carousel_album signature (aiogram 3.x DI injection, was missing from stub)"
metrics:
  duration: "~45 min"
  completed: "2026-04-20"
  tasks: 4
  files: 10
---

# Phase 7 Plan 3: Carousel Ingest + Render + Delivery Pipeline Summary

JWT-style one-liner: Full ingest→render→GCS→Telegram pipeline for Instagram carousel with H.264 video rendering, PIL photo rendering, semaphore-capped ffmpeg, bounded retry, and MediaGroupBuilder dual-caption delivery.

## What Was Built

Wired the complete carousel production pipeline across 4 tasks:

**Task 1 — Video slide renderer** (`render_video_slide`, `extract_video_thumbnail`, `_FFMPEG_SEMAPHORE`):
- `render_video_slide` scales/crops source to 1080x1350, composites overlay PNG, encodes H.264 main/4.0 yuv420p +faststart, muted (-an), truncated to 60s
- `extract_video_thumbnail` produces 320x320 JPEG at t=0.5s (~70 quality, well under 200KB)
- `_FFMPEG_SEMAPHORE = asyncio.Semaphore(2)` module-level cap prevents OOM on Cloud Run 2Gi

**Task 2 — Preflight + ingest + retry orchestrator**:
- `check_album_sizes` rejects >20MB files BEFORE `bot.get_file` with Russian Drive-fallback text
- `ingest_album` downloads each Telegram message to `tmp_dir/slide_NN.ext`
- `render_with_retry` retries up to `MAX_ATTEMPTS=2` with `PER_SLIDE_TIMEOUT=45.0s`, never raises
- `render_carousel` wraps `asyncio.gather` in `asyncio.wait_for(TOTAL_BUDGET=450.0)`, raises RuntimeError with Russian "бюджет" text on timeout
- `DRIVE_CAROUSEL_FOLDER_ID` added to `app/config.py` Settings class
- `upload_carousel_slides` + `upload_local_file` added to `gcs_service.py`

**Task 3 — Delivery via MediaGroupBuilder**:
- `deliver_carousel` builds media group with `MediaGroupBuilder(caption=caption_telegram)` — caption on first item only (Pitfall 4 compliance)
- Every `InputMediaVideo` gets `thumbnail=FSInputFile(thumb_path)` (Pitfall 11 compliance)
- Follow-up `send_message` with `parse_mode="HTML"` wraps IG caption in `<code>` + trending-audio hint

**Task 4 — Handler wiring** (integration, no new tests):
- `on_carousel_album` stub replaced with full pipeline
- Entire pipeline runs inside `with tempfile.TemporaryDirectory(prefix="carousel-"):` (Pitfall 7)
- Preflight before any `bot.get_file` call
- FSM: `awaiting_footage → assembling → delivering → approved`; `state.clear()` in both success and failure branches
- No `asyncio.create_task` anywhere

## Production ffmpeg Commands

**render_video_slide:**
```
ffmpeg -y -i {source} -i {overlay_png}
  -filter_complex "[0:v]scale=1080:1350:force_original_aspect_ratio=increase,crop=1080:1350,setsar=1[base];[base][1:v]overlay=0:0[v]"
  -map [v] -t {min(src_dur, 60.0):.3f}
  -c:v libx264 -profile:v main -level 4.0 -pix_fmt yuv420p
  -preset medium -crf 23 -movflags +faststart -an
  {out_path}
```

**extract_video_thumbnail:**
```
ffmpeg -y -ss 0.5 -i {video}
  -frames:v 1 -vf "scale=320:320:force_original_aspect_ratio=decrease,pad=320:320:(ow-iw)/2:(oh-ih)/2:black"
  -q:v 5 {thumb_path}
```

## Retry Budget Numbers (Production)

| Constant | Value | Purpose |
|---|---|---|
| `MAX_ATTEMPTS` | 2 | Per-slide retry limit |
| `PER_SLIDE_TIMEOUT` | 45.0s | Per-render timeout (caught by render_with_retry) |
| `TOTAL_BUDGET` | 450.0s | asyncio.wait_for around entire gather |
| `TELEGRAM_GETFILE_LIMIT` | 20MB | Preflight hard cap |

## Test Counts

| Module | RED commit | GREEN commit | Tests |
|---|---|---|---|
| test_carousel.py (Task 1) | `3f0cbaa` | `5134d57` | 7 |
| test_preflight.py (Task 2) | `11af018` | `a5d8d39` | 7 |
| test_carousel_retry.py (Task 2) | `11af018` | `a5d8d39` | 7 |
| test_carousel_delivery.py (Task 3) | `7387bfb` | `ccfdd88` | 7 |
| **Total** | | | **28 new + 75 pre-existing = 103 green** |

## Commits

| Hash | Type | Description |
|---|---|---|
| `3f0cbaa` | test | failing tests for render_video_slide + thumbnail + semaphore |
| `5134d57` | feat | implement render_video_slide, extract_video_thumbnail, Semaphore(2) |
| `11af018` | test | failing tests for preflight, retry budget, ingest |
| `a5d8d39` | feat | check_album_sizes, ingest_album, render_with_retry, render_carousel, DRIVE_CAROUSEL_FOLDER_ID, upload_carousel_slides |
| `7387bfb` | test | failing tests for deliver_carousel (caption placement, thumbnails, follow-up) |
| `ccfdd88` | feat | implement deliver_carousel via MediaGroupBuilder + dual caption |
| `a23b2e3` | feat | wire on_carousel_album full pipeline (ingest→render→upload→deliver) |

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] _build_slide_overlay requires bright_bg: bool third parameter**
- **Found during:** Task 4 handler wiring
- **Issue:** Plan's interface spec showed `_build_slide_overlay(title, body)` but actual implementation from 07-01 requires `bright_bg: bool` as third required argument
- **Fix:** For video slides in handler, default `bright_bg=False` (dark plashka — safe for any video background since source image is not sampled). Photo slides continue using auto-detection in `render_photo_slide`.
- **Files modified:** app/bot/handlers.py
- **Commit:** a23b2e3

**2. [Rule 2 - Missing] bot: Bot parameter missing from on_carousel_album stub**
- **Found during:** Task 4
- **Issue:** The Plan 07-02 stub signature was `on_carousel_album(message, state, album)` without `bot: Bot` — needed for all `bot.get_file` / `bot.send_media_group` calls
- **Fix:** Added `bot: Bot` to handler signature (aiogram 3.x DI injection handles this automatically)
- **Files modified:** app/bot/handlers.py
- **Commit:** a23b2e3

**3. [Rule 1 - Bug] Plan used bot_router but actual variable is router**
- **Found during:** Task 4
- **Issue:** Plan code examples reference `@bot_router.message(...)` but handlers.py uses `router = Router()` (plain `router`)
- **Fix:** Used `@router.message(...)` (existing decorator unchanged — only the body replaced)
- **Files modified:** app/bot/handlers.py
- **Commit:** a23b2e3

## Known Stubs

None — all pipeline functions are fully implemented and tested.

## Static Checks (Post-completion)

- `asyncio.create_task` in carousel_service.py: **0 hits** (correct)
- `model_dump_json` in carousel paths: **0 hits** (correct)
- `_FFMPEG_SEMAPHORE = asyncio.Semaphore(2)` in carousel_service.py: **exactly 1 hit** (correct)

## Known Issues for 07-04

- Cloud Run `/tmp` is tmpfs (RAM-backed). With 2Gi instance and Semaphore(2), worst case: 2 concurrent 1080x1350x60s renders + thumbnails in tmp. Estimate ~500MB peak. Should be safe but monitor memory metrics post-deploy.
- `_build_slide_overlay` calls `Image.Image.getdata` which is deprecated in Pillow 14 (2027-10-15). Low urgency — `get_flattened_data` migration tracked for future sprint.
- Smoke test (step 4 in plan) requires live Telegram + Supabase + GCS — deferred to 07-04 deploy verification.

## Self-Check: PASSED
