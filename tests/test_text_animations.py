"""Tests for text popup element construction and karaoke animation in creatomate_service."""

from unittest.mock import patch, MagicMock
import pytest

from app.services.creatomate_service import (
    TEXT_ANIM_MAP,
    TEXT_POPUP_TRACK,
    apply_visual_blueprint,
)


# ── TEXT_ANIM_MAP structure tests ────────────────────────────────

def test_text_anim_map_has_all_types():
    """TEXT_ANIM_MAP contains fade, slide-up, typewriter, pop."""
    for t in ("fade", "slide-up", "typewriter", "pop"):
        assert t in TEXT_ANIM_MAP, f"Missing TEXT_ANIM_MAP key: {t}"


def test_text_anim_map_exit_has_reversed_true():
    """All exit animations have reversed: True."""
    for name, config in TEXT_ANIM_MAP.items():
        exit_anim = config["exit"]
        assert exit_anim.get("reversed") is True, f"{name} exit missing reversed: True"
        assert exit_anim.get("time") == "end", f"{name} exit missing time: end"


def test_text_anim_map_slide_up_enter():
    """slide-up enter has text-slide with scope split-clip."""
    enter = TEXT_ANIM_MAP["slide-up"]["enter"]
    assert enter["type"] == "text-slide"
    assert enter["scope"] == "split-clip"
    assert enter["split"] == "word"
    assert enter["direction"] == "up"


def test_text_anim_map_typewriter_enter():
    """typewriter enter has text-typewriter with split character."""
    enter = TEXT_ANIM_MAP["typewriter"]["enter"]
    assert enter["type"] == "text-typewriter"
    assert enter["scope"] == "split-clip"
    assert enter["split"] == "character"


def test_text_anim_map_pop_enter():
    """pop enter has text-appear with split word."""
    enter = TEXT_ANIM_MAP["pop"]["enter"]
    assert enter["type"] == "text-appear"
    assert enter["scope"] == "split-clip"
    assert enter["split"] == "word"


def test_text_popup_track_is_7():
    """TEXT_POPUP_TRACK is 7."""
    assert TEXT_POPUP_TRACK == 7


# ── Text popup element construction tests ────────────────────────

@patch("app.services.gcs_service.GCSService")
def test_text_popup_element_structure(mock_gcs_cls):
    """Text popup element has correct Creatomate structure."""
    mock_gcs = MagicMock()
    mock_gcs.generate_presigned_url.return_value = "https://fake.com/sfx.mp3"
    mock_gcs_cls.return_value = mock_gcs

    elements = [
        {"type": "video", "name": "clip-0", "track": 1, "duration": 5.0},
        {"type": "video", "name": "clip-1", "track": 1, "duration": 5.0},
    ]
    blueprint = {
        "font_family": "Montserrat",
        "clips": [
            {"index": 0, "transition": None, "animation_type": "fade"},
            {"index": 1, "transition": None, "animation_type": "pop"},
        ],
        "overlays": [],
        "text_popups": [
            {"clip_index": 1, "text": "Fun fact!", "animation_type": "pop", "x": "50%", "y": "35%"},
        ],
    }
    clip_durations = [5.0, 5.0]

    result_elements, _ = apply_visual_blueprint(elements, blueprint, clip_durations)

    popup_els = [el for el in result_elements if el.get("track") == 7 and el.get("type") == "text"]
    assert len(popup_els) == 1

    popup = popup_els[0]
    assert popup["type"] == "text"
    assert popup["track"] == 7
    assert popup["text"] == "Fun fact!"
    assert popup["x"] == "50%"
    assert popup["y"] == "35%"
    assert popup["font_family"] == "Montserrat"
    assert popup["fill_color"] == "#FFFFFF"
    assert popup["stroke_color"] == "#000000"
    assert popup["background_color"] == "rgba(0,0,0,0.4)"

    # Animations: enter + exit
    anims = popup["animations"]
    assert len(anims) == 2
    assert anims[0]["type"] == "text-appear"  # pop enter
    assert anims[1]["reversed"] is True  # exit has reversed


