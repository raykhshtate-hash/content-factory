"""
Step 0 — Test Anchor Element pattern for Creatomate.

Hypothesis: An invisible "anchor" element sharing the same source URL
as a visible video element can serve as transcript_source for a text
element, working around the one-transcript_source-per-URL limitation.

Plan A: Anchor pattern (invisible element → transcript_source)
Plan B: Manual text timing (explicit time/duration text elements)

Usage:
    source venv/bin/activate
    python3 test_anchor.py
"""

import json
import os
import sys
import time

import requests
from dotenv import load_dotenv

load_dotenv(override=True)

# ── Config ──────────────────────────────────────────────────────────

CREATOMATE_API_KEY = os.getenv("CREATOMATE_API_KEY", "")
GCS_VIDEO_URI = "gs://romina-content-factory-489121/renders/087b9a40-bf85-471d-aec6-960edc398cd2/final.mp4"

# Dev quality
WIDTH = 720
HEIGHT = 1280
FPS = 24

CREATOMATE_API = "https://api.creatomate.com/v1/renders"
POLL_INTERVAL = 5
MAX_WAIT = 300  # 5 min


def get_presigned_url(gcs_uri: str) -> str:
    """Generate presigned URL via GCSService."""
    from app.services.gcs_service import GCSService
    gcs = GCSService()
    return gcs.generate_presigned_url(gcs_uri, expiration_minutes=360)


