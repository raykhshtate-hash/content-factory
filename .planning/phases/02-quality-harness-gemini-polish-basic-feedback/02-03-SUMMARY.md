---
phase: 02-quality-harness-gemini-polish-basic-feedback
plan: 03
subsystem: bot, ai
tags: [aiogram, fsm, haiku, feedback, telegram, supabase]

requires:
  - phase: 01-cost-visibility-retry-infra
    provides: webhook delivery flow with buttons
provides:
  - Approve/Redo button UI replacing Approve/Reject
  - RedoFeedback FSM for free text capture
  - classify_feedback function using Haiku for routing
  - feedback_history JSONB persistence
  - redo_requested status flow
affects: [03-scene-plan-sfx-feedback-routing]

tech-stack:
  added: [claude-haiku-4-5-20251001]
  patterns: [standalone async classifier function, FSM-based feedback capture]

key-files:
  created:
    - scripts/add_feedback_column.sql
  modified:
    - app/services/claude_service.py
    - app/webhooks/creatomate_webhook.py
    - app/bot/handlers.py

key-decisions:
  - "classify_feedback as standalone function (not ClaudeService method) for simplicity"
  - "Haiku model (claude-haiku-4-5-20251001) for cheapest classification cost"
  - "Fallback on parse failure puts full text into gemini_instruction"
  - "FSM handler registered before fallback text handler for correct priority"

patterns-established:
  - "Standalone async classifier: function-level API with api_key param, not class method"
  - "Feedback persistence: append to JSONB array via get-then-update pattern"

requirements-completed: [FEED-01, FEED-02]

duration: 3min
completed: 2026-03-28
---

# Phase 02 Plan 03: Basic Feedback Flow Summary

**Approve/Redo buttons with Haiku-classified free text feedback, FSM capture, and JSONB persistence**

## Performance

- **Duration:** 3 min
- **Started:** 2026-03-28T22:33:05Z
- **Completed:** 2026-03-28T22:36:16Z
- **Tasks:** 2
- **Files modified:** 4

## Accomplishments
- Replaced Approve/Reject buttons with Approve/Redo in webhook delivery
- Added RedoFeedback FSM state for capturing free text feedback
- Added classify_feedback function using Haiku to route feedback to gemini_instruction and/or director_instruction
- Feedback stored as JSONB array in feedback_history with classification and timestamp
- Russian emoji labels shown to user after classification

## Task Commits

Each task was committed atomically:

1. **Task 1: Add feedback_history column and classify_feedback function** - `0986ec5` (feat)
2. **Task 2: Replace Approve/Reject with Approve/Redo UI and wire FSM feedback flow** - `033a343` (feat)

## Files Created/Modified
- `scripts/add_feedback_column.sql` - SQL migration for feedback_history JSONB column
- `app/services/claude_service.py` - Added classify_feedback standalone async function using Haiku
- `app/webhooks/creatomate_webhook.py` - Replaced Reject button with Redo button
- `app/bot/handlers.py` - Added RedoFeedback FSM, redo callback handler, feedback classification display

## Decisions Made
- classify_feedback as standalone function (not ClaudeService method) -- simpler, no class state needed
- Used claude-haiku-4-5-20251001 as cheapest model for binary classification (~$0.001/call)
- On JSON parse failure, full feedback text goes to gemini_instruction as safe fallback
- Moved approve/redo handlers after fallback text handler (callbacks unaffected by message handler order)
- Moved FSM message handler before fallback text handler for correct priority

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Fixed handler registration order**
- **Found during:** Task 2 (FSM handler wiring)
- **Issue:** Plan placed redo handlers after fallback text handler; FSM message handler would be shadowed
- **Fix:** Moved process_redo_feedback FSM handler before fallback text handler; callback handlers stay after (unaffected)
- **Files modified:** app/bot/handlers.py
- **Verification:** Confirmed registration order: FSM handler -> fallback text -> callback handlers
- **Committed in:** 033a343 (Task 2 commit)

**2. [Rule 1 - Bug] Replaced print statements with logger calls in approve handler**
- **Found during:** Task 2 (handler refactoring)
- **Issue:** Old approve handler used print() instead of logger for consistency
- **Fix:** Changed to logger.info/logger.error
- **Files modified:** app/bot/handlers.py
- **Committed in:** 033a343 (Task 2 commit)

---

**Total deviations:** 2 auto-fixed (2 bugs)
**Impact on plan:** Both fixes necessary for correct operation. No scope creep.

## Issues Encountered
- Python dependencies not installed in worktree env (anthropic, aiogram) -- verified via AST parse instead of import test

## User Setup Required

Run `scripts/add_feedback_column.sql` against Supabase SQL Editor to add the feedback_history column.

## Known Stubs

None - all data flows are fully wired.

## Next Phase Readiness
- Feedback classification ready for Phase 03 routing (gemini_instruction -> Gemini re-analysis, director_instruction -> Visual Director adjustment)
- feedback_history column must be added to Supabase before deployment

---
*Phase: 02-quality-harness-gemini-polish-basic-feedback*
*Completed: 2026-03-28*
