---
phase: 01-stability-observability
plan: 02
subsystem: billing
tags: [cost-tracking, prompt-versioning, gemini, whisper, claude, creatomate, supabase]

requires:
  - phase: 01-01
    provides: "Error recovery infrastructure (retry, safety filter handling)"
provides:
  - "Per-service cost extraction (Gemini, Whisper, Claude, Creatomate)"
  - "Cost aggregation per render saved to Supabase"
  - "Prompt version constants (GEMINI_PROMPT_V, DIRECTOR_PROMPT_V)"
  - "Migration script for 7 new cost/version columns"
affects: [01-03, billing-dashboard]

tech-stack:
  added: []
  patterns: ["_last_cost_usd instance attribute for cost passthrough", "tuple return (result, cost) for standalone functions"]

key-files:
  created:
    - scripts/add_cost_columns.sql
  modified:
    - app/config.py
    - app/services/gemini_service.py
    - app/services/whisper_service.py
    - app/services/visual_director.py
    - app/services/creatomate_service.py
    - app/bot/handlers.py
    - app/services/supabase_service.py

key-decisions:
  - "Used _last_cost_usd instance attribute pattern for class-based services (Gemini, Whisper, Creatomate) to avoid breaking existing return types"
  - "Changed get_visual_blueprint return to tuple[dict, float] since it is a standalone function with fewer callers"
  - "Gemini/Whisper costs default to 0.0 in _start_render since those services run earlier in pipeline — wiring deferred to future plan"

patterns-established:
  - "_last_cost_usd: float instance attribute for tracking API call costs on service classes"
  - "Tuple return (result, cost_usd) for standalone async functions returning cost data"
  - "Cost estimation based on token counts and published API pricing"

requirements-completed: [BILL-01, BILL-02, QUAL-03]

duration: 5min
completed: 2026-03-28
---

# Phase 01 Plan 02: Cost Tracking + Prompt Versioning Summary

**Per-render cost extraction from all 4 AI services with Supabase persistence and prompt version traceability**

## Performance

- **Duration:** 5 min
- **Started:** 2026-03-28T15:54:34Z
- **Completed:** 2026-03-28T15:59:17Z
- **Tasks:** 2
- **Files modified:** 8

## Accomplishments
- Cost extraction added to GeminiService, WhisperService, Visual Director, and CreatomateService
- Cost aggregation in _start_render saves per-service and total costs to Supabase content_items
- Prompt version constants (GEMINI_PROMPT_V, DIRECTOR_PROMPT_V) logged and persisted per render
- Migration script ready for 7 new columns (5 cost + 2 version)

## Task Commits

Each task was committed atomically:

1. **Task 1: Add cost extraction to each service and prompt version constants** - `c3b486f` (feat)
2. **Task 2: Aggregate costs and prompt versions in render pipeline** - `8184531` (feat)

## Files Created/Modified
- `app/config.py` - Added GEMINI_PROMPT_V and DIRECTOR_PROMPT_V constants
- `app/services/gemini_service.py` - Added _extract_cost helper and _last_cost_usd tracking
- `app/services/whisper_service.py` - Added _last_cost_usd with duration-based cost calculation
- `app/services/visual_director.py` - Changed get_visual_blueprint to return tuple[dict, float] with cost
- `app/services/creatomate_service.py` - Added CREATOMATE_COST_PER_RENDER constant and _last_cost_usd
- `app/bot/handlers.py` - Added cost aggregation, Supabase persistence, and prompt version logging
- `app/services/supabase_service.py` - Added cost column documentation
- `scripts/add_cost_columns.sql` - Migration script for 7 new Supabase columns

## Decisions Made
- Used `_last_cost_usd` instance attribute pattern for class-based services to avoid breaking existing return types
- Changed `get_visual_blueprint` return to `tuple[dict, float]` since it's a standalone function with fewer callers to update
- Gemini/Whisper costs default to 0.0 in `_start_render` since those services run in earlier pipeline stages; wiring the actual costs through is deferred

## Deviations from Plan

None - plan executed exactly as written.

## User Setup Required

**Database migration required.** Run `scripts/add_cost_columns.sql` against Supabase SQL Editor to add cost tracking columns:
- 5 cost columns (cost_whisper, cost_gemini, cost_claude, cost_creatomate, cost_total_usd)
- 2 version columns (gemini_prompt_version, director_prompt_version)

## Known Stubs

- Gemini and Whisper costs are always 0.0 in `_start_render` because those services run before `_start_render` is called and their cost data is not yet passed through the pipeline. Future plan should wire `gemini._last_cost_usd` and `whisper._last_cost_usd` from the analysis phase into the render path.

## Next Phase Readiness
- Cost infrastructure ready for Plan 03 to build user-facing Telegram cost breakdown message
- Migration script must be run before cost data will persist in production

---
*Phase: 01-stability-observability*
*Completed: 2026-03-28*
