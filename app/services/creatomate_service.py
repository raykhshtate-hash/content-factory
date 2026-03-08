"""
Creatomate Render Service — Dynamic JSON Source.

Builds video renders entirely via JSON source (no predefined templates).
Supports dynamic dimensions, background music, and word-level subtitles.
"""

import json
import logging
from dataclasses import dataclass
import httpx
from pydantic import BaseModel
from tenacity import retry, retry_if_exception_type, wait_exponential, stop_after_attempt

from app.config import settings

logger = logging.getLogger(__name__)

API_URL = "https://api.creatomate.com/v1/renders"

# ── Music Library (GCS public URLs by mood) ──────────────────────
# TODO: Upload real tracks to GCS and replace these placeholder URLs.
GCS_BUCKET_BASE = f"https://storage.googleapis.com/{settings.GCS_BUCKET_NAME}"

MUSIC_LIBRARY: dict[str, str] = {
    "upbeat":   f"{GCS_BUCKET_BASE}/music/upbeat.mp3",
    "chill":    f"{GCS_BUCKET_BASE}/music/chill.mp3",
    "dramatic": f"{GCS_BUCKET_BASE}/music/dramatic.mp3",
    "funny":    f"{GCS_BUCKET_BASE}/music/funny.mp3",
}


@dataclass
class Clip:
    source: str       # public video URL
    trim_start: float # seconds
    trim_duration: float


