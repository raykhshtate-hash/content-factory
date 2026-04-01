"""Tests for SFX mapping and audio element construction in creatomate_service."""

from unittest.mock import patch, MagicMock
import pytest

from app.services.creatomate_service import (
    SFX_MAP,
    SFX_VOLUME,
    SFX_DURATION,
    SFX_FADE_OUT,
    _pick_sfx,
    apply_visual_blueprint,
)


# ── SFX_MAP coverage tests ──────────────────────────────────────

EXPECTED_TRANSITION_TYPES = {
    "wipe", "slide", "fade", "circular-wipe", "color-wipe",
    "film-roll", "squash", "rotate-slide", "shift", "sticker_enter",
}


def test_sfx_map_has_all_transition_types():
    """SFX_MAP contains keys for all transition types plus sticker_enter."""
    for t_type in EXPECTED_TRANSITION_TYPES:
        assert t_type in SFX_MAP, f"Missing SFX_MAP key: {t_type}"


def test_sfx_map_values_are_gcs_uri_lists():
    """All SFX_MAP values are lists of strings starting with gs://."""
    for key, uris in SFX_MAP.items():
        assert isinstance(uris, list), f"SFX_MAP[{key}] should be a list"
        for uri in uris:
            assert uri.startswith("gs://romina-content-factory"), (
                f"SFX_MAP[{key}] contains invalid URI: {uri}"
            )


def test_sfx_map_has_at_least_10_keys():
    """SFX_MAP has at least 10 keys (9 transitions + sticker_enter + hard_cut)."""
    assert len(SFX_MAP) >= 10


# ── _pick_sfx tests ──────────────────────────────────────────────

def test_pick_sfx_returns_valid_uri():
    """_pick_sfx('wipe') returns a string from SFX_MAP['wipe']."""
    result = _pick_sfx("wipe")
    assert result is not None
    assert result in SFX_MAP["wipe"]


def test_pick_sfx_unknown_type_returns_none():
    """_pick_sfx('unknown_type') returns None."""
    assert _pick_sfx("unknown_type") is None


def test_pick_sfx_hard_cut_returns_none():
    """Hard cuts have no SFX (empty list)."""
    assert _pick_sfx("hard_cut") is None


# ── SFX constants tests ─────────────────────────────────────────

def test_sfx_constants():
    """SFX constants have expected values."""
    assert SFX_VOLUME == "20%"
    assert SFX_DURATION == 0.8
    assert SFX_FADE_OUT == 0.2


# ── SFX element construction in apply_visual_blueprint ───────────

@patch("app.services.gcs_service.GCSService")
def test_sfx_element_structure(mock_gcs_cls):
    """SFX audio element has correct structure: type=audio, track=6, volume=20%, etc."""
    mock_gcs = MagicMock()
    mock_gcs.generate_presigned_url.return_value = "https://fake-signed-url.com/sfx.mp3"
    mock_gcs_cls.return_value = mock_gcs

    elements = [
        {"type": "video", "name": "clip-0", "track": 1, "duration": 5.0},
        {"type": "video", "name": "clip-1", "track": 1, "duration": 5.0},
    ]
    blueprint = {
        "clips": [
            {"index": 0, "transition": None},
            {"index": 1, "transition": {"type": "wipe"}},
        ],
        "overlays": [],
    }
    clip_durations = [5.0, 5.0]

    result_elements, _ = apply_visual_blueprint(elements, blueprint, clip_durations)

    sfx_els = [el for el in result_elements if el.get("type") == "audio" and el.get("track") == 6]
    assert len(sfx_els) >= 1, "Expected at least one SFX audio element on track 6"

    sfx = sfx_els[0]
    assert sfx["type"] == "audio"
    assert sfx["track"] == 6
    assert sfx["volume"] == "20%"
    assert sfx["duration"] == 0.8
    assert sfx["audio_fade_out"] == 0.2
    assert sfx["source"] == "https://fake-signed-url.com/sfx.mp3"


@patch("app.services.gcs_service.GCSService")
def test_sfx_time_matches_clip_render_start(mock_gcs_cls):
    """SFX element time matches clip_render_starts[idx] for the transitioned clip."""
    mock_gcs = MagicMock()
    mock_gcs.generate_presigned_url.return_value = "https://fake.com/sfx.mp3"
    mock_gcs_cls.return_value = mock_gcs

    elements = [
        {"type": "video", "name": "clip-0", "track": 1, "duration": 4.0},
        {"type": "video", "name": "clip-1", "track": 1, "duration": 6.0},
        {"type": "video", "name": "clip-2", "track": 1, "duration": 5.0},
    ]
    blueprint = {
        "clips": [
            {"index": 0, "transition": None},
            {"index": 1, "transition": {"type": "slide", "direction": "left"}},
            {"index": 2, "transition": {"type": "fade"}},
        ],
        "overlays": [],
    }
    clip_durations = [4.0, 6.0, 5.0]

    result_elements, _ = apply_visual_blueprint(elements, blueprint, clip_durations)

    sfx_els = [el for el in result_elements if el.get("type") == "audio" and el.get("track") == 6]
    assert len(sfx_els) == 2

    # clip-1 starts at 4.0, clip-2 starts at 10.0
    assert sfx_els[0]["time"] == 4.0
    assert sfx_els[1]["time"] == 10.0


@patch("app.services.gcs_service.GCSService")
def test_no_sfx_when_pick_returns_none(mock_gcs_cls):
    """When _pick_sfx returns None (no mapping), no SFX element is added."""
    mock_gcs = MagicMock()
    mock_gcs_cls.return_value = mock_gcs

    elements = [
        {"type": "video", "name": "clip-0", "track": 1, "duration": 5.0},
        {"type": "video", "name": "clip-1", "track": 1, "duration": 5.0},
    ]
    # Use a non-existent transition type that _pick_sfx won't match
    blueprint = {
        "clips": [
            {"index": 0, "transition": None},
            {"index": 1, "transition": {"type": "nonexistent_transition"}},
        ],
        "overlays": [],
    }
    clip_durations = [5.0, 5.0]

    result_elements, _ = apply_visual_blueprint(elements, blueprint, clip_durations)

    sfx_els = [el for el in result_elements if el.get("type") == "audio" and el.get("track") == 6]
    assert len(sfx_els) == 0


@patch("app.services.gcs_service.GCSService")
def test_sfx_skipped_on_gcs_error(mock_gcs_cls):
    """When GCS presigned URL fails, SFX is skipped without crash."""
    mock_gcs = MagicMock()
    mock_gcs.generate_presigned_url.side_effect = Exception("GCS unavailable")
    mock_gcs_cls.return_value = mock_gcs

    elements = [
        {"type": "video", "name": "clip-0", "track": 1, "duration": 5.0},
        {"type": "video", "name": "clip-1", "track": 1, "duration": 5.0},
    ]
    blueprint = {
        "clips": [
            {"index": 0, "transition": None},
            {"index": 1, "transition": {"type": "wipe"}},
        ],
        "overlays": [],
    }
    clip_durations = [5.0, 5.0]

    # Should not raise
    result_elements, _ = apply_visual_blueprint(elements, blueprint, clip_durations)

    sfx_els = [el for el in result_elements if el.get("type") == "audio" and el.get("track") == 6]
    assert len(sfx_els) == 0, "SFX should be skipped on GCS error"
