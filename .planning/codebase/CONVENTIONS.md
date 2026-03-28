# Coding Conventions

**Analysis Date:** 2026-03-28

## Naming Patterns

**Files:**
- `snake_case.py` for all modules: `creatomate_service.py`, `visual_director.py`, `audio_processing.py`
- Suffix `_service.py` for stateful classes with API clients: `gemini_service.py`, `gcs_service.py`
- No suffix for functional modules: `timeline_utils.py`, `audio_processing.py`
- Bot handlers in `app/bot/handlers.py` — single monolithic file

**Functions:**
- `snake_case` throughout
- Private helpers prefixed with `_`: `_build_sticker_anim`, `_parse_gs_uri`, `_get_client`, `_now`
- Async functions for anything I/O-bound: `async def process_voiceover(...)`, `async def get_duration(...)`
- Sync functions for pure computation: `def analyze_silence(...)`, `def map_broll_to_render_timeline(...)`

**Variables:**
- `snake_case` for all local variables
- Constants in `UPPER_SNAKE_CASE`: `API_URL`, `FILLER_WORDS`, `QUALITY_PRESETS`, `KARAOKE_STYLES`
- Module-level singletons with leading `_`: `_client: Optional[Client] = None`

**Classes:**
- `PascalCase`: `GeminiService`, `CreatomateService`, `DriveService`, `GCSService`, `WhisperService`
- Pydantic models: `ClipCandidate`, `VideoAnalysis`, `StoryboardScene`, `StoryboardAnalysis`, `ContentItem`
- `@dataclass` for simple data structs: `Clip` in `app/services/creatomate_service.py`
- FSM state groups in `handlers.py`: `ClipSelection(StatesGroup)`, `ScriptEdit(StatesGroup)`

**Type Annotations:**
- Used consistently on all function signatures (Python 3.12 style)
- Modern union syntax: `str | None`, `list[dict] | None`, `int | None` (not `Optional[str]`)
- `Optional` still appears in older code (supabase_service, drive_service) — being phased out
- `dict` preferred over `Dict` (no `from typing import Dict`)

## Code Style

**Formatting:**
- No enforced formatter (no `.prettierrc`, `pyproject.toml`, or `ruff.toml` found)
- Indentation: 4 spaces
- Line length: generally under 100 chars, prompt strings use `\` line continuation
- Blank lines: 2 between top-level definitions, 1 within classes between methods

**Section separators inside functions/modules:**
- Section comments styled as `# ── Section Name ──` with Unicode box-drawing dashes
- Used heavily in `creatomate_service.py`, `visual_director.py`, `handlers.py`

**Imports:**
- Standard library first, then third-party, then `app.*` internal
- No strict enforcement — `handlers.py` has deferred imports mid-file (after initial router setup)
- Deferred imports used to avoid circular dependencies: `from app.services.drive_service import DriveService` at line 70 in `handlers.py`

## Import Organization

**Order observed:**
1. Standard library (`json`, `logging`, `asyncio`, `os`, `re`, `tempfile`)
2. Third-party (`aiogram`, `anthropic`, `pydantic`, `tenacity`, `httpx`)
3. Internal app imports (`from app.config import settings`, `from app.services import ...`)

**Path Aliases:**
- None — all imports use full `app.` prefix

## Error Handling

**Strategy:** Broad `except Exception` at handler boundaries, specific exception types at service layer.

**Service layer pattern (services/):**
- Specific exceptions caught and re-raised or returned as `None`:
  ```python
  # gemini_service.py — let @retry handle specific errors
  except (ClientError, ServerError) as e:
      raise  # let @retry handle 429 / 5xx
  except Exception as e:
      logger.warning("...")
      return None
  ```
- `tenacity` `@retry` on all external API calls: Gemini, Whisper, Drive, Creatomate
  - Pattern: `retry_if_exception_type`, `wait_exponential(multiplier, min, max)`, `stop_after_attempt(3)`, `reraise=True`

