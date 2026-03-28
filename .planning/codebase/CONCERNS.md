# Codebase Concerns

**Analysis Date:** 2026-03-28

---

## Tech Debt

**Dead code — pexels_service.py:**
- Issue: Entire file is dead code; Pexels feature was disabled per CLAUDE.md
- Files: `app/services/pexels_service.py`
- Impact: Increases maintenance surface; `PEXELS_API_KEY` still present in `app/config.py`
- Fix approach: Delete `pexels_service.py`, remove `PEXELS_API_KEY` from `config.py`

**Dead code — apply_voiceover_ducking:**
- Issue: `apply_voiceover_ducking()` function remains after ducking was removed from hybrid mode
- Files: `app/services/audio_processing.py` (lines 182–268)
- Impact: ~90 lines of dead code with ffmpeg + GCS operations; misleads future maintainers
- Fix approach: Delete the function entirely

**Dead code — _build_broll_elements:**
- Issue: Large commented-out block kept "for potential Pexels/GIF future use"
- Files: `app/services/creatomate_service.py` (lines 400–434)
- Impact: Adds noise to the most fragile file in the codebase
- Fix approach: Delete the commented block; history is in git

**Dead scaffolding — keyboards.py:**
- Issue: Both functions return empty `InlineKeyboardMarkup` with TODO stubs; never used
- Files: `app/bot/keyboards.py`
- Impact: Misleads about planned feature scope
- Fix approach: Delete file or stub with `raise NotImplementedError`

**Disabled compliance check:**
- Issue: Compliance logic removed with `# TODO: re-enable compliance for medical content only`
- Files: `app/webhooks/creatomate_webhook.py` (lines 80–86)
- Impact: Romina is a medical professional; no content validation before Telegram delivery
- Fix approach: Implement lightweight compliance check or document the decision to remove it

**Disabled scene label sorting:**
- Issue: Scene label sorting code is commented out
- Files: `app/bot/handlers.py` (lines 1677–1685)
- Impact: Storyboard clips may be processed out of order
- Fix approach: Re-enable or delete with explicit reasoning

**Print statements mixed with structured logger:**
- Issue: 41+ `print()` calls across 5 files instead of `logger.*`
- Files: `app/services/gemini_service.py` (~11 calls), `app/bot/handlers.py` (~15 calls), `app/services/visual_director.py` (~3 calls), `app/services/creatomate_service.py`, `app/services/audio_processing.py`
- Impact: Critical Gemini success/error events go to stdout and are not captured by Cloud Run structured logging; impossible to filter or alert on
- Fix approach: Replace all `print()` with `logger.info/warning/error` calls

---

## Known Bugs

**analysis_data potentially unbound in _start_render:**
- Symptoms: `UnboundLocalError` crash when `th_voiceover_gcs_uri` is set but `raw_analysis` is falsy (e.g., Gemini returns empty)
- Files: `app/bot/handlers.py` (lines ~1309, ~1369, ~1371)
- Trigger: Hybrid mode video upload where Gemini analysis fails but voiceover is present
- Workaround: None — this would surface as an unhandled exception in the background task

**is_same_source_transitions always False:**
- Symptoms: Audio decoupling (video muted, clean audio track) never triggers for talking_head mode despite being listed as critical in CLAUDE.md
- Files: `app/services/creatomate_service.py` (lines 500–508)
- Trigger: Detection compares `c.source` values across clips, but presigned URLs for the same GCS file differ per call — `len(set(...)) == 1` is always False
- Workaround: None currently; the audio decoupling path is unreachable

**_parse_mmss heuristic breaks for videos > 2 minutes:**
- Symptoms: Timestamps like "10:26.0" are re-interpreted as 10.26 seconds instead of 626 seconds; clips map to wrong positions
- Files: `app/bot/handlers.py` (function `_parse_mmss`)
- Trigger: Any source video longer than 2 minutes (120s) where Gemini returns MM:SS.f format timestamps
- Workaround: Only use source clips under 2 minutes