def submit_render(source: dict) -> dict:
    """Submit render to Creatomate API, return response JSON."""
    resp = requests.post(
        CREATOMATE_API,
        headers={
            "Authorization": f"Bearer {CREATOMATE_API_KEY}",
            "Content-Type": "application/json",
        },
        json={"source": source},
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    # API returns a list of renders
    return data[0] if isinstance(data, list) else data


def poll_render(render_id: str) -> dict:
    """Poll render status until done or timeout."""
    url = f"{CREATOMATE_API}/{render_id}"
    headers = {"Authorization": f"Bearer {CREATOMATE_API_KEY}"}

    elapsed = 0
    while elapsed < MAX_WAIT:
        resp = requests.get(url, headers=headers, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        status = data.get("status", "unknown")
        print(f"  [{elapsed}s] status={status}")

        if status == "succeeded":
            return data
        if status in ("failed", "error"):
            print(f"  RENDER FAILED: {data.get('error_message', 'unknown error')}")
            return data

        time.sleep(POLL_INTERVAL)
        elapsed += POLL_INTERVAL

    print(f"  TIMEOUT after {MAX_WAIT}s")
    return {"status": "timeout"}


# ── Plan A: Anchor Element Pattern ──────────────────────────────────

def build_plan_a(video_url: str) -> dict:
    """
    Anchor pattern:
    - Track 1: visible video "clip-0" (trim 0-5s)
    - Track 1: visible video "clip-1" (trim 5-10s)
    - Track 5 (hidden): anchor "anchor-0" (same source as clip-0, invisible)
    - Track 5 (hidden): anchor "anchor-1" (same source as clip-1, invisible)
    - Track 2: text with transcript_source="anchor-0"
    - Track 2: text with transcript_source="anchor-1"

    If this works, each text element gets karaoke from the anchor's
    audio segment, while the visible clips can share the same source URL.
    """
    karaoke_text_base = {
        "transcript_effect": "karaoke",
        "transcript_maximum_length": 15,
        "transcript_color": "#FF3232",
        "width": "90%",
        "height": "20%",
        "x": "50%",
        "y": "78%",
        "x_alignment": "50%",
        "y_alignment": "50%",
        "font_family": "Montserrat",
        "font_weight": "700",
        "font_size": "8 vmin",
        "fill_color": "#FFFFFF",
        "stroke_color": "#000000",
        "stroke_width": "1.6 vmin",
    }

    return {
        "output_format": "mp4",
        "width": WIDTH,
        "height": HEIGHT,
        "frame_rate": FPS,
        "duration": 10.0,
        "elements": [
            # ── Track 1: visible video clips ──
            {
                "type": "video",
                "name": "clip-0",
                "track": 1,
                "source": video_url,
                "width": "100%",
                "height": "100%",
                "fit": "cover",
                "trim_start": 0,
                "trim_duration": 5.0,
                "duration": 5.0,
            },
            {
                "type": "video",
                "name": "clip-1",
                "track": 1,
                "source": video_url,
                "width": "100%",
                "height": "100%",
                "fit": "cover",
                "trim_start": 5.0,
                "trim_duration": 5.0,
                "duration": 5.0,
            },
            # ── Track 5: invisible anchor elements ──
            # Same source URL but different names.
            # Hidden: moved off-screen, volume 0%.
            {
                "type": "video",
                "name": "anchor-0",
                "track": 5,
                "source": video_url,
                "x": "-200%",
                "y": "-200%",
                "width": "1%",
                "height": "1%",
                "volume": "0%",
                "trim_start": 0,
                "trim_duration": 5.0,
                "duration": 5.0,
            },
            {
                "type": "video",
                "name": "anchor-1",
                "track": 5,
                "source": video_url,
                "x": "-200%",
                "y": "-200%",
                "width": "1%",
                "height": "1%",
                "volume": "0%",
                "trim_start": 5.0,
                "trim_duration": 5.0,
                "duration": 5.0,
            },
            # ── Track 2: karaoke text referencing anchors ──
            {
                "type": "text",
                "track": 2,
                "transcript_source": "anchor-0",
                **karaoke_text_base,
                "duration": 5.0,
            },
            {
                "type": "text",
                "track": 2,
                "transcript_source": "anchor-1",
                **karaoke_text_base,
                "duration": 5.0,
            },
        ],
    }


# ── Plan B: Manual Text Timing ─────────────────────────────────────

def build_plan_b(video_url: str) -> dict:
    """
    Fallback: no transcript_source at all. Instead, place text elements
    with explicit time/duration to simulate subtitles. No karaoke effect.

    This tests whether manually timed text overlay works as an alternative
    to Creatomate's built-in transcription.
    """
    return {
        "output_format": "mp4",
        "width": WIDTH,
        "height": HEIGHT,
        "frame_rate": FPS,
        "duration": 10.0,
        "elements": [
            # ── Track 1: video clips ──
            {
                "type": "video",
                "name": "clip-0",
                "track": 1,
                "source": video_url,
                "width": "100%",
                "height": "100%",
                "fit": "cover",
                "trim_start": 0,
                "trim_duration": 5.0,
                "duration": 5.0,
            },
            {
                "type": "video",
                "name": "clip-1",
                "track": 1,
                "source": video_url,
                "width": "100%",
                "height": "100%",
                "fit": "cover",
                "trim_start": 5.0,
                "trim_duration": 5.0,
                "duration": 5.0,
            },
            # ── Track 2: manually timed text (simulated subtitles) ──
            {
                "type": "text",
                "track": 2,
                "time": 0.5,
                "duration": 2.0,
                "text": "Первая фраза теста",
                "width": "90%",
                "height": "15%",
                "x": "50%",
                "y": "78%",
                "x_alignment": "50%",
                "y_alignment": "50%",
                "font_family": "Montserrat",
                "font_weight": "700",
                "font_size": "8 vmin",
                "fill_color": "#FFFFFF",
                "stroke_color": "#000000",
                "stroke_width": "1.6 vmin",
                "animations": [
                    {"type": "fade", "time": 0, "duration": 0.2},
                    {"type": "fade", "time": "end", "duration": 0.2, "reversed": True},
                ],
            },
            {
                "type": "text",
                "track": 2,
                "time": 3.0,
                "duration": 2.0,
                "text": "Вторая фраза теста",
                "width": "90%",
                "height": "15%",
                "x": "50%",
                "y": "78%",
                "x_alignment": "50%",
                "y_alignment": "50%",
                "font_family": "Montserrat",
                "font_weight": "700",
                "font_size": "8 vmin",
                "fill_color": "#FFFFFF",
                "stroke_color": "#000000",
                "stroke_width": "1.6 vmin",
                "animations": [
                    {"type": "fade", "time": 0, "duration": 0.2},
                    {"type": "fade", "time": "end", "duration": 0.2, "reversed": True},
                ],
            },
            {
                "type": "text",
                "track": 2,
                "time": 5.5,
                "duration": 2.0,
                "text": "Третья фраза теста",
                "width": "90%",
                "height": "15%",
                "x": "50%",
                "y": "78%",
                "x_alignment": "50%",
                "y_alignment": "50%",
                "font_family": "Montserrat",
                "font_weight": "700",
                "font_size": "8 vmin",
                "fill_color": "#FFFFFF",
                "stroke_color": "#000000",
                "stroke_width": "1.6 vmin",
                "animations": [
                    {"type": "fade", "time": 0, "duration": 0.2},
                    {"type": "fade", "time": "end", "duration": 0.2, "reversed": True},
                ],
            },
            {
                "type": "text",
                "track": 2,
                "time": 8.0,
                "duration": 1.5,
                "text": "Четвёртая фраза",
                "width": "90%",
                "height": "15%",
                "x": "50%",
                "y": "78%",
                "x_alignment": "50%",
                "y_alignment": "50%",
                "font_family": "Montserrat",
                "font_weight": "700",
                "font_size": "8 vmin",
                "fill_color": "#FFFFFF",
                "stroke_color": "#000000",
                "stroke_width": "1.6 vmin",
                "animations": [
                    {"type": "fade", "time": 0, "duration": 0.2},
                    {"type": "fade", "time": "end", "duration": 0.2, "reversed": True},
                ],
            },
        ],
    }


# ── Main ────────────────────────────────────────────────────────────

def main():
    if not CREATOMATE_API_KEY:
        print("ERROR: CREATOMATE_API_KEY not set")
        sys.exit(1)

    print("Generating presigned URL...")
    video_url = get_presigned_url(GCS_VIDEO_URI)
    print(f"  URL ready ({len(video_url)} chars)")

    # ── Plan A ──
    print("\n" + "=" * 60)
    print("PLAN A: Anchor Element Pattern")
    print("=" * 60)

    source_a = build_plan_a(video_url)
    print(f"\nPayload ({len(source_a['elements'])} elements):")
    print(json.dumps(source_a, indent=2, ensure_ascii=False)[:2000])
    print("\nSubmitting to Creatomate...")

    try:
        render_a = submit_render(source_a)
        render_id_a = render_a.get("id", "unknown")
        print(f"  Render ID: {render_id_a}")
        print(f"  Status: {render_a.get('status')}")

        result_a = poll_render(render_id_a)
        status_a = result_a.get("status")
        print(f"\n  PLAN A RESULT: {status_a}")
        if status_a == "succeeded":
            print(f"  URL: {result_a.get('url')}")
            print("  >>> CHECK: Download video and verify karaoke text appears <<<")
        else:
            print(f"  Error: {result_a.get('error_message', 'N/A')}")
    except Exception as e:
        print(f"  PLAN A FAILED: {e}")
        status_a = "error"

    # ── Plan B ──
    print("\n" + "=" * 60)
    print("PLAN B: Manual Text Timing (fallback)")
    print("=" * 60)

    source_b = build_plan_b(video_url)
    print(f"\nPayload ({len(source_b['elements'])} elements):")
    print(json.dumps(source_b, indent=2, ensure_ascii=False)[:2000])
    print("\nSubmitting to Creatomate...")

    try:
        render_b = submit_render(source_b)
        render_id_b = render_b.get("id", "unknown")
        print(f"  Render ID: {render_id_b}")
        print(f"  Status: {render_b.get('status')}")

        result_b = poll_render(render_id_b)
        status_b = result_b.get("status")
        print(f"\n  PLAN B RESULT: {status_b}")
        if status_b == "succeeded":
            print(f"  URL: {result_b.get('url')}")
            print("  >>> CHECK: Download video and verify text overlays appear <<<")
        else:
            print(f"  Error: {result_b.get('error_message', 'N/A')}")
    except Exception as e:
        print(f"  PLAN B FAILED: {e}")
        status_b = "error"

    # ── Summary ──
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    print(f"  Plan A (Anchor):      {status_a}")
    print(f"  Plan B (Manual text): {status_b}")

    if status_a == "succeeded":
        print("\n  NEXT: Watch Plan A video — if karaoke highlights words,")
        print("  anchor pattern WORKS and we can use it for multi-clip")
        print("  transcript_source with a single source URL.")
    elif status_b == "succeeded":
        print("\n  NEXT: Watch Plan B video — manual text timing works")
        print("  as fallback. Need Whisper for word-level timestamps.")


if __name__ == "__main__":
    main()
