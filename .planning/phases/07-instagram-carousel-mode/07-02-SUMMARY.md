---
phase: "07"
plan: "02"
subsystem: "carousel"
tags: [carousel, fsm, claude, middleware, aiogram, tdd]
dependency_graph:
  requires: ["07-01"]
  provides: ["carousel-fsm", "claude-carousel-service", "media-group-middleware"]
  affects: ["handlers.py", "claude_service.py", "supabase_service.py", "main.py"]
tech_stack:
  added:
    - "aiogram FSMContext / StatesGroup (CarouselStates)"
    - "InlineKeyboardBuilder (aiogram.utils.keyboard)"
    - "asyncio.sleep-based media group aggregation (custom BaseMiddleware)"
  patterns:
    - "Module-level AsyncAnthropic singleton for carousel (separate from ClaudeService class)"
    - "patch.object(module, '_chat') for testing async module-level functions"
    - "TDD RED→GREEN per task, 3 commits"
key_files:
  created:
    - "app/services/claude_service.py (carousel functions appended)"
    - "app/bot/middlewares/__init__.py"
    - "app/bot/middlewares/media_group.py"
    - "tests/test_carousel_claude.py"
    - "tests/test_media_group_middleware.py"
    - "tests/test_carousel_fsm.py"
    - "tests/test_carousel_preview.py"
    - "tests/fixtures/sample_brief.txt"
    - "tests/fixtures/sample_carousel_response.json"
  modified:
    - "app/services/supabase_service.py (set_carousel_slides, set_stage_detail)"
    - "app/bot/handlers.py (CarouselStates, keyboard builders, FSM handlers, import fix)"
    - "app/main.py (MediaGroupAggregatorMiddleware wiring)"
decisions:
  - "Module-level _chat() function added alongside existing ClaudeService class to enable patch.object mocking in tests without touching existing class-based code"
  - "FSM imports moved to top of handlers.py because cmd_carousel signature uses FSMContext which must be defined before the function decorator is evaluated"
  - "Hand-rolled BaseMiddleware for album aggregation (1.5s debounce, message_id sort, HARD_CAP=10) instead of aiogram-media-group package — fewer dependencies, same behavior"
metrics:
  duration_minutes: 45
  completed_date: "2026-04-20"
  tasks_completed: 3
  tasks_total: 3
  files_modified: 5
  files_created: 9
  tests_added: 18
---

# Phase 07 Plan 02: Claude Carousel Service + Bot FSM Summary

**One-liner:** TDD implementation of `generate_carousel_slides` + `MediaGroupAggregatorMiddleware` + full `/carousel` FSM with per-slide approve/reject/regen keyboards.

## Tasks Completed

| Task | Description | Commit | Tests |
|------|-------------|--------|-------|
| 1 | `claude_service.generate_carousel_slides` + `regenerate_single_slide` | `2032393` | 7 passed |
| 2 | `MediaGroupAggregatorMiddleware` + Supabase helpers | `3b07d98` | 5 passed |
| 3 | `/carousel` FSM handlers + keyboard builders + middleware wiring | `3a87509` | 6 passed |

**Total: 18 tests, 75 suite-wide (0 failures, 0 regressions)**

## What Was Built

### Task 1 — Claude Carousel Service (`claude_service.py`)

Added module-level functions at the bottom of the existing file:

