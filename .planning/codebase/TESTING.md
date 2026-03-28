# Testing Patterns

**Analysis Date:** 2026-03-28

## Test Framework

**Runner:**
- pytest (installed in venv at `venv/lib/python3.12/site-packages/_pytest/`)
- No `pytest.ini`, `setup.cfg`, or `pyproject.toml` config file — pytest runs with defaults

**Assertion Library:**
- pytest built-in `assert`

**Run Commands:**
```bash
# Run all tests in tests/ directory
python -m pytest tests/

# Run specific test file
python -m pytest tests/test_silence_map.py

# Run with verbose output
python -m pytest tests/ -v

# Run smoke scripts directly (not pytest)
python3 -m scripts.test_supabase
python3 test_voiceover.py
python3 test_anchor.py
```

## Test File Organization

**Location:**
- Formal pytest tests: `tests/` directory (co-located with project root, separate from `app/`)
- Exploration/smoke scripts: project root (`test_voiceover.py`, `test_anchor.py`, `test_voiceover_complex.py`)
- Supabase smoke test: `scripts/test_supabase.py`

**Naming:**
- pytest files: `tests/test_<module>.py` — e.g. `tests/test_silence_map.py`
- Root-level exploration scripts: `test_<feature>.py` — these are manual, not run by pytest
- Script smoke tests: `scripts/test_<service>.py`

**Structure:**
```
content-factory/
├── tests/
│   ├── __init__.py
│   └── test_silence_map.py          # Only formal pytest test suite
├── scripts/
│   └── test_supabase.py             # asyncio.run() smoke test (no pytest)
├── test_anchor.py                   # Creatomate API exploration script
├── test_voiceover.py                # Creatomate audio element variants
└── test_voiceover_complex.py        # Creatomate payload complexity test
```

## Test Structure

**Suite organization in `tests/test_silence_map.py`:**
```python
"""Tests for analyze_silence (Silence Map Generator)."""

from app.services.whisper_service import analyze_silence


def _w(word: str, start: float, end: float) -> dict:
    """Shorthand for a word timestamp dict."""
    return {"word": word, "start": start, "end": end}


# ── 1. Normal speech, 5 words, no pauses ────────────────────────────

def test_continuous_speech_single_block():
    words = [_w("привет", 0.5, 0.9), ...]
    result = analyze_silence(words)
    assert len(result) == 2
    assert result[0]["type"] == "silence"
```

**Patterns:**
- No `class`-based test suites — all top-level `def test_*` functions
- Section comments `# ── N. Description ──` separate logical test groups
- Helper factory function `_w()` for constructing test input data
- Tests are pure functions with no setup/teardown — no fixtures used
- Each test is self-contained with inline data construction

## Mocking

**Framework:** None — no `unittest.mock`, `pytest-mock`, or `MagicMock` used in any test.

**What to mock:**
- Not applicable for current test suite — `analyze_silence` is a pure function with no external dependencies.
- For future tests of service classes: mock `asyncio.to_thread` calls and HTTP clients.

**What NOT to mock:**
- Pure computation functions (`analyze_silence`, `map_broll_to_render_timeline`, `_validate_blueprint`) — test with real inputs.

## Fixtures and Factories

**Test Data:**
```python
# Pattern from tests/test_silence_map.py
def _w(word: str, start: float, end: float) -> dict:
    """Shorthand for a word timestamp dict."""
    return {"word": word, "start": start, "end": end}

# Usage in tests:
words = [
    _w("привет", 0.5, 0.9),
    _w("как", 0.95, 1.1),
]
```

**Location:**
- No shared fixtures directory. Helpers defined at the top of the test file.
- No `conftest.py` found.

## Coverage

**Requirements:** None enforced (no coverage config).

**View Coverage:**
```bash
python -m pytest tests/ --cov=app --cov-report=term-missing
```
(pytest-cov would need to be installed; not in current requirements.txt)

## Test Types

**Unit Tests (`tests/`):**
- Only `tests/test_silence_map.py` exists — covers `analyze_silence()` in `whisper_service.py`
- 9 test cases covering: normal speech, pauses, filler word removal, edge cases (empty input, short words, uppercase), safety margin capping
- Tests are deterministic pure-function tests — no network, no I/O

**Smoke / Integration Scripts (root + scripts/):**
- `scripts/test_supabase.py`: full asyncio CRUD flow against real Supabase — create, get, find, update, list, delete. Uses `assert` for basic correctness. Run with `python3 -m scripts.test_supabase`.
- `test_voiceover.py`: submits 4 Creatomate render variants (track/duration combos) and polls for result. Manual comparison test.
- `test_voiceover_complex.py`: submits 3 progressively complex payloads to find audio element breakage. Manual.
- `test_anchor.py`: tests Creatomate anchor element pattern for multi-transcript_source workaround. Manual.

**E2E Tests:** None.

## Common Patterns

**Async Testing:**
```python
# Smoke scripts use asyncio.run() directly, not pytest-asyncio
async def main() -> None:
    item = await svc.create_content_item(...)
    assert item["status"] == "idea"

if __name__ == "__main__":
    asyncio.run(main())
```

No `pytest-asyncio` in use — pytest tests only cover synchronous pure functions.

**Error Testing:**
```python
# Edge case: empty input returns empty list
def test_empty_input():
    assert analyze_silence([]) == []

# Edge case: all words too short to pass min_duration filter
def test_short_words_all_removed():
    words = [_w("а", 0.0, 0.3), _w("б", 2.0, 2.3), _w("в", 4.0, 4.3)]
    result = analyze_silence(words)
    assert result == []
```

**Filtering/classification tests:**
```python
# Verify items NOT in result
all_words = [w["word"] for b in speech_blocks for w in b["words"]]
assert "э" not in all_words

# Verify count of result type
speech_blocks = [s for s in result if s["type"] == "speech"]
assert len(speech_blocks) == 2
```

## Testing Gaps

**No tests exist for:**
- `creatomate_service.py` — `build_source`, `apply_visual_blueprint`, `resolve_overlay_render_times`
- `visual_director.py` — `_validate_blueprint`, `_make_fallback`, `get_visual_blueprint`
- `timeline_utils.py` — `map_broll_to_render_timeline`
- `audio_processing.py` — `process_voiceover`, `get_duration`, `adjust_voiceover_for_transitions`
- `gemini_service.py` — all analysis methods
- `handlers.py` — entire bot flow
- `supabase_service.py` — only covered by manual smoke script, not pytest

The two highest-risk files (`creatomate_service.py` and `visual_director.py`) identified in CLAUDE.md as "where every production bug has lived" have zero automated test coverage.

---

*Testing analysis: 2026-03-28*
