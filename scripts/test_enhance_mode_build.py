"""Smoke test for enhance_mode wiring.

Verifies:
  1. `apply_visual_blueprint(skip_sfx=True)` produces zero track-6 (SFX) elements
     even when the blueprint contains transitions and track-4 stickers that
     would normally trigger SFX.
  2. `_validate_blueprint(enhance_mode=True)` nulls all clip transitions and
     sets overall_style='clean'.

Run:  python3.12 scripts/test_enhance_mode_build.py
"""
from __future__ import annotations

import os
import sys
import types

# Make the repo root importable when running from scripts/
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

# ── Stub heavy deps so we can import without the full runtime env ──
for _name in ("anthropic", "dotenv", "httpx", "tenacity"):
    if _name not in sys.modules:
        sys.modules[_name] = types.ModuleType(_name)


class _Fake:
    def __init__(self, *a, **kw):
        pass

    def __getattr__(self, _):
        return _Fake()


sys.modules["anthropic"].AsyncAnthropic = _Fake  # type: ignore[attr-defined]


def _load_dotenv(*a, **kw):
    return None


sys.modules["dotenv"].load_dotenv = _load_dotenv  # type: ignore[attr-defined]


# tenacity: decorators must pass through the wrapped function unchanged
def _passthrough_decorator(*dargs, **dkwargs):
    # Support @retry without parens AND @retry(...) with config
    if len(dargs) == 1 and callable(dargs[0]) and not dkwargs:
        return dargs[0]

    def _wrap(fn):
        return fn

    return _wrap


for _attr in ("retry", "retry_if_exception_type", "retry_if_exception",
              "wait_exponential", "stop_after_attempt"):
    setattr(sys.modules["tenacity"], _attr, _passthrough_decorator)

# ── Now safe to import project modules ──
from app.services.creatomate_service import apply_visual_blueprint  # noqa: E402
from app.services.visual_director import _validate_blueprint  # noqa: E402


def test_skip_sfx_produces_no_track6() -> None:
    """With skip_sfx=True, no SFX elements land on track 6."""
    elements: list[dict] = [
        # Two video clips on track 1
        {"type": "video", "track": 1, "source": "https://example/a.mp4", "duration": 3.0, "trim_duration": 3.0},
        {"type": "video", "track": 1, "source": "https://example/b.mp4", "duration": 3.0, "trim_duration": 3.0},
        # A pre-existing sticker on track 4 (normally triggers sticker_enter SFX)
        {"type": "image", "track": 4, "source": "https://example/sticker.png"},
    ]
    # Blueprint with a slide transition on clip 1 (normally adds a swoosh SFX)
    blueprint = {
        "overall_style": "dynamic",
        "font_family": "Montserrat",
        "subtitle_color": "#FFFFFF",
        "clips": [
            {"index": 0, "transition": None, "animation_type": "fade"},
            {"index": 1, "transition": {"type": "slide", "direction": "left"}, "animation_type": "fade"},
        ],
        "overlays": [],
        "text_popups": [],
    }

    modified, _tcount = apply_visual_blueprint(
        elements,
        blueprint,
        clip_durations=[3.0, 3.0],
        skip_sfx=True,
    )
    track6 = [el for el in modified if el.get("track") == 6]
    assert track6 == [], f"expected zero track-6 SFX, got {len(track6)}: {track6}"
    print("OK: skip_sfx=True produces zero track-6 elements")


def test_enhance_mode_nulls_transitions() -> None:
    """_validate_blueprint(enhance_mode=True) nulls transitions + forces clean."""
    bp = {
        "overall_style": "dynamic",
        "font_family": "Montserrat",
        "subtitle_color": "#FFFFFF",
        "clips": [
            {"index": 0, "transition": None, "animation_type": "fade"},
            {"index": 1, "transition": {"type": "fade"}, "animation_type": "fade"},
            {"index": 2, "transition": {"type": "slide", "direction": "left"}, "animation_type": "fade"},
        ],
        "overlays": [],
        "text_popups": [],
    }
    out = _validate_blueprint(
        bp, num_clips=3, clip_durations=[3.0, 3.0, 3.0], enhance_mode=True,
    )
    assert out is not None, "validator returned None"
    assert out["overall_style"] == "clean", f"expected clean, got {out['overall_style']}"
    for c in out["clips"]:
        assert c["transition"] is None, f"transition not nulled: {c}"
    print("OK: enhance_mode=True → overall_style=clean + all transitions null")


def test_enhance_mode_false_preserves_transitions() -> None:
    """_validate_blueprint(enhance_mode=False) preserves valid transitions."""
    bp = {
        "overall_style": "dynamic",
        "font_family": "Montserrat",
        "subtitle_color": "#FFFFFF",
        "clips": [
            {"index": 0, "transition": None, "animation_type": "fade"},
            {"index": 1, "transition": {"type": "fade"}, "animation_type": "fade"},
        ],
        "overlays": [],
        "text_popups": [],
    }
    out = _validate_blueprint(
        bp, num_clips=2, clip_durations=[3.0, 3.0], enhance_mode=False,
    )
    assert out is not None
    assert out["clips"][1]["transition"] == {"type": "fade"}, (
        f"expected fade, got {out['clips'][1]['transition']}"
    )
    print("OK: enhance_mode=False preserves transitions")


if __name__ == "__main__":
    test_skip_sfx_produces_no_track6()
    test_enhance_mode_nulls_transitions()
    test_enhance_mode_false_preserves_transitions()
    print("\nAll enhance_mode smoke tests passed.")
