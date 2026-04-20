# Requirements: Content Factory — Sprint 1+1.5

**Defined:** 2026-03-28
**Core Value:** Every reel should feel creatively directed — not just assembled — with feedback-driven iteration and a growing toolkit of visual effects.

## v1 Requirements

Requirements for Sprint 1+1.5 milestone. Each maps to roadmap phases.

### Visual Effects

- [x] **VFX-01**: Test renders validate safe/not-safe text animations (fade, slide-up, typewriter, pop) and zoom variants
- [x] **VFX-02**: Static popup text replaced with animated motion text, Visual Director chooses animation_type from safe enum
- [x] **VFX-03**: Python guardrails enforce fallback to fade for long text and duration caps on text animations
- [x] **VFX-04**: Unmatched B-roll clips display Gemini-generated text overlay (humor/question, 3-5 words) with fade in/out
- [ ] **VFX-05**: PiP-lite overlay renders one video element in 2-3 fixed layouts with round window (border_radius clip)
- [ ] **VFX-06**: PiP-lite is brief-driven — triggered by ScenePlan instruction
- [ ] **VFX-07**: Split screen renders as separate content_mode with top/bottom layout (1080x960 + 1080x960) and labels
- [ ] **VFX-08**: Split screen uses dedicated Gemini prompt for paired clip selection

### Audio

- [x] **AUD-01**: 5-10 SFX sounds sourced (whoosh_soft, whoosh_hard, pop_ui, click_cut, swoosh_slide, fade_soft) from Pixabay
- [x] **AUD-02**: SFX files uploaded to GCS with presigned URLs
- [x] **AUD-03**: Hardcoded SFX_MAP in creatomate_service.py maps transitions to sounds (wipe→whoosh, slide→swoosh, fade→fade_soft, sticker→pop, hard_cut→click)
- [x] **AUD-04**: SFX audio elements render on dedicated track, timed to transition timing

### Pipeline

- [ ] **PIPE-01**: ScenePlan Pydantic model with schema_version and extension points replaces scenario_text
- [ ] **PIPE-02**: Free-form brief text parsed by Claude into structured ScenePlan JSON
- [ ] **PIPE-03**: Capability registry (AVAILABLE_CAPABILITIES set) injected into planner prompt
- [ ] **PIPE-04**: Directed stickers driven by explicit quantity from ScenePlan
- [ ] **PIPE-05**: Auto-fill stickers only on permissive brief, with distance >=3-4s and no duplicate concepts
- [ ] **PIPE-06**: ScenePlan moments[] flow: Claude Planner (skeleton) -> Gemini (fill clips)
- [ ] **PIPE-07**: Creative direction refinement integrated into ScenePlan structure

### Feedback

- [x] **FEED-01**: Telegram UI shows "Approve" / "Redo" buttons after render delivery
- [x] **FEED-02**: Claude Haiku classifies feedback into gemini_instruction and/or director_instruction
- [ ] **FEED-03**: B-lite instruction filtering — each pipeline step reruns only with its relevant instruction
- [ ] **FEED-04**: Classification trace logged to Supabase (feedback_text, classified_type, affected_dimensions, acceptance)

### Billing

- [x] **BILL-01**: Cost columns added to Supabase content_items (cost_whisper, cost_gemini, cost_claude, cost_creatomate, cost_total_usd)
- [x] **BILL-02**: Usage logged after each API call
- [x] **BILL-03**: Bot sends cost breakdown in Telegram after each render

### Quality

- [x] **QUAL-01**: Regression harness script (scripts/gemini_regression.py) with 8-12 fixture cases
- [x] **QUAL-02**: Automated assertions: clip count, total duration, dedup score, unique sources, speech/broll ratio
- [x] **QUAL-03**: gemini_prompt_version and director_prompt_version logged with each render
- [x] **QUAL-04**: Gemini prompt improvements: dedup penalty, diversity heuristic, anti-linear selection (time-boxed)

### Stability

- [x] **STAB-01**: Retry logic for Creatomate 500 errors with exponential backoff
- [x] **STAB-02**: Retry logic for Gemini safety filter rejections
- [x] **STAB-03**: Render state saved to Supabase enabling "Retry render" button
- [x] **STAB-04**: Error recovery preserves partial pipeline state (no full restart needed)

### Instagram Carousel (Phase 7)

