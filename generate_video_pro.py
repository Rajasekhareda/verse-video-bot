"""
generate_video_pro.py
======================
Clean, professional 45-second YouTube Bible-verse video generator.

Pipeline (production mode, default):
    Google Sheet (Telugu / English / explanation) -> render 1920x1080 video
    with elegant fade text animation -> generate thumbnail -> upload to YouTube
    -> mark sheet row as used.

Test mode (no Sheets / no YouTube required):
    python generate_video_pro.py --test
    Renders a bundled bilingual (Telugu + English) example to ./output/verse_video.mp4

Design goals covered:
    - Exactly 45.0s duration, 1920x1080, 16:9
    - Text always inside a validated safe area, well clear of the animated
      neon border traced around the frame edge
    - Never more than 3 lines on screen at once (auto pagination)
    - Any Unicode script (tested with Telugu + English), automatic font fallback
    - Large, bold text; no boxes/panels/backgrounds directly behind it -
      just a soft shadow + thin stroke for readability
    - Animated rainbow-cycling glow border + gently twinkling corner stars
    - Smooth fade-in/fade-out with a subtle upward drift; segments never overlap
    - Modular: layout, timing, animation and rendering are separate functions
    - H.264 + AAC MP4 output, Windows/Linux/macOS compatible
"""

import argparse
import colorsys
import glob
import json
import math
import os
import platform
import random
import re
import sys
import time
import unicodedata
from bisect import bisect_right
from http.client import IncompleteRead
from ssl import SSLError

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

# moviepy 2.x removed the `moviepy.editor` shim (deprecated) in favour of
# top-level imports. Support both so the script runs on either version
# without manual edits.
try:
    from moviepy import AudioFileClip, VideoClip, concatenate_audioclips
    _MOVIEPY_V2 = True
except ImportError:  # moviepy 1.x
    from moviepy.editor import AudioFileClip, VideoClip, concatenate_audioclips
    _MOVIEPY_V2 = False

from google.oauth2 import service_account
from google.oauth2.credentials import Credentials as UserCredentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload

# ================= SHEET LAYOUT =================
# Column A = Telugu verse text
# Column B = English verse text
# Column C = optional brief explanation/note (English or Telugu) - leave blank if none
# Column D = "used" marker, written automatically by this script
# ==================================================

SHEET_ID = os.environ.get("SHEET_ID", "")
SHEET_TAB = os.environ.get("SHEET_TAB", "Sheet1")

MUSIC_DIR = os.environ.get("MUSIC_DIR", "assets/music")

OUTPUT_DIR = "output"
THUMBNAIL_DIR = os.path.join(OUTPUT_DIR, "thumbnails")

# ---------------- VIDEO SPEC ----------------
FPS = 30
VIDEO_SIZE = (1920, 1080)          # 16:9 Full HD
TOTAL_DURATION = 45.0              # exact video length, seconds
MAX_LINES = 3                      # hard cap, enforced + validated

# Safe area: text must never enter these margins
SAFE_MARGIN_X_RATIO = 0.09         # 9% left/right
SAFE_MARGIN_TOP_RATIO = 0.12       # 12% top
SAFE_MARGIN_BOTTOM_RATIO = 0.14    # 14% bottom (slightly larger -> lower-center bias)
VERTICAL_BIAS = 0.60               # 0=top of safe zone, 1=bottom -> lower-center placement

SAFE_LEFT = int(VIDEO_SIZE[0] * SAFE_MARGIN_X_RATIO)
SAFE_RIGHT = int(VIDEO_SIZE[0] * (1 - SAFE_MARGIN_X_RATIO))
SAFE_TOP = int(VIDEO_SIZE[1] * SAFE_MARGIN_TOP_RATIO)
SAFE_BOTTOM = int(VIDEO_SIZE[1] * (1 - SAFE_MARGIN_BOTTOM_RATIO))
SAFE_TEXT_WIDTH = int((SAFE_RIGHT - SAFE_LEFT) * 0.96)

# Animation timing
FADE_IN = 0.55
FADE_OUT = 0.45
RISE_PIXELS = 22.0                 # subtle upward drift during fade-in
MIN_PAGE_DURATION = 2.4
BASE_PAGE_SECONDS = 1.4
PER_WORD_SECONDS = 0.42            # contemplative reading pace

# Typography
TEXT_COLOR = (250, 250, 250, 255)
SHADOW_COLOR = (0, 0, 0, 160)
STROKE_COLOR = (0, 0, 0, 150)

# ---------------- ANIMATED NEON BORDER + CORNER STARS ----------------
# A thin rainbow-cycling glow traces the frame edge, well outside the safe
# text area (SAFE_LEFT/RIGHT/TOP/BOTTOM below), so text never touches it.
RENDER_SCALE = VIDEO_SIZE[0] / 1280
NEON_MARGIN = int(18 * RENDER_SCALE)
NEON_THICK = int(4 * RENDER_SCALE)
NEON_GLOW = int(10 * RENDER_SCALE)
NEON_SPEED = 0.10
NEON_SEGMENTS = 14
STARS_PER_SIDE = 5

