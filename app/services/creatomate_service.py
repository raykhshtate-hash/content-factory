"""
Creatomate Render Service — Dynamic JSON Source.

Builds video renders entirely via JSON source (no predefined templates).
Supports dynamic dimensions and native karaoke subtitles via
Creatomate's transcript_effect (no external Whisper needed).

Quality modes:
  - "dev"  → 720×1280 @24fps  (saves Creatomate credits during testing)
  - "prod" → 1080×1920 @60fps (full quality for publication)
"""

import json
import logging
import random
from dataclasses import dataclass
import httpx
from tenacity import retry, retry_if_exception_type, wait_exponential, stop_after_attempt

from app.config import settings

logger = logging.getLogger(__name__)

API_URL = "https://api.creatomate.com/v1/renders"

# Creatomate pricing: fixed cost per render based on plan
CREATOMATE_COST_PER_RENDER = 0.00  # TODO: set actual cost when plan is known


@dataclass
class Clip:
    source: str       # signed video URL
    trim_start: float # seconds
    trim_duration: float
    clip_type: str = "speech"     # "speech" or "broll"
    video_index: int = 1          # 1-based, for whisper_words lookup
    matched_voiceover_segment: int | None = None  # 0-based index into voiceover_segments


# ── Quality presets ─────────────────────────────────────────────
QUALITY_PRESETS = {
    "dev": {
        "vertical":   (720, 1280),
        "horizontal": (1280, 720),
        "square":     (720, 720),
        "fps": 24,
    },
    "prod": {
        "vertical":   (1080, 1920),
        "horizontal": (1920, 1080),
        "square":     (1080, 1080),
        "fps": 60,
    },
}

# ── Karaoke subtitle styles by mood ─────────────────────────────
KARAOKE_STYLES: dict[str, dict] = {
    "energetic": {
        "fill_color": "#FFFFFF",
        "transcript_color": "#FF3232",
        "font_weight": "700",
        "stroke_color": "#000000",
        "stroke_width": "2 vmin",
    },
    "calm": {
        "fill_color": "rgba(255,255,255,0.7)",
        "transcript_color": "#FFFFFF",
        "font_weight": "400",
        "shadow_color": "rgba(0,0,0,0.4)",
        "shadow_blur": "3 vmin",
    },
    "humor": {
        "fill_color": "#FFFFFF",
        "transcript_color": "#FFE600",
        "font_weight": "600",
        "stroke_color": "#000000",
        "stroke_width": "2.5 vmin",
    },
    "professional": {
        "fill_color": "rgba(255,255,255,0.8)",
        "transcript_color": "#FFFFFF",
        "font_weight": "600",
        "shadow_color": "rgba(0,0,0,0.5)",
        "shadow_blur": "2 vmin",
    },
    "upbeat": {
        "fill_color": "#FFFFFF",
        "transcript_color": "#FF6B9D",
        "font_weight": "600",
        "stroke_color": "#000000",
        "stroke_width": "2 vmin",
    },
    "dramatic": {
        "fill_color": "#FFFFFF",
        "transcript_color": "#FF4444",
        "font_weight": "600",
        "stroke_color": "#000000",
        "stroke_width": "2 vmin",
    },
    "funny": {
        "fill_color": "#FFFFFF",
        "transcript_color": "#FFE600",
        "font_weight": "600",
        "stroke_color": "#000000",
        "stroke_width": "2.5 vmin",
    },
    "chill": {
        "fill_color": "rgba(255,255,255,0.7)",
        "transcript_color": "#FFFFFF",
        "font_weight": "400",
        "shadow_color": "rgba(0,0,0,0.3)",
        "shadow_blur": "3 vmin",
    },
}

# ── Transition presets (legacy, used by Gemini storyboard fallback) ──
TRANSITION_MAP = {
    "cut": None,
    "crossfade": {"type": "crossfade"},
    "slide-left": {"type": "slide", "direction": "left"},
    "slide-right": {"type": "slide", "direction": "right"},
    "wipe": {"type": "wipe"},
    "circular-wipe": {"type": "circular-wipe"},
}

# ── Sticker animation presets (image-safe, no scale/spin) ────────
STICKER_ANIM_PRESETS = {
    "fade": {"type": "fade", "duration": 0.3},
    "wipe": {"type": "wipe", "duration": 0.4},
    "slide": {"type": "slide", "duration": 0.4, "direction": "up", "fade": True},
    "flip": {"type": "flip", "duration": 0.5, "x_rotation": "-180°", "y_rotation": "0°"},
    "bounce": {"type": "bounce", "duration": 0.5, "axis": "y"},
    "circular-wipe": {"type": "circular-wipe", "duration": 0.5},
    "film-roll": {"type": "film-roll", "duration": 0.4, "direction": "up"},
    "shift": {"type": "shift", "duration": 0.4, "direction": "up"},
    "squash": {"type": "squash", "duration": 0.4, "direction": "down", "fade": True},
    "rotate-slide": {"type": "rotate-slide", "duration": 0.5, "direction": "right", "clockwise": True},
}


def _build_sticker_anim(anim_type: str, is_exit: bool) -> dict:
    """Build a sticker enter/exit animation dict from a type name."""
    base = STICKER_ANIM_PRESETS.get(anim_type, STICKER_ANIM_PRESETS["fade"])
    anim = {**base, "time": "end" if is_exit else 0}
    if is_exit:
        anim["reversed"] = True
    return anim


