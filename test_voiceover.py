"""
Test: minimal Creatomate payload with audio element.

Goal: find the minimum config where Creatomate plays an audio element.

Variants:
  A) track 2, no duration (storyboard pattern)
  B) track 3, no duration
  C) track 2, with duration
  D) track 3, with duration

Usage:
    source venv/bin/activate
    python3 test_voiceover.py
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

# GCS URIs
DUCKED_VOICEOVER_URI = "gs://romina-content-factory-489121/renders/65646a64-04c5-4a31-a495-cabc745fe9c3/ducked_voiceover.mp3"
VIDEO_URI = "gs://romina-content-factory-489121/footage/65646a64-04c5-4a31-a495-cabc745fe9c3/82e6118f_IMG_7802.MOV"  # smallest video ~10MB

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


def build_variant(video_url: str, audio_url: str, track: int, with_duration: bool) -> dict:
    """Build minimal payload: 1 video + 1 audio element."""
    audio_el = {
        "type": "audio",
        "id": "voiceover",
        "track": track,
        "time": 0,
        "source": audio_url,
        "volume": "100%",
    }
    if with_duration:
        audio_el["duration"] = CLIP_DURATION

    return {
        "output_format": "mp4",
        "width": WIDTH,
        "height": HEIGHT,
        "frame_rate": FPS,
        "duration": CLIP_DURATION,
        "elements": [
            {
                "type": "video",
                "track": 1,
                "source": video_url,
                "width": "100%",
                "height": "100%",
                "fit": "cover",
                "trim_start": 0,
                "trim_duration": CLIP_DURATION,
                "duration": CLIP_DURATION,
            },
            audio_el,
        ],
    }


VARIANTS = [
    ("A", 2, False, "track 2, no duration (storyboard pattern)"),
    ("B", 3, False, "track 3, no duration"),
    ("C", 2, True,  "track 2, with duration"),
    ("D", 3, True,  "track 3, with duration"),
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

    for label, track, with_dur, desc in VARIANTS:
        print(f"\n{'=' * 60}")
        print(f"VARIANT {label}: {desc}")
        print("=" * 60)

        source = build_variant(video_url, audio_url, track, with_dur)
        audio_el = [e for e in source["elements"] if e["type"] == "audio"][0]
        print(f"\nAudio element: {json.dumps(audio_el, indent=2)}")
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
    for label, track, with_dur, desc in VARIANTS:
        status = results.get(label, "?")
        print(f"  {label}) {desc:45s} → {status}")


if __name__ == "__main__":
    main()