- [ ] **CAR-01**: Supabase schema extended with `carousel_slides` JSONB column + `stage_detail` TEXT column (migration)
- [ ] **CAR-02**: Design tokens module `assets/carousel_tokens.py` defines fonts, plashka opacity/radius/margin, text color/sizing, slide dimensions (1080×1350)
- [ ] **CAR-03**: `assets/fonts/Inter-Bold.ttf` + `Inter-Regular.ttf` bundled with OFL.txt license; module-level font cache (`_FONTS: dict[int, FreeTypeFont]`)
- [ ] **CAR-04**: `carousel_service.render_photo_slide()` renders JPEG (q=92) via PIL with pillow-heif HEIC support, semi-transparent plashka overlay + Russian text (emoji stripped, `draw.textbbox` centering, `asyncio.to_thread` wrap)
- [x] **CAR-05**: `carousel_service.render_video_slide()` renders MP4 via ffmpeg filter_complex (scale+crop to 1080×1350, overlay PNG), codec H.264 main/4.0/yuv420p/+faststart/-an, truncate >60s, concurrency capped via `asyncio.Semaphore(2)`
- [x] **CAR-06**: Video slide generates explicit JPEG thumbnail (<200KB, 320×320) for Telegram `InputMediaVideo` preview
- [ ] **CAR-07**: Claude generates `carousel_slides` JSON + `caption_telegram` (≤1024) + `caption_instagram` (≤2200) from free-form Russian brief
- [ ] **CAR-08**: `/carousel` FSM flow: awaiting_brief → analyzing → preview (numbered slides + 4 inline buttons: ✏️ edit / ✅ approve / 🔁 regenerate / ❌ reject) → awaiting_footage → assembling → delivering → approved
- [ ] **CAR-09**: `MediaGroupAggregatorMiddleware` aggregates Telegram media_group with 1.5s debounce, sorts by `message_id`, hard-caps at 10 files; non-album uploads rejected with Russian error
- [x] **CAR-10**: Preflight >20MB file check rejects with Drive fallback prompt ("Файл слишком большой для Telegram. Загрузи в Drive-папку и пришли /ready.") using new `DRIVE_CAROUSEL_FOLDER_ID` env var
- [x] **CAR-11**: Per-slide `tempfile.TemporaryDirectory` cleanup on success AND failure paths; GCS manifest as single source of truth post-ingest
- [x] **CAR-12**: Bounded retry budget per slide (max_attempts=2, per_slide_timeout=30-45s, total=420-450s); all-or-nothing delivery (any slide fails 2 retries → status=failed, user notified, no partial delivery)
- [x] **CAR-13**: `send_media_group` delivers assembled carousel to Telegram with caption on first InputMedia; `caption_instagram` sent as follow-up message for copy-paste
- [ ] **CAR-14**: `scripts/deploy.sh` adds `--memory 2Gi --timeout 540 --cpu 2`; `requirements.txt` adds `Pillow>=12` + `pillow-heif>=1.3`
- [ ] **CAR-15**: Romina UAT gate — one approved carousel end-to-end in production before phase marked complete (typography/plashka visual parity with Reels)

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
| VFX-01 | Phase 3 | Complete |
| VFX-02 | Phase 3 | Complete |
| VFX-03 | Phase 3 | Complete |
| VFX-04 | Phase 3 | Complete |
| VFX-05 | Phase 6 | Pending |
| VFX-06 | Phase 6 | Pending |
| VFX-07 | Phase 6 | Pending |
| VFX-08 | Phase 6 | Pending |
| AUD-01 | Phase 3 | Complete |
| AUD-02 | Phase 3 | Complete |
| AUD-03 | Phase 3 | Complete |
| AUD-04 | Phase 3 | Complete |
| PIPE-01 | Phase 4 | Pending |
| PIPE-02 | Phase 4 | Pending |
| PIPE-03 | Phase 4 | Pending |
| PIPE-04 | Phase 4 | Pending |
| PIPE-05 | Phase 4 | Pending |
| PIPE-06 | Phase 4 | Pending |
| PIPE-07 | Phase 4 | Pending |
| FEED-01 | Phase 2 | Complete |
| FEED-02 | Phase 2 | Complete |
| FEED-03 | Phase 5 | Pending |
| FEED-04 | Phase 5 | Pending |
| BILL-01 | Phase 1 | Complete |
| BILL-02 | Phase 1 | Complete |
| BILL-03 | Phase 1 | Complete |
| QUAL-01 | Phase 2 | Complete |
| QUAL-02 | Phase 2 | Complete |
| QUAL-03 | Phase 1 | Complete |
| QUAL-04 | Phase 2 | Complete |
| STAB-01 | Phase 1 | Complete |
| STAB-02 | Phase 1 | Complete |
| STAB-03 | Phase 1 | Complete |
| STAB-04 | Phase 1 | Complete |
| CAR-01 | Phase 7 | Pending |
| CAR-02 | Phase 7 | Pending |
| CAR-03 | Phase 7 | Pending |
| CAR-04 | Phase 7 | Pending |
| CAR-05 | Phase 7 | Complete |
| CAR-06 | Phase 7 | Complete |
| CAR-07 | Phase 7 | Pending |
| CAR-08 | Phase 7 | Pending |
| CAR-09 | Phase 7 | Pending |
| CAR-10 | Phase 7 | Complete |
| CAR-11 | Phase 7 | Complete |
| CAR-12 | Phase 7 | Complete |
| CAR-13 | Phase 7 | Complete |
| CAR-14 | Phase 7 | Pending |
| CAR-15 | Phase 7 | Pending |

**Coverage:**
- v1 requirements: 49 total
- Mapped to phases: 49
- Unmapped: 0

---
*Requirements defined: 2026-03-28*
*Last updated: 2026-03-28 after roadmap revision (FEED-01/02 moved to Phase 2)*