# ── SFX mapping (transition type -> GCS URIs) ──────────────────
SFX_MAP: dict[str, list[str]] = {
    "wipe": ["gs://romina-content-factory-489121/sfx/whoosh_1.mp3", "gs://romina-content-factory-489121/sfx/whoosh_2.mp3", "gs://romina-content-factory-489121/sfx/whoosh_3.mp3"],
    "circular-wipe": ["gs://romina-content-factory-489121/sfx/whoosh_1.mp3", "gs://romina-content-factory-489121/sfx/whoosh_2.mp3", "gs://romina-content-factory-489121/sfx/whoosh_3.mp3"],
    "color-wipe": ["gs://romina-content-factory-489121/sfx/whoosh_1.mp3", "gs://romina-content-factory-489121/sfx/whoosh_2.mp3", "gs://romina-content-factory-489121/sfx/whoosh_3.mp3"],
    "film-roll": ["gs://romina-content-factory-489121/sfx/whoosh_1.mp3", "gs://romina-content-factory-489121/sfx/whoosh_2.mp3", "gs://romina-content-factory-489121/sfx/whoosh_3.mp3"],
    "slide": ["gs://romina-content-factory-489121/sfx/swoosh_1.mp3", "gs://romina-content-factory-489121/sfx/swoosh_2.mp3", "gs://romina-content-factory-489121/sfx/swoosh_3.mp3"],
    "rotate-slide": ["gs://romina-content-factory-489121/sfx/swoosh_1.mp3", "gs://romina-content-factory-489121/sfx/swoosh_2.mp3", "gs://romina-content-factory-489121/sfx/swoosh_3.mp3"],
    "shift": ["gs://romina-content-factory-489121/sfx/swoosh_1.mp3", "gs://romina-content-factory-489121/sfx/swoosh_2.mp3", "gs://romina-content-factory-489121/sfx/swoosh_3.mp3"],
    "squash": ["gs://romina-content-factory-489121/sfx/pop_1.mp3", "gs://romina-content-factory-489121/sfx/pop_2.mp3", "gs://romina-content-factory-489121/sfx/pop_3.mp3"],
    "sticker_enter": ["gs://romina-content-factory-489121/sfx/pop_1.mp3", "gs://romina-content-factory-489121/sfx/pop_2.mp3", "gs://romina-content-factory-489121/sfx/pop_3.mp3"],
    "fade": ["gs://romina-content-factory-489121/sfx/whoosh_1.mp3", "gs://romina-content-factory-489121/sfx/whoosh_2.mp3", "gs://romina-content-factory-489121/sfx/whoosh_3.mp3"],
    "hard_cut": [],
}
SFX_VOLUME = "20%"
SFX_DURATION = 0.8
SFX_FADE_OUT = 0.2


def _pick_sfx(event_type: str) -> str | None:
    """Pick random SFX GCS URI for a transition/event type. Returns None if no mapping."""
    pool = SFX_MAP.get(event_type, [])
    if not pool:
        return None
    return random.choice(pool)


def _group_whisper_phrases(words: list[dict], max_chars: int = 15) -> list[dict]:
    """Group Whisper words into display phrases of ≤max_chars."""
    phrases: list[dict] = []
    current_words: list[dict] = []
    current_len = 0
    for w in words:
        text = w["word"].strip()
        new_len = current_len + len(text) + (1 if current_words else 0)
        if new_len > max_chars and current_words:
            phrases.append({
                "text": " ".join(cw["word"].strip() for cw in current_words),
                "start": current_words[0]["start"],
                "end": current_words[-1]["end"],
            })
            current_words = [w]
            current_len = len(text)
        else:
            current_words.append(w)
            current_len = new_len
    if current_words:
        phrases.append({
            "text": " ".join(cw["word"].strip() for cw in current_words),
            "start": current_words[0]["start"],
            "end": current_words[-1]["end"],
        })
    return phrases


