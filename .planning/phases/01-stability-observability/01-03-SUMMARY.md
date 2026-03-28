---
phase: 01-stability-observability
plan: 03
subsystem: delivery
tags: [telegram, creatomate, webhook, retry, cost-tracking]

requires:
  - phase: 01-stability-observability/01-02
    provides: "Cost columns in Supabase content_items (cost_whisper, cost_gemini, cost_claude, cost_creatomate, cost_total_usd)"
provides:
  - "Cost breakdown Telegram message after successful render delivery"
  - "Retry render inline button on failure messages"
  - "on_retry_render callback handler re-submitting saved source to Creatomate"
  - "render_source JSONB column for render state persistence"
affects: [creatomate-webhook, bot-handlers, supabase-schema]

tech-stack:
  added: []
  patterns: ["Retry via saved JSONB source (no re-analysis)", "Idempotent callback buttons (remove reply_markup)"]

key-files:
  created: ["scripts/add_render_source_column.sql"]
  modified: ["app/webhooks/creatomate_webhook.py", "app/bot/handlers.py"]

key-decisions:
  - "Save full render source JSON to Supabase for retry instead of rebuilding from clips"
  - "render_source saved in both talking_head and storyboard render paths"

patterns-established:
  - "Retry pattern: save source JSONB on render, re-submit on failure without re-running pipeline"
  - "Cost breakdown: fetch from Supabase after delivery, silent fail if message send fails"

requirements-completed: [BILL-03, STAB-03]

duration: 3min
completed: 2026-03-28
---

# Phase 01 Plan 03: User-Facing Cost & Retry Summary

**Cost breakdown message after renders + one-tap retry button on failures using saved Creatomate source JSON**

## Performance

- **Duration:** 3 min
- **Started:** 2026-03-28T16:01:50Z
- **Completed:** 2026-03-28T16:04:20Z
- **Tasks:** 2
- **Files modified:** 3

## Accomplishments
- Cost breakdown (Whisper, Gemini, Claude, Creatomate, total) sent as follow-up Telegram message after successful render delivery
- Retry render inline button on failure messages with idempotent button removal
- Full Creatomate source JSON saved to Supabase on every render for retry capability
- SQL migration for render_source JSONB column

## Task Commits

Each task was committed atomically:

1. **Task 1: Add cost breakdown message and retry button to webhook** - `34c3bff` (feat)
2. **Task 2: Add retry render callback handler and save render source** - `56366a3` (feat)

## Files Created/Modified
- `app/webhooks/creatomate_webhook.py` - Cost breakdown after delivery + retry button on failure
- `app/bot/handlers.py` - on_retry_render callback handler + render_source saved in update_item
- `scripts/add_render_source_column.sql` - Migration adding render_source JSONB column

## Decisions Made
- Save full render source JSON to Supabase rather than rebuilding from selected_clips -- simpler, more reliable for retry
- render_source saved in both _start_render (talking_head) and storyboard_render paths for consistent retry support

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 2 - Missing Critical] Save render_source in storyboard path too**
- **Found during:** Task 2 (render source save)
- **Issue:** Plan only mentioned _start_render but storyboard_render has its own submit_render + update_item call
- **Fix:** Added render_source=source to both render paths
- **Files modified:** app/bot/handlers.py
- **Verification:** grep confirms render_source in both locations
- **Committed in:** 56366a3 (Task 2 commit)

---

**Total deviations:** 1 auto-fixed (1 missing critical)
**Impact on plan:** Essential for storyboard retry support. No scope creep.

## Issues Encountered
None

## Known Stubs
None -- all data paths wired to real Supabase columns.

## User Setup Required
SQL migration must be run: `scripts/add_render_source_column.sql` adds render_source JSONB column to content_items.

## Next Phase Readiness
- Phase 01 (stability-observability) is complete with all 3 plans executed
- Cost tracking, retry, and observability features ready for production
- render_source column migration required before deploy

---
*Phase: 01-stability-observability*
*Completed: 2026-03-28*