**Handler layer pattern (handlers.py):**
- Pipeline steps wrapped in `try/except Exception as e` with fallback behavior
- Fallback on Gemini failure: equal-split clips
- Fallback on audio processing failure: use original audio
- Silent `except Exception: pass` used in UI update calls (e.g. `message.edit_text`)
- `raise RuntimeError(...)` used to signal unrecoverable failure to outer handler

**GCS/HTTP errors:**
- `response.raise_for_status()` on all HTTP calls
- `RuntimeError` raised for missing credentials or config

**Validation in visual_director.py:**
- `_validate_blueprint()` returns `None` on invalid Claude response
- Caller falls back to `_make_fallback(num_clips)` on `None`

## Logging

**Framework:** `logging` standard library

**Setup pattern (every module):**
```python
logger = logging.getLogger(__name__)
```

**Style inconsistency:** Mix of `%`-style and f-string formatting:
- Preferred (services): `logger.info("Video durations: %s (total=%.1fs)", video_durations, total_video_duration)`
- Older/webhook style: `logger.error(f"Creatomate render failed for item {item_id}: {payload}")`
- Newer code (handlers, services) uses `%`-style; webhook and some handlers use f-strings

**Log levels used:**
- `logger.debug(...)` — detailed internal state (blueprint output, b-roll dedup)
- `logger.info(...)` — pipeline milestones, clip counts, render IDs
- `logger.warning(...)` — recoverable failures with fallback applied
- `logger.error(...)` — failures that reach the user or stop the pipeline

**Avoid:** `print()` in production code — only used in GeminiService init (`print(f"⚠️ Vertex AI init failed")`) as legacy

## Comments

**Module docstrings:**
Every service file has a triple-quoted module docstring explaining purpose and approach:
```python
"""
Creatomate Render Service — Dynamic JSON Source.

Builds video renders entirely via JSON source (no predefined templates).
...
"""
```

**Inline comments:**
- Section headers: `# ── Step 1: Group words into raw speech blocks ──`
- Critical rule reminders inline (e.g. safety rules near Creatomate element builders)
- `# TODO:` for disabled/deferred features (kept in codebase, not tracked externally)

**JSDoc equivalent:** Not used. Type hints on signatures serve this purpose.

## Function Design

**Size:** Varies widely. `handlers.py` functions run 50-200 lines. Service functions typically 20-50 lines. No enforced limit.

**Parameters:** Keyword arguments with defaults for optional fields. Long parameter lists accepted for builder functions (e.g. `build_source` in `creatomate_service.py` has 12 params).

**Return Values:**
- `None` as sentinel for "not found" or "failed" (service layer)
- `dict` for structured data (Supabase rows, Creatomate source JSON)
- `tuple` for multi-value returns: `tuple[str, float, float]` in `process_voiceover`
- Pydantic models for typed Gemini responses (`VideoAnalysis`, `StoryboardAnalysis`)

## Module Design

**Services pattern:** Two coexist:
1. Class-based with stateful clients: `GCSService`, `DriveService`, `GeminiService`, `WhisperService`, `CreatomateService`, `ClaudeService` — instantiated at call site
2. Module-level functions with singleton client: `supabase_service.py` — all functions are top-level `async def`, client created via `_get_client()`

**Exports:** No `__all__` defined anywhere. Public API is implicit (no leading `_`).

**Barrel files:** Not used. Each module imported directly.

**Constants modules:** Constants defined at module top level in the file that uses them (`QUALITY_PRESETS` in `creatomate_service.py`, `FILLER_WORDS` in `whisper_service.py`).

## Async Patterns

**Critical rule:** Never use `asyncio.create_task()` — use FastAPI `BackgroundTasks`. Cloud Run kills orphaned tasks.

**Blocking I/O wrapping:**
```python
# All blocking sync SDK calls wrapped in asyncio.to_thread
result = await asyncio.to_thread(
    lambda: client.table(TABLE).select("*").eq("id", item_id).single().execute()
)
```

**ffmpeg/ffprobe:** Always via `asyncio.to_thread(subprocess.run, args, ...)` — never blocking the event loop directly.

---

*Convention analysis: 2026-03-28*
