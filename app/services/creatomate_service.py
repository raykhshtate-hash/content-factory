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
from dataclasses import dataclass
import httpx
from tenacity import retry, retry_if_exception_type, wait_exponential, stop_after_attempt

from app.config import settings

logger = logging.getLogger(__name__)

API_URL = "https://api.creatomate.com/v1/renders"


@dataclass
class Clip:
    source: str       # signed video URL
    trim_start: float # seconds
    trim_duration: float
    clip_type: str = "speech"     # "speech" or "broll"
    video_index: int = 1          # 1-based, for whisper_words lookup


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
        "font_weight": "800",
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
        "font_weight": "800",
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
        "font_weight": "700",
        "stroke_color": "#000000",
        "stroke_width": "2 vmin",
    },
    "dramatic": {
        "fill_color": "#FFFFFF",
        "transcript_color": "#FF4444",
        "font_weight": "700",
        "stroke_color": "#000000",
        "stroke_width": "2 vmin",
    },
    "funny": {
        "fill_color": "#FFFFFF",
        "transcript_color": "#FFE600",
        "font_weight": "800",
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


def apply_visual_blueprint(
    elements: list[dict],
    blueprint: dict,
    clip_durations: list[float],
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

    # ── Sticker overlays (top-level, timeline-based) ──
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
            "provider": "openai model=gpt-image-1.5",
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

    return elements, transition_count


class CreatomateService:
    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or settings.CREATOMATE_API_KEY
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
    #             "provider": "openai model=gpt-image-1.5",
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
        use_whisper_karaoke = (
            whisper_words is not None
            and not voiceover_url
            and any(
                str(clip.video_index) in whisper_words
                for clip in clips if clip.clip_type != "broll"
            )
        )

        for i, clip in enumerate(clips):
            clip_name = f"clip-{i}"

            adjusted_trim_start = max(0, clip.trim_start - 0.5)
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

            if voiceover_url:
                video_el["volume"] = "0%"
            elif clip.clip_type == "broll":
                video_el["volume"] = "70%"

            elements.append(video_el)

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

                    if clip_words:
                        phrases = _group_whisper_phrases(clip_words)
                        clip_end_time = current_time + adjusted_duration

                        for p_i, phrase in enumerate(phrases):
                            phrase_time = current_time + (phrase["start"] - adjusted_trim_start)

                            # Skip phrases beyond clip end (prevents subtitles on black screen)
                            if phrase_time >= clip_end_time:
                                continue

                            if p_i + 1 < len(phrases):
                                next_time = current_time + (phrases[p_i + 1]["start"] - adjusted_trim_start)
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
                                "font_family": "Montserrat",
                                "font_weight": "700",
                                "font_size": "6 vmin",
                                "stroke_color": "#000000",
                                "stroke_width": "1.6 vmin",
                            }
                            text_el.update(style)
                            # transcript_color is for native karaoke only; use it as fill_color for popup text
                            if "transcript_color" in text_el:
                                text_el["fill_color"] = text_el.pop("transcript_color")
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
                        "font_family": "Montserrat",
                        "font_weight": "700",
                        "font_size": "6 vmin",
                        "stroke_color": "#000000",
                        "stroke_width": "1.6 vmin",
                    }
                    karaoke_el.update(style)
                    karaoke_elements.append(karaoke_el)

            current_time += adjusted_duration

        total_duration = current_time

        if voiceover_url:
            # Voiceover audio track
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
                    "font_family": "Montserrat",
                    "font_weight": "700",
                    "font_size": "6 vmin",
                    "stroke_color": "#000000",
                    "stroke_width": "1.6 vmin",
                }
                vo_karaoke.update(style)
                elements.append(vo_karaoke)
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
        if not voiceover_url:
            source["duration"] = total_duration

        logger.info(
            "Creatomate source built: format=%s, quality=%s (%dx%d@%dfps), clips=%d, "
            "karaoke=%s, mood=%s, voiceover=%s",
            video_format, quality, width, height, fps, len(clips), karaoke,
            music_mood, bool(voiceover_url),
        )

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

        resp = await _trigger_creatomate_api()

        if resp.status_code not in (200, 202):
            logger.error("Creatomate API error %d: %s", resp.status_code, resp.text)
            raise RuntimeError(f"Creatomate failed with status {resp.status_code}")

        data = resp.json()
        render = data[0] if isinstance(data, list) else data
        render_id = render["id"]

        logger.info("Creatomate render created: %s", render_id)
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
