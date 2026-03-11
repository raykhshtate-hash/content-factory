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

    def _build_broll_elements(self, broll_overlays: list[dict], track: int) -> list[dict]:
        """Build Creatomate sticker elements for B-roll overlays."""
        elements = []
        for idx, ov in enumerate(broll_overlays):
            url = ov.get("url")
            if not url:
                continue

            start_sec = float(ov.get("start_sec", 0))
            x = "25%" if idx % 2 == 0 else "75%"

            el = {
                "type": "image",
                "track": track,
                "source": url,
                "time": start_sec,
                "duration": 2.5,
                "width": "25%",
                "height": "25%",
                "x": x,
                "y": "18%",
                "border_radius": 50,
                "fit": "cover",
            }

            elements.append(el)
        return elements

    async def create_render(
        self,
        clips: list[Clip],
        video_format: str = "reels",
        music_mood: str | None = None,
        karaoke: bool = True,
        quality: str = "prod",
        broll_overlays: list[dict] | None = None,
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

        # ── Build flat timeline — one named video + one karaoke text per clip ──
        elements = []
        karaoke_elements = []
        current_time = 0.0

        mood_key = (music_mood or "professional").lower() if karaoke else None
        style = KARAOKE_STYLES.get(mood_key, KARAOKE_STYLES["professional"]) if karaoke else {}

        for i, clip in enumerate(clips):
            clip_name = f"clip-{i}"
            # Ken Burns: even clips zoom in, odd clips zoom out
            if i % 2 == 0:
                start_scale, end_scale = "100%", "110%"
            else:
                start_scale, end_scale = "110%", "100%"

            # Transition only on scene change (different source video)
            is_scene_change = i > 0 and clip.source != clips[i - 1].source

            video_el = {
                "type": "video",
                "name": clip_name,
                "track": 1,
                "source": clip.source,
                "width": "100%",
                "height": "100%",
                "fit": "cover",
                "time": current_time,
                "trim_start": clip.trim_start,
                "trim_duration": clip.trim_duration,
                "duration": clip.trim_duration,
                "animations": [
                    {
                        "time": "start",
                        "duration": clip.trim_duration,
                        "easing": "linear",
                        "type": "scale",
                        "scope": "element",
                        "start_scale": start_scale,
                        "end_scale": end_scale,
                    }
                ],
            }

            if is_scene_change:
                video_el["transition"] = {
                    "duration": 0.1,
                    "type": "fade",
                }

            elements.append(video_el)

            if karaoke:
                karaoke_el = {
                    "type": "text",
                    "track": 2,
                    "transcript_source": clip_name,
                    "transcript_effect": "karaoke",
                    "transcript_maximum_length": 15,
                    "time": current_time,
                    "duration": clip.trim_duration,
                    "width": "90%",
                    "height": "20%",
                    "x": "50%",
                    "y": "78%",
                    "x_alignment": "50%",
                    "y_alignment": "50%",
                    "font_family": "Montserrat",
                    "font_weight": "700",
                    "font_size": "8 vmin",
                    "stroke_color": "#000000",
                    "stroke_width": "1.6 vmin",
                }
                karaoke_el.update(style)
                karaoke_elements.append(karaoke_el)

            current_time += clip.trim_duration

        total_duration = current_time
        elements.extend(karaoke_elements)

        # ── B-roll overlays (Pexels video/photo) ─────────────
        if broll_overlays:
            elements.extend(self._build_broll_elements(broll_overlays, track=4))

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
            "Creatomate render: format=%s, quality=%s (%dx%d@%dfps), clips=%d, karaoke=%s, broll=%d, mood=%s",
            video_format, quality, width, height, fps, len(clips), karaoke, len(broll_overlays or []), music_mood,
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
