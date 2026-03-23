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

## Ops

- 2026-03-23 | OpenAI billing limits silently break Creatomate renders | AI sticker generation via gpt-image-1.5 fails. Error only visible via Creatomate render status API, not in our logs.
- 2026-03-23 | Google Drive JWT token expiry ("invalid_grant") | Caused by system clock drift. Fix: sync Mac clock via System Settings or `sudo sntp -sS time.apple.com`.
