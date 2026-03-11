"""
Pexels Service — search stock video/photo for B-roll overlays.

Provides:
 - search_video  — find portrait video by keyword → {url, width, height, duration}
 - search_photo  — find portrait photo by keyword → {url, width, height}
 - search_broll  — video-first with photo fallback
"""

import logging

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

API_BASE = "https://api.pexels.com"


def _headers() -> dict:
    return {"Authorization": settings.PEXELS_API_KEY}


async def search_video(
    keyword: str,
    orientation: str = "portrait",
    per_page: int = 5,
) -> dict | None:
    """Search Pexels for a video. Returns best HD file as direct URL.

    Returns: {url, width, height, duration} or None.
    """
    params = {
        "query": keyword,
        "orientation": orientation,
        "per_page": per_page,
    }
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(
            f"{API_BASE}/v1/videos/search",
            headers=_headers(),
            params=params,
        )
        if resp.status_code != 200:
            logger.error("Pexels video search failed: %s %s", resp.status_code, resp.text)
            return None

    data = resp.json()
    videos = data.get("videos", [])
    if not videos:
        return None

    video = videos[0]
    # Pick best HD mp4 file
    best_file = _pick_video_file(video.get("video_files", []))
    if not best_file:
        return None

    return {
        "url": best_file["link"],
        "width": best_file.get("width", video.get("width")),
        "height": best_file.get("height", video.get("height")),
        "duration": video.get("duration"),
    }


async def search_photo(
    keyword: str,
    orientation: str = "portrait",
    per_page: int = 5,
) -> dict | None:
    """Search Pexels for a photo. Returns direct image URL.

    Returns: {url, width, height} or None.
    """
    params = {
        "query": keyword,
        "orientation": orientation,
        "per_page": per_page,
    }
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(
            f"{API_BASE}/v1/search",
            headers=_headers(),
            params=params,
        )
        if resp.status_code != 200:
            logger.error("Pexels photo search failed: %s %s", resp.status_code, resp.text)
            return None

    data = resp.json()
    photos = data.get("photos", [])
    if not photos:
        return None

    photo = photos[0]
    src = photo.get("src", {})
    # Use "large" for good quality without being huge
    url = src.get("large") or src.get("original")
    if not url:
        return None

    return {
        "url": url,
        "width": photo.get("width"),
        "height": photo.get("height"),
    }


async def search_broll(
    keyword: str,
    orientation: str = "portrait",
) -> dict | None:
    """Search photo first, fall back to video. Photos work better for stickers."""
    result = await search_photo(keyword, orientation=orientation)
    if result:
        result["type"] = "image"
        return result

    result = await search_video(keyword, orientation=orientation)
    if result:
        result["type"] = "video"
        return result

    logger.warning("No Pexels results for keyword: %s", keyword)
    return None


def _pick_video_file(video_files: list[dict]) -> dict | None:
    """Pick the best HD mp4 file from video_files array."""
    mp4s = [f for f in video_files if f.get("file_type") == "video/mp4"]
    if not mp4s:
        mp4s = video_files

    # Prefer HD quality
    hd = [f for f in mp4s if f.get("quality") == "hd"]
    candidates = hd or mp4s

    if not candidates:
        return None

    # Pick smallest HD file (good enough for overlay, saves bandwidth)
    candidates.sort(key=lambda f: f.get("width", 9999))
    return candidates[0]