---

## Security Considerations

**No webhook signature verification:**
- Risk: Any caller who knows or guesses a valid item UUID can trigger the delivery webhook, potentially marking items as delivered or sending videos to Telegram
- Files: `app/webhooks/creatomate_webhook.py`
- Current mitigation: UUID is reasonably hard to guess; endpoint is not publicly advertised
- Recommendations: Verify Creatomate webhook signature header (`X-Creatomate-Signature`) before processing

**No startup validation of critical API keys:**
- Risk: All settings default to empty string `""`; a missing `BOT_TOKEN`, `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, or `GOOGLE_APPLICATION_CREDENTIALS` causes silent runtime failures rather than a clean startup crash
- Files: `app/config.py`
- Current mitigation: Cloud Run env vars are set in deployment config
- Recommendations: Add `@validator` or `model_validator` in Settings to assert non-empty values for critical keys at startup

**GCP project ID hardcoded:**
- Risk: Hardcoded `"romina-content-factory-489121"` in source — if project changes, code silently routes to wrong project
- Files: `app/services/gemini_service.py` (line 98)
- Current mitigation: Only one project in use
- Recommendations: Read from `config.GCP_PROJECT_ID` env var; add to config.py

---

## Performance Bottlenecks

**Multiple GCSService instantiations per request:**
- Problem: `GCSService()` constructed 4+ times in `handlers.py` (lines ~108, ~354, ~714, ~1375) — each creates new GCP credentials + storage client
- Files: `app/bot/handlers.py`, `app/services/audio_processing.py`, `app/webhooks/creatomate_webhook.py`
- Cause: No singleton or dependency injection pattern; each call site creates fresh instance
- Improvement path: Module-level singleton in `gcs_service.py` (matching `supabase_service.py` pattern), or inject via FastAPI `Depends()`

**No presigned URL caching at service level:**
- Problem: CLAUDE.md says "Cache signed URLs per gs:// URI" but `GCSService.generate_presigned_url()` generates a new signed URL on every call
- Files: `app/services/gcs_service.py`
- Cause: Cache not implemented despite being documented as required
- Improvement path: Add `functools.lru_cache` or `dict` cache with TTL (< 360min) keyed on `gs://` URI

**Whisper downloads entire video to disk for word timestamps:**
- Problem: `transcribe_url_with_timestamps()` and `transcribe_url_with_segments()` each download the full video to a temp mp4, then convert to mp3 — for long videos this can be hundreds of MB of disk I/O
- Files: `app/services/whisper_service.py` (lines 156–228, 230–312)
- Cause: No streaming conversion; full download required for ffmpeg
- Improvement path: Pipe directly through ffmpeg without intermediate disk write using `-` stdin/stdout

---

## Fragile Areas

**handlers.py — God file (2001 lines):**
- Files: `app/bot/handlers.py`
- Why fragile: All 4 content modes (talking_head, storyboard ordered, storyboard smart, hybrid), render orchestration, Whisper pipeline, Visual Director integration, clip building, and fallback logic in a single file; any change risks breaking an unrelated mode
- Safe modification: Add new modes as separate handler functions at the bottom; never inline new logic into existing mode branches without full regression test of all 4 modes
- Test coverage: Zero automated tests for this file
- Note: CLAUDE.md explicitly forbids refactoring this file

**storyboard_render fallback path — voiceover_signed unbound:**
- Files: `app/bot/handlers.py` (storyboard render section)
- Why fragile: `voiceover_signed` is defined only inside the `else` (Gemini success) branch, but the `clips` fallback path (line ~193) does not set it; if `voiceover_signed` is then referenced after both branches, this crashes
- Safe modification: Assign `voiceover_signed = None` before the conditional block

