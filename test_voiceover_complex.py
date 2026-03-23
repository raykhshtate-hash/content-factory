"""
Test: progressively complex payloads to find what breaks audio element.

Test 1: 2 video clips + 1 audio (baseline — should work)
Test 2: 2 video + 5 text elements (track 2) + 1 audio (track 5)
Test 3: 2 video + 5 text + 2 image stickers (track 4) + 1 audio (track 5)

Usage:
    source venv/bin/activate
    python3 test_voiceover_complex.py
"""

import json
import os
import sys
import time

import requests
from dotenv import load_dotenv

load_dotenv(override=True)

CREATOMATE_API_KEY = os.getenv("CREATOMATE_API_KEY", "")
CREATOMATE_API = "https://api.creatomate.com/v1/renders"
POLL_INTERVAL = 5
MAX_WAIT = 300

# GCS URIs — from last hybrid render
DUCKED_VOICEOVER_URI = "gs://romina-content-factory-489121/renders/65646a64-04c5-4a31-a495-cabc745fe9c3/ducked_voiceover.mp3"
VIDEO_URI = "gs://romina-content-factory-489121/footage/65646a64-04c5-4a31-a495-cabc745fe9c3/82e6118f_IMG_7802.MOV"

WIDTH = 720
HEIGHT = 1280
FPS = 24
CLIP_DURATION = 10.0


def get_presigned_url(gcs_uri: str) -> str:
    from app.services.gcs_service import GCSService
    gcs = GCSService()
    return gcs.generate_presigned_url(gcs_uri, expiration_minutes=360)


def submit_render(source: dict) -> dict:
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
    return data[0] if isinstance(data, list) else data


def poll_render(render_id: str) -> dict:
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
            print(f"  RENDER FAILED: {data.get('error_message', 'unknown')}")
            return data
        time.sleep(POLL_INTERVAL)
        elapsed += POLL_INTERVAL
    print(f"  TIMEOUT after {MAX_WAIT}s")
    return {"status": "timeout"}


def make_audio_el(audio_url: str) -> dict:
    return {
        "type": "audio",
        "id": "hybrid_voiceover",
        "track": 5,
        "time": 0,
        "source": audio_url,
        "volume": "100%",
    }


def make_video_els(video_url: str) -> list[dict]:
    return [
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
    ]


def make_text_els() -> list[dict]:
    """5 Whisper-popup-style text elements on track 2."""
    texts = [
        ("Hello world", 0.5, 2.0),
        ("Second phrase", 2.5, 4.0),
        ("Third phrase", 4.5, 6.0),
        ("Fourth phrase", 6.5, 8.0),
        ("Fifth phrase", 8.5, 9.5),
    ]
    elements = []
    for text, t, end in texts:
        elements.append({
            "type": "text",
            "track": 2,
            "time": t,
            "duration": end - t,
            "text": text,
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
        })
    return elements


def make_sticker_els() -> list[dict]:
    """2 image sticker elements on track 4 (using placeholder color)."""
    return [
        {
            "type": "shape",
            "track": 4,
            "time": 1.0,
            "duration": 3.0,
            "width": "15%",
            "height": "15%",
            "x": "85%",
            "y": "20%",
            "shape": "star-4-pointed",
            "fill_color": "#FF3232",
            "animations": [
                {"type": "fade", "time": 0, "duration": 0.3},
                {"type": "fade", "time": "end", "duration": 0.3, "reversed": True},
            ],
        },
        {
            "type": "shape",
            "track": 4,
            "time": 6.0,
            "duration": 3.0,
            "width": "15%",
            "height": "15%",
            "x": "15%",
            "y": "25%",
            "shape": "star-4-pointed",
            "fill_color": "#32FF32",
            "animations": [
                {"type": "fade", "time": 0, "duration": 0.3},
                {"type": "fade", "time": "end", "duration": 0.3, "reversed": True},
            ],
        },
    ]


def build_source(elements: list[dict]) -> dict:
    return {
        "output_format": "mp4",
        "width": WIDTH,
        "height": HEIGHT,
        "frame_rate": FPS,
        "duration": CLIP_DURATION,
        "elements": elements,
    }


TESTS = [
    (
        "Test 1: 2 video + 1 audio (baseline)",
        lambda v, a: make_video_els(v) + [make_audio_el(a)],
    ),
    (
        "Test 2: 2 video + 5 text (track 2) + 1 audio (track 5)",
        lambda v, a: make_video_els(v) + make_text_els() + [make_audio_el(a)],
    ),
    (
        "Test 3: 2 video + 5 text + 2 stickers (track 4) + 1 audio (track 5)",
        lambda v, a: make_video_els(v) + make_text_els() + make_sticker_els() + [make_audio_el(a)],
    ),
]


def main():
    if not CREATOMATE_API_KEY:
        print("ERROR: CREATOMATE_API_KEY not set")
        sys.exit(1)

    print("Generating presigned URLs...")
    video_url = get_presigned_url(VIDEO_URI)
    audio_url = get_presigned_url(DUCKED_VOICEOVER_URI)
    print(f"  Video URL ready ({len(video_url)} chars)")
    print(f"  Audio URL ready ({len(audio_url)} chars)")

    results = {}

    for label, build_fn in TESTS:
        print(f"\n{'=' * 60}")
        print(label)
        print("=" * 60)

        elements = build_fn(video_url, audio_url)
        source = build_source(elements)

        el_types = {}
        for el in elements:
            t = el.get("type", "?")
            el_types[t] = el_types.get(t, 0) + 1
        print(f"  Elements: {el_types} ({len(elements)} total)")

        audio_el = [e for e in elements if e["type"] == "audio"][0]
        print(f"  Audio element: track={audio_el['track']}, id={audio_el['id']}")

        print(f"\nSubmitting to Creatomate...")
        try:
            render = submit_render(source)
            render_id = render.get("id", "unknown")
            print(f"  Render ID: {render_id}")

            result = poll_render(render_id)
            status = result.get("status")
            results[label] = status
            if status == "succeeded":
                print(f"  URL: {result.get('url')}")
            else:
                print(f"  Error: {result.get('error_message', 'N/A')}")
        except Exception as e:
            print(f"  FAILED: {e}")
            results[label] = "error"

    # Summary
    print(f"\n{'=' * 60}")
    print("SUMMARY — listen to each video for voiceover audio")
    print("=" * 60)
    for label, _ in TESTS:
        status = results.get(label, "?")
        marker = "LISTEN" if status == "succeeded" else status.upper()
        print(f"  {label}")
        print(f"    -> {marker}")
    print("\nIf Test 1 has audio but Test 2/3 don't, text/sticker elements suppress audio.")
    print("If all have audio, the issue is in the real payload structure (transitions, etc).")


if __name__ == "__main__":
    main()