- `_get_carousel_client()` — lazy `AsyncAnthropic` singleton (separate from `ClaudeService` class)
- `_chat(prompt, system, max_tokens)` — async wrapper around `client.messages.create`
- `_strip_json_fence(raw)` — strips ` ```json ` fences from Claude output
- `_validate_carousel_payload(payload)` — validates slide count (3–10), title length (≤60 chars)
- `generate_carousel_slides(brief)` — calls Claude, parses JSON, validates, returns `dict`
- `regenerate_single_slide(brief, slide, slide_index, total)` — regenerates one slide preserving `order`

Claude prompt uses a system role with persona (Romina, doctor-cosmetologist), requests 5–7 slides in JSON with `slides[]`, `caption_telegram`, `caption_instagram`.

### Task 2 — Middleware + Supabase Helpers

**`app/bot/middlewares/media_group.py`** — `MediaGroupAggregatorMiddleware(BaseMiddleware)`:
- 1.5s `asyncio.sleep` debounce to wait for all album frames
- First message in group triggers the sleep and becomes the handler caller
- Subsequent messages in same group are stored but return `None` (swallowed)
- `message_id` sort ensures deterministic slide order regardless of Telegram delivery order
- `HARD_CAP=10` prevents memory abuse on large albums

**`app/services/supabase_service.py`** — two new async functions:
- `set_carousel_slides(item_id, payload)` — stores `dict` directly as JSONB (not `model_dump_json()`)
- `set_stage_detail(item_id, detail)` — writes freeform progress text to `stage_detail` column

### Task 3 — `/carousel` FSM Handlers

**`app/bot/handlers.py`** additions:

`CarouselStates(StatesGroup)` — 7 states: `awaiting_brief`, `analyzing`, `preview`, `editing_slide`, `awaiting_footage`, `assembling`, `delivering`

Keyboard builders:
- `_build_slide_keyboard(item_id, slide_index)` — 4 buttons in 2×2: ✏️ ✅ 🔁 ❌
- `_build_bulk_keyboard(item_id)` — 2 buttons: ✅ Одобрить все / 🔁 Всё пересобрать

Async helper:
- `_render_carousel_preview(message, item_id, payload)` — sends all slide previews with per-slide keyboards + caption preview with bulk keyboard

FSM handlers:
- `cmd_carousel` — replaced stub default branch with FSM init (creates item, sets state, prompts brief)
- `on_carousel_brief` — receives brief, calls Claude, saves payload, transitions to `preview`
- `on_carousel_preview_cb` — handles `car:approve/reject/regen/approve_all/regen_all/edit` callbacks
- `on_carousel_edit_text` — receives edited slide text (title\nbody), saves, returns to `preview`
- `on_carousel_single_rejected` — guides user to send album instead of single photo
- `on_carousel_album` — stub receiver for photo albums (full render in Plan 07-03)

**`app/main.py`** — wired `bot_router.message.outer_middleware(MediaGroupAggregatorMiddleware())`

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] FSMContext import used before definition**
- **Found during:** Task 3 test run (NameError at module import)
- **Issue:** `cmd_carousel` signature uses `FSMContext` but the deferred mid-file import block (line ~140) is after the function definition (line ~58). Python evaluates parameter annotations at module load, causing `NameError`.
- **Fix:** Moved `FSMContext`, `StateFilter`, `StatesGroup`, `State`, `InlineKeyboardMarkup`, `InlineKeyboardButton`, `InlineKeyboardBuilder` imports to top of file (lines 1-6). Removed duplicate mid-file imports.
- **Files modified:** `app/bot/handlers.py`
- **Commit:** `3a87509`

**2. [Rule 2 - Missing critical functionality] `full_name` may be None for bots/anonymous users**
- **Found during:** Task 3 implementation review
- **Issue:** `message.from_user.full_name` can be `None` if user has no name set
- **Fix:** Used `message.from_user.full_name or str(message.from_user.id)` as fallback
- **Files modified:** `app/bot/handlers.py`
- **Commit:** `3a87509`

## Known Stubs

| Stub | File | Reason |
|------|------|---------|
| `on_carousel_album` — no actual render, just logs count | `app/bot/handlers.py` | Full render pipeline (PIL assembly + Telegram album send) is Plan 07-03 scope |

The stub does not prevent the plan's goal (FSM flow, brief→slides→preview→approval). Album rendering is explicitly deferred to 07-03.

## Self-Check: PASSED

Files verified:
- `app/bot/middlewares/__init__.py` — exists
- `app/bot/middlewares/media_group.py` — exists
- `tests/test_carousel_claude.py` — exists
- `tests/test_media_group_middleware.py` — exists
- `tests/test_carousel_fsm.py` — exists
- `tests/test_carousel_preview.py` — exists

Commits verified:
- `2032393` — feat(07-02): claude_service.generate_carousel_slides
- `3b07d98` — feat(07-02): media_group aggregator middleware + supabase carousel helpers
- `3a87509` — feat(07-02): /carousel FSM flow, preview keyboards, middleware wiring

All 75 tests pass (18 new + 57 pre-existing).
