# Content Factory — Sprint 1+1.5: Visual Arsenal + Stabilisation

## What This Is

Telegram bot that transforms raw video into polished Instagram Reels for Romina (doctor-cosmetologist, Germany/Israel). Upload video → AI analysis (Gemini) → Visual Director (Claude) → Creatomate render → delivery. Russian-language content. Currently supports talking_head, storyboard (ordered/smart), and hybrid modes.

This milestone evolves Content Factory from a functional render pipeline into a creative production tool — adding rich visual effects, structured creative briefs, audience feedback loops, and production stability.

## Core Value

Every reel should feel creatively directed — not just assembled — with feedback-driven iteration and a growing toolkit of visual effects.

## Requirements

### Validated

- ✓ Video upload via Telegram → GCS storage — existing
- ✓ Gemini video analysis with clip selection — existing
- ✓ Claude Visual Director (transitions, stickers, mood) — existing
- ✓ Creatomate JSON render with karaoke subtitles — existing
- ✓ talking_head mode (speaker on camera, Gemini clip selection) — existing
- ✓ storyboard ordered mode (numbered clips + voiceover) — existing
- ✓ storyboard smart mode (unnumbered clips, Gemini pipeline) — existing
- ✓ hybrid mode (talking_head + per-clip voiceover) — existing
- ✓ Whisper word-level transcription + silence analysis — existing
- ✓ AI sticker overlays (OpenAI gpt-image-1.5) — existing
- ✓ Decoupled audio (video muted track 1, audio track 2) — existing
- ✓ Gap-aware pre-buffer for clip transitions — existing
- ✓ Webhook delivery via Creatomate → Telegram — existing

### Active

- [ ] Animated text overlays (motion text replacing static popups)
- [ ] SFX pack (transition-matched sound effects on dedicated track)
- [ ] Feedback loop v1 (approve/redo with Claude Haiku classification)
- [ ] Billing tracking (per-API cost logging + Telegram breakdown)
- [ ] Regression harness for Gemini prompts (fixture-based assertions)
- [ ] Prompt versioning (gemini + director versions in logs)
- [ ] Hybrid mode polish (dedup, diversity, clip selection improvements)
- [ ] ScenePlan (structured JSON replacing scenario_text)
- [ ] Brief parser (free-form text → Claude → ScenePlan JSON)
- [ ] Capability registry (AVAILABLE_CAPABILITIES injected into planner)
- [ ] Directed stickers (quantity-driven from ScenePlan)
- [ ] PiP-lite (single overlay video, 2-3 fixed layouts, border_radius clip)
- [ ] Split screen v1 (separate content_mode, top/bottom layout, paired clip selection)
- [ ] Text overlay for unmatched broll (Gemini-generated humor/question)
- [ ] Creative direction refinement in ScenePlan
- [ ] Error recovery (Creatomate 500 retry, Gemini safety filter retry)
- [ ] Render state persistence for "Retry render" button
- [ ] Test harness for safe animations + zoom variants

### Out of Scope

- Background music — Romina adds via CapCut/IG (IG algorithm promotes trending audio)
- Whisper enhance / smart cut — deferred to Phase 2A
- Visual effect presets (ghost, collage, Instagram overlay) — deferred to Phase 2B
- GIF stickers via Giphy — deferred to Phase 2B
- Higgsfield AI video — deferred to Phase 2B
- Multiple render variants / A/B testing — deferred to Phase 2B
- Speaker cutout / dynamic background — deferred to Phase 4
- Kinetic typography highlight — deferred to Phase 4
- Video B-roll from stock (Pexels) — deferred to Phase 4
- Auto-posting to Instagram — deferred to Phase 5
- ElevenLabs voice clone — deferred to Phase 5
- Content calendar — deferred to Phase 5
- Billing dashboard (React/HTML) — only on trigger: >2 users or >100 renders/month

## Context

- **Source:** Roadmap v3 derived from Consilium (8 rounds, Gemini + ChatGPT consensus)
- **User:** Romina, single user. No multi-tenancy needed.
- **Language:** All content Russian. Bot UI Russian.
- **Deployment:** Cloud Run (europe-west1), single container, stateless. No asyncio.create_task — only BackgroundTasks.
- **Existing patterns:** Service-per-integration, handlers.py orchestrates pipeline, Supabase content_items as single state store, GCS presigned URLs for Creatomate.
- **Known fragile areas:** creatomate_service.py and visual_director.py — all production bugs have lived here. Auto-reiterate before editing.
- **Render architecture:** Creatomate dynamic JSON (no templates), animations[] array for transitions, transcript_source per-clip karaoke.

## Constraints

- **Cloud Run**: No asyncio.create_task() — BackgroundTasks only. Stateless.
- **Creatomate**: animations[] for transitions (not property keyframes). Read pitfalls.md before any change.
- **GCS**: Always presigned URLs (bucket not public). 6hr expiry minimum.
- **Supabase**: model_dump() for JSONB (not model_dump_json()).
- **AI Models**: Claude Opus 4.6 (prod/1080p) / Sonnet 4.6 (dev/720p) for Visual Director. Gemini 2.5 Flash via Vertex AI. Whisper-1 for transcription.

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| Sprint 1+1.5 as milestone scope | Natural stabilisation boundary at Session 12. Core features must deploy, edge features (PiP, split screen) can slip. | — Pending |
| GSD regroups sessions into phases | Dependency-optimized phases > rigid session numbering. Consilium order is input, not constraint. | — Pending |
| SFX hardcoded mapping (no AI) | Zero AI cost, deterministic, simple. wipe→whoosh, slide→swoosh, fade→fade_soft, sticker→pop, hard_cut→click. | — Pending |
| ScenePlan replaces scenario_text | Structured JSON enables directed stickers, capability registry, future effects. Full replacement with temporary shim. | — Pending |
| Feedback classification via Haiku | Cheapest model sufficient for binary routing (gemini vs director instruction). | — Pending |

## Evolution

This document evolves at phase transitions and milestone boundaries.

**After each phase transition** (via `/gsd:transition`):
1. Requirements invalidated? → Move to Out of Scope with reason
2. Requirements validated? → Move to Validated with phase reference
3. New requirements emerged? → Add to Active
4. Decisions to log? → Add to Key Decisions
5. "What This Is" still accurate? → Update if drifted

**After each milestone** (via `/gsd:complete-milestone`):
1. Full review of all sections
2. Core Value check — still the right priority?
3. Audit Out of Scope — reasons still valid?
4. Update Context with current state

---
*Last updated: 2026-03-28 after initialization*
