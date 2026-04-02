# Lessons Learned — Content Factory

Журнал ошибок и уроков. После каждого болезненного бага — добавь запись.
Если урок формирует правило — перенеси правило в CLAUDE.md.

Format: `YYYY-MM-DD | Что случилось | Почему | Правило (если есть)`

---

## Creatomate

- 2026-03 | Ken Burns (scale animation) затемнял видео | scope: "element" + scale = darkening bug | → CLAUDE.md: Ken Burns removed permanently
- 2026-03 | Sticker невидим, мигает в конце | Exit animation без `reversed: true` | → CLAUDE.md: exit animation MUST have reversed: true
- 2026-03 | border_radius "50%" не работал | Creatomate ожидает number, не string | → CLAUDE.md: border_radius must be number (50)
- 2026-03 | Karaoke "Transcription was unsuccessful" | x_scale/y_scale keyframes конфликтуют с transcript_source | → CLAUDE.md: always use animations array, never property keyframes
- 2026-03 | Duplicate transcript_source на одном source URL | Creatomate не может транскрибировать один URL параллельно | → CLAUDE.md: use transcribed_sources set
- 2026-03 | Image elements ломались без ошибок | Extra properties (shadow, background_color) silently break images | → CLAUDE.md: image elements — minimal fields only
- 2026-03 | Transition через отдельное свойство `"transition"` не работал | Нужно в animations[] с `"transition": true` | → CLAUDE.md: transition animations in animations array only

## Cloud Run / Infra

- 2026-03 | Двойное видео в Telegram | asyncio.create_task() + Cloud Run kills orphaned tasks | → CLAUDE.md: NEVER use asyncio.create_task(), use BackgroundTasks
- 2026-03 | Supabase JSONB silent failure | model_dump_json() returns string, not dict | → CLAUDE.md: use model_dump() for JSONB
- 2026-03 | Webhook idempotency — дубли при долгом processing | Telegram resends callback during long operations | → CLAUDE.md: immediately set "rendering" + remove keyboard on first click

## Gemini

- 2026-03 | Prompt change broke clip selection for unrelated fields | Gemini prompt is holistically sensitive | → CLAUDE.md: prompt is sensitive, test changes separately
- 2026-03 | scene_label sorting changed Gemini behavior | Sorting instructions in prompt affected selection logic | → CLAUDE.md: scene_label sorting DISABLED, needs post-processing

## Audio

- 2026-03 | Audio gap between voiceover and video | Transition compensation calculated wrong overlap | → Fixed in v0.1.5: real gap instead of transition overlap only

## Architecture

- 2026-03-23 | One continuous voiceover audio element causes timeline sync issues | LLMs cannot calculate cumulative clip positions. Per-clip audio (Creatomate trim per segment) eliminates sync problems entirely.
- 2026-03-23 | Ducking (ffmpeg volume filter) creates circular dependency with dynamic timeline assembly | Removing ducking simplified pipeline by ~50 lines and eliminated circular dependency.
- 2026-03-23 | Gemini excels at semantic matching (broll → voiceover segment) but fails at cumulative math | Separate responsibilities: Gemini = semantics, Python = math.
- 2026-03-23 | Voiceover segments are independent phrases, not continuous narrative | This insight unlocked per-clip architecture — clip order on timeline doesn't affect voiceover correctness.

## Parsing / Data

- 2026-03-28 | `_parse_mmss` failed on Gemini timestamps >120s | Gemini returns seconds.fractional (e.g. `245.7`), not `MM:SS` | Values >120 = seconds, not minutes
- 2026-03-28 | Speech trim cut off last words or left silence | Need Whisper `speech_start - 0.3s` AND `speech_end + 1.0s` padding | Both start and end trimming required
- 2026-03-28 | Too many render buttons confused users | Simplified to test (720p) / prod (1080p) quality only, no intermediate mode selection
- 2026-03-28 | Subtitle color not validated, bad hex crashed render | Validate hex format, fallback `#FFFFFF` | Always validate user-facing color inputs

## Ops

- 2026-03-23 | OpenAI billing limits silently break Creatomate renders | AI sticker generation via gpt-image-1.5 fails. Error only visible via Creatomate render status API, not in our logs.
- 2026-03-23 | Google Drive JWT token expiry ("invalid_grant") | Caused by system clock drift. Fix: sync Mac clock via System Settings or `sudo sntp -sS time.apple.com`.

## Smart Storyboard / Montage (Apr 2026)

- 2026-04-01 | Black screen at end of render | Music loop (55s) had no `duration` → Creatomate extended composition | → Set `duration: total_duration` on music audio element
- 2026-04-02 | Black screen from clip overflow | Gemini selected timestamps beyond source video length (e.g. 4.5-9.5s from 4.8s video) | → Clamp in `_candidates_to_clips`: `end = min(end, src_dur - 0.5)` with ffprobe check
- 2026-04-02 | Prebuffer extends clip beyond source | 0.5s prebuffer + trim_duration > source → black frames at clip end | → Clamp accounts for prebuffer margin (safe_dur = src_dur - 0.5)
- 2026-04-01 | `_parse_style_params` crash on None | Storyboard has no script → `re.search(None)` | → Guard: `if not text: return "", {}`
- 2026-04-01 | `_update_cost` NameError | Method was `_extract_cost`, not `_update_cost` | → Fix method name
- 2026-04-01 | Pass 1 ШАГ 3 poisoned Pass 2 | Pass 1 created rigid clip plan → Pass 2 followed bad clips | → Removed ШАГ 3, Pass 1 only describes + story arc
- 2026-04-01 | subtitle_color applied to wrong field | Applied to `fill_color` (base text) instead of `transcript_color` (active word) | → Changed to `transcript_color` in all 3 karaoke elements
- 2026-04-02 | GCS reuse never matched | Compared all_files (7 = videos+voiceover) with gcs_uris (6 = videos only) | → Filter only VIDEO_EXT for comparison, fetch voiceover_gcs_uri separately
- 2026-04-02 | Catch-all text handler intercepted FSM messages | `handle_text` registered BEFORE AddClipState handlers | → Move catch-all to LAST in file; aiogram priority = registration order
- 2026-04-02 | video_index 0-based in manual addclip | Pipeline expects 1-based (Gemini convention) | → Use `video_num` directly (1-based)
- 2026-04-02 | Pass 2 reorder dropped user-added clip | Gemini Pro decided clip wasn't needed | → Add "ОБЯЗАТЕЛЬНО" instruction for user-added clips in reorder prompt
- 2026-04-02 | Stuck items block `/ready` forever | Items in processing_video/analyzing after bot restart | → Auto-cancel after 10 min timeout
- 2026-04-02 | FSM state leaked on early return / exception | `state.clear()` missing in error paths of addclip handler | → Add state.clear() to all exit paths
