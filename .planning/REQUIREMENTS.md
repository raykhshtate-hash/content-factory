# Requirements: Content Factory — Sprint 1+1.5

**Defined:** 2026-03-28
**Core Value:** Every reel should feel creatively directed — not just assembled — with feedback-driven iteration and a growing toolkit of visual effects.

## v1 Requirements

Requirements for Sprint 1+1.5 milestone. Each maps to roadmap phases.

### Visual Effects

- [ ] **VFX-01**: Test renders validate safe/not-safe text animations (fade, slide-up, typewriter, pop) and zoom variants
- [ ] **VFX-02**: Static popup text replaced with animated motion text, Visual Director chooses animation_type from safe enum
- [ ] **VFX-03**: Python guardrails enforce fallback to fade for long text and duration caps on text animations
- [ ] **VFX-04**: Unmatched B-roll clips display Gemini-generated text overlay (humor/question, 3-5 words) with fade in/out
- [ ] **VFX-05**: PiP-lite overlay renders one video element in 2-3 fixed layouts with round window (border_radius clip)
- [ ] **VFX-06**: PiP-lite is brief-driven — triggered by ScenePlan instruction
- [ ] **VFX-07**: Split screen renders as separate content_mode with top/bottom layout (1080x960 + 1080x960) and labels
- [ ] **VFX-08**: Split screen uses dedicated Gemini prompt for paired clip selection

### Audio

- [ ] **AUD-01**: 5-10 SFX sounds sourced (whoosh_soft, whoosh_hard, pop_ui, click_cut, swoosh_slide, fade_soft) from Pixabay
- [ ] **AUD-02**: SFX files uploaded to GCS with presigned URLs
- [ ] **AUD-03**: Hardcoded SFX_MAP in creatomate_service.py maps transitions to sounds (wipe→whoosh, slide→swoosh, fade→fade_soft, sticker→pop, hard_cut→click)
- [ ] **AUD-04**: SFX audio elements render on dedicated track, timed to transition timing

### Pipeline

- [ ] **PIPE-01**: ScenePlan Pydantic model with schema_version and extension points replaces scenario_text
- [ ] **PIPE-02**: Free-form brief text parsed by Claude into structured ScenePlan JSON
- [ ] **PIPE-03**: Capability registry (AVAILABLE_CAPABILITIES set) injected into planner prompt
- [ ] **PIPE-04**: Directed stickers driven by explicit quantity from ScenePlan
- [ ] **PIPE-05**: Auto-fill stickers only on permissive brief, with distance >=3-4s and no duplicate concepts
- [ ] **PIPE-06**: ScenePlan moments[] flow: Claude Planner (skeleton) -> Gemini (fill clips)
- [ ] **PIPE-07**: Creative direction refinement integrated into ScenePlan structure

### Feedback

- [ ] **FEED-01**: Telegram UI shows "Approve" / "Redo" buttons after render delivery
- [ ] **FEED-02**: Claude Haiku classifies feedback into gemini_instruction and/or director_instruction
- [ ] **FEED-03**: B-lite instruction filtering — each pipeline step reruns only with its relevant instruction
- [ ] **FEED-04**: Classification trace logged to Supabase (feedback_text, classified_type, affected_dimensions, acceptance)

### Billing

- [x] **BILL-01**: Cost columns added to Supabase content_items (cost_whisper, cost_gemini, cost_claude, cost_creatomate, cost_total_usd)
- [x] **BILL-02**: Usage logged after each API call
- [x] **BILL-03**: Bot sends cost breakdown in Telegram after each render

### Quality

- [ ] **QUAL-01**: Regression harness script (scripts/gemini_regression.py) with 8-12 fixture cases
- [ ] **QUAL-02**: Automated assertions: clip count, total duration, dedup score, unique sources, speech/broll ratio
- [x] **QUAL-03**: gemini_prompt_version and director_prompt_version logged with each render
- [ ] **QUAL-04**: Gemini prompt improvements: dedup penalty, diversity heuristic, anti-linear selection (time-boxed)

### Stability