# Manual-run controls
PRIVACY_STATUS = os.environ.get("PRIVACY_STATUS", "private").strip().lower()
MUSIC_CHOICE = os.environ.get("MUSIC_CHOICE", "random").strip()
BACKGROUND_THEME = os.environ.get("BACKGROUND_THEME", "random").strip()
INCLUDE_EXPLANATION = os.environ.get("INCLUDE_EXPLANATION", "Auto").strip().lower()
TELUGU_OVERRIDE = os.environ.get("TELUGU_OVERRIDE", "").strip()
ENGLISH_OVERRIDE = os.environ.get("ENGLISH_OVERRIDE", "").strip()
EXPLANATION_OVERRIDE = os.environ.get("EXPLANATION_OVERRIDE", "").strip()

# Optional explicit font overrides (recommended if you know your system's paths)
FONT_PATH_TELUGU_ENV = os.environ.get("FONT_PATH_TELUGU", "").strip()
FONT_PATH_LATIN_ENV = os.environ.get("FONT_PATH_LATIN", "").strip()

# Cinematic, subdued gradients (no flashing colors, still professional)
GRADIENT_PALETTES = {
    "Midnight Purple": ((18, 12, 52), (46, 22, 74)),
    "Ocean Blue":       ((8, 30, 70), (16, 55, 96)),
    "Wine Red":         ((36, 8, 20), (72, 22, 42)),
    "Emerald Teal":     ((8, 38, 36), (14, 66, 60)),
    "Sunset Amber":     ((40, 20, 10), (86, 46, 20)),
    "Indigo Violet":    ((20, 14, 50), (48, 34, 96)),
    "Midnight Slate":   ((14, 20, 34), (26, 38, 60)),
    "Charcoal":         ((16, 16, 20), (30, 30, 36)),
}

BASE_HASHTAGS = ["#BibleVerse", "#DailyVerse", "#Faith", "#God", "#Jesus", "#Scripture"]
TELUGU_HASHTAGS = ["#TeluguChristian", "#YesuKrishtu", "#Telugu"]
ENGLISH_HASHTAGS = ["#Christian", "#Gospel", "#WordOfGod"]

# ---------------- Cross-platform Unicode font candidates ----------------
FONT_CANDIDATES_TELUGU = [p for p in [
    FONT_PATH_TELUGU_ENV,
    "/usr/share/fonts/truetype/noto/NotoSansTelugu-Bold.ttf",
    "/usr/share/fonts/truetype/noto/NotoSansTelugu-Regular.ttf",
    "/usr/share/fonts/truetype/noto/NotoSerifTelugu-Bold.ttf",
    r"C:\Windows\Fonts\Nirmala.ttf",        # Nirmala UI ships with Windows 10/11, covers Telugu
    r"C:\Windows\Fonts\NirmalaB.ttf",
    r"C:\Windows\Fonts\NotoSansTelugu-Regular.ttf",
    "/System/Library/Fonts/Supplemental/NotoSansTelugu-Regular.ttf",
    "/Library/Fonts/NotoSansTelugu-Regular.ttf",
] if p]

FONT_CANDIDATES_LATIN = [p for p in [
    FONT_PATH_LATIN_ENV,
    "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    r"C:\Windows\Fonts\segoeuib.ttf",        # Segoe UI Bold - broad Unicode coverage
    r"C:\Windows\Fonts\arialbd.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
] if p]

# Secondary scan patterns per platform, used only if none of the explicit
# candidates above exist on this machine.
_FONT_SCAN_DIRS = {
    "Linux": ["/usr/share/fonts", "/usr/local/share/fonts", os.path.expanduser("~/.fonts")],
    "Windows": [r"C:\Windows\Fonts"],
    "Darwin": ["/System/Library/Fonts", "/Library/Fonts", os.path.expanduser("~/Library/Fonts")],
}
_FONT_SCAN_PATTERNS = {
    "telugu": ["*Telugu*Bold*.ttf", "*Telugu*.ttf", "*Nirmala*.ttf"],
    "latin": ["*NotoSans*Bold*.ttf", "*DejaVuSans*Bold*.ttf", "*Segoe*.ttf", "*Arial*Bold*.ttf", "*.ttf"],
}


# ===================================================================
# Text helpers (sanitizing, language detection, hashtags)
# ===================================================================

def is_telugu(text):
    return any("ఀ" <= ch <= "౿" for ch in text)


_PUNCT_MAP = {
    "'": "'", "'": "'",
    """: '"', """: '"',
    "\u2013": "-", "\u2014": "-",
    "\u2026": "...",
    "\u2022": "-", "\u25cf": "-", "\u2023": "-",
    "\u2020": "*", "\u2021": "*",
    "\u00a7": "Sec.",
    "\u00b6": "",
    "\u2212": "-",
    "\u00d7": "x",
    "\u00f7": "/",
    "\u00b0": " deg",
    "\u00ab": '"', "\u00bb": '"',
    "\u00a0": " ", "\u2007": " ", "\u2009": " ", "\u200a": " ", "\u2028": " ",
    "\u200b": "", "\u200c": "", "\u200d": "", "\ufeff": "",
}