**creatomate_service.py — most bug-dense file:**
- Files: `app/services/creatomate_service.py` (1016 lines)
- Why fragile: Every production bug cited in CLAUDE.md traces to this file; Creatomate's JSON format is highly sensitive (wrong field type, extra field, wrong layer order all cause silent render failures)
- Safe modification: Follow CLAUDE.md auto-reiterate rule — run mental shadow check before any edit; read `pitfalls.md` + `our-working-payload.md` first; test changes in dev (Sonnet/720p) before prod

**Supabase update_item silently ignores unknown columns:**
- Files: `app/services/supabase_service.py`
- Why fragile: `update_item()` accepts arbitrary `**kwargs`; if a column name is wrong (e.g., `voiceover_gcs_uri` vs `gcs_voiceover_uri`) the update silently no-ops — data is never written but no exception is raised
- Safe modification: Document expected column names; add an allowlist of valid column names or validate against schema

**Direct _client access bypasses GCSService abstraction:**
- Files: `app/services/audio_processing.py` (lines 168, 251), `app/webhooks/creatomate_webhook.py` (line 64)
- Why fragile: Calls `gcs._client.bucket(...)` directly — if GCSService internals change (lazy init, connection pooling) these call sites break silently
- Safe modification: Add a `get_bucket(name)` method to GCSService and use it at all call sites

**MIME type hardcoded as video/mp4:**
- Files: `app/services/gemini_service.py` (line 194)
- Why fragile: `.mov`, `.avi`, `.mkv` files uploaded via Drive/storyboard INBOX are sent to Gemini with wrong MIME type; Gemini may reject or misparse
- Safe modification: Derive MIME type from file extension using `mimetypes.guess_type()`

---

## Scaling Limits

**Single Cloud Run instance for long ffmpeg jobs:**
- Current capacity: Cloud Run default concurrency (80 requests per instance)
- Limit: Multiple simultaneous video uploads each spawning ffmpeg silence-removal + speedup processes saturate CPU; Cloud Run may spin new instances but ffmpeg processes don't share state across instances
- Scaling path: Move audio processing to Cloud Tasks or a dedicated worker with concurrency=1 per task

---

## Dependencies at Risk

**requests imported inside functions:**
- Risk: `import requests as req` at function call site inside `audio_processing.py` (lines 82, 212, 310) — not in top-level imports; easy to miss in dependency audits
- Impact: If `requests` is removed from requirements, failure is at runtime not import time
- Migration plan: Move to top-level import or replace with `httpx` (already a project dependency)

---

## Missing Critical Features

**No structured error reporting to operators:**
- Problem: Render failures, Whisper failures, and Gemini failures log to Cloud Run but no alert/notification is sent to bot admin or Telegram
- Blocks: Silent failures are discovered only when Romina notices a missing reel
- Recommended: Send a Telegram message to admin chat on unhandled exceptions in background tasks

---

## Test Coverage Gaps

**Entire render pipeline untested:**
- What's not tested: Clip building, Creatomate JSON construction, mode routing (talking_head / storyboard / hybrid), Visual Director output parsing, Gemini response parsing, webhook delivery
- Files: `app/bot/handlers.py`, `app/services/creatomate_service.py`, `app/services/visual_director.py`, `app/services/gemini_service.py`, `app/webhooks/creatomate_webhook.py`
- Risk: Any refactor or new feature silently breaks a different mode with no automated detection
- Priority: High — only `tests/test_silence_map.py` exists for the entire codebase

**Visual Director JSON parsing untested:**
- What's not tested: `visual_director.py` response parsing, fallback on malformed JSON, sticker timing calculation
- Files: `app/services/visual_director.py`
- Risk: Claude API response format change breaks sticker placement with no test to catch it
- Priority: High

**Audio processing pipeline untested:**
- What's not tested: Silence removal, speedup calculation, `adjust_voiceover_for_transitions`, GCS upload path
- Files: `app/services/audio_processing.py`
- Risk: ffmpeg filter string bugs go undetected until production render
- Priority: Medium

---

*Concerns audit: 2026-03-28*