- [x] **STAB-01**: Retry logic for Creatomate 500 errors with exponential backoff
- [x] **STAB-02**: Retry logic for Gemini safety filter rejections
- [x] **STAB-03**: Render state saved to Supabase enabling "Retry render" button
- [x] **STAB-04**: Error recovery preserves partial pipeline state (no full restart needed)

## v2 Requirements

Deferred to future milestones. Tracked but not in current roadmap.

### Whisper Enhance (Phase 2A)

- **WENH-01**: Smart cut removes silence >0.5s and fillers from single video
- **WENH-02**: Speed matching via ffmpeg atempo per-segment (zone-based)
- **WENH-03**: 1.3x B-roll speedup with pre-computed timestamp adjustment
- **WENH-04**: Auto speech-vs-broll detection for automatic mode selection

### Effect Presets (Phase 2B)

- **EFPR-01**: Ghost effect (B&W double-exposure via effect manifest)
- **EFPR-02**: Collage effect (five clips, white borders, rotated grid)
- **EFPR-03**: Instagram overlay (profile screenshot with entrance/exit animation)
- **EFPR-04**: GIF stickers via Giphy API with attribution
- **EFPR-05**: Multiple render variants for A/B testing

## Out of Scope

| Feature | Reason |
|---------|--------|
| Background music | Romina adds via CapCut/IG — IG algorithm promotes trending audio |
| Billing dashboard (React/HTML) | Only on trigger: >2 users or >100 renders/month |
| Higgsfield AI video | Isolated async branch, not core pipeline — Phase 2B |
| Speaker cutout / dynamic background | Phase 4 — requires AI segmentation |
| Kinetic typography highlight | Phase 4 — separate system from animated overlays |
| Video B-roll from stock (Pexels) | Phase 4 |
| Auto-posting to Instagram | Phase 5 — requires approval state machine |
| ElevenLabs voice clone | Phase 5 |
| Content calendar | Phase 5 |
| Script editing in Telegram | Phase 5 |

## Traceability

Which phases cover which requirements. Updated during roadmap creation.

| Requirement | Phase | Status |
|-------------|-------|--------|
| VFX-01 | Phase 3 | Pending |
| VFX-02 | Phase 3 | Pending |
| VFX-03 | Phase 3 | Pending |
| VFX-04 | Phase 3 | Pending |
| VFX-05 | Phase 6 | Pending |
| VFX-06 | Phase 6 | Pending |
| VFX-07 | Phase 6 | Pending |
| VFX-08 | Phase 6 | Pending |
| AUD-01 | Phase 3 | Pending |
| AUD-02 | Phase 3 | Pending |
| AUD-03 | Phase 3 | Pending |
| AUD-04 | Phase 3 | Pending |
| PIPE-01 | Phase 4 | Pending |
| PIPE-02 | Phase 4 | Pending |
| PIPE-03 | Phase 4 | Pending |
| PIPE-04 | Phase 4 | Pending |
| PIPE-05 | Phase 4 | Pending |
| PIPE-06 | Phase 4 | Pending |
| PIPE-07 | Phase 4 | Pending |
| FEED-01 | Phase 2 | Pending |
| FEED-02 | Phase 2 | Pending |
| FEED-03 | Phase 5 | Pending |
| FEED-04 | Phase 5 | Pending |
| BILL-01 | Phase 1 | Complete |
| BILL-02 | Phase 1 | Complete |
| BILL-03 | Phase 1 | Complete |
| QUAL-01 | Phase 2 | Pending |
| QUAL-02 | Phase 2 | Pending |
| QUAL-03 | Phase 1 | Complete |
| QUAL-04 | Phase 2 | Pending |
| STAB-01 | Phase 1 | Complete |
| STAB-02 | Phase 1 | Complete |
| STAB-03 | Phase 1 | Complete |
| STAB-04 | Phase 1 | Complete |

**Coverage:**
- v1 requirements: 34 total
- Mapped to phases: 34
- Unmapped: 0

---
*Requirements defined: 2026-03-28*
*Last updated: 2026-03-28 after roadmap revision (FEED-01/02 moved to Phase 2)*