def sanitize_text(text):
    if text is None:
        return text
    for bad, good in _PUNCT_MAP.items():
        text = text.replace(bad, good)

    out = []
    for ch in text:
        code = ord(ch)
        if ch in "\n\t" or 0x20 <= code <= 0x7e:
            out.append(ch)
        elif 0x0c00 <= code <= 0x0c7f:  # Telugu block - keep as-is
            out.append(ch)
        else:
            decomposed = unicodedata.normalize("NFKD", ch)
            base = "".join(c for c in decomposed if not unicodedata.combining(c) and 0x20 <= ord(c) <= 0x7e)
            out.append(base)  # may drop unsupported glyphs, never boxed
    return "".join(out)


def extract_reference_tag(text):
    match = re.search(r"\(([^()]+)\)\s*$", text.strip())
    if not match:
        return None
    inner = match.group(1).strip()
    first_word = inner.split()[0] if inner.split() else None
    return first_word


def count_words(text):
    if not text:
        return 0
    return len(re.findall(r"\b\w+\b", text))


def generate_hashtags(telugu_text, english_text):
    tags = list(BASE_HASHTAGS)
    tags += TELUGU_HASHTAGS if is_telugu(telugu_text) else []
    tags += ENGLISH_HASHTAGS if english_text else []

    for source in (english_text, telugu_text):
        tag_word = extract_reference_tag(source or "")
        if tag_word:
            cleaned = "".join(
                ch for ch in tag_word if not unicodedata.category(ch).startswith(("P", "Z", "C", "N"))
            )
            book_tag = "#" + cleaned
            if book_tag != "#" and book_tag not in tags:
                tags.append(book_tag)
            break

    return tags[:10]


# ===================================================================
# Font resolution (robust, cross-platform, Unicode-first with fallback)
# ===================================================================

def _scan_for_font(script_key):
    system = platform.system()
    dirs = _FONT_SCAN_DIRS.get(system, [])
    patterns = _FONT_SCAN_PATTERNS[script_key]
    for base_dir in dirs:
        if not os.path.isdir(base_dir):
            continue
        for pattern in patterns:
            matches = glob.glob(os.path.join(base_dir, "**", pattern), recursive=True)
            if matches:
                return matches[0]
    return None


def resolve_font_path(candidates, script_key):
    for path in candidates:
        if path and os.path.isfile(path):
            return path
    scanned = _scan_for_font(script_key)
    if scanned:
        return scanned
    return None


_FONT_CACHE = {}


def load_font(font_path, size):
    key = (font_path, size)
    if key in _FONT_CACHE:
        return _FONT_CACHE[key]
    try:
        if font_path:
            font = ImageFont.truetype(font_path, size)
        else:
            raise OSError("no font path resolved")
    except OSError:
        print(f"WARNING: could not load font at '{font_path}'. Falling back to a basic bitmap "
              f"font (Unicode scripts like Telugu will NOT render correctly). "
              f"Install 'Noto Sans Telugu' / 'Noto Sans' for correct output.")
        font = ImageFont.load_default()
    _FONT_CACHE[key] = font
    return font


# ===================================================================
# Layout: wrapping, pagination, font sizing
# ===================================================================

def _char_wrap_word(draw, word, font, max_width):
    """Hard-break a single word that alone exceeds max_width (rare, e.g. a
    very long compound word with no spaces)."""
    pieces = []
    current = ""
    for ch in word:
        candidate = current + ch
        if draw.textlength(candidate, font=font) <= max_width or not current:
            current = candidate
        else:
            pieces.append(current)
            current = ch
    if current:
        pieces.append(current)
    return pieces or [word]


def wrap_text_to_lines(draw, text, font, max_width):
    """Word-wrap text to fit max_width, with a character-level fallback for
    unbreakable long tokens. Returns a flat list of lines."""
    lines = []
    for para in text.split("\n"):
        if para.strip() == "":
            continue
        words = para.split(" ")
        current = ""
        for word in words:
            if not word:
                continue
            candidate = f"{current} {word}".strip() if current else word
            if draw.textlength(candidate, font=font) <= max_width:
                current = candidate
                continue

            # candidate doesn't fit
            if current:
                lines.append(current)
                current = ""
            if draw.textlength(word, font=font) <= max_width:
                current = word
            else:
                parts = _char_wrap_word(draw, word, font, max_width)
                lines.extend(parts[:-1])
                current = parts[-1]
        if current:
            lines.append(current)
    return lines or [""]


def paginate_lines(lines, max_lines=MAX_LINES):
    return [lines[i:i + max_lines] for i in range(0, len(lines), max_lines)]


def choose_font_size(total_words, video_size):
    """One consistent font size for the whole video. Scales down gently for
    longer verses so they still resolve into a reasonable number of pages."""
    base = int(video_size[1] * 0.10)
    if total_words > 60:
        scale = 0.70
    elif total_words > 40:
        scale = 0.80
    elif total_words > 24:
        scale = 0.90
    else:
        scale = 1.0
    size = int(base * scale)
    min_size = int(video_size[1] * 0.045)
    max_size = int(video_size[1] * 0.115)
    return max(min_size, min(size, max_size))


def _explanation_enabled():
    return INCLUDE_EXPLANATION in ("auto", "yes", "true", "1")


