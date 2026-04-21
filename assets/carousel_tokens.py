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
# Dark variant: used when photo bottom region is bright (luma > threshold)
PLASHKA_BG_DARK = (15, 23, 42)       # #0F172A dark navy
PLASHKA_TEXT_DARK = (255, 255, 255)  # white text on dark

# Light variant: used when photo bottom region is dark (luma <= threshold)
PLASHKA_BG_LIGHT = (255, 255, 255)   # white
PLASHKA_TEXT_LIGHT = (15, 23, 42)    # dark navy text on white

# Luminance threshold (0-255): above → bright photo → dark plashka
PLASHKA_LUMA_THRESHOLD = 140

PLASHKA_OPACITY = 210            # 0-255; ~82% opacity
PLASHKA_RADIUS = 24              # border_radius (px)
PLASHKA_PADDING_X = 56
PLASHKA_PADDING_Y = 40
PLASHKA_MARGIN_X = 72            # gap between plashka edge and canvas edge
PLASHKA_MARGIN_BOTTOM = 96       # gap between plashka bottom and canvas bottom
PLASHKA_MARGIN_TOP = 96          # gap between plashka top and canvas top (when position=top)
PLASHKA_MIN_WIDTH = 520          # adaptive width floor (short single-line text)

# Legacy alias — kept for any test that imports TEXT_COLOR directly
TEXT_COLOR = PLASHKA_TEXT_DARK

# ── Subtitle overlay (video slides only) ──
SUBTITLE_BG = (0, 0, 0)             # pure black background
SUBTITLE_OPACITY = 175              # ~69% opacity
SUBTITLE_TEXT_COLOR = (255, 255, 255)
SUBTITLE_FONT_SIZE_TITLE = 64
SUBTITLE_FONT_SIZE_BODY = 40
SUBTITLE_LINE_HEIGHT_TITLE = 1.15
SUBTITLE_LINE_HEIGHT_BODY = 1.30
SUBTITLE_PADDING_X = 48             # horizontal text inset
SUBTITLE_PADDING_Y = 32             # vertical padding inside bar
SUBTITLE_GAP = 14                   # gap between title block and body block

# ── Output ──
PHOTO_JPEG_QUALITY = 92

# ── Video (consumed by Plan 07-03) ──
VIDEO_MAX_DURATION_SEC = 60       # IG carousel video cap
THUMBNAIL_SIZE = (320, 320)
THUMBNAIL_MAX_BYTES = 200_000
