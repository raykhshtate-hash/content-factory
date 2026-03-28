---
phase: 02-quality-harness-gemini-polish-basic-feedback
plan: 01
subsystem: testing
tags: [gemini, regression, fixtures, assertions, clip-quality]

requires:
  - phase: 01-cost-tracking-error-recovery-retry
    provides: "Gemini service, config.py, production content items in Supabase"
provides:
  - "Gemini regression harness (scripts/gemini_regression.py)"
  - "8 fixture files with real production GCS URIs"
  - "Default quality thresholds (scripts/fixtures/defaults.json)"
  - "GEMINI_PROMPT_V constant in app/config.py"
affects: [02-02-PLAN, prompt-versioning, gemini-quality]

tech-stack:
  added: []
  patterns: [fixture-based regression testing, broll-only quality assertions]

key-files:
  created:
    - scripts/gemini_regression.py
    - scripts/fixtures/defaults.json
    - scripts/fixtures/talking_head_01.json
    - scripts/fixtures/talking_head_02.json
    - scripts/fixtures/talking_head_03.json
    - scripts/fixtures/talking_head_04.json
    - scripts/fixtures/storyboard_smart_01.json
    - scripts/fixtures/storyboard_smart_02.json
    - scripts/fixtures/storyboard_smart_03.json
    - scripts/fixtures/storyboard_smart_04.json
  modified:
    - app/config.py

key-decisions:
  - "GEMINI_PROMPT_V added to config.py as constant (was missing from codebase)"
  - "Storyboard smart fixtures use analyze_video path (not analyze_storyboard) matching actual production routing"
  - "Prompt built inline in harness mirroring handlers.py analyze_and_propose() — no prompt stored in fixtures"

patterns-established:
  - "Fixture-based regression: JSON fixture defines inputs + expected ranges, harness validates Gemini output"
  - "Broll-only quality assertions: dedup, linearity, consecutive checks exclude speech clips (D-13)"

requirements-completed: [QUAL-01, QUAL-02]

duration: 6min
completed: 2026-03-28
---

# Phase 02 Plan 01: Gemini Regression Harness Summary

**Fixture-based Gemini regression harness with 5 assertion types, 8 production fixtures, and --record baseline capture**

## Performance

- **Duration:** 6 min
- **Started:** 2026-03-28T22:32:40Z
- **Completed:** 2026-03-28T22:38:16Z
- **Tasks:** 2
- **Files modified:** 11

## Accomplishments
- Regression harness script (~300 lines) with CLI for run, record, and single-fixture modes
- 5 assertion types: clip count (hard), total duration (hard), dedup ratio (warn), consecutive same-source (warn), anti-linearity (warn)
- Broll-only filtering for quality assertions per D-13
- 8 fixture files using real GCS URIs from Supabase production data (4 talking_head, 4 storyboard_smart)
- GEMINI_PROMPT_V constant added to config.py for prompt versioning

## Task Commits

Each task was committed atomically:

1. **Task 1: Create regression harness script and default thresholds** - `2d9a314` (feat)
2. **Task 2: Create 8 fixture files from real production videos** - `21c7a0d` (feat)

## Files Created/Modified
- `scripts/gemini_regression.py` - Main regression harness with CLI, assertions, Gemini invocation
- `scripts/fixtures/defaults.json` - Global default thresholds for quality assertions
- `scripts/fixtures/talking_head_01.json` - Single video dentist fixture
- `scripts/fixtures/talking_head_02.json` - 6-video restaurant fixture
- `scripts/fixtures/talking_head_03.json` - 2-video kids fixture
- `scripts/fixtures/talking_head_04.json` - Single video dentist v2 fixture
- `scripts/fixtures/storyboard_smart_01.json` - 3-clip dumplings with voiceover
- `scripts/fixtures/storyboard_smart_02.json` - 3-clip dumplings v2
- `scripts/fixtures/storyboard_smart_03.json` - 3-clip dumplings v3
- `scripts/fixtures/storyboard_smart_04.json` - 3-clip dumplings v4
- `app/config.py` - Added GEMINI_PROMPT_V constant

## Decisions Made
- Added GEMINI_PROMPT_V = "1.0" to config.py (referenced by plan but did not exist in codebase)
- Storyboard smart fixtures use analyze_video (not analyze_storyboard) because smart mode routes through the talking_head Gemini pipeline in production
- Prompt is built inline in harness mirroring handlers.py analyze_and_propose() to ensure Plan 02-02 prompt changes are always tested with current version

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 2 - Missing Critical] Added GEMINI_PROMPT_V to config.py**
- **Found during:** Task 1
- **Issue:** Plan references importing GEMINI_PROMPT_V from app.config but the constant did not exist
- **Fix:** Added `GEMINI_PROMPT_V = "1.0"` to app/config.py
- **Files modified:** app/config.py
- **Verification:** Import succeeds in harness script
- **Committed in:** 2d9a314 (Task 1 commit)

---

**Total deviations:** 1 auto-fixed (1 missing critical)
**Impact on plan:** Essential for harness to function. No scope creep.

## Issues Encountered
None

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- Harness ready for `--record` baseline capture once Gemini API access confirmed
- Fixtures contain real production GCS URIs — ready to validate actual Gemini responses
- Plan 02-02 can modify prompts and immediately test via harness

---
*Phase: 02-quality-harness-gemini-polish-basic-feedback*
*Completed: 2026-03-28*
