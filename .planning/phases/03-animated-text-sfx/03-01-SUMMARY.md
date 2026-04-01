---
phase: "03"
plan: "01"
subsystem: visual-director
tags: [text-animation, blueprint-schema, guardrails, tdd]
dependency_graph:
  requires: []
  provides: [text_popups_schema, animation_type_field, text_validation_guardrails]
  affects: [creatomate_service, handlers]
tech_stack:
  added: []
  patterns: [_parse_pct helper, text_popups validation, clip_contexts parameter]
key_files:
  created:
    - tests/test_visual_director_text.py
  modified:
    - app/services/visual_director.py
decisions:
  - ALLOWED_TEXT_ANIMATIONS = {fade, slide-up, typewriter, pop} -- matches D-02
  - MAX_TEXT_POPUPS = 4 per D-16
  - LONG_TEXT_THRESHOLD = 20 chars per D-14
  - clip_contexts parameter added to get_visual_blueprint instead of separate unmatched_overlays dict (simpler, no signature bloat)
metrics:
  duration: 3min
  completed: "2026-04-01T08:49:45Z"
---

# Phase 03 Plan 01: Visual Director Text Animation Blueprint Summary

Extended Visual Director blueprint schema with per-clip animation_type selection and text_popups[] array for unmatched B-roll, with Python guardrails for typewriter fallback, popup cap, y-position clamping, and text truncation -- all validated by 12 passing TDD tests.

## What Changed

### Task 1: _validate_blueprint extension + TDD tests (b86f3e8, 416ea02)

Added three module-level constants: `ALLOWED_TEXT_ANIMATIONS`, `MAX_TEXT_POPUPS=4`, `LONG_TEXT_THRESHOLD=20`. Created `_parse_pct()` helper for percentage parsing and clamping. Extended `_validate_blueprint()` with:
- Per-clip `animation_type` validation (invalid defaults to "fade")
- `text_popups[]` section: validates clip_index, text, animation_type, x/y position
- Guardrails: typewriter fallback for text >20 chars, cap at 4 popups, y max 65%, text truncated at 50 chars
- `_make_fallback()` updated with `animation_type` and `text_popups` fields

12 test functions in `tests/test_visual_director_text.py` covering all guardrail behaviors.

### Task 2: Claude prompt extension (d5d8ac5)

Extended the Visual Director system prompt with two new tool sections:
- **Tool 3 (animation_type)**: Instructions for choosing fade/slide-up/typewriter/pop per clip based on energy
- **Tool 4 (text_popups)**: Instructions for placing text overlays on unmatched broll clips

Updated JSON schema example to include `animation_type` per clip and `text_popups[]` array. Added `clip_contexts` parameter to `get_visual_blueprint()` for passing `unmatched_text_overlay` data from Gemini through to the Claude prompt.

## Commits

| Task | Commit | Message |
|------|--------|---------|
| 1 (RED) | 416ea02 | test(03-01): add failing tests for text animation blueprint validation |
| 1 (GREEN) | b86f3e8 | feat(03-01): extend _validate_blueprint with text_popups[] and animation_type |
| 2 | d5d8ac5 | feat(03-01): extend Visual Director prompt with text_popups and animation_type |

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] Created _parse_pct helper from scratch**
- **Found during:** Task 1
- **Issue:** Plan referenced `_parse_pct` as existing function but it did not exist in codebase
- **Fix:** Implemented `_parse_pct(val, lo, hi, default)` utility for percentage parsing and clamping
- **Files modified:** app/services/visual_director.py

## Known Stubs

None -- all data flows are wired. The `clip_contexts` parameter in `get_visual_blueprint()` is ready to receive data from callers (handlers.py), but callers are not yet passing it. This is expected -- Plan 03 will wire the full pipeline through `creatomate_service.py`.

## Self-Check: PASSED
