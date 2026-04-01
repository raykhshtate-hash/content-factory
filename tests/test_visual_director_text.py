"""Tests for Visual Director text animation extensions.

Covers: animation_type per clip, text_popups[] validation,
long text fallback, popup cap, y-position clamping, text truncation.
"""

import pytest

from app.services.visual_director import (
    ALLOWED_TEXT_ANIMATIONS,
    LONG_TEXT_THRESHOLD,
    MAX_TEXT_POPUPS,
    _validate_blueprint,
)


# ── Helpers ──

def _base_blueprint(num_clips: int, **overrides) -> dict:
    """Minimal valid blueprint with N clips."""
    clips = [{"index": i, "transition": None} for i in range(num_clips)]
    bp = {
        "overall_style": "dynamic",
        "clips": clips,
        "overlays": [],
    }
    bp.update(overrides)
    return bp


def _durations(num_clips: int, dur: float = 5.0) -> list[float]:
    return [dur] * num_clips


# ── Per-clip animation_type tests ──

def test_clip_animation_type_preserved():
    """Valid animation_type on a clip is preserved in output."""
    bp = _base_blueprint(2)
    bp["clips"][0]["animation_type"] = "pop"
    bp["clips"][1]["animation_type"] = "slide-up"
    result = _validate_blueprint(bp, 2, _durations(2))
    assert result is not None
    assert result["clips"][0]["animation_type"] == "pop"
    assert result["clips"][1]["animation_type"] == "slide-up"


def test_clip_animation_type_invalid_defaults_to_fade():
    """Invalid animation_type falls back to 'fade'."""
    bp = _base_blueprint(1)
    bp["clips"][0]["animation_type"] = "explode"
    result = _validate_blueprint(bp, 1, _durations(1))
    assert result is not None
    assert result["clips"][0]["animation_type"] == "fade"


def test_clip_animation_type_missing_defaults_to_fade():
    """Missing animation_type defaults to 'fade'."""
    bp = _base_blueprint(1)
    result = _validate_blueprint(bp, 1, _durations(1))
    assert result is not None
    assert result["clips"][0]["animation_type"] == "fade"


# ── text_popups[] validation tests ──

def test_text_popups_valid_entry():
    """Valid text popup is returned with all cleaned fields."""
    bp = _base_blueprint(2, text_popups=[
        {"clip_index": 1, "text": "Hello!", "animation_type": "pop", "x": "30%", "y": "40%"}
    ])
    result = _validate_blueprint(bp, 2, _durations(2))
    assert result is not None
    assert len(result["text_popups"]) == 1
    popup = result["text_popups"][0]
    assert popup["clip_index"] == 1
    assert popup["text"] == "Hello!"
    assert popup["animation_type"] == "pop"
    assert popup["x"] == "30%"
    assert popup["y"] == "40%"


def test_text_popups_capped_at_max():
    """Only MAX_TEXT_POPUPS popups are kept, rest dropped."""
    popups = [
        {"clip_index": i, "text": f"Text {i}", "animation_type": "fade"}
        for i in range(MAX_TEXT_POPUPS + 2)
    ]
    bp = _base_blueprint(MAX_TEXT_POPUPS + 2, text_popups=popups)
    result = _validate_blueprint(bp, MAX_TEXT_POPUPS + 2, _durations(MAX_TEXT_POPUPS + 2))
    assert result is not None
    assert len(result["text_popups"]) == MAX_TEXT_POPUPS


def test_long_text_typewriter_falls_back_to_fade():
    """Text > LONG_TEXT_THRESHOLD with typewriter animation falls back to fade."""
    long_text = "x" * (LONG_TEXT_THRESHOLD + 1)
    bp = _base_blueprint(2, text_popups=[
        {"clip_index": 0, "text": long_text, "animation_type": "typewriter"}
    ])
    result = _validate_blueprint(bp, 2, _durations(2))
    assert result is not None
    assert len(result["text_popups"]) == 1
    assert result["text_popups"][0]["animation_type"] == "fade"


def test_text_popup_y_clamped_to_65():
    """Text popup y > 65% is clamped to default 40%."""
    bp = _base_blueprint(2, text_popups=[
        {"clip_index": 0, "text": "Hi", "y": "80%"}
    ])
    result = _validate_blueprint(bp, 2, _durations(2))
    assert result is not None
    # _parse_pct with range 10-65 and "80%" out of range -> returns default "40%"
    assert result["text_popups"][0]["y"] == "40%"


def test_text_popup_y_at_boundary():
    """Text popup y at exactly 65% is accepted."""
    bp = _base_blueprint(2, text_popups=[
        {"clip_index": 0, "text": "Hi", "y": "65%"}
    ])
    result = _validate_blueprint(bp, 2, _durations(2))
    assert result is not None
    assert result["text_popups"][0]["y"] == "65%"


def test_text_popup_text_truncated_at_50():
    """Text longer than 50 chars is truncated."""
    long_text = "A" * 60
    bp = _base_blueprint(1, text_popups=[
        {"clip_index": 0, "text": long_text, "animation_type": "fade"}
    ])
    result = _validate_blueprint(bp, 1, _durations(1))
    assert result is not None
    assert len(result["text_popups"][0]["text"]) == 50


def test_text_popup_missing_text_skipped():
    """Popup with missing/empty text is skipped."""
    bp = _base_blueprint(2, text_popups=[
        {"clip_index": 0, "text": ""},
        {"clip_index": 1},
    ])
    result = _validate_blueprint(bp, 2, _durations(2))
    assert result is not None
    assert len(result["text_popups"]) == 0


def test_text_popup_missing_clip_index_skipped():
    """Popup with missing clip_index is skipped."""
    bp = _base_blueprint(1, text_popups=[
        {"text": "Hello", "animation_type": "fade"},
    ])
    result = _validate_blueprint(bp, 1, _durations(1))
    assert result is not None
    assert len(result["text_popups"]) == 0


def test_blueprint_no_text_popups_key_returns_empty_list():
    """Blueprint without text_popups key returns empty list."""
    bp = _base_blueprint(1)
    result = _validate_blueprint(bp, 1, _durations(1))
    assert result is not None
    assert result["text_popups"] == []


def test_text_popup_invalid_animation_defaults_to_fade():
    """Invalid animation_type in text popup defaults to fade."""
    bp = _base_blueprint(1, text_popups=[
        {"clip_index": 0, "text": "Hey", "animation_type": "spin"}
    ])
    result = _validate_blueprint(bp, 1, _durations(1))
    assert result is not None
    assert result["text_popups"][0]["animation_type"] == "fade"
