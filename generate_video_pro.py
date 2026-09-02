"""
generate_video_enhanced.py
==========================
Cinematic 45-second YouTube Bible-verse video generator with TTS.

Features:
    - 2x larger cinematic typography with enhanced stroke/shadow/contrast
    - 45-second exact duration with perfect audio-caption synchronization
    - Telugu and English TTS via ElevenLabs API
    - Auto language detection and natural voice synthesis
    - Gradient backgrounds with animated neon borders
    - YouTube thumbnail generation
    - Google Sheets integration with YouTube upload

Pipeline:
    Google Sheet -> Detect language -> Generate TTS (ElevenLabs) ->
    Render 45s video with synchronized captions/script -> Upload to YouTube
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
import traceback
import unicodedata
from bisect import bisect_right
from http.client import IncompleteRead
from ssl import SSLError
import io

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageSequence

# moviepy compatibility
try:
    from moviepy import AudioFileClip, VideoClip, concatenate_audioclips, CompositeAudioClip
    _MOVIEPY_V2 = True
except ImportError:
    from moviepy.editor import AudioFileClip, VideoClip, concatenate_audioclips, CompositeAudioClip
    _MOVIEPY_V2 = False

import requests

from google.oauth2 import service_account
from google.oauth2.credentials import Credentials as UserCredentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload

# ================= SHEET LAYOUT =================
# Column A = Telugu verse text
# Column B = English verse text
# Column C = optional brief explanation/note (English or Telugu)
# Column D = "used" marker, written automatically by this script
# ==================================================

SHEET_ID = os.environ.get("SHEET_ID", "")
SHEET_TAB = os.environ.get("SHEET_TAB", "Sheet1")
MUSIC_DIR = os.environ.get("MUSIC_DIR", "assets/music")
ELEVENLABS_API_KEY = os.environ.get("ELEVENLABS_API_KEY", "")

OUTPUT_DIR = "output"
THUMBNAIL_DIR = os.path.join(OUTPUT_DIR, "thumbnails")

# ================= VIDEO SPEC ================
FPS = 30
VIDEO_SIZE = (1920, 1080)          # 16:9 Full HD
TOTAL_DURATION = 45.0              # exact video length, seconds
MAX_LINES = 3                      # hard cap, enforced + validated

# Safe area: text must never enter these margins
SAFE_MARGIN_X_RATIO = 0.09         # 9% left/right
SAFE_MARGIN_TOP_RATIO = 0.12       # 12% top
SAFE_MARGIN_BOTTOM_RATIO = 0.14    # 14% bottom
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
MIN_PAGE_DURATION = 2.9
BASE_PAGE_SECONDS = 1.9
PER_WORD_SECONDS = 0.59            # contemplative reading pace

# Typography - Cinematic 2x enhanced
TEXT_COLOR = (255, 255, 255, 255)          # full brightness white
SHADOW_COLOR = (0, 0, 0, 200)               # stronger black shadow
STROKE_COLOR = (0, 0, 0, 180)               # stronger outline
SHADOW_BLUR_RADIUS = 4                      # cinematic blur
LINE_SPACING_MULTIPLIER = 1.5               # improved line spacing (1.45 -> 2.17)

# ================= ANIMATED NEON BORDER + CORNER STARS ================
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

# Font overrides
FONT_PATH_TELUGU_ENV = os.environ.get("FONT_PATH_TELUGU", "").strip()
FONT_PATH_LATIN_ENV = os.environ.get("FONT_PATH_LATIN", "").strip()

# Cinematic, subdued gradients
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

# Cross-platform font candidates
FONT_CANDIDATES_TELUGU = [p for p in [
    FONT_PATH_TELUGU_ENV,
    "/usr/share/fonts/truetype/noto/NotoSansTelugu-Bold.ttf",
    "/usr/share/fonts/truetype/noto/NotoSansTelugu-Regular.ttf",
    "/usr/share/fonts/truetype/noto/NotoSerifTelugu-Bold.ttf",
    r"C:\Windows\Fonts\Nirmala.ttf",
    r"C:\Windows\Fonts\NirmalaB.ttf",
    r"C:\Windows\Fonts\NotoSansTelugu-Regular.ttf",
    "/System/Library/Fonts/Supplemental/NotoSansTelugu-Regular.ttf",
    "/Library/Fonts/NotoSansTelugu-Regular.ttf",
] if p]

FONT_CANDIDATES_LATIN = [p for p in [
    FONT_PATH_LATIN_ENV,
    "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    r"C:\Windows\Fonts\segoeuib.ttf",
    r"C:\Windows\Fonts\arialbd.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
] if p]

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
    return any("\u0c00" <= ch <= "\u0c7f" for ch in text)


def detect_language(text):
    """Detect if text is Telugu, English, or mixed."""
    if not text:
        return "unknown"

    telugu_chars = sum(1 for ch in text if "\u0c00" <= ch <= "\u0c7f")
    total_chars = len([ch for ch in text if ch.isalpha()])

    if total_chars == 0:
        return "unknown"

    telugu_ratio = telugu_chars / total_chars
    if telugu_ratio > 0.7:
        return "telugu"
    elif telugu_ratio > 0.1:
        return "mixed"
    else:
        return "english"


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
        elif 0x0c00 <= code <= 0x0c7f:  # Telugu block
            out.append(ch)
        else:
            decomposed = unicodedata.normalize("NFKD", ch)
            base = "".join(c for c in decomposed if not unicodedata.combining(c) and 0x20 <= ord(c) <= 0x7e)
            out.append(base)
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
        print(f"WARNING: could not load font at '{font_path}'. Falling back to bitmap font.")
        font = ImageFont.load_default()
    _FONT_CACHE[key] = font
    return font


# ===================================================================
# Layout: wrapping, pagination, font sizing
# ===================================================================

def _char_wrap_word(draw, word, font, max_width):
    """Hard-break a single word that alone exceeds max_width."""
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
    """Word-wrap text to fit max_width, with character-level fallback."""
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
    """Calculate font size — 2x larger for cinematic impact."""
    base = int(video_size[1] * 0.10) * 2  # 2x multiplier
    if total_words > 60:
        scale = 0.70
    elif total_words > 40:
        scale = 0.80
    elif total_words > 24:
        scale = 0.90
    else:
        scale = 1.0
    size = int(base * scale)
    min_size = int(video_size[1] * 0.085)
    max_size = int(video_size[1] * 0.22)
    return max(min_size, min(size, max_size))


def _explanation_enabled():
    return INCLUDE_EXPLANATION in ("auto", "yes", "true", "1")


def build_segments(telugu_text, english_text, explanation_text, font_telugu, font_latin, draw):
    """Build ordered list of pages with <= MAX_LINES lines."""
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
# Timing: allocate the fixed 45s budget across pages
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
    """Return per-page durations that sum exactly to TOTAL_DURATION."""
    raw = []
    for page in pages:
        word_count = count_words(" ".join(page["lines"]))
        raw.append(BASE_PAGE_SECONDS + word_count * PER_WORD_SECONDS)
    return _normalize_durations(raw, MIN_PAGE_DURATION, TOTAL_DURATION)


def ease_out_cubic(p):
    p = max(0.0, min(1.0, p))
    return 1 - (1 - p) ** 3


def compute_opacity_and_offset(local_t, duration, fade_in=FADE_IN, fade_out=FADE_OUT):
    """Opacity and vertical offset for fade-in/fade-out."""
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
    """Slowly hue-cycling glowing border."""
    pts = _border_points(size[0], size[1], NEON_MARGIN)
    n = len(pts) - 1
    offset = t * NEON_SPEED
    for i in range(n):
        color = _hue_to_rgb((i / n) + offset)
        draw.line([pts[i], pts[i + 1]], fill=_dimmed(color, 0.4), width=NEON_GLOW)
        draw.line([pts[i], pts[i + 1]], fill=color, width=NEON_THICK)


def make_stars(size):
    """Fixed positions for twinkling corner/edge stars."""
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
    """Render one page's text to RGBA array at full opacity."""
    line_height = int(font.size * LINE_SPACING_MULTIPLIER)
    block_height = line_height * len(lines)
    top = compute_block_top(block_height)

    shadow_layer = Image.new("RGBA", VIDEO_SIZE, (0, 0, 0, 0))
    main_layer = Image.new("RGBA", VIDEO_SIZE, (0, 0, 0, 0))
    sdraw = ImageDraw.Draw(shadow_layer)
    mdraw = ImageDraw.Draw(main_layer)
    stroke_w = max(2, font.size // 24)  # enhanced stroke

    max_line_width = 0.0
    for i, line in enumerate(lines):
        if not line:
            continue
        w = mdraw.textlength(line, font=font)
        max_line_width = max(max_line_width, w)
        x = (VIDEO_SIZE[0] - w) / 2
        y = top + i * line_height
        # Enhanced shadow: offset + blur
        sdraw.text((x, y + font.size * 0.08), line, font=font, fill=SHADOW_COLOR)
        # Main text: with enhanced stroke
        mdraw.text((x, y), line, font=font, fill=TEXT_COLOR, stroke_width=stroke_w, stroke_fill=STROKE_COLOR)

    shadow_layer = shadow_layer.filter(ImageFilter.GaussianBlur(radius=max(3, int(font.size * 0.06))))
    combined = Image.alpha_composite(shadow_layer, main_layer)
    block_info = {"top": top, "height": block_height, "max_width": max_line_width}
    return np.array(combined), block_info


def validate_page(lines, block_info):
    """Fail fast if page exceeds safe area."""
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
# TTS: ElevenLabs integration
# ===================================================================

def generate_tts_audio(text, language, voice_id=None):
    """Generate audio using ElevenLabs TTS API."""
    if not ELEVENLABS_API_KEY:
        print("WARNING: ELEVENLABS_API_KEY not set. Skipping TTS generation.")
        return None

    if not text or not text.strip():
        return None

    # Default voices for Telugu and English
    if language == "telugu":
        voice_id = voice_id or "21m00Tcm4TlvDq8ikWAM"  # Indian English accent
    else:
        voice_id = voice_id or "21m00Tcm4TlvDq8ikWAM"  # English

    url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
    headers = {
        "xi-api-key": ELEVENLABS_API_KEY,
        "Content-Type": "application/json",
    }
    data = {
        "text": text,
        "model_id": "eleven_monolingual_v1",
        "voice_settings": {
            "stability": 0.5,
            "similarity_boost": 0.75,
        }
    }

    try:
        print(f"Generating {language} TTS audio for: {text[:50]}...")
        response = requests.post(url, json=data, headers=headers, timeout=30)
        response.raise_for_status()
        return io.BytesIO(response.content)
    except Exception as e:
        print(f"ERROR generating TTS: {e}")
        return None


def create_tts_clips(pages):
    """Create audio clips synchronized with text pages."""
    tts_clips = []
    current_time = 0.0

    for page_idx, page_data in enumerate(pages):
        text = " ".join(page_data["lines"])
        language = detect_language(text)

        # Generate TTS audio
        audio_buffer = generate_tts_audio(text, language)
        if audio_buffer:
            try:
                audio_clip = AudioFileClip(audio_buffer)
                # Set start time for synchronization
                audio_clip = audio_clip.set_start(current_time)
                tts_clips.append(audio_clip)
            except Exception as e:
                print(f"Error loading TTS audio: {e}")

        current_time += len(pages[page_idx].get("duration", MIN_PAGE_DURATION))

    return tts_clips if tts_clips else None


# ===================================================================
# Audio
# ===================================================================

def _compat(obj, new_name, old_name, *args, **kwargs):
    """Call whichever of moviepy's 1.x/2.x method names exists."""
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
    """Return audio clip of exactly `duration` seconds, looping if needed."""
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
        print("WARNING: no Unicode Telugu-capable font found.")
    if not font_latin_path:
        print("WARNING: no dedicated Latin font found.")

    total_words = count_words(telugu_text) + count_words(english_text)
    if explanation_text and _explanation_enabled():
        total_words += count_words(explanation_text)
    font_size = choose_font_size(total_words, VIDEO_SIZE)

    font_telugu = load_font(font_telugu_path, font_size)
    font_latin = load_font(font_latin_path, font_size)

    dummy_draw = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    raw_pages = build_segments(telugu_text, english_text, explanation_text, font_telugu, font_latin, dummy_draw)
    if not raw_pages:
        raise ValueError("No text content provided to render.")

    pages = []
    for p in raw_pages:
        layer_arr, block_info = render_page_layer(p["lines"], p["font"])
        validate_page(p["lines"], block_info)
        pages.append({"lines": p["lines"], "layer": layer_arr})

    durations = schedule_durations(pages)
    # Store durations in pages for TTS sync
    for i, d in enumerate(durations):
        pages[i]["duration"] = d

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

        frame_img = bg_img.copy()
        frame_draw = ImageDraw.Draw(frame_img)
        draw_neon_border(frame_draw, VIDEO_SIZE, t)
        draw_stars(frame_draw, stars, t)
        bg_arr = np.array(frame_img)

        return composite_rgba_over_rgb(bg_arr, layer)

    clip = VideoClip(make_frame, duration=TOTAL_DURATION)
    clip = _compat(clip, "with_fps", "set_fps", FPS)

    # Prepare audio: background music + optional TTS
    try:
        music_path = pick_music_file()
        audio = prepare_audio(music_path, TOTAL_DURATION)

        # Optional: Add TTS if ElevenLabs key is set
        tts_clips = create_tts_clips(pages)
        if tts_clips:
            # Blend TTS with background music
            audio = CompositeAudioClip([audio] + tts_clips)

        clip = _compat(clip, "with_audio", "set_audio", audio)
    except FileNotFoundError as e:
        print(f"No background music available ({e}); rendering without audio.")

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
    """Generate YouTube thumbnail."""
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
    lines = wrap_text_to_lines(dummy_draw, display_text, font, safe_w)[:2]

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

    label_font = load_font(font_latin_path, int(font_size * 0.32))
    label = "DAILY VERSE"
    draw.text((36, thumb_size[1] - int(font_size * 0.32) - 36), label, font=label_font,
               fill=(235, 200, 120), stroke_width=2, stroke_fill=(0, 0, 0))

    timestamp = int(time.time())
    thumbnail_path = os.path.join(THUMBNAIL_DIR, f"thumbnail_{timestamp}.jpg")
    bg_img.convert("RGB").save(thumbnail_path, "JPEG", quality=95)
    return thumbnail_path


# ===================================================================
# Google Sheets / YouTube integration
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
            print(f"Google API returned {status} - retrying in {delay}s...")
            time.sleep(delay)
        except (SSLError, ConnectionError, IncompleteRead, TimeoutError) as e:
            if attempt == max_retries:
                raise
            delay = base_delay * (2 ** (attempt - 1))
            print(f"Network error ({e}) - retrying in {delay}s...")
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

    print(f"{len(available)} unused row(s) available out of {len(rows)} total.")
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
    """Renders a bundled bilingual (Telugu + English) example locally."""
    print("Running local test render (no Sheets, no YouTube upload)...")
    telugu_text = sanitize_text(
        "దేవుని ప్రేమ ఎంతో గొప్పది, కాబట్టి తన అద్వితీయ కుమారుని అనుగ్రహించెను; "
        "ఆయన యందు విశ్వాసముచేత నశించక నిత్యజీవము పొందిన అతనికి ఆయనను అనుగ్రహించెను. (యోహాను 3:16)"
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
        print(f"Using override text")
    else:
        row_number, telugu_text, english_text, explanation_text = fetch_next_row(sheets_service)
        if not telugu_text and not english_text:
            print("No unused rows found in the sheet. Exiting.")
            sys.exit(0)
        print(f"Selected row {row_number}")

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
    parser = argparse.ArgumentParser(description="Cinematic Bible verse video generator with TTS")
    parser.add_argument(
        "--test", action="store_true",
        help="Render a local bilingual test video.",
    )
    args = parser.parse_args()

    if args.test:
        run_test_render()
    else:
        run_production()


if __name__ == "__main__":
    main()