def build_segments(telugu_text, english_text, explanation_text, font_telugu, font_latin, draw):
    """Turn the raw verse fields into an ordered list of pages, each with
    <= MAX_LINES lines and a resolved font."""
    segments = []
    if telugu_text:
        segments.append((telugu_text, font_telugu))
    if english_text:
        segments.append((english_text, font_latin))
    if explanation_text and _explanation_enabled():
        exp_font = font_telugu if is_telugu(explanation_text) else font_latin
        segments.append((explanation_text, exp_font))

    pages = []
    for text, font in segments:
        lines = wrap_text_to_lines(draw, text, font, SAFE_TEXT_WIDTH)
        for chunk in paginate_lines(lines, MAX_LINES):
            pages.append({"lines": chunk, "font": font})
    return pages


# ===================================================================
# Timing: allocate the fixed 45s budget across however many pages exist
# ===================================================================

def _normalize_durations(durations, min_d, total):
    n = len(durations)
    if n == 0:
        return []
    if min_d * n >= total:
        return [total / n] * n

    durations = list(durations)
    for _ in range(12):
        deficit = 0.0
        free_idx = []
        for i, d in enumerate(durations):
            if d < min_d:
                deficit += (min_d - d)
                durations[i] = min_d
            else:
                free_idx.append(i)
        if deficit <= 1e-6 or not free_idx:
            break
        free_total = sum(durations[i] for i in free_idx)
        if free_total <= 0:
            break
        for i in free_idx:
            durations[i] -= deficit * (durations[i] / free_total)

    scale = total / sum(durations)
    return [d * scale for d in durations]


def schedule_durations(pages):
    """Return a list of per-page durations that sum exactly to
    TOTAL_DURATION, weighted by each page's word count."""
    raw = []
    for page in pages:
        word_count = count_words(" ".join(page["lines"]))
        raw.append(BASE_PAGE_SECONDS + word_count * PER_WORD_SECONDS)
    return _normalize_durations(raw, MIN_PAGE_DURATION, TOTAL_DURATION)


def ease_out_cubic(p):
    p = max(0.0, min(1.0, p))
    return 1 - (1 - p) ** 3


def compute_opacity_and_offset(local_t, duration, fade_in=FADE_IN, fade_out=FADE_OUT):
    """Opacity in [0,1] and a vertical pixel offset for the subtle rise-in,
    for a page currently at local_t seconds into its own `duration`-length
    display window. Fades are fully contained within the page's own window,
    so segments never visually overlap."""
    fi = min(fade_in, duration * 0.4)
    fo = min(fade_out, duration * 0.4)

    if local_t < fi and fi > 0:
        p = local_t / fi
        opacity = ease_out_cubic(p)
        offset = (1 - opacity) * RISE_PIXELS
    elif local_t > duration - fo and fo > 0:
        p = (duration - local_t) / fo
        opacity = ease_out_cubic(max(0.0, p))
        offset = 0.0
    else:
        opacity = 1.0
        offset = 0.0
    return opacity, offset


# ===================================================================
# Rendering: background, text layers, validation, compositing
# ===================================================================

def create_background():
    if BACKGROUND_THEME and BACKGROUND_THEME.lower() != "random" and BACKGROUND_THEME in GRADIENT_PALETTES:
        top_color, bottom_color = GRADIENT_PALETTES[BACKGROUND_THEME]
    else:
        top_color, bottom_color = random.choice(list(GRADIENT_PALETTES.values()))

    background = Image.new("RGB", VIDEO_SIZE)
    draw = ImageDraw.Draw(background)
    for y in range(VIDEO_SIZE[1]):
        ratio = y / VIDEO_SIZE[1]
        r = int(top_color[0] * (1 - ratio) + bottom_color[0] * ratio)
        g = int(top_color[1] * (1 - ratio) + bottom_color[1] * ratio)
        b = int(top_color[2] * (1 - ratio) + bottom_color[2] * ratio)
        draw.line([(0, y), (VIDEO_SIZE[0], y)], fill=(r, g, b))

    vignette = Image.new("L", VIDEO_SIZE, 0)
    vdraw = ImageDraw.Draw(vignette)
    vdraw.ellipse([-VIDEO_SIZE[0] * 0.2, -VIDEO_SIZE[1] * 0.2, VIDEO_SIZE[0] * 1.2, VIDEO_SIZE[1] * 1.2], fill=255)
    vignette = vignette.filter(ImageFilter.GaussianBlur(int(140 * VIDEO_SIZE[0] / 1920)))
    dark = Image.new("RGB", VIDEO_SIZE, (0, 0, 0))
    background = Image.composite(background, dark, vignette)
    return background


def _hue_to_rgb(hue):
    r, g, b = colorsys.hsv_to_rgb(hue % 1.0, 1.0, 1.0)
    return (int(r * 255), int(g * 255), int(b * 255))


def _dimmed(color, factor):
    return tuple(int(c * factor) for c in color)


def _border_points(w, h, margin, segments=NEON_SEGMENTS):
    pts = []
    x0, y0, x1, y1 = margin, margin, w - margin, h - margin
    for i in range(segments + 1):
        pts.append((x0 + (x1 - x0) * i / segments, y0))
    for i in range(1, segments + 1):
        pts.append((x1, y0 + (y1 - y0) * i / segments))
    for i in range(1, segments + 1):
        pts.append((x1 - (x1 - x0) * i / segments, y1))
    for i in range(1, segments + 1):
        pts.append((x0, y1 - (y1 - y0) * i / segments))
    return pts


