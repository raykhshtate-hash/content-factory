# Codebase Structure

**Analysis Date:** 2026-03-28

## Directory Layout

```
content-factory/
├── app/                        # All application code
│   ├── main.py                 # FastAPI app + lifespan + webhook endpoint
│   ├── config.py               # Settings (env vars), module-level singleton `settings`
│   ├── __init__.py
│   ├── bot/                    # Telegram bot layer
│   │   ├── handlers.py         # All command + callback handlers (main pipeline logic)
│   │   ├── keyboards.py        # Inline keyboard factories (mostly stubs)
│   │   └── messages.py         # Static string constants for bot messages
│   ├── models/                 # Pydantic data models
│   │   └── content_item.py     # ContentItem model (legacy — DB schema in supabase_service)
│   ├── prompts/                # AI prompt text files
│   │   ├── claude_system.txt   # System prompt for ClaudeService script generation
│   │   ├── compliance_check.txt # Disabled compliance check prompt
│   │   └── gemini_video.txt    # Legacy Gemini prompt (superseded by inline prompts)
│   ├── services/               # Service layer — one file per integration/domain
│   │   ├── supabase_service.py # DB CRUD (content_items table)
│   │   ├── drive_service.py    # Google Drive listing + streaming copy to GCS
│   │   ├── gcs_service.py      # GCS upload/download/delete + V4 presigned URLs
│   │   ├── gemini_service.py   # Vertex AI Gemini video analysis (structured output)
│   │   ├── visual_director.py  # Claude: transitions + sticker overlay blueprint
│   │   ├── creatomate_service.py # Render JSON builder + Creatomate API submit
│   │   ├── audio_processing.py # ffmpeg silence removal + speedup for storyboard voiceover
│   │   ├── whisper_service.py  # OpenAI Whisper word-level transcription + silence map
│   │   ├── claude_service.py   # Claude script generation (text content)
│   │   ├── timeline_utils.py   # B-roll source→render timeline mapping utility
│   │   ├── video_processing.py # Lightweight video utilities
│   │   └── pexels_service.py   # Pexels stock video search (disabled/legacy)
│   └── webhooks/               # Incoming webhook handlers (non-Telegram)
│       └── creatomate_webhook.py # POST /webhooks/creatomate/{item_id}
├── scripts/                    # Dev/ops scripts (not deployed)
│   ├── deploy.sh               # Build + push Docker, deploy to Cloud Run, set webhook
│   ├── merge.sh                # Merge dev→main git workflow
│   ├── cleanup_gcs.py          # Manual GCS bucket cleanup
│   ├── test_supabase.py        # Supabase smoke test
│   ├── test_broll_keywords.py  # Pexels keyword test (legacy)
│   └── test_pexels.py          # Pexels API test (legacy)
├── tasks/
│   └── lessons.md              # Project lessons / post-mortems
├── tests/
│   └── test_silence_map.py     # Unit tests for analyze_silence()
├── templates/                  # Empty (reserved for future Jinja2 or similar)
├── Dockerfile                  # python:3.12-slim + ffmpeg + uvicorn
├── requirements.txt            # Python dependencies (unpinned)
├── CLAUDE.md                   # Project instructions for Claude Code
├── test_anchor.py              # Root-level integration test (anchor/sticker timing)
├── test_voiceover.py           # Root-level voiceover processing test
└── test_voiceover_complex.py   # Root-level complex voiceover test
```

## Directory Purposes

**`app/bot/`:**
- Purpose: All Telegram-facing logic
- Contains: handlers (pipeline orchestration), message strings, keyboard builders
- Key files: `handlers.py` — 800+ lines, contains ALL callback and command handlers plus inline helper functions (`storyboard_render`, `analyze_and_propose`, `render_talking_head`, `_build_candidate_spans`, `_legacy_select_anchors`, `_merge_anchors_into_overlays`, `_parse_style_params`)

**`app/services/`:**
- Purpose: External API wrappers and domain utilities
- Contains: One class or set of functions per external system
- Key files: `creatomate_service.py` (most complex — render JSON builder), `visual_director.py` (Claude integration + sticker logic), `gemini_service.py` (Vertex AI + Pydantic schemas)

**`app/webhooks/`:**
- Purpose: FastAPI routes for external service callbacks
- Contains: Only `creatomate_webhook.py` — delivery flow after render completes

**`app/models/`:**
- Purpose: Pydantic models
- Key note: `content_item.py` is a legacy stub. Real DB schema is implicit in `supabase_service.py`. Gemini output schemas live in `gemini_service.py`.

**`app/prompts/`:**
- Purpose: Long-form prompt text loaded at import time
- Usage: `claude_service.py` reads `claude_system.txt` via `Path(__file__).parent.parent / "prompts" / "claude_system.txt"`
- Note: Visual Director system prompt is inline in `visual_director.py` (not a file)

