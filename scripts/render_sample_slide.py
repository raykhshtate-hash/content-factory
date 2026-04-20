"""Render one carousel slide locally to disk for visual inspection.

Usage:
    python3 scripts/render_sample_slide.py \\
        --source tests/fixtures/sample.jpg \\
        --title "Тест заголовка" \\
        --body "Краткое описание слайда" \\
        --output /tmp/slide.jpg

Intended for local dev / UAT Gate 1 preview without requiring the bot running.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

# Allow running as `python scripts/render_sample_slide.py` from repo root.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from app.services.carousel_service import render_photo_slide  # noqa: E402


async def _main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--source", required=True, type=Path,
                    help="Path to source image (JPG/PNG/HEIC)")
    ap.add_argument("--title", required=True,
                    help="Title text (Russian supported)")
    ap.add_argument("--body", default=None,
                    help="Optional body/subtitle text")
    ap.add_argument("--output", type=Path, default=Path("/tmp/slide.jpg"),
                    help="Output JPEG path (default /tmp/slide.jpg)")
    args = ap.parse_args()

    out = await render_photo_slide(
        args.source, args.title, args.body, args.output
    )
    print(f"Rendered -> {out}")


if __name__ == "__main__":
    asyncio.run(_main())