def draw_neon_border(draw, size, t):
    """Slowly hue-cycling glowing border, well outside the text safe area."""
    pts = _border_points(size[0], size[1], NEON_MARGIN)
    n = len(pts) - 1
    offset = t * NEON_SPEED
    for i in range(n):
        color = _hue_to_rgb((i / n) + offset)
        draw.line([pts[i], pts[i + 1]], fill=_dimmed(color, 0.4), width=NEON_GLOW)
        draw.line([pts[i], pts[i + 1]], fill=color, width=NEON_THICK)


def make_stars(size):
    """Fixed positions for gently twinkling corner/edge stars along the border."""
    w, h = size
    outer = max(NEON_MARGIN - 10, 6)
    edge_margin = int(40 * RENDER_SCALE)
    stars = []

    def add(xs, ys):
        for x, y in zip(xs, ys):
            stars.append({
                "x": x, "y": y,
                "phase": random.uniform(0, math.tau),
                "speed": random.uniform(1.2, 2.4),
            })

    xs = np.linspace(edge_margin, w - edge_margin, STARS_PER_SIDE)
    add(xs, [outer] * STARS_PER_SIDE)
    add(xs, [h - outer] * STARS_PER_SIDE)
    ys = np.linspace(edge_margin, h - edge_margin, STARS_PER_SIDE)
    add([outer] * STARS_PER_SIDE, ys)
    add([w - outer] * STARS_PER_SIDE, ys)
    return stars


def draw_stars(draw, stars, t):
    for s in stars:
        brightness = 0.4 + 0.6 * (0.5 + 0.5 * math.sin(s["speed"] * t + s["phase"]))
        size = (4 + 5 * brightness) * RENDER_SCALE
        shade = int(255 * brightness)
        color = (shade, shade, shade)
        x, y = s["x"], s["y"]
        line_w = max(1, int(2 * RENDER_SCALE))
        draw.line([x - size, y, x + size, y], fill=color, width=line_w)
        draw.line([x, y - size, x, y + size], fill=color, width=line_w)
        draw.ellipse([x - 2, y - 2, x + 2, y + 2], fill=color)


def compute_block_top(block_height, safe_top=SAFE_TOP, safe_bottom=SAFE_BOTTOM, bias=VERTICAL_BIAS):
    zone_height = safe_bottom - safe_top
    desired_center = safe_top + zone_height * bias
    top = desired_center - block_height / 2
    return max(safe_top, min(top, safe_bottom - block_height))