@patch("app.services.gcs_service.GCSService")
def test_text_popup_time_matches_clip_start(mock_gcs_cls):
    """Text popup time is clip_render_starts[clip_index] + 0.3."""
    mock_gcs = MagicMock()
    mock_gcs.generate_presigned_url.return_value = "https://fake.com/sfx.mp3"
    mock_gcs_cls.return_value = mock_gcs

    elements = [
        {"type": "video", "name": "clip-0", "track": 1, "duration": 4.0},
        {"type": "video", "name": "clip-1", "track": 1, "duration": 6.0},
    ]
    blueprint = {
        "clips": [
            {"index": 0, "transition": None},
            {"index": 1, "transition": None},
        ],
        "overlays": [],
        "text_popups": [
            {"clip_index": 1, "text": "Hello", "animation_type": "fade"},
        ],
    }
    clip_durations = [4.0, 6.0]

    result_elements, _ = apply_visual_blueprint(elements, blueprint, clip_durations)

    popup_els = [el for el in result_elements if el.get("track") == 7]
    assert len(popup_els) == 1
    # clip-1 starts at 4.0, popup at 4.3
    assert popup_els[0]["time"] == 4.3


@patch("app.services.gcs_service.GCSService")
def test_empty_text_popups_no_track_7(mock_gcs_cls):
    """Empty text_popups[] produces no track 7 elements."""
    mock_gcs = MagicMock()
    mock_gcs_cls.return_value = mock_gcs

    elements = [
        {"type": "video", "name": "clip-0", "track": 1, "duration": 5.0},
    ]
    blueprint = {
        "clips": [{"index": 0, "transition": None}],
        "overlays": [],
        "text_popups": [],
    }
    clip_durations = [5.0]

    result_elements, _ = apply_visual_blueprint(elements, blueprint, clip_durations)

    popup_els = [el for el in result_elements if el.get("track") == 7]
    assert len(popup_els) == 0


@patch("app.services.gcs_service.GCSService")
def test_text_popup_invalid_clip_index_skipped(mock_gcs_cls):
    """Text popup with clip_index >= num clips is skipped."""
    mock_gcs = MagicMock()
    mock_gcs_cls.return_value = mock_gcs

    elements = [
        {"type": "video", "name": "clip-0", "track": 1, "duration": 5.0},
    ]
    blueprint = {
        "clips": [{"index": 0, "transition": None}],
        "overlays": [],
        "text_popups": [
            {"clip_index": 99, "text": "Nope", "animation_type": "fade"},
        ],
    }
    clip_durations = [5.0]

    result_elements, _ = apply_visual_blueprint(elements, blueprint, clip_durations)

    popup_els = [el for el in result_elements if el.get("track") == 7]
    assert len(popup_els) == 0


# ── Karaoke animation tests ─────────────────────────────────────

@patch("app.services.gcs_service.GCSService")
def test_transcript_source_karaoke_gets_fade(mock_gcs_cls):
    """transcript_source karaoke text elements get simple fade animations."""
    mock_gcs = MagicMock()
    mock_gcs_cls.return_value = mock_gcs

    elements = [
        {"type": "video", "name": "clip-0", "track": 1, "duration": 10.0},
        {"type": "text", "track": 2, "time": 0, "transcript_source": "clip-0"},
    ]
    blueprint = {
        "clips": [{"index": 0, "transition": None, "animation_type": "pop"}],
        "overlays": [],
    }
    clip_durations = [10.0]

    result_elements, _ = apply_visual_blueprint(elements, blueprint, clip_durations)

    karaoke_el = [el for el in result_elements if el.get("transcript_source") == "clip-0"][0]
    assert "animations" in karaoke_el
    assert len(karaoke_el["animations"]) == 2
    assert karaoke_el["animations"][0]["type"] == "fade"
    assert karaoke_el["animations"][1]["reversed"] is True


@patch("app.services.gcs_service.GCSService")
def test_whisper_popup_gets_blueprint_animation(mock_gcs_cls):
    """Whisper popup subtitle (no transcript_source) gets blueprint animation_type."""
    mock_gcs = MagicMock()
    mock_gcs_cls.return_value = mock_gcs

    elements = [
        {"type": "video", "name": "clip-0", "track": 1, "duration": 10.0},
        {"type": "text", "track": 2, "time": 1.0, "text": "Hello world"},
    ]
    blueprint = {
        "clips": [{"index": 0, "transition": None, "animation_type": "slide-up"}],
        "overlays": [],
    }
    clip_durations = [10.0]

    result_elements, _ = apply_visual_blueprint(elements, blueprint, clip_durations)

    whisper_el = [el for el in result_elements if el.get("text") == "Hello world"][0]
    assert "animations" in whisper_el
    assert len(whisper_el["animations"]) == 2
    # slide-up maps to text-slide
    assert whisper_el["animations"][0]["type"] == "text-slide"
    assert whisper_el["animations"][1]["reversed"] is True