def resolve_overlay_render_times(
    anchored_overlays: list[dict],
    clips: list[Clip],
    clip_render_starts: list[float],
    total_render_duration: float,
) -> list[dict]:
    """Convert anchor-based overlays (audio_time) to render-time sticker elements.

    Two-pass algorithm: compute all render times first, sort, then place with
    pre-cap to guarantee 0.5s gaps. Track 4 is independent — no clip boundary clamping.
    """
    # --- Pass 1: compute render_time for all valid overlays ---
    candidates: list[tuple[float, float, dict]] = []  # (render_time, requested_duration, overlay)

    for ov in anchored_overlays:
        clip_index = ov.get("clip_index")
        audio_time = ov.get("audio_time")
        prompt = ov.get("image_prompt", "")
        duration_seconds = ov.get("duration_seconds", 6)

        if not prompt or clip_index is None or audio_time is None:
            continue
        if clip_index < 0 or clip_index >= len(clips):
            logger.warning("resolve_overlay: clip_index %d out of range, skipping", clip_index)
            continue

        clip = clips[clip_index]
        adjusted_trim_start = max(0, clip.trim_start - 0.5)
        trim_end = clip.trim_start + clip.trim_duration

        if audio_time > trim_end:
            logger.debug("resolve_overlay: audio_time %.2f > trim_end %.2f, skipping", audio_time, trim_end)
            continue

        # Map audio_time → render_time
        if audio_time < clip.trim_start:
            render_time = clip_render_starts[clip_index]
        else:
            render_time = clip_render_starts[clip_index] + (audio_time - adjusted_trim_start)

        # Anticipate: start sticker before trigger phrase so it's fully
        # visible when the phrase is spoken. 1.0s = max enter animation
        # duration (0.5s) + 0.5s notice time for the viewer.
        render_time = max(0.3, render_time - 1.0)

        candidates.append((render_time, float(duration_seconds), ov))

    # Sort by render_time
    candidates.sort(key=lambda c: c[0])

    # --- Pass 2: place with pre-cap ---
    sticker_elements: list[dict] = []
    sticker_idx = 0

    for i, (render_time, requested_duration, ov) in enumerate(candidates):
        # Safe start margin
        if render_time < 0.3:
            logger.debug("resolve_overlay: render_time %.2f < 0.3s, skipping", render_time)
            continue

        # Smart Duration Clamp: span-aware when span_end available
        span_end = ov.get("span_end")
        if span_end is not None:
            span_duration = span_end - ov.get("audio_time", span_end)
            duration = min(requested_duration, max(span_duration + 0.3, 2.0), 5.0)
        else:
            duration = min(requested_duration, 5.0)

        # Safe end margin
        duration = min(duration, total_render_duration - render_time - 0.5)

        # Pre-cap: ensure 0.5s gap before next sticker
        if i + 1 < len(candidates):
            next_render_time = candidates[i + 1][0]
            duration = min(duration, next_render_time - render_time - 0.5)

        if duration < 1.0:
            logger.debug("resolve_overlay: duration %.2f < 1.5s after caps, skipping", duration)
            continue

        default_x = "75%" if sticker_idx % 2 == 0 else "25%"
        sticker_el = {
            "type": "image",
            "track": 4,
            "time": round(render_time, 3),
            "duration": round(duration, 3),
            "x": ov.get("x", default_x),
            "y": ov.get("y", "60%"),
            "width": ov.get("width", "25%"),
            "height": ov.get("height", "25%"),
            "source": ov.get("image_prompt", ""),
            "provider": "openai model=gpt-image-1.5 background=transparent",
            "dynamic": True,
            "border_radius": 50,
            "opacity": "85%",
            "animations": [
                _build_sticker_anim(ov.get("sticker_enter_animation", "fade"), is_exit=False),
                _build_sticker_anim(ov.get("sticker_exit_animation", "fade"), is_exit=True),
            ],
        }
        sticker_elements.append(sticker_el)
        sticker_idx += 1
        logger.info(
            "resolve_overlay: anchor=%s render_time=%.2f duration=%.2f clip=%d",
            ov.get("anchor_id", "?"), render_time, duration, ov.get("clip_index"),
        )

    return sticker_elements


def apply_visual_blueprint(
    elements: list[dict],
    blueprint: dict,
    clip_durations: list[float],
    anchored_overlays: list[dict] | None = None,
    clips: list[Clip] | None = None,
) -> tuple[list[dict], int]:
    """Apply visual blueprint (transitions + sticker overlays) to elements.

    Returns (modified_elements, applied_transition_count).
    """
    video_elements = [el for el in elements if el.get("type") == "video"]
    transition_count = 0
    sticker_idx = 0

    # ── Compute render start time for each clip ──
    clip_render_starts = []
    t = 0.0
    for vel in video_elements:
        clip_render_starts.append(t)
        t += vel.get("duration", vel.get("trim_duration", 0))
    total_render_duration = t

    for clip_info in blueprint.get("clips", []):
        idx = clip_info.get("index", 0)

        # ── Transitions ──
        transition = clip_info.get("transition")
        if transition is not None:
            if 0 < idx < len(video_elements):
                dur = clip_durations[idx] if idx < len(clip_durations) else 0
                if dur >= 2.5:
                    video_el = video_elements[idx]
                    if "animations" not in video_el:
                        video_el["animations"] = []

                    anim = {
                        "type": transition["type"],
                        "time": 0,
                        # Duration 0.5s is fixed starting value.
                        # Replace with clip_duration-based formula after
                        # manual UI testing confirms optimal range.
                        "duration": 0.5,
                        "transition": True,
                    }

                    for key in ("direction", "color", "clockwise"):
                        if key in transition:
                            anim[key] = transition[key]

                    video_el["animations"].append(anim)
                    transition_count += 1

    # ── Sticker overlays ──
    if anchored_overlays is not None and clips is not None:
        # Anchor-based: resolve audio_time → render_time using karaoke formula
        sticker_els = resolve_overlay_render_times(
            anchored_overlays, clips, clip_render_starts, total_render_duration,
        )
        elements.extend(sticker_els)
    else:
        # Legacy timeline-based stickers (backward compat)
        for overlay in blueprint.get("overlays", []):
            if overlay.get("type") != "ai_image":
                continue
            prompt = overlay.get("image_prompt", "")
            if not prompt:
                continue
            start = overlay.get("start_second")
            end = overlay.get("end_second")
            if start is None or end is None:
                continue
            sticker_duration = float(end - start)
            if sticker_duration < 4.0:
                continue
            sticker_time = float(start)

            # Guard: don't exceed total duration
            if sticker_time + sticker_duration > total_render_duration:
                continue

            # Position from Visual Director blueprint, with safe defaults
            default_x = "75%" if sticker_idx % 2 == 0 else "25%"

            # Sticker track: 4 for both modes
            sticker_el = {
                "type": "image",
                "track": 4,
                "time": sticker_time,
                "duration": sticker_duration,
                "x": overlay.get("x", default_x),
                "y": overlay.get("y", "60%"),
                "width": overlay.get("width", "25%"),
                "height": overlay.get("height", "25%"),
                "source": prompt,
                "provider": "openai model=gpt-image-1.5 background=transparent",
                "dynamic": True,
                "border_radius": 50,
                "opacity": "85%",
                "animations": [
                    _build_sticker_anim(overlay.get("sticker_enter_animation", "fade"), is_exit=False),
                    _build_sticker_anim(overlay.get("sticker_exit_animation", "fade"), is_exit=True),
                ],
            }
            elements.append(sticker_el)
            sticker_idx += 1

    # ── SFX audio elements (track 6) ──
    sfx_elements: list[dict] = []
    for clip_info in blueprint.get("clips", []):
        idx = clip_info.get("index", 0)
        transition = clip_info.get("transition")
        if transition is not None and 0 < idx < len(clip_render_starts):
            sfx_uri = _pick_sfx(transition["type"])
            if sfx_uri:
                try:
                    from app.services.gcs_service import GCSService
                    gcs = GCSService()
                    sfx_url = gcs.generate_presigned_url(sfx_uri)
                    sfx_elements.append({
                        "type": "audio",
                        "track": 6,
                        "time": clip_render_starts[idx],
                        "source": sfx_url,
                        "volume": SFX_VOLUME,
                        "duration": SFX_DURATION,
                        "audio_fade_out": SFX_FADE_OUT,
                    })
                except Exception as e:
                    logger.warning("SFX skipped for %s: %s", transition["type"], e)

    # ── SFX for sticker entrances ──
    sticker_track_els = [el for el in elements if el.get("track") == 4 and el.get("type") == "image"]
    for sticker_el in sticker_track_els:
        sfx_uri = _pick_sfx("sticker_enter")
        if sfx_uri:
            try:
                from app.services.gcs_service import GCSService
                gcs = GCSService()
                sfx_url = gcs.generate_presigned_url(sfx_uri)
                sfx_elements.append({
                    "type": "audio",
                    "track": 6,
                    "time": sticker_el.get("time", 0),
                    "source": sfx_url,
                    "volume": SFX_VOLUME,
                    "duration": SFX_DURATION,
                    "audio_fade_out": SFX_FADE_OUT,
                })
            except Exception as e:
                logger.warning("SFX skipped for sticker_enter: %s", e)

    elements.extend(sfx_elements)

    return elements, transition_count


