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


class CreatomateService:
    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or settings.CREATOMATE_API_KEY
        if not self.api_key:
            raise ValueError("CREATOMATE_API_KEY is not set")

    async def create_render(
        self,
        clips: list[Clip],
        video_format: str = "reels",
        music_mood: str | None = None,
        karaoke: bool = True,
        quality: str = "prod",
        webhook_url: str | None = None,
    ) -> str:
        """
        Creates a dynamic JSON-based render request.

        quality: "dev" (720p 24fps) or "prod" (1080p 60fps).
        When karaoke=True, Creatomate auto-transcribes the audio and
        highlights words in sync — no external Whisper/subtitles needed.

        Returns render_id (str) on success, raises on failure.
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

        # ── Build clip timeline inside a rigid composition ──────
        composition_children = []
        current_time = 0.0

        for clip in clips:
            composition_children.append({
                "type": "video",
                "track": 1,
                "source": clip.source,
                "width": "100%",
                "height": "100%",
                "fit": "cover",
                "time": current_time,
                "trim_start": clip.trim_start,
                "trim_duration": clip.trim_duration,
                "duration": clip.trim_duration,
            })
            current_time += clip.trim_duration

        total_duration = current_time

        elements = [{
            "type": "composition",
            "id": "main-comp",
            "track": 1,
            "width": width,
            "height": height,
            "fill_color": "#000000",
            "duration": total_duration,
            "elements": composition_children,
        }]

        # ── Karaoke subtitles (Creatomate native) ──────────────
        if karaoke:
            mood_key = (music_mood or "professional").lower()
            style = KARAOKE_STYLES.get(mood_key, KARAOKE_STYLES["professional"])

            karaoke_el = {
                "type": "text",
                "track": 2,
                "transcript_source": "main-comp",
                "transcript_effect": "highlight",
                "transcript_maximum_length": 15,
                "time": 0,
                "duration": total_duration,
                "width": "90%",
                "height": "20%",
                "x": "50%",
                "y": "78%",
                "x_alignment": "50%",
                "y_alignment": "50%",
                "font_family": "Montserrat",
                "font_size": "6.5 vmin",
            }
            karaoke_el.update(style)
            elements.append(karaoke_el)

        # ── Final source JSON ─────────────────────────────────
        source = {
            "output_format": "mp4",
            "width": width,
            "height": height,
            "frame_rate": fps,
            "duration": total_duration,
            "elements": elements,
        }

        logger.info(
            "Creatomate render: format=%s, quality=%s (%dx%d@%dfps), clips=%d, karaoke=%s, mood=%s",
            video_format, quality, width, height, fps, len(clips), karaoke, music_mood,
        )
        logger.debug("Creatomate source payload:\n%s", json.dumps(source, indent=2))

        # ── Send POST to Creatomate ───────────────────────────
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

        logger.info("Creatomate render created: %s (quality=%s)", render_id, quality)
        return render_id