**`scripts/`:**
- Purpose: Developer tooling only — not imported by app code
- DO NOT REFACTOR: `deploy.sh` (sets Telegram webhook), `merge.sh`

**`tasks/`:**
- Purpose: Project management artifacts
- `lessons.md` — append-only lesson log (written after mistakes)

## Key File Locations

**Entry Points:**
- `app/main.py`: FastAPI app creation, lifespan, Telegram webhook endpoint, polling setup
- `app/bot/handlers.py`: All user-facing logic and full pipeline orchestration

**Configuration:**
- `app/config.py`: All env var access via `settings` singleton
- `.env`: Local secrets (never commit, never read contents)
- `service-account.json`: GCP service account credentials (local dev only)

**Core Pipeline Logic:**
- `app/bot/handlers.py`: Mode detection, Drive→GCS copy, pipeline routing
- `app/services/gemini_service.py`: Video analysis schemas + Vertex AI calls
- `app/services/visual_director.py`: Claude blueprint generation + validation
- `app/services/creatomate_service.py`: `build_source()` and `apply_visual_blueprint()` — render JSON assembly
- `app/services/audio_processing.py`: Voiceover silence removal + speedup
- `app/services/whisper_service.py`: Word-level transcription + silence map

**Database:**
- `app/services/supabase_service.py`: All Supabase CRUD — `create_content_item`, `get_item`, `update_item`, `find_active_item`, `list_items_by_status`, `list_user_items`

**Delivery:**
- `app/webhooks/creatomate_webhook.py`: Render callback → download → GCS archive → Telegram send

**Testing:**
- `tests/test_silence_map.py`: Unit tests (pytest)
- `test_anchor.py`, `test_voiceover.py`, `test_voiceover_complex.py`: Integration/manual tests at root

## Naming Conventions

**Files:**
- `snake_case.py` for all Python modules
- Service files named `{service_name}_service.py` (e.g., `gemini_service.py`, `gcs_service.py`)
- Webhook files named `{provider}_webhook.py`
- Test files named `test_{subject}.py`

**Directories:**
- `snake_case` throughout (`app/bot/`, `app/services/`, `app/webhooks/`)

**Classes:**
- `PascalCase` for service classes: `GeminiService`, `CreatomateService`, `DriveService`, `GCSService`
- `PascalCase` for Pydantic models: `VideoAnalysis`, `ClipCandidate`, `StoryboardScene`
- `PascalCase` for dataclasses: `Clip`

**Functions:**
- `snake_case` throughout
- Private helpers prefixed with `_`: `_build_candidate_spans`, `_parse_style_params`, `_get_client`, `_parse_gs_uri`
- Async handlers follow aiogram convention: `cmd_{command}`, callback data uses `{action}:{sub}:{item_id}` format

**Constants:**
- `UPPER_SNAKE_CASE`: `QUALITY_PRESETS`, `KARAOKE_STYLES`, `TRANSITION_MAP`, `TABLE`, `API_URL`

## Where to Add New Code

**New content mode (pipeline variant):**
- Mode detection logic: `app/bot/handlers.py` in `cmd_ready()` / mode routing block
- New render function: add as `async def {mode}_render(...)` in `app/bot/handlers.py`
- New Gemini schema: add Pydantic models to `app/services/gemini_service.py`, add `analyze_{mode}()` method

**New external service integration:**
- Implementation: `app/services/{provider}_service.py`
- Config: add env var to `app/config.py` Settings class
- Instantiate in `app/bot/handlers.py` where needed (not module-level singletons)

**New Creatomate render element type:**
- Read `~/.claude/skills/creatomate/references/pitfalls.md` and `our-working-payload.md` first
- Add builder logic to `app/services/creatomate_service.py` in `build_source()` or `apply_visual_blueprint()`

**New bot command:**
- Add handler with `@router.message(Command("{name}"))` in `app/bot/handlers.py`
- Add user-facing string to `app/bot/messages.py`

**New AI prompt (long-form):**
- Add `.txt` file to `app/prompts/`
- Load via `Path(__file__).parent.parent / "prompts" / "{name}.txt"` at module level

**New test:**
- Unit tests: `tests/test_{subject}.py` (pytest)
- Integration/manual tests: root-level `test_{subject}.py`

## Special Directories

**`.planning/`:**
- Purpose: GSD planning artifacts (phases, codebase analysis)
- Generated: No
- Committed: Yes

**`venv/`:**
- Purpose: Python virtual environment
- Generated: Yes
- Committed: No (in `.gitignore`)

**`__pycache__/`:**
- Purpose: Python bytecode cache
- Generated: Yes
- Committed: No

**`templates/`:**
- Purpose: Reserved, currently empty
- Generated: No
- Committed: Yes (empty)

---

*Structure analysis: 2026-03-28*