def render_page_layer(lines, font):
    """Render one page's text (<=MAX_LINES lines) to a full-frame RGBA numpy
    array at full opacity. Returns (array, block_info) where block_info is
    used for the safe-area validation step."""
    line_height = int(font.size * 1.45)
    block_height = line_height * len(lines)
    top = compute_block_top(block_height)

    shadow_layer = Image.new("RGBA", VIDEO_SIZE, (0, 0, 0, 0))
    main_layer = Image.new("RGBA", VIDEO_SIZE, (0, 0, 0, 0))
    sdraw = ImageDraw.Draw(shadow_layer)
    mdraw = ImageDraw.Draw(main_layer)
    stroke_w = max(1, font.size // 32)

    max_line_width = 0.0
    for i, line in enumerate(lines):
        if not line:
            continue
        w = mdraw.textlength(line, font=font)
        max_line_width = max(max_line_width, w)
        x = (VIDEO_SIZE[0] - w) / 2
        y = top + i * line_height
        sdraw.text((x, y + font.size * 0.06), line, font=font, fill=SHADOW_COLOR)
        mdraw.text((x, y), line, font=font, fill=TEXT_COLOR, stroke_width=stroke_w, stroke_fill=STROKE_COLOR)

    shadow_layer = shadow_layer.filter(ImageFilter.GaussianBlur(radius=max(2, font.size * 0.045)))
    combined = Image.alpha_composite(shadow_layer, main_layer)
    block_info = {"top": top, "height": block_height, "max_width": max_line_width}
    return np.array(combined), block_info


def validate_page(lines, block_info):
    """Fail fast (before wasting time rendering 45s of video) if a page
    would ever cross the safe area or exceed the line limit."""
    if len(lines) > MAX_LINES:
        raise ValueError(f"Page exceeds max lines ({len(lines)} > {MAX_LINES}): {lines!r}")
    if block_info["max_width"] > SAFE_TEXT_WIDTH + 1:
        raise ValueError(
            f"Line width {block_info['max_width']:.0f}px exceeds safe width {SAFE_TEXT_WIDTH}px: {lines!r}"
        )
    if block_info["top"] < SAFE_TOP - 1 or block_info["top"] + block_info["height"] > SAFE_BOTTOM + 1:
        raise ValueError(f"Text block falls outside the vertical safe area: {lines!r}")


def apply_opacity_and_offset(layer_arr, opacity, offset_y):
    if opacity <= 0:
        return None
    out = layer_arr.copy()
    out[..., 3] = (out[..., 3].astype(np.float32) * opacity).astype(np.uint8)
    oy = int(round(offset_y))
    if oy > 0:
        shifted = np.zeros_like(out)
        if oy < out.shape[0]:
            shifted[oy:, :, :] = out[:-oy, :, :]
        out = shifted
    return out


def composite_rgba_over_rgb(bg_rgb_arr, layer_rgba_arr):
    if layer_rgba_arr is None:
        return bg_rgb_arr
    alpha = layer_rgba_arr[..., 3:4].astype(np.float32) / 255.0
    fg = layer_rgba_arr[..., :3].astype(np.float32)
    bg = bg_rgb_arr.astype(np.float32)
    out = fg * alpha + bg * (1 - alpha)
    return out.astype(np.uint8)


# ===================================================================
# Audio
# ===================================================================

def _compat(obj, new_name, old_name, *args, **kwargs):
    """Call whichever of moviepy's 1.x/2.x method names exists on obj."""
    if hasattr(obj, new_name):
        return getattr(obj, new_name)(*args, **kwargs)
    return getattr(obj, old_name)(*args, **kwargs)


def pick_music_file():
    if not os.path.isdir(MUSIC_DIR):
        raise FileNotFoundError(f"Music directory '{MUSIC_DIR}' does not exist")
    music_files = sorted(f for f in os.listdir(MUSIC_DIR) if f.lower().endswith(".mp3"))
    if not music_files:
        raise FileNotFoundError(f"No .mp3 files found in {MUSIC_DIR}")
    if MUSIC_CHOICE and MUSIC_CHOICE.lower() != "random":
        for f in music_files:
            if f.lower() == MUSIC_CHOICE.lower():
                return os.path.join(MUSIC_DIR, f)
        print(f"Warning: '{MUSIC_CHOICE}' not found, picking randomly instead.")
    return os.path.join(MUSIC_DIR, random.choice(music_files))


def prepare_audio(music_path, duration):
    """Return an audio clip of exactly `duration` seconds, looping the
    source track if it's shorter than needed."""
    src = AudioFileClip(music_path)
    start_offset = min(5.0, max(0.0, src.duration * 0.05))
    available = src.duration - start_offset
    if available <= 0:
        start_offset, available = 0.0, src.duration

    if available >= duration:
        audio = _compat(src, "subclipped", "subclip", start_offset, start_offset + duration)
    else:
        clips = [_compat(src, "subclipped", "subclip", start_offset, src.duration)]
        remaining = duration - available
        while remaining > 0.01:
            take = min(src.duration, remaining)
            clips.append(_compat(src, "subclipped", "subclip", 0, take))
            remaining -= take
        audio = concatenate_audioclips(clips)

    return _compat(audio, "with_volume_scaled", "volumex", 0.28)


# ===================================================================
# Video builder
# ===================================================================

def build_video(telugu_text, english_text, explanation_text):
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    os.makedirs(THUMBNAIL_DIR, exist_ok=True)

    font_telugu_path = resolve_font_path(FONT_CANDIDATES_TELUGU, "telugu")
    font_latin_path = resolve_font_path(FONT_CANDIDATES_LATIN, "latin")
    if not font_telugu_path and (telugu_text or (explanation_text and is_telugu(explanation_text))):
        print("WARNING: no Unicode Telugu-capable font found on this system. "
              "Install 'Noto Sans Telugu' for correct rendering.")
    if not font_latin_path:
        print("WARNING: no dedicated Latin font found; using a system default.")

    total_words = count_words(telugu_text) + count_words(english_text)
    if explanation_text and _explanation_enabled():
        total_words += count_words(explanation_text)
    font_size = choose_font_size(total_words, VIDEO_SIZE)

    font_telugu = load_font(font_telugu_path, font_size)
    font_latin = load_font(font_latin_path, font_size)

    dummy_draw = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    raw_pages = build_segments(telugu_text, english_text, explanation_text, font_telugu, font_latin, dummy_draw)
    if not raw_pages:
        raise ValueError("No text content provided to render (need Telugu and/or English text).")

    pages = []
    for p in raw_pages:
        layer_arr, block_info = render_page_layer(p["lines"], p["font"])
        validate_page(p["lines"], block_info)  # fail fast on any safe-area/line-count issue
        pages.append({"lines": p["lines"], "layer": layer_arr})

    durations = schedule_durations(pages)
    starts, acc = [], 0.0
    for d in durations:
        starts.append(acc)
        acc += d

    print(f"Prepared {len(pages)} text page(s) across {TOTAL_DURATION:.1f}s:")
    for i, (p, d, s) in enumerate(zip(pages, durations, starts)):
        preview = " / ".join(p["lines"])
        print(f"  Page {i + 1}: {s:5.2f}s -> {s + d:5.2f}s ({d:4.2f}s)  {preview}")

    bg_img = create_background()
    stars = make_stars(VIDEO_SIZE)

    def make_frame(t):
        t = min(t, TOTAL_DURATION - 1e-3)
        idx = max(0, min(bisect_right(starts, t) - 1, len(pages) - 1))
        local_t = t - starts[idx]
        opacity, offset = compute_opacity_and_offset(local_t, durations[idx])
        layer = apply_opacity_and_offset(pages[idx]["layer"], opacity, offset)

        # Animated border + stars are redrawn each frame on a fresh copy of
        # the static gradient background, then the (also fresh) text layer
        # is composited on top.
        frame_img = bg_img.copy()
        frame_draw = ImageDraw.Draw(frame_img)
        draw_neon_border(frame_draw, VIDEO_SIZE, t)
        draw_stars(frame_draw, stars, t)
        bg_arr = np.array(frame_img)

        return composite_rgba_over_rgb(bg_arr, layer)

    clip = VideoClip(make_frame, duration=TOTAL_DURATION)
    clip = _compat(clip, "with_fps", "set_fps", FPS)

    try:
        music_path = pick_music_file()
        audio = prepare_audio(music_path, TOTAL_DURATION)
        clip = _compat(clip, "with_audio", "set_audio", audio)
    except FileNotFoundError as e:
        print(f"No background music available ({e}); rendering without an audio track.")

    output_path = os.path.join(OUTPUT_DIR, "verse_video.mp4")
    clip.write_videofile(
        output_path,
        fps=FPS,
        codec="libx264",
        audio_codec="aac",
        bitrate="10M",
        preset="medium",
        threads=4,
        ffmpeg_params=["-pix_fmt", "yuv420p"],
    )

    thumbnail_path = generate_thumbnail(telugu_text, english_text, font_telugu_path, font_latin_path)
    return output_path, thumbnail_path


def generate_thumbnail(telugu_text, english_text, font_telugu_path, font_latin_path):
    """Clean YouTube thumbnail: same typography language as the video, no
    boxes/panels behind the verse text."""
    thumb_size = (1280, 720)
    bg_img = create_background().resize(thumb_size, Image.LANCZOS)
    thumb_draw_for_border = ImageDraw.Draw(bg_img)
    draw_neon_border(thumb_draw_for_border, thumb_size, t=0)
    draw_stars(thumb_draw_for_border, make_stars(thumb_size), t=0)

    display_text = telugu_text or english_text or "Daily Bible Verse"
    use_telugu_font = is_telugu(display_text)
    font_path = font_telugu_path if use_telugu_font else font_latin_path
    font_size = int(thumb_size[1] * 0.115)
    font = load_font(font_path, font_size)

    dummy_draw = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    safe_w = int(thumb_size[0] * 0.86)
    lines = wrap_text_to_lines(dummy_draw, display_text, font, safe_w)[:2]  # punchy: max 2 lines

    line_height = int(font_size * 1.4)
    block_height = line_height * len(lines)
    top = (thumb_size[1] - block_height) // 2

    draw = ImageDraw.Draw(bg_img)
    stroke_w = max(1, font_size // 30)
    for i, line in enumerate(lines):
        w = draw.textlength(line, font=font)
        x = (thumb_size[0] - w) / 2
        y = top + i * line_height
        draw.text((x, y), line, font=font, fill=(255, 255, 255), stroke_width=stroke_w, stroke_fill=(0, 0, 0))

    # small, unobtrusive label - no background box, just bold accent text
    label_font = load_font(font_latin_path, int(font_size * 0.32))
    label = "DAILY VERSE"
    draw.text((36, thumb_size[1] - int(font_size * 0.32) - 36), label, font=label_font,
               fill=(235, 200, 120), stroke_width=2, stroke_fill=(0, 0, 0))

    timestamp = int(time.time())
    thumbnail_path = os.path.join(THUMBNAIL_DIR, f"thumbnail_{timestamp}.jpg")
    bg_img.convert("RGB").save(thumbnail_path, "JPEG", quality=95)
    return thumbnail_path


# ===================================================================
# Google Sheets / YouTube integration (unchanged workflow)
# ===================================================================

def get_user_credentials():
    return UserCredentials(
        None,
        refresh_token=os.environ["YT_REFRESH_TOKEN"],
        client_id=os.environ["YT_CLIENT_ID"],
        client_secret=os.environ["YT_CLIENT_SECRET"],
        token_uri="https://oauth2.googleapis.com/token",
        scopes=[
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/youtube.upload",
        ],
    )


def get_sheets_service():
    sa_info = json.loads(os.environ["GCP_SERVICE_ACCOUNT_JSON"])
    sa_creds = service_account.Credentials.from_service_account_info(
        sa_info, scopes=["https://www.googleapis.com/auth/spreadsheets"]
    )
    return build("sheets", "v4", credentials=sa_creds)


def get_youtube_service(creds):
    return build("youtube", "v3", credentials=creds)


def call_with_retries(func, max_retries=5, base_delay=5):
    RETRYABLE_HTTP_STATUSES = {429, 500, 502, 503, 504}
    for attempt in range(1, max_retries + 1):
        try:
            return func()
        except HttpError as e:
            status = e.resp.status if getattr(e, "resp", None) else None
            if status not in RETRYABLE_HTTP_STATUSES or attempt == max_retries:
                raise
            delay = base_delay * (2 ** (attempt - 1))
            print(f"Google API returned {status} - retrying in {delay}s (attempt {attempt}/{max_retries})...")
            time.sleep(delay)
        except (SSLError, ConnectionError, IncompleteRead, TimeoutError) as e:
            if attempt == max_retries:
                raise
            delay = base_delay * (2 ** (attempt - 1))
            print(f"Network hiccup ({e}) - retrying in {delay}s (attempt {attempt}/{max_retries})...")
            time.sleep(delay)


def fetch_next_row(service):
    range_ = f"{SHEET_TAB}!A2:D"
    result = call_with_retries(
        lambda: service.spreadsheets().values().get(spreadsheetId=SHEET_ID, range=range_).execute()
    )
    rows = result.get("values", [])
    available = []
    for i, row in enumerate(rows):
        telugu = row[0] if len(row) > 0 else ""
        english = row[1] if len(row) > 1 else ""
        explanation = row[2] if len(row) > 2 else ""
        used = row[3] if len(row) > 3 else ""
        if (telugu or english) and used.strip().lower() != "used":
            available.append((i + 2, telugu.strip(), english.strip(), explanation.strip()))

    print(f"{len(available)} unused row(s) available out of {len(rows)} total sheet row(s).")
    if not available:
        return None, None, None, None
    return random.choice(available)


def mark_row_used(service, row_number):
    call_with_retries(lambda: service.spreadsheets().values().update(
        spreadsheetId=SHEET_ID,
        range=f"{SHEET_TAB}!D{row_number}",
        valueInputOption="RAW",
        body={"values": [["used"]]},
    ).execute())
    print(f"Marked row {row_number}, Column D as 'used'.")


def upload_to_youtube(youtube, video_path, telugu_text, english_text):
    base_text = english_text or telugu_text
    title_source = re.sub(r"\([^()]*\)\s*$", "", base_text).strip()
    title = (title_source[:80] + "...") if len(title_source) > 80 else title_source
    if not title:
        title = "Daily Bible Verse"

    hashtags = generate_hashtags(telugu_text, english_text)
    description = f"{telugu_text}\n\n{english_text}\n\n" + " ".join(hashtags)
    privacy = PRIVACY_STATUS if PRIVACY_STATUS in ("private", "public", "unlisted") else "private"

    body = {
        "snippet": {
            "title": title[:100],
            "description": description,
            "categoryId": "22",
            "tags": [t.lstrip("#") for t in hashtags],
        },
        "status": {"privacyStatus": privacy},
    }

    media = MediaFileUpload(video_path, chunksize=-1, resumable=True, mimetype="video/mp4")
    request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)
    response = None
    while response is None:
        status, response = call_with_retries(lambda: request.next_chunk())
        if status:
            print(f"Upload progress: {int(status.progress() * 100)}%")
    print(f"Uploaded video ID: {response['id']} (privacy: {privacy})")
    return response["id"]


# ===================================================================
# Entry points
# ===================================================================

def run_test_render():
    """Renders a bundled bilingual (Telugu + English) example locally.
    No Google Sheets / YouTube credentials required."""
    print("Running local test render (no Sheets, no YouTube upload)...")
    telugu_text = sanitize_text(
        "దేవుడు లోకమును ఎంతో ప్రేమించెను, కాబట్టి తన అద్వితీయకుమారుని అనుగ్రహించెను; "
        "ఆయనయందు విశ్వాసముంచు ప్రతివాడును నశింపక నిత్యజీవము పొందునట్లు ఆయనను అనుగ్రహించెను. (యోహాను 3:16)"
    )
    english_text = sanitize_text(
        "For God so loved the world that he gave his one and only Son, that whoever "
        "believes in him shall not perish but have eternal life. (John 3:16)"
    )
    video_path, thumbnail_path = build_video(telugu_text, english_text, "")
    print(f"Test video created at:     {video_path}")
    print(f"Test thumbnail created at: {thumbnail_path}")


def run_production():
    creds = get_user_credentials()
    sheets_service = get_sheets_service()

    row_number = None
    if TELUGU_OVERRIDE or ENGLISH_OVERRIDE:
        telugu_text, english_text, explanation_text = TELUGU_OVERRIDE, ENGLISH_OVERRIDE, EXPLANATION_OVERRIDE
        print(f"Using override text - Telugu: {telugu_text!r}  English: {english_text!r}  Explanation: {explanation_text!r}")
    else:
        row_number, telugu_text, english_text, explanation_text = fetch_next_row(sheets_service)
        if not telugu_text and not english_text:
            print("No unused rows found in the sheet. Exiting.")
            sys.exit(0)
        print(f"Selected row {row_number} - Telugu: {telugu_text!r}  English: {english_text!r}  Explanation: {explanation_text!r}")

    telugu_text = sanitize_text(telugu_text)
    english_text = sanitize_text(english_text)
    explanation_text = sanitize_text(explanation_text)

    video_path, thumbnail_path = build_video(telugu_text, english_text, explanation_text)
    print(f"Generated video: {video_path}")
    print(f"Generated thumbnail: {thumbnail_path}")

    youtube_service = get_youtube_service(creds)
    upload_to_youtube(youtube_service, video_path, telugu_text, english_text)

    if row_number is not None:
        mark_row_used(sheets_service, row_number)

    print("Done.")


def main():
    parser = argparse.ArgumentParser(description="Bible verse video generator")
    parser.add_argument(
        "--test", action="store_true",
        help="Render a local bilingual test video and skip Google Sheets / YouTube entirely.",
    )
    args = parser.parse_args()

    if args.test:
        run_test_render()
    else:
        run_production()


if __name__ == "__main__":
    main()