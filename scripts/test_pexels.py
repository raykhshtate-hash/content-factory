"""
Test Pexels service: python -m scripts.test_pexels "skincare cream"
"""

import asyncio
import json
import sys

from app.services.pexels_service import search_broll, search_photo, search_video


async def main():
    keyword = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else "skincare routine"
    print(f"\n  Keyword: {keyword}\n")

    print("--- Video search ---")
    video = await search_video(keyword)
    if video:
        print(json.dumps(video, indent=2))
    else:
        print("No video found")

    print("\n--- Photo search ---")
    photo = await search_photo(keyword)
    if photo:
        print(json.dumps(photo, indent=2))
    else:
        print("No photo found")

    print("\n--- B-roll search (video-first fallback) ---")
    broll = await search_broll(keyword)
    if broll:
        print(json.dumps(broll, indent=2))
    else:
        print("No b-roll found")


if __name__ == "__main__":
    asyncio.run(main())