@dataclass
class Subtitle:
    text: str
    time: float       # seconds from the start of the composition
    duration: float   # seconds


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
        subtitles: list[Subtitle] | None = None,
        texts: list[str] | None = None,
        webhook_url: str | None = None,
    ) -> str:
        """
        Creates a dynamic JSON-based render request.
        Clips are laid out sequentially on an explicit timeline.
        Returns render_id (str) on success, raises on failure.
        """
        if texts is None:
            texts = []
        if subtitles is None:
            subtitles = []

        # ── Dimensions based on format ──────────────────────────
        # Substring matching to catch "Instagram Reels", "YT Shorts", "Vertical", etc.
        fmt = video_format.lower()
        if any(kw in fmt for kw in ("reel", "short", "tiktok", "vertical", "9:16")):
            width, height = 1080, 1920
        elif any(kw in fmt for kw in ("youtube", "horizontal", "16:9")):
            width, height = 1920, 1080
        else:
            width, height = 1080, 1080

        # ── Rigid Vertical Composition Wrapper ───────────────────
        # This forces Creatomate to strictly use width/height and crop
        # internal elements with fit:cover, preventing auto-resizing.
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

        # Wrap all videos in a locked container
        elements = [{
            "type": "composition",
            "track": 1,
            "width": width,       # rigid canvas size
            "height": height,     # rigid canvas size
            "fill_color": "#000000",
            "duration": total_duration,
            "elements": composition_children
        }]

        # ── Subtitle style by mood ──────────────────────────────
        mood_styles = {
            "energetic": {
                "fill_color": "#FFFFFF",
                "font_weight": "800",
                "stroke_color": "#000000",
                "stroke_width": "2 vmin",
                "background_color": "rgba(255,50,50,0.35)",
                "background_x_padding": "5%",
                "background_y_padding": "3%",
                "background_border_radius": "3%",
            },
            "calm": {
                "fill_color": "#FFFFFFCC",
                "font_weight": "300",
                "shadow_color": "rgba(0,0,0,0.3)",
                "shadow_blur": "3 vmin",
            },
            "humor": {
                "fill_color": "#FFE600",
                "font_weight": "800",
                "stroke_color": "#000000",
                "stroke_width": "2.5 vmin",
                "background_color": "rgba(0,0,0,0.4)",
                "background_x_padding": "5%",
                "background_y_padding": "3%",
                "background_border_radius": "4%",
            },
            "professional": {
                "fill_color": "#FFFFFF",
                "font_weight": "400",
                "shadow_color": "rgba(0,0,0,0.25)",
                "shadow_blur": "2 vmin",
            },
            "upbeat": {
                "fill_color": "#FFFFFF",
                "font_weight": "700",
                "stroke_color": "#FF6B9D",
                "stroke_width": "2 vmin",
                "background_color": "rgba(0,0,0,0.3)",
                "background_x_padding": "5%",
                "background_y_padding": "3%",
                "background_border_radius": "3%",
            },
            "dramatic": {
                "fill_color": "#FF4444",
                "font_weight": "700",
                "stroke_color": "#000000",
                "stroke_width": "2 vmin",
                "shadow_color": "rgba(0,0,0,0.6)",
                "shadow_blur": "5 vmin",
            },
            "funny": {
                "fill_color": "#FFE600",
                "font_weight": "800",
                "stroke_color": "#000000",
                "stroke_width": "2.5 vmin",
                "background_color": "rgba(0,0,0,0.4)",
                "background_x_padding": "5%",
                "background_y_padding": "3%",
                "background_border_radius": "4%",
            },
        }
        style = mood_styles.get((music_mood or "professional").lower(), mood_styles["professional"])

        # ── Subtitles as keyframe-based single text element ─────
        # Best practice: one text element with keyframes array,
        # NOT individual text elements per phrase.
        if subtitles:
            # Build keyframes: [{time, value}, ...]
            keyframes = [{"time": 0, "value": ""}]
            for sub in subtitles:
                keyframes.append({"time": sub.time, "value": sub.text})
                # Clear text after phrase ends
                keyframes.append({"time": round(sub.time + sub.duration, 4), "value": ""})

            caption_el = {
                "type": "text",
                "track": 2,
                "time": 0,
                "duration": total_duration,
                "width": "100%",
                "height": "100%",
                "x_padding": "3 vmin",
                "y_padding": "8 vmin",
                "x_alignment": "50%",
                "y_alignment": "100%",  # Bottom-aligned
                "font_family": "Montserrat",
                "font_size": "6.5 vmin",
                "shadow_color": "rgba(0,0,0,0.65)",
                "shadow_blur": "1.6 vmin",
                "text": keyframes,
            }
            caption_el.update(style)
            elements.append(caption_el)

        # ── Background music ──────────────────────────────────────────
        # Intentionally omitted. Users should add trending audio directly 
        # in Instagram/TikTok during upload based on the suggested mood.

        # ── Final source JSON ───────────────────────────────────
        # 60fps for social media (TikTok/Reels), 30fps for YouTube
        fps = 60 if fmt in ("reels", "shorts", "tiktok") else 30

        source = {
            "output_format": "mp4",
            "width": width,
            "height": height,
            "frame_rate": fps,
            "duration": total_duration,
            "elements": elements,
        }

        logger.info("Creatomate dynamic render: format=%s, clips=%d, subs=%d, mood=%s",
                     video_format, len(clips), len(subtitles), music_mood)
        logger.info("Creatomate source payload: %s", json.dumps(source, indent=2))

        # ── Send exactly ONE POST request to Creatomate ─────────
        api_key = settings.CREATOMATE_API_KEY
        if not api_key:
            raise ValueError("No Creatomate API key configured.")

        # Inner bounded function for retry logic specifically for the HTTP call
        @retry(
            retry=retry_if_exception_type((httpx.HTTPStatusError, httpx.RequestError)),
            wait=wait_exponential(multiplier=2, min=5, max=15),
            stop=stop_after_attempt(3),
            reraise=True,
        )
        async def _trigger_creatomate_api():
            async with httpx.AsyncClient(timeout=30) as client:
                res = await client.post(
                    "https://api.creatomate.com/v1/renders",
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                    json={"source": source, "webhook_url": webhook_url},
                )
                # Only retry on 5xx or timeout. 4xx (like 400 Bad Request) are usually fatal syntax errors
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
