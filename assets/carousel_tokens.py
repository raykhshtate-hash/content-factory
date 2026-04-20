"""Design tokens for Instagram Carousel slide rendering.

Single source of truth for all visual constants. If Romina requests
tweaks during UAT Gate 1, edit this file ONLY — render code reads
from here.
"""
from pathlib import Path

# ── Canvas ──
CANVAS_W = 1080
CANVAS_H = 1350  # 4:5 aspect, IG carousel spec

# ── Fonts ──
_ASSETS_DIR = Path(__file__).parent
FONT_BOLD_PATH = _ASSETS_DIR / "fonts" / "Inter-Bold.ttf"
FONT_REGULAR_PATH = _ASSETS_DIR / "fonts" / "Inter-Regular.ttf"
FONT_SIZE_TITLE = 68
FONT_SIZE_BODY = 40
LINE_HEIGHT_TITLE = 1.12
LINE_HEIGHT_BODY = 1.30

# ── Plashka (semi-transparent overlay) ──
PLASHKA_BG_COLOR = (0, 0, 0)     # RGB — alpha added via PLASHKA_OPACITY
PLASHKA_OPACITY = 190            # 0-255; ~75% opacity
PLASHKA_RADIUS = 24              # border_radius (px)
PLASHKA_PADDING_X = 56
PLASHKA_PADDING_Y = 40
PLASHKA_MARGIN_X = 72            # gap between plashka edge and canvas edge
PLASHKA_MARGIN_BOTTOM = 96       # gap between plashka bottom and canvas bottom

# ── Text ──
TEXT_COLOR = (255, 255, 255)

# ── Output ──
PHOTO_JPEG_QUALITY = 92

# ── Video (consumed by Plan 07-03) ──
VIDEO_MAX_DURATION_SEC = 60       # IG carousel video cap
THUMBNAIL_SIZE = (320, 320)
THUMBNAIL_MAX_BYTES = 200_000