class CreatomateService:
    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or settings.CREATOMATE_API_KEY
        self._last_cost_usd: float = 0.0
        if not self.api_key:
            raise ValueError("CREATOMATE_API_KEY is not set")

    # TODO: old broll pipeline, replaced by visual_director
    # Keep code for potential Pexels/GIF future use
    # def _build_broll_elements(self, broll_overlays: list[dict], track: int, mood: str = "chill") -> list[dict]:
    #     """Build Creatomate AI-generated sticker elements for B-roll overlays."""
    #     elements = []
    #     for idx, ov in enumerate(broll_overlays):
    #         prompt = ov.get("broll_keyword")
    #         if not prompt:
    #             continue
    #         start_sec = float(ov.get("start_sec", 0))
    #         donut_duration = float(ov.get("donut_duration", 4.0))
    #         fade_duration = min(0.5, donut_duration * 0.15)
    #         x = "25%" if idx % 2 == 0 else "75%"
    #         el = {
    #             "type": "image",
    #             "track": track,
    #             "time": start_sec,
    #             "duration": donut_duration,
    #             "x": x,
    #             "y": "18%",
    #             "width": "25%",
    #             "height": "25%",
    #             "source": prompt,
    #             "provider": "openai model=gpt-image-1.5 background=transparent",
    #             "dynamic": True,
    #             "fit": "cover",
    #             "border_radius": 50,
    #             "opacity": "85%",
    #             "animations": [
    #                 {"time": "start", "duration": fade_duration, "type": "fade"},
    #                 {"time": "end", "duration": fade_duration, "type": "fade"}
    #             ],
    #         }
    #         elements.append(el)
    #     return elements

    def build_source(
        self,
        clips: list[Clip],
        video_format: str = "reels",
        music_mood: str | None = None,
        karaoke: bool = True,
        quality: str = "prod",
        voiceover_url: str | None = None,
        voiceover_duration: float = 0.0,
        whisper_words: dict[str, list[dict]] | None = None,
        voiceover_words: list[dict] | None = None,
        transition_durations: list[float] | None = None,
        hybrid_voiceover_url: str | None = None,
        voiceover_segments: list[dict] | None = None,
        per_clip_voiceover_url: str | None = None,
        font_family: str = "Montserrat",
        subtitle_color: str | None = None,
    ) -> dict:
        """
        Build Creatomate source JSON (elements + metadata).

        Returns the source dict ready for submit_render() or further
        modification (e.g. apply_visual_blueprint on source["elements"]).
        """
        preset = QUALITY_PRESETS.get(quality, QUALITY_PRESETS["prod"])

        # ── Dimensions based on format + quality ────────────────
        fmt = video_format.lower()
        if any(kw in fmt for kw in ("reel", "short", "tiktok", "vertical", "9:16")):
            width, height = preset["vertical"]
        elif any(kw in fmt for kw in ("youtube", "horizontal", "16:9")):
            width, height = preset["horizontal"]
        else:
            width, height = preset["square"]

        fps = preset["fps"]

        # ── Build flat timeline — one named video + one karaoke text per clip ──
        elements = []
        karaoke_elements = []
        current_time = 0.0

        mood_key = (music_mood or "professional").lower() if karaoke else None
        style = KARAOKE_STYLES.get(mood_key, KARAOKE_STYLES["professional"]) if karaoke else {}

        # Decide: Whisper popup subtitles vs transcript_source fallback
        # Hybrid mode (hybrid_voiceover_url) also uses Whisper popup for speech clips
        use_whisper_karaoke = (
            whisper_words is not None
            and not voiceover_url
            and any(
                str(clip.video_index) in whisper_words
                for clip in clips if clip.clip_type != "broll"
            )
        )

        # Pre-compute cumulative transition offsets for subtitle sync
        cumulative_offsets: list[float] = []
        running = 0.0
        if transition_durations:
            for td in transition_durations:
                running += td
                cumulative_offsets.append(running)

        # Detect talking_head with transitions: same source, has transitions, no voiceover.
        # Audio must be decoupled to a separate track to prevent doubling during overlap.
        is_same_source_transitions = (
            not voiceover_url
            and not voiceover_segments
            and transition_durations is not None
            and any(td > 0 for td in (transition_durations or []))
            and len(set(c.source for c in clips)) == 1
        )
        adjusted_durations_list: list[float] = []   # for audio element construction
        adjusted_trim_starts_list: list[float] = []

        for i, clip in enumerate(clips):
            clip_name = f"clip-{i}"

            # Pre-buffer: start clip slightly earlier to avoid abrupt word cuts.
            # For same-source clips (talking_head), limit to the gap between clips
            # so we never replay source audio the previous clip already covered.
            if i > 0 and clips[i - 1].source == clip.source:
                prev_end = clips[i - 1].trim_start + clips[i - 1].trim_duration
                gap = max(0, clip.trim_start - prev_end)
                prebuffer = min(0.5, gap)
            else:
                prebuffer = 0.5
            adjusted_trim_start = max(0, clip.trim_start - prebuffer)
            adjusted_duration = clip.trim_duration + (clip.trim_start - adjusted_trim_start)

            video_el = {
                "type": "video",
                "name": clip_name,
                "track": 1,
                "source": clip.source,
                "width": "100%",
                "height": "100%",
                "fit": "cover",
                "trim_start": adjusted_trim_start,
                "trim_duration": adjusted_duration,
                "duration": adjusted_duration,
            }

            if is_same_source_transitions:
                video_el["volume"] = "0%"  # Audio decoupled to track 2
            elif voiceover_url:
                video_el["volume"] = "0%"
            elif voiceover_segments and clip.matched_voiceover_segment is not None:
                video_el["volume"] = "0%"  # Per-clip: voiceover replaces broll ambient
            elif hybrid_voiceover_url and clip.clip_type == "broll":
                video_el["volume"] = "0%"  # Legacy hybrid: voiceover replaces broll ambient
            elif clip.clip_type == "broll":
                video_el["volume"] = "70%"

            elements.append(video_el)
            adjusted_durations_list.append(adjusted_duration)
            adjusted_trim_starts_list.append(adjusted_trim_start)

            if karaoke and not voiceover_url and clip.clip_type != "broll":
                if use_whisper_karaoke:
                    # ── Whisper popup subtitles ──
                    raw_words = whisper_words.get(str(clip.video_index), [])
                    trim_end = clip.trim_start + clip.trim_duration
                    clip_words = [
                        w for w in raw_words
                        if w["start"] >= clip.trim_start and w["start"] < trim_end
                    ]
                    clip_words = [
                        {**w, "end": min(w["end"], trim_end)} for w in clip_words
                    ]

                    logger.info(
                        "[Speech karaoke] clip=%d vid=%s trim=[%.2f-%.2f] raw_words=%d clip_words=%d first_word=%.2f",
                        i, clip.video_index, clip.trim_start, trim_end,
                        len(raw_words), len(clip_words),
                        clip_words[0]["start"] if clip_words else -1,
                    )

                    if clip_words:
                        phrases = _group_whisper_phrases(clip_words)
                        # Cumulative transition offset: all preceding overlaps shift subtitles
                        cumulative_offset = cumulative_offsets[i] if cumulative_offsets and i < len(cumulative_offsets) else 0.0
                        clip_end_time = (current_time - cumulative_offset) + adjusted_duration

                        for p_i, phrase in enumerate(phrases):
                            phrase_time = (current_time - cumulative_offset) + (phrase["start"] - adjusted_trim_start)

                            # Skip phrases beyond clip end (prevents subtitles on black screen)
                            if phrase_time >= clip_end_time:
                                continue

                            if p_i + 1 < len(phrases):
                                next_time = (current_time - cumulative_offset) + (phrases[p_i + 1]["start"] - adjusted_trim_start)
                                phrase_dur = next_time - phrase_time
                            else:
                                phrase_dur = max(0.3, clip_end_time - phrase_time)

                            text_el = {
                                "type": "text",
                                "track": 2,
                                "time": round(phrase_time, 3),
                                "duration": round(phrase_dur, 3),
                                "text": phrase["text"],
                                "width": "90%",
                                "height": "20%",
                                "x": "50%",
                                "y": "78%",
                                "x_alignment": "50%",
                                "y_alignment": "50%",
                                "font_family": font_family,
                                "font_weight": "700",
                                "font_size": "6 vmin",
                                "stroke_color": "#000000",
                                "stroke_width": "1.6 vmin",
                            }
                            text_el.update(style)
                            # transcript_color is for native karaoke only; use it as fill_color for popup text
                            if "transcript_color" in text_el:
                                text_el["fill_color"] = text_el.pop("transcript_color")
                            if subtitle_color:
                                text_el["fill_color"] = subtitle_color
                            logger.debug(
                                "Popup phrase: text=%r time=%.3f dur=%.3f | clip_trim=%.2f adj_trim=%.2f cur_time=%.2f phrase_start=%.2f clip_end=%.2f",
                                phrase["text"][:20], phrase_time, phrase_dur,
                                clip.trim_start, adjusted_trim_start, current_time, phrase["start"], clip_end_time,
                            )
                            karaoke_elements.append(text_el)
                    # No words for this video → no subtitles (better than garbage)
                else:
                    # ── Fallback: transcript_source (all Whisper failed) ──
                    karaoke_el = {
                        "type": "text",
                        "track": 2,
                        "transcript_source": clip_name,
                        "transcript_effect": "karaoke",
                        "transcript_maximum_length": 15,
                        "duration": adjusted_duration,
                        "width": "90%",
                        "height": "20%",
                        "x": "50%",
                        "y": "78%",
                        "x_alignment": "50%",
                        "y_alignment": "50%",
                        "font_family": font_family,
                        "font_weight": "700",
                        "font_size": "6 vmin",
                        "stroke_color": "#000000",
                        "stroke_width": "1.6 vmin",
                    }
                    karaoke_el.update(style)
                    if subtitle_color:
                        karaoke_el["fill_color"] = subtitle_color
                    karaoke_elements.append(karaoke_el)

            # ── Per-clip voiceover audio + karaoke (new hybrid per-clip mode) ──
            if (
                clip.clip_type == "broll"
                and clip.matched_voiceover_segment is not None
                and voiceover_segments
                and per_clip_voiceover_url
            ):
                seg_idx = clip.matched_voiceover_segment
                if 0 <= seg_idx < len(voiceover_segments):
                    segment = voiceover_segments[seg_idx]
                    seg_start = segment["start"]
                    seg_end = segment["end"]
                    seg_duration = seg_end - seg_start
                    cumulative_offset = cumulative_offsets[i] if cumulative_offsets and i < len(cumulative_offsets) else 0.0
                    clip_render_start = current_time - cumulative_offset

                    # Safety: audio trim_duration never exceeds broll duration
                    trim_dur = min(seg_duration, adjusted_duration)

                    # Snap to last complete word boundary to avoid mid-word cuts
                    if voiceover_words and trim_dur < seg_duration:
                        complete_words = [
                            w for w in voiceover_words
                            if w["start"] >= seg_start and w["end"] <= seg_start + trim_dur
                        ]
                        if complete_words:
                            trim_dur = complete_words[-1]["end"] - seg_start + 0.15
                            logger.info("[Word snap] clip=%d: trim_dur=%.2f (after '%s')", i, trim_dur, complete_words[-1]["word"])

                    audio_el = {
                        "type": "audio",
                        "track": 5,
                        "time": round(clip_render_start, 3),
                        "source": per_clip_voiceover_url,
                        "trim_start": round(seg_start, 3),
                        "trim_duration": round(trim_dur, 3),
                        "volume": "100%",
                    }
                    elements.append(audio_el)
                    logger.info(
                        "[Per-clip audio] clip=%d, track=5, time=%.2f, trim_start=%.2f, trim_dur=%.2f",
                        i, clip_render_start, seg_start, trim_dur,
                    )

                    # Per-clip karaoke: filter voiceover_words to this segment
                    if karaoke and voiceover_words:
                        seg_words = [
                            w for w in voiceover_words
                            if w["start"] >= seg_start and w["end"] <= seg_start + trim_dur
                        ]
                        if seg_words:
                            phrases = _group_whisper_phrases(seg_words)
                            broll_end_time = clip_render_start + adjusted_duration
                            for p_i, phrase in enumerate(phrases):
                                phrase_time = clip_render_start + (phrase["start"] - seg_start)
                                if phrase_time >= broll_end_time:
                                    continue
                                if p_i + 1 < len(phrases):
                                    next_time = clip_render_start + (phrases[p_i + 1]["start"] - seg_start)
                                    phrase_dur = next_time - phrase_time
                                else:
                                    phrase_dur = max(0.3, broll_end_time - phrase_time)

                                text_el = {
                                    "type": "text",
                                    "track": 2,
                                    "time": round(phrase_time, 3),
                                    "duration": round(phrase_dur, 3),
                                    "text": phrase["text"],
                                    "width": "90%",
                                    "height": "20%",
                                    "x": "50%",
                                    "y": "78%",
                                    "x_alignment": "50%",
                                    "y_alignment": "50%",
                                    "font_family": font_family,
                                    "font_weight": "700",
                                    "font_size": "6 vmin",
                                    "stroke_color": "#000000",
                                    "stroke_width": "1.6 vmin",
                                }
                                text_el.update(style)
                                if "transcript_color" in text_el:
                                    text_el["fill_color"] = text_el.pop("transcript_color")
                                if subtitle_color:
                                    text_el["fill_color"] = subtitle_color
                                karaoke_elements.append(text_el)
                            logger.info(
                                "[VO Karaoke] clip=%d, phrases=%d, first='%s' at %.2f",
                                i, len(phrases), phrases[0]["text"][:20], clip_render_start + (phrases[0]["start"] - seg_start),
                            )

            # ── Broll popup karaoke from voiceover (legacy — skipped when per-clip active) ──
            elif karaoke and clip.clip_type == "broll" and voiceover_words and not voiceover_url:
                cumulative_offset = cumulative_offsets[i] if cumulative_offsets and i < len(cumulative_offsets) else 0.0
                broll_render_start = current_time - cumulative_offset
                broll_render_end = broll_render_start + adjusted_duration

                broll_words = [
                    w for w in voiceover_words
                    if w["start"] >= broll_render_start and w["start"] < broll_render_end
                ]

                if broll_words:
                    broll_phrases = _group_whisper_phrases(broll_words)

                    for p_i, phrase in enumerate(broll_phrases):
                        phrase_time = phrase["start"]

                        if p_i + 1 < len(broll_phrases):
                            phrase_dur = broll_phrases[p_i + 1]["start"] - phrase_time
                        else:
                            phrase_dur = max(0.3, broll_render_end - phrase_time)

                        text_el = {
                            "type": "text",
                            "track": 2,
                            "time": round(phrase_time, 3),
                            "duration": round(phrase_dur, 3),
                            "text": phrase["text"],
                            "width": "90%",
                            "height": "20%",
                            "x": "50%",
                            "y": "78%",
                            "x_alignment": "50%",
                            "y_alignment": "50%",
                            "font_family": font_family,
                            "font_weight": "700",
                            "font_size": "6 vmin",
                            "stroke_color": "#000000",
                            "stroke_width": "1.6 vmin",
                        }
                        text_el.update(style)
                        if "transcript_color" in text_el:
                            text_el["fill_color"] = text_el.pop("transcript_color")
                        if subtitle_color:
                            text_el["fill_color"] = subtitle_color
                        logger.debug(
                            "Broll karaoke: text=%r time=%.3f dur=%.3f | broll_start=%.2f broll_end=%.2f",
                            phrase["text"][:20], phrase_time, phrase_dur,
                            broll_render_start, broll_render_end,
                        )
                        karaoke_elements.append(text_el)

            current_time += adjusted_duration

        # ── Decoupled audio track for talking_head with transitions ────
        # Audio on track 2 with explicit time positioning, no overlap.
        # Cut at transition midpoints so audio switches cleanly.
        if is_same_source_transitions:
            render_starts = [0.0]
            for idx in range(1, len(clips)):
                td = transition_durations[idx] if idx < len(transition_durations) else 0
                render_starts.append(render_starts[-1] + adjusted_durations_list[idx - 1] - td)

            for idx in range(len(clips)):
                # Outgoing clip loses its tail (0.5s max) — less noticeable than
                # losing speech beginning. Incoming clip keeps full audio start.
                audio_time = render_starts[idx]

                if idx < len(clips) - 1:
                    audio_end = render_starts[idx + 1]  # stop when next clip starts
                else:
                    audio_end = render_starts[idx] + adjusted_durations_list[idx]

                audio_dur = audio_end - audio_time
                audio_el = {
                    "type": "audio",
                    "track": 2,
                    "source": clips[idx].source,
                    "time": round(audio_time, 3),
                    "trim_start": round(adjusted_trim_starts_list[idx], 3),
                    "trim_duration": round(audio_dur, 3),
                    "volume": "100%",
                    "audio_fade_in": 0.03,
                    "audio_fade_out": 0.03,
                }
                elements.append(audio_el)
                logger.info(
                    "[TH audio] clip=%d time=%.3f trim=%.3f dur=%.3f",
                    idx, audio_time, audio_el["trim_start"], audio_dur,
                )

        total_duration = current_time
        # Subtract total transition overlap to match actual Creatomate timeline
        if cumulative_offsets:
            total_duration -= cumulative_offsets[-1]

        if voiceover_url:
            # Storyboard: voiceover audio track
            elements.append({
                "type": "audio",
                "id": "voiceover",
                "track": 2,
                "time": 0,
                "source": voiceover_url,
                "volume": "100%",
            })

            # Single karaoke element for entire timeline, synced to voiceover
            if karaoke:
                vo_karaoke = {
                    "type": "text",
                    "track": 3,
                    "transcript_source": "voiceover",
                    "transcript_effect": "karaoke",
                    "transcript_maximum_length": 15,
                    "time": 0,
                    "duration": voiceover_duration if voiceover_duration > 0 else total_duration,
                    "width": "90%",
                    "height": "20%",
                    "x": "50%",
                    "y": "78%",
                    "x_alignment": "50%",
                    "y_alignment": "50%",
                    "font_family": font_family,
                    "font_weight": "700",
                    "font_size": "6 vmin",
                    "stroke_color": "#000000",
                    "stroke_width": "1.6 vmin",
                }
                vo_karaoke.update(style)
                if subtitle_color:
                    vo_karaoke["fill_color"] = subtitle_color
                elements.append(vo_karaoke)
        elif voiceover_segments and per_clip_voiceover_url:
            # Per-clip hybrid mode: audio elements already added in clip loop
            elements.extend(karaoke_elements)
            logger.info("Per-clip hybrid mode: %d audio elements on track 5",
                         sum(1 for el in elements if el.get("type") == "audio" and el.get("track") == 5))
        elif hybrid_voiceover_url:
            # Legacy hybrid mode: ducked voiceover on track 5 (isolated), Whisper popup on track 2
            elements.extend(karaoke_elements)
            hybrid_audio_el = {
                "type": "audio",
                "id": "hybrid_voiceover",
                "track": 5,
                "time": 0,
                "source": hybrid_voiceover_url,
                "volume": "100%",
            }
            logger.info("Hybrid audio element: %s", {k: (v[:60] + '...' if isinstance(v, str) and len(v) > 60 else v) for k, v in hybrid_audio_el.items()})
            elements.append(hybrid_audio_el)
        else:
            elements.extend(karaoke_elements)

        # ── Final source JSON ─────────────────────────────────
        source = {
            "output_format": "mp4",
            "width": width,
            "height": height,
            "frame_rate": fps,
            "elements": elements,
        }
        if not voiceover_url and not hybrid_voiceover_url:
            source["duration"] = total_duration

        mode = "storyboard" if voiceover_url else (
            "hybrid-perclip" if (voiceover_segments and per_clip_voiceover_url) else (
                "hybrid" if hybrid_voiceover_url else "talking_head"
            )
        )
        logger.info(
            "Creatomate source built: format=%s, quality=%s (%dx%d@%dfps), clips=%d, "
            "karaoke=%s, mood=%s, mode=%s",
            video_format, quality, width, height, fps, len(clips), karaoke,
            music_mood, mode,
        )

        # Dump payload for debugging (truncate URLs to keep log readable)
        if mode in ("hybrid", "hybrid-perclip"):
            import json as _json
            debug_src = _json.loads(_json.dumps(source))
            for el in debug_src.get("elements", []):
                if "source" in el and len(str(el["source"])) > 80:
                    el["source"] = el["source"][:60] + "...[truncated]"
            logger.info("Hybrid payload: %s", _json.dumps(debug_src, indent=2))

        return source

    async def submit_render(
        self,
        source: dict,
        webhook_url: str | None = None,
    ) -> str:
        """
        Send source JSON to Creatomate API.

        Returns render_id (str) on success, raises on failure.
        """
        # Log sticker elements (track 4) at INFO for debugging
        sticker_count = sum(1 for el in source.get("elements", []) if el.get("track") == 4)
        logger.info("submit_render: %d elements total, %d stickers (track 4)", len(source.get("elements", [])), sticker_count)
        for el in source.get("elements", []):
            if el.get("track") == 4:
                logger.info("  sticker: time=%.2f dur=%.2f src=%s", el.get("time", 0), el.get("duration", 0), el.get("source", "")[:80])
        logger.debug("Creatomate source payload:\n%s", json.dumps(source, indent=2))

        api_key = settings.CREATOMATE_API_KEY
        if not api_key:
            raise ValueError("No Creatomate API key configured.")

        @retry(
            retry=retry_if_exception_type((httpx.HTTPStatusError, httpx.RequestError)),
            wait=wait_exponential(multiplier=2, min=5, max=15),
            stop=stop_after_attempt(3),
            reraise=True,
        )
        async def _trigger_creatomate_api():
            async with httpx.AsyncClient(timeout=30) as client:
                res = await client.post(
                    API_URL,
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                    json={"source": source, "webhook_url": webhook_url},
                )
                if res.status_code >= 500:
                    res.raise_for_status()
                return res

        try:
            resp = await _trigger_creatomate_api()
        except (httpx.HTTPStatusError, httpx.RequestError) as e:
            logger.error("Creatomate render failed after 3 retries: %s", e)
            logger.warning("ADMIN_ALERT: Creatomate render exhausted retries — manual intervention may be needed")
            raise

        if resp.status_code not in (200, 202):
            logger.error("Creatomate API error %d: %s", resp.status_code, resp.text)
            raise RuntimeError(f"Creatomate failed with status {resp.status_code}")

        data = resp.json()
        render = data[0] if isinstance(data, list) else data
        render_id = render["id"]

        self._last_cost_usd = CREATOMATE_COST_PER_RENDER
        logger.info("Creatomate render created: %s", render_id)
        logger.info("Creatomate cost: $%.2f (render_id=%s)", CREATOMATE_COST_PER_RENDER, render_id)
        return render_id

    async def create_render(
        self,
        clips: list[Clip],
        video_format: str = "reels",
        music_mood: str | None = None,
        karaoke: bool = True,
        quality: str = "prod",
        broll_overlays: list[dict] | None = None,
        webhook_url: str | None = None,
        voiceover_url: str | None = None,
        voiceover_duration: float = 0.0,
        transition_types: list[str] | None = None,
    ) -> str:
        """
        Legacy all-in-one render method. Kept for backward compatibility.

        New code should use build_source() + apply_visual_blueprint() + submit_render().
        """
        source = self.build_source(
            clips=clips,
            video_format=video_format,
            music_mood=music_mood,
            karaoke=karaoke,
            quality=quality,
            voiceover_url=voiceover_url,
            voiceover_duration=voiceover_duration,
        )

        # Legacy transition_types support (Gemini-based)
        if transition_types:
            video_els = [el for el in source["elements"] if el.get("type") == "video"]
            for i, vel in enumerate(video_els):
                if i > 0 and i < len(transition_types):
                    t_type = transition_types[i]
                    transition_base = TRANSITION_MAP.get(t_type)
                    if transition_base:
                        t_duration = round(max(0.4, min(0.8, clips[i].trim_duration * 0.07)), 2)
                        vel["transition"] = {**transition_base, "duration": t_duration}

        return await self.submit_render(source, webhook_url)
