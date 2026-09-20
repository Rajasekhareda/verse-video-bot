"""
generate_video_pro.py
====================
Cinematic 45-second YouTube Bible-verse video generator.

Features:
    - Cinematic Nature & Video Backgrounds: Mountains, Ocean, Waterfall,
      Forest, Desert, Sky, Sea Life, and Celestial themes with Ken Burns motion
    - Smooth Typography Animation: Hermite smoothstep easing, gentle upward float,
      dual-tone ivory/gold devotional palette with soft ambient shadow
    - Automated Neural Voice-Over (Free Edge-TTS + ElevenLabs fallback):
      Telugu (te-IN-MohanNeural) & English (en-US-ChristopherNeural)
    - Dynamic Audio Synchronization: Page duration adapts naturally to narration
    - Background Music with Smart Narration Ducking
    - Landscape (16:9) and Shorts/Reels (9:16) aspect ratio support
    - Google Sheets integration: Column A = Telugu, B = English,
      C = explanation, D = "used" marker written back automatically
    - High-definition YouTube thumbnail generation and automated upload
"""

import argparse
import asyncio
import atexit
import glob
import json
import math
import os
import platform
import random
import re
import sys
import tempfile
import time
import unicodedata
from bisect import bisect_right
from http.client import IncompleteRead
from ssl import SSLError

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

try:
    from fontTools.ttLib import TTFont as _FTFont
    _HAS_FONTTOOLS = True
except ImportError:
    _HAS_FONTTOOLS = False

try:
    from moviepy import (AudioFileClip, VideoClip, VideoFileClip,
                         concatenate_audioclips, CompositeAudioClip)
    _MOVIEPY_V2 = True
except ImportError:
    from moviepy.editor import (AudioFileClip, VideoClip, VideoFileClip,
                                concatenate_audioclips, CompositeAudioClip)
    _MOVIEPY_V2 = False

import requests

try:
    import edge_tts
    _HAS_EDGE_TTS = True
except ImportError:
    _HAS_EDGE_TTS = False

try:
    import uharfbuzz as _hb
    import freetype as _ft
    _HAS_HARFBUZZ = True
except ImportError:
    _HAS_HARFBUZZ = False

# Windows consoles default to cp1252; make all Telugu/Unicode prints safe
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from google.oauth2 import service_account
from google.oauth2.credentials import Credentials as UserCredentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload

# ================= SHEET LAYOUT & ENV VARS =================
# Column A = Telugu verse text
# Column B = English verse text
# Column C = optional brief explanation/note (any language)
# Column D = "used" marker, written automatically by this script
# ==========================================================

SHEET_ID = os.environ.get("SHEET_ID", "")
SHEET_TAB = os.environ.get("SHEET_TAB", "Sheet1")
MUSIC_DIR = os.environ.get("MUSIC_DIR", "assets/music")
BACKGROUND_DIR = os.environ.get("BACKGROUND_DIR", "assets/backgrounds")
ELEVENLABS_API_KEY = os.environ.get("ELEVENLABS_API_KEY", "").strip()

# TTS Configuration
ENABLE_TTS = os.environ.get("ENABLE_TTS", "yes").strip().lower() in ("yes", "true", "1", "auto")
VOICE_TELUGU = os.environ.get("VOICE_TELUGU", "te-IN-MohanNeural").strip()
VOICE_ENGLISH = os.environ.get("VOICE_ENGLISH", "en-US-ChristopherNeural").strip()

OUTPUT_DIR = "output"
THUMBNAIL_DIR = os.path.join(OUTPUT_DIR, "thumbnails")

# ================= VIDEO & FORMAT SPEC ================
VIDEO_FORMAT = os.environ.get("VIDEO_FORMAT", "landscape").strip().lower()
FPS = int(os.environ.get("FPS", "30"))
TOTAL_DURATION = float(os.environ.get("TOTAL_DURATION", "45.0"))

if VIDEO_FORMAT in ("shorts", "vertical", "reels", "9:16"):
    VIDEO_SIZE = (1080, 1920)         # 9:16 Portrait for Shorts / Reels
    SAFE_MARGIN_X_RATIO = 0.08
    SAFE_MARGIN_TOP_RATIO = 0.16      # clearance from top UI
    SAFE_MARGIN_BOTTOM_RATIO = 0.22   # clearance from bottom UI / captions
    MAX_LINES = 4
else:
    VIDEO_SIZE = (1920, 1080)         # 16:9 Full HD Landscape
    SAFE_MARGIN_X_RATIO = 0.09
    SAFE_MARGIN_TOP_RATIO = 0.12
    SAFE_MARGIN_BOTTOM_RATIO = 0.14
    MAX_LINES = 3

VERTICAL_BIAS = 0.0                   # centered vertically in safe area

SAFE_LEFT = int(VIDEO_SIZE[0] * SAFE_MARGIN_X_RATIO)
SAFE_RIGHT = int(VIDEO_SIZE[0] * (1 - SAFE_MARGIN_X_RATIO))
SAFE_TOP = int(VIDEO_SIZE[1] * SAFE_MARGIN_TOP_RATIO)
SAFE_BOTTOM = int(VIDEO_SIZE[1] * (1 - SAFE_MARGIN_BOTTOM_RATIO))
SAFE_TEXT_WIDTH = int((SAFE_RIGHT - SAFE_LEFT) * 0.96)

# ============ LINE ANIMATION & EASING ============
LINE_FADE = 0.80           # seconds for each line to smoothly fade in
LINE_STAGGER = 0.55        # seconds between consecutive line starts
LINE_RISE_PIXELS = 18      # gentle upward float (pixels)
ENTRANCE_CAP = 5.5         # max seconds for a page's entrance
HOLD_SECONDS = 5.5         # base hold duration after entrance
PAGE_FADE_OUT = 0.80       # clean dissolve duration at page end
MIN_PAGE_DURATION = 3.5    # minimum allowed duration per slide

# Typography & Colors
GOLD_ACCENT = (250, 218, 94)          # Radiant warm gold (headers, book tags)
CREAM_WHITE = (255, 252, 246)         # Spiritual ivory white (primary verse text)
SHADOW_COLOR = (0, 0, 0, 220)         # Deep ambient drop shadow
STROKE_COLOR = (14, 14, 20, 210)       # Crisp contrast outline
SHADOW_BLUR_RADIUS = 6
LINE_SPACING_MULTIPLIER = 1.45

# ============ BACKGROUND MODES & THEMES ============
# Modes: auto | video | image | gif | celestial | gradient
BACKGROUND_MODE = os.environ.get("BACKGROUND_MODE", "auto").strip().lower()
# Themes: random | mountains | ocean | waterfall | forest | desert | sky | celestial
BACKGROUND_THEME = os.environ.get("BACKGROUND_THEME", "random").strip().lower()

BACKGROUND_IMAGE = os.environ.get("BACKGROUND_IMAGE", "").strip()
BACKGROUND_GIF = os.environ.get("BACKGROUND_GIF", "").strip()
BACKGROUND_VIDEO = os.environ.get("BACKGROUND_VIDEO", "").strip()
PEXELS_API_KEY = os.environ.get("PEXELS_API_KEY", "").strip()

IMAGE_DIM = 0.40          # scenery dimming factor
VIDEO_DIM = 0.45          # video background dimming factor
GIF_FRAME_CAP = 25

# Manual-run controls
PRIVACY_STATUS = os.environ.get("PRIVACY_STATUS", "private").strip().lower()
MUSIC_CHOICE = os.environ.get("MUSIC_CHOICE", "random").strip()
INCLUDE_EXPLANATION = os.environ.get("INCLUDE_EXPLANATION", "auto").strip().lower()
TELUGU_OVERRIDE = os.environ.get("TELUGU_OVERRIDE", "").strip()
ENGLISH_OVERRIDE = os.environ.get("ENGLISH_OVERRIDE", "").strip()
EXPLANATION_OVERRIDE = os.environ.get("EXPLANATION_OVERRIDE", "").strip()

FONT_PATH_TELUGU_ENV = os.environ.get("FONT_PATH_TELUGU", "").strip()
FONT_PATH_LATIN_ENV = os.environ.get("FONT_PATH_LATIN", "").strip()

GRADIENT_PALETTES = {
    "Midnight Purple": ((16, 10, 48), (42, 18, 70)),
    "Ocean Blue":       ((6, 26, 64), (14, 52, 92)),
    "Wine Red":         ((34, 6, 18), (68, 20, 38)),
    "Emerald Teal":     ((6, 36, 32), (12, 64, 58)),
    "Sunset Amber":     ((38, 18, 8), (82, 42, 18)),
    "Indigo Violet":    ((18, 12, 46), (44, 30, 90)),
    "Midnight Slate":   ((12, 18, 30), (24, 36, 56)),
    "Charcoal":         ((14, 14, 18), (28, 28, 34)),
}

BASE_HASHTAGS = ["#BibleVerse", "#DailyVerse", "#Faith", "#God", "#Jesus", "#Scripture"]
TELUGU_HASHTAGS = ["#TeluguChristian", "#YesuKrishtu", "#TeluguBible"]
ENGLISH_HASHTAGS = ["#Christian", "#Gospel", "#WordOfGod", "#FaithJourney"]

# Bundled font covers Telugu + Latin
_REPO_DIR = os.path.dirname(os.path.abspath(__file__))
_BUNDLED_FONT = os.path.join(_REPO_DIR, "NotoSerifMerged-Bold.ttf")

FONT_CANDIDATES_TELUGU = [p for p in [
    FONT_PATH_TELUGU_ENV,
    r"C:\Windows\Fonts\NirmalaB.ttf",
    r"C:\Windows\Fonts\Nirmala.ttf",
    "/usr/share/fonts/truetype/noto/NotoSerifTelugu-Bold.ttf",
    "/usr/share/fonts/truetype/noto/NotoSansTelugu-Bold.ttf",
    "/usr/share/fonts/truetype/noto/NotoSansTelugu-Regular.ttf",
    _BUNDLED_FONT,
    "/System/Library/Fonts/Supplemental/NotoSansTelugu-Regular.ttf",
    "/Library/Fonts/NotoSansTelugu-Regular.ttf",
] if p and os.path.isfile(p)]

FONT_CANDIDATES_LATIN = [p for p in [
    FONT_PATH_LATIN_ENV,
    _BUNDLED_FONT,
    "/usr/share/fonts/truetype/noto/NotoSerif-Bold.ttf",
    "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    r"C:\Windows\Fonts\georgia.ttf",
    r"C:\Windows\Fonts\segoeuib.ttf",
    r"C:\Windows\Fonts\arialbd.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
] if p and os.path.isfile(p)]

_FONT_SCAN_DIRS = {
    "Linux": ["/usr/share/fonts", "/usr/local/share/fonts", os.path.expanduser("~/.fonts")],
    "Windows": [r"C:\Windows\Fonts"],
    "Darwin": ["/System/Library/Fonts", "/Library/Fonts", os.path.expanduser("~/Library/Fonts")],
}
_FONT_SCAN_PATTERNS = {
    "telugu": ["*Telugu*Bold*.ttf", "*Telugu*Serif*.ttf", "*Telugu*.ttf", "*Nirmala*.ttf"],
    "latin": ["*Serif*Bold*.ttf", "*Georgia*.ttf", "*NotoSans*Bold*.ttf",
              "*DejaVuSans*Bold*.ttf", "*Segoe*.ttf", "*Arial*Bold*.ttf", "*.ttf"],
}

_SCRIPT_RANGES = [
    ("Telugu", 0x0C00, 0x0C7F), ("Kannada", 0x0C80, 0x0CFF),
    ("Malayalam", 0x0D00, 0x0D7F), ("Tamil", 0x0B80, 0x0BFF),
    ("Bengali", 0x0980, 0x09FF), ("Gurmukhi", 0x0A00, 0x0A7F),
    ("Gujarati", 0x0A80, 0x0AFF), ("Oriya", 0x0B00, 0x0B7F),
    ("Devanagari", 0x0900, 0x097F), ("Sinhala", 0x0D80, 0x0DFF),
    ("Greek", 0x0370, 0x03FF), ("Cyrillic", 0x0400, 0x04FF),
]

_LANCZOS = getattr(getattr(Image, "Resampling", Image), "LANCZOS")

# ===================================================================
# Easing Curves
# ===================================================================

def smooth_ease(p):
    """Hermite smoothstep easing (3p^2 - 2p^3):
    Gentle start, smooth glide, gentle stop. Zero abrupt snapping.
    """
    p = max(0.0, min(1.0, float(p)))
    return p * p * (3.0 - 2.0 * p)


def ease_out_cubic(p):
    p = max(0.0, min(1.0, float(p)))
    return 1.0 - math.pow(1.0 - p, 3)

# ===================================================================
# Text helpers & Language Detection
# ===================================================================

def is_telugu(text):
    return any("\u0c00" <= ch <= "\u0c7f" for ch in text)


def detect_language(text):
    if not text:
        return "unknown"
    telugu_chars = sum(1 for ch in text if "\u0c00" <= ch <= "\u0c7f")
    total_chars = len([ch for ch in text if ch.isalpha()])
    if total_chars == 0:
        return "unknown"
    telugu_ratio = telugu_chars / total_chars
    if telugu_ratio > 0.4:
        return "telugu"
    return "english"


_PUNCT_MAP = {
    "\u2018": "'", "\u2019": "'",
    "\u201c": '"', "\u201d": '"',
    "\u2013": "-", "\u2014": "-",
    "\u2026": "...",
    "\u2022": "-", "\u25cf": "-", "\u2023": "-",
    "\u2020": "*", "\u2021": "*",
    "\u00a7": "Sec.", "\u00b6": "", "\u2212": "-",
    "\u00d7": "x", "\u00f7": "/", "\u00b0": " deg",
    "\u00ab": '"', "\u00bb": '"',
    "\u00a0": " ", "\u2007": " ", "\u2009": " ", "\u200a": " ", "\u2028": " ",
    "\u200b": "", "\u200c": "", "\u200d": "", "\ufeff": "",
}


def sanitize_text(text):
    if text is None:
        return text
    for bad, good in _PUNCT_MAP.items():
        text = text.replace(bad, good)
    text = text.replace("\n", " ").replace("\t", " ")
    text = re.sub(r" {2,}", " ", text).strip()
    return text


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
# Font resolution & Caching
# ===================================================================

def _scan_for_font(script_key):
    system = platform.system()
    dirs = _FONT_SCAN_DIRS.get(system, [])
    patterns = _FONT_SCAN_PATTERNS.get(script_key, ["*.ttf"])
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
        if font_path and os.path.isfile(font_path):
            font = ImageFont.truetype(font_path, size)
        else:
            raise OSError("no font path resolved")
    except OSError:
        print(f"WARNING: Could not load font at '{font_path}'. Falling back to default.")
        font = ImageFont.load_default()
    _FONT_CACHE[key] = font
    return font


_CMAP_CACHE = {}


def _font_cmap(path):
    if path in _CMAP_CACHE:
        return _CMAP_CACHE[path]
    cmap = None
    try:
        f = _FTFont(path, lazy=True, fontNumber=0)
        cmap = set(f.getBestCmap().keys())
        f.close()
    except Exception:
        cmap = None
    _CMAP_CACHE[path] = cmap
    return cmap


def font_covers(path, text):
    if not path or not os.path.isfile(path):
        return False
    if not _HAS_FONTTOOLS:
        return True
    cmap = _font_cmap(path)
    if cmap is None:
        return False
    for ch in text:
        if ch.isspace():
            continue
        if ord(ch) not in cmap:
            return False
    return True

# ===================================================================
# Layout & Typography
# ===================================================================

def _char_wrap_word(draw, word, font, max_width):
    pieces = []
    current = ""
    for ch in word:
        candidate = current + ch
        if draw.textlength(candidate, font=font) <= max_width:
            current = candidate
        else:
            if current:
                pieces.append(current)
            current = ch
    if current:
        pieces.append(current)
    return pieces or [word]


def wrap_text_to_lines(draw, text, font, max_width):
    if not text:
        return [""]
    words = text.split()
    lines = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip() if current else word
        if draw.textlength(candidate, font=font) <= max_width:
            current = candidate
        else:
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
    pages = [lines[i:i + max_lines] for i in range(0, len(lines), max_lines)]
    if len(pages) >= 2:
        last = pages[-1]
        words_in_last = sum(len(l.split()) for l in last)
        if words_in_last <= 2 and len(last) + len(pages[-2]) <= max_lines + 1:
            pages[-2] = pages[-2] + last
            pages.pop()
    return pages


def choose_font_size(total_words, video_size):
    h = video_size[1]
    is_vertical = video_size[1] > video_size[0]
    base = int(h * (0.095 if is_vertical else 0.155))
    if total_words > 60:
        scale = 0.55
    elif total_words > 40:
        scale = 0.65
    elif total_words > 24:
        scale = 0.76
    elif total_words > 12:
        scale = 0.88
    else:
        scale = 1.0
    size = int(base * scale)
    min_size = int(h * (0.045 if is_vertical else 0.075))
    max_size = int(h * (0.12 if is_vertical else 0.18))
    return max(min_size, min(size, max_size))


def _explanation_enabled():
    return INCLUDE_EXPLANATION in ("auto", "yes", "true", "1")


def build_segments(telugu_text, english_text, explanation_text, font_telugu, font_latin, draw):
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
            if any(l.strip() for l in chunk):
                pages.append({"lines": chunk, "font": font, "raw_text": " ".join(chunk)})
    return pages

# ===================================================================
# Timing & Dynamic Scheduling
# ===================================================================

def schedule_pages(pages, audio_clips=None):
    """Assign durations summing to exactly TOTAL_DURATION.
    If TTS audio clips are available, the page durations dynamically
    synchronize with the spoken narration!
    """
    n = len(pages)
    if n == 0:
        return []

    # Calculate desired duration per page
    for i, p in enumerate(pages):
        nl = max(1, p["n_lines"])
        entrance = min(ENTRANCE_CAP, (nl - 1) * LINE_STAGGER + LINE_FADE)
        p["entrance"] = entrance
        p["fade_out"] = PAGE_FADE_OUT

        if audio_clips and i < len(audio_clips) and audio_clips[i]:
            # Page matches speech duration + 1.2s contemplation hold
            speech_dur = audio_clips[i].duration
            p["raw"] = max(MIN_PAGE_DURATION, speech_dur + 1.2 + PAGE_FADE_OUT)
        else:
            p["raw"] = entrance + HOLD_SECONDS + PAGE_FADE_OUT

    total_raw = sum(p["raw"] for p in pages)
    scale = TOTAL_DURATION / total_raw
    durs = [p["raw"] * scale for p in pages]

    # Constrain to min page duration
    for _ in range(3):
        tight = [i for i, d in enumerate(durs) if d < MIN_PAGE_DURATION]
        if not tight:
            break
        for i in tight:
            durs[i] = MIN_PAGE_DURATION
        free = [i for i, d in enumerate(durs) if d > MIN_PAGE_DURATION]
        if not free:
            durs = [TOTAL_DURATION / n] * n
            break
        free_sum = TOTAL_DURATION - MIN_PAGE_DURATION * len(tight)
        sub = sum(durs[i] for i in free)
        if sub <= 0:
            durs = [TOTAL_DURATION / n] * n
            break
        for i in free:
            durs[i] *= free_sum / sub

    total = sum(durs)
    durs = [d * TOTAL_DURATION / total for d in durs]

    starts = []
    acc = 0.0
    for p, d in zip(pages, durs):
        p["duration"] = d
        f = d / p["raw"]
        p["fade_out"] = max(0.2, min(PAGE_FADE_OUT, d * 0.25))
        p["entrance"] = max(0.3, min(p["entrance"] * f, d - p["fade_out"] - 0.2))
        lf = max(0.25, min(LINE_FADE, p["entrance"] * 0.65))
        p["line_fade"] = lf
        nl = p["n_lines"]
        if nl > 1:
            stagger = max(0.05, (p["entrance"] - lf) / (nl - 1))
        else:
            stagger = 0.0
        p["line_starts"] = [i * stagger for i in range(nl)]
        starts.append(acc)
        acc += d

    return starts

# ===================================================================
# Background Engine: Nature Scenery, Ken Burns, Video Loops, Celestial
# ===================================================================

_VIGNETTE_MASK = None


def _get_cinematic_scrim():
    """Generates a rich center-dimmed scrim and soft edge vignette.
    Ensures white/gold text is 100% readable over any bright scenery.
    """
    global _VIGNETTE_MASK
    if _VIGNETTE_MASK is None or _VIGNETTE_MASK.size != VIDEO_SIZE:
        w, h = VIDEO_SIZE
        # Black base
        scrim = Image.new("L", (w, h), 0)
        draw = ImageDraw.Draw(scrim)

        # Center reading zone opacity (lighter in center, dark around margins)
        # Ellipse for vignette
        draw.ellipse([-w * 0.25, -h * 0.25, w * 1.25, h * 1.25], fill=210)
        # Inner soft scrim
        inner_box = [int(w * 0.06), int(h * 0.08), int(w * 0.94), int(h * 0.92)]
        draw.rounded_rectangle(inner_box, radius=int(min(w, h) * 0.1), fill=175)

        _VIGNETTE_MASK = scrim.filter(ImageFilter.GaussianBlur(int(min(w, h) * 0.15)))
    return _VIGNETTE_MASK


def _cover_resize(img):
    w, h = VIDEO_SIZE
    iw, ih = img.size
    if (iw, ih) == (w, h):
        return img
    scale = max(w / iw, h / ih)
    nw, nh = int(iw * scale + 0.5), int(ih * scale + 0.5)
    img = img.resize((nw, nh), _LANCZOS)
    left = (nw - w) // 2
    top = (nh - h) // 2
    return img.crop((left, top, left + w, top + h))


def _apply_scrim(img, dim_factor=IMAGE_DIM):
    """Dims and vignettes an image so text pops with pristine contrast."""
    w, h = VIDEO_SIZE
    if img.size != (w, h):
        img = _cover_resize(img)
    dimmed = Image.blend(img.convert("RGB"), Image.new("RGB", (w, h), (0, 0, 0)), dim_factor)
    return Image.composite(dimmed, Image.new("RGB", (w, h), (0, 0, 0)), _get_cinematic_scrim())


def make_ken_burns_image_bg(path):
    """Cinematic Ken Burns camera motion over high-res scenery:
    Slow, regal drone-like pan across the image at 60+ fps rendering speed.
    """
    w, h = VIDEO_SIZE
    with Image.open(path) as orig:
        orig = orig.convert("RGB")
        # Scale to 1.12x of target size once upfront
        scaled_w = int(w * 1.12)
        scaled_h = int(h * 1.12)
        base = _cover_resize(orig.resize((scaled_w, scaled_h), _LANCZOS))

    max_x = max(0, scaled_w - w)
    max_y = max(0, scaled_h - h)
    scrim = _get_cinematic_scrim()
    black = Image.new("RGB", (w, h), (0, 0, 0))

    def provider(t):
        p = t / max(1.0, TOTAL_DURATION)
        # Gentle smooth easing for camera sweep
        eased_p = smooth_ease(p)
        cx = int(max_x * (0.2 + 0.6 * eased_p))
        cy = int(max_y * (0.8 - 0.6 * eased_p))

        cropped = base.crop((cx, cy, cx + w, cy + h))
        dimmed = Image.blend(cropped, black, IMAGE_DIM)
        return Image.composite(dimmed, black, scrim)

    return provider


def make_video_bg(path):
    """Loads a scenic video loop, scales to VIDEO_SIZE upfront, and loops smoothly."""
    w, h = VIDEO_SIZE
    src = VideoFileClip(path, audio=False)
    if hasattr(src, "resized"):
        src = src.resized((w, h))
    elif hasattr(src, "resize"):
        src = src.resize((w, h))

    black = Image.new("RGB", (w, h), (0, 0, 0))
    scrim = _get_cinematic_scrim()

    def provider(t):
        tt = t % max(0.1, src.duration)
        frame = src.get_frame(tt)
        img = Image.fromarray(frame).convert("RGB")
        if img.size != (w, h):
            img = _cover_resize(img)
        dimmed = Image.blend(img, black, VIDEO_DIM)
        return Image.composite(dimmed, black, scrim)

    return provider


def make_celestial_bg():
    """Generates an atmospheric celestial motion background with
    radiant cosmic glow and soft floating golden bokeh particles.
    """
    w, h = VIDEO_SIZE
    # Base cosmic gradient
    base = Image.new("RGB", (w, h))
    draw = ImageDraw.Draw(base)
    top_c = (14, 10, 38)
    bot_c = (32, 16, 56)
    for y in range(h):
        r = y / h
        draw.line([(0, y), (w, y)], fill=(
            int(top_c[0] * (1 - r) + bot_c[0] * r),
            int(top_c[1] * (1 - r) + bot_c[1] * r),
            int(top_c[2] * (1 - r) + bot_c[2] * r)
        ))

    # Static celestial dust seeds
    random.seed(42)
    stars = []
    for _ in range(45):
        stars.append({
            "x": random.uniform(0, w),
            "y0": random.uniform(0, h),
            "radius": random.uniform(2, 6),
            "speed": random.uniform(12, 28),
            "gold": random.choice([(255, 235, 160), (250, 210, 90), (220, 240, 255)])
        })

    def provider(t):
        frame = base.copy()
        fdraw = ImageDraw.Draw(frame)
        for s in stars:
            y = (s["y0"] - s["speed"] * t) % h
            # Gentle breathing pulse
            alpha_pulse = 0.5 + 0.5 * math.sin(t * 1.5 + s["x"])
            r = s["radius"] * (0.8 + 0.3 * alpha_pulse)
            fdraw.ellipse([s["x"] - r, y - r, s["x"] + r, y + r],
                          fill=s["gold"])
        # Soft atmospheric blur on particles
        frame = frame.filter(ImageFilter.BoxBlur(1))
        return _apply_scrim(frame, dim_factor=0.20)

    return provider


def make_gradient_bg():
    """Static high-profile gradient with cinematic vignette."""
    w, h = VIDEO_SIZE
    name = random.choice(list(GRADIENT_PALETTES.keys()))
    top_color, bottom_color = GRADIENT_PALETTES[name]
    print(f"Gradient palette: {name}")

    background = Image.new("RGB", VIDEO_SIZE)
    draw = ImageDraw.Draw(background)
    for y in range(h):
        ratio = y / h
        r = int(top_color[0] * (1 - ratio) + bottom_color[0] * ratio)
        g = int(top_color[1] * (1 - ratio) + bottom_color[1] * ratio)
        b = int(top_color[2] * (1 - ratio) + bottom_color[2] * ratio)
        draw.line([(0, y), (w, y)], fill=(r, g, b))

    final_bg = _apply_scrim(background, dim_factor=0.15)

    def provider(t):
        return final_bg.copy()

    return provider


def _find_matching_bg_file(theme_filter=None):
    """Scans BACKGROUND_DIR for matching video or image scenery files."""
    if not os.path.isdir(BACKGROUND_DIR):
        return None, None

    vid_exts = (".mp4", ".mov", ".webm", ".mkv")
    img_exts = (".jpg", ".jpeg", ".png", ".webp")

    all_files = sorted(os.listdir(BACKGROUND_DIR))
    videos = [os.path.join(BACKGROUND_DIR, f) for f in all_files if f.lower().endswith(vid_exts)]
    images = [os.path.join(BACKGROUND_DIR, f) for f in all_files if f.lower().endswith(img_exts)]

    # Filter by theme if specified
    if theme_filter and theme_filter != "random":
        th = theme_filter.lower()
        matched_vids = [f for f in videos if th in os.path.basename(f).lower()]
        if matched_vids:
            return random.choice(matched_vids), "video"
        matched_imgs = [f for f in images if th in os.path.basename(f).lower()]
        if matched_imgs:
            return random.choice(matched_imgs), "image"

    # Prioritize video if available, else images
    if videos:
        return random.choice(videos), "video"
    if images:
        return random.choice(images), "image"

    return None, None


def resolve_background():
    """Returns (provider_function, mode_name) based on configuration and files."""
    theme = BACKGROUND_THEME if BACKGROUND_THEME != "random" else None

    if BACKGROUND_MODE == "video":
        if BACKGROUND_VIDEO and os.path.isfile(BACKGROUND_VIDEO):
            return make_video_bg(BACKGROUND_VIDEO), "video"
        path, kind = _find_matching_bg_file(theme)
        if path and kind == "video":
            return make_video_bg(path), f"video ({os.path.basename(path)})"

    if BACKGROUND_MODE == "image":
        if BACKGROUND_IMAGE and os.path.isfile(BACKGROUND_IMAGE):
            return make_ken_burns_image_bg(BACKGROUND_IMAGE), "image"
        path, kind = _find_matching_bg_file(theme)
        if path:
            return make_ken_burns_image_bg(path), f"image ({os.path.basename(path)})"

    if BACKGROUND_MODE == "celestial":
        return make_celestial_bg(), "celestial"

    if BACKGROUND_MODE == "gradient":
        return make_gradient_bg(), "gradient"

    # Default 'auto' mode: check local assets first
    path, kind = _find_matching_bg_file(theme)
    if path:
        if kind == "video":
            print(f"Loaded scenic video background: {os.path.basename(path)}")
            return make_video_bg(path), f"video ({os.path.basename(path)})"
        else:
            print(f"Loaded cinematic scenery background: {os.path.basename(path)}")
            return make_ken_burns_image_bg(path), f"image ({os.path.basename(path)})"

    # Fallback to rich celestial motion
    print("Using celestial ambient motion background.")
    return make_celestial_bg(), "celestial"

# ===================================================================
# Page rendering & Line-by-Line Staggered Animations
# ===================================================================

def compute_block_top(block_height, safe_top=SAFE_TOP, safe_bottom=SAFE_BOTTOM):
    zone_height = safe_bottom - safe_top
    desired_center = safe_top + zone_height * 0.5
    top = desired_center - block_height / 2
    return max(safe_top, min(top, safe_bottom - block_height))


def _render_shaped_line(line, font_path, font_size, text_color, stroke_color, stroke_w, shadow_color):
    """Uses HarfBuzz + FreeType for 100% accurate Indic/Telugu ligatures and matras."""
    if not (_HAS_HARFBUZZ and font_path and os.path.isfile(font_path)):
        return None, 0, 0
    try:
        face = _ft.Face(font_path)
        face.set_char_size(font_size * 64)
        with open(font_path, "rb") as f:
            fontdata = f.read()
        hb_blob = _hb.Blob(fontdata)
        hb_face = _hb.Face(hb_blob)
        hb_font = _hb.Font(hb_face)
        hb_font.scale = (font_size * 64, font_size * 64)
        buf = _hb.Buffer()
        buf.add_str(line)
        buf.guess_segment_properties()
        _hb.shape(hb_font, buf)
        infos = buf.glyph_infos
        positions = buf.glyph_positions

        total_w = sum(pos.x_advance for pos in positions) // 64
        line_h = int(font_size * 1.8)
        pad = int(font_size * 0.8)
        layer_w = total_w + pad * 2
        layer_h = line_h + pad * 2

        mask = Image.new("L", (layer_w, layer_h), 0)
        x = pad
        y = pad + int(font_size * 1.1)

        for info, pos in zip(infos, positions):
            face.load_glyph(info.codepoint, _ft.FT_LOAD_RENDER | _ft.FT_LOAD_TARGET_NORMAL)
            bm = face.glyph.bitmap
            bx = x + (pos.x_offset // 64) + face.glyph.bitmap_left
            by = y - (pos.y_offset // 64) - face.glyph.bitmap_top
            w, h = bm.width, bm.rows
            if w > 0 and h > 0:
                arr = np.array(bm.buffer, dtype=np.uint8).reshape((h, w))
                sub = mask.crop((bx, by, bx + w, by + h))
                combined = np.maximum(np.array(sub), arr)
                mask.paste(Image.fromarray(combined), (bx, by))
            x += (pos.x_advance // 64)
            y += (pos.y_advance // 64)

        shadow_mask = mask.filter(ImageFilter.GaussianBlur(SHADOW_BLUR_RADIUS))
        shadow = Image.new("RGBA", (layer_w, layer_h), shadow_color)
        shadow.putalpha(shadow_mask)

        stroke_mask = mask.filter(ImageFilter.MaxFilter(stroke_w * 2 + 1))
        stroke = Image.new("RGBA", (layer_w, layer_h), stroke_color)
        stroke.putalpha(stroke_mask)

        main = Image.new("RGBA", (layer_w, layer_h), text_color)
        main.putalpha(mask)

        combined = Image.alpha_composite(shadow, stroke)
        combined = Image.alpha_composite(combined, main)
        return combined, total_w, pad
    except Exception:
        return None, 0, 0


def render_page_lines(lines, font, default_font_path):
    """Pre-renders each line as an RGBA layer with multi-pass devotional shadow."""
    line_height = int(font.size * LINE_SPACING_MULTIPLIER)
    block_height = line_height * len(lines)
    top = compute_block_top(block_height)

    stroke_w = max(2, font.size // 22)
    pad_x = int(font.size * 0.8)
    pad_y = int(font.size * 0.9)
    line_layers = []
    max_line_width = 0.0

    measure = ImageDraw.Draw(Image.new("RGB", (10, 10)))

    for i, line in enumerate(lines):
        if not line.strip():
            continue
        y = top + i * line_height

        # Check if line contains Bible reference tag e.g. (యోహాను 3:16)
        is_ref = line.strip().startswith("(") and line.strip().endswith(")")
        text_fill = GOLD_ACCENT + (255,) if is_ref else CREAM_WHITE + (255,)

        # Try HarfBuzz shaped rendering first for pristine complex script shaping
        shaped_layer, shaped_w, shaped_pad = _render_shaped_line(
            line, default_font_path, font.size, text_fill, STROKE_COLOR, stroke_w, SHADOW_COLOR
        )

        if shaped_layer is not None:
            w = shaped_w
            max_line_width = max(max_line_width, w)
            x = (VIDEO_SIZE[0] - w) / 2
            line_layers.append({
                "layer": shaped_layer,
                "x": int(x - shaped_pad),
                "y": int(y - shaped_pad // 2),
            })
            continue

        # Fallback to standard Pillow rendering
        w = int(measure.textlength(line, font=font))
        max_line_width = max(max_line_width, w)
        x = (VIDEO_SIZE[0] - w) / 2

        layer_w = w + pad_x * 2
        layer_h = int(font.size * 2.2) + pad_y

        shadow = Image.new("RGBA", (layer_w, layer_h), (0, 0, 0, 0))
        sdraw = ImageDraw.Draw(shadow)
        main = Image.new("RGBA", (layer_w, layer_h), (0, 0, 0, 0))
        mdraw = ImageDraw.Draw(main)

        tx = pad_x
        ty = pad_y // 2

        sdraw.text((tx, ty + font.size * 0.08), line, font=font, fill=SHADOW_COLOR)
        sdraw.text((tx + 2, ty + 3), line, font=font, fill=(0, 0, 0, 160))
        shadow = shadow.filter(ImageFilter.GaussianBlur(radius=SHADOW_BLUR_RADIUS))

        mdraw.text((tx, ty), line, font=font, fill=text_fill,
                   stroke_width=stroke_w, stroke_fill=STROKE_COLOR)

        combined = Image.alpha_composite(shadow, main)
        line_layers.append({
            "layer": combined,
            "x": int(x - pad_x),
            "y": int(y - pad_y // 2),
        })

    block_info = {"top": top, "height": block_height, "max_width": max_line_width}
    return line_layers, block_info


def apply_line_alpha(layer_img, alpha, rise_px):
    """Applies smooth opacity fade and gentle upward elevation."""
    a = np.array(layer_img)
    if alpha < 1.0:
        a[..., 3] = (a[..., 3].astype(np.float32) * max(0.0, min(1.0, alpha))).astype(np.uint8)
    if rise_px > 0:
        shifted = np.zeros_like(a)
        r = int(round(rise_px))
        if 0 < r < a.shape[0]:
            shifted[r:, :, :] = a[:-r, :, :]
            a = shifted
    return a

# ===================================================================
# Neural Voice-Over (TTS): Edge-TTS & ElevenLabs
# ===================================================================

async def _edge_tts_speak(text, voice, output_file):
    communicate = edge_tts.Communicate(text, voice=voice, rate="-4%")
    await communicate.save(output_file)


def generate_tts_audio(text, language):
    """Generates crystal-clear neural narration using free Edge-TTS (or ElevenLabs)."""
    if not ENABLE_TTS:
        return None
    if not text or not text.strip():
        return None

    # Try ElevenLabs first if API key is provided
    if ELEVENLABS_API_KEY:
        try:
            url = "https://api.elevenlabs.io/v1/text-to-speech/21m00Tcm4TlvDq8ikWAM"
            headers = {"xi-api-key": ELEVENLABS_API_KEY, "Content-Type": "application/json"}
            data = {"text": text, "model_id": "eleven_multilingual_v2"}
            resp = requests.post(url, json=data, headers=headers, timeout=20)
            if resp.status_code == 200:
                fd, tmp = tempfile.mkstemp(suffix=".mp3")
                with os.fdopen(fd, "wb") as fh:
                    fh.write(resp.content)
                return tmp
        except Exception as e:
            print(f"ElevenLabs TTS failed ({e}); falling back to Edge-TTS...")

    # Edge-TTS (Free, no API key needed, studio neural quality)
    if _HAS_EDGE_TTS:
        try:
            fd, tmp = tempfile.mkstemp(suffix=".mp3")
            os.close(fd)
            voice = VOICE_TELUGU if language == "telugu" else VOICE_ENGLISH
            asyncio.run(_edge_tts_speak(text, voice, tmp))
            return tmp
        except Exception as e:
            print(f"Edge-TTS narration failed: {e}")
            return None

    return None


def create_tts_clips(pages):
    """Generates speech clips for each page and returns list of AudioFileClip."""
    if not ENABLE_TTS:
        return [None] * len(pages)

    clips = []
    for i, p in enumerate(pages):
        text = p.get("raw_text", " ".join(p["lines"]))
        lang = detect_language(text)
        print(f"Generating narration ({lang}) for Page {i + 1}: {text[:45]}...")
        audio_path = generate_tts_audio(text, lang)
        if audio_path and os.path.isfile(audio_path):
            try:
                clip = AudioFileClip(audio_path)
                clips.append(clip)
            except Exception as e:
                print(f"Could not load audio clip ({e})")
                clips.append(None)
        else:
            clips.append(None)

    # Register temporary files cleanup
    def _cleanup():
        for c in clips:
            if c:
                p = getattr(c, "filename", None)
                if p and os.path.isfile(str(p)):
                    try:
                        c.close()
                        os.remove(str(p))
                    except OSError:
                        pass
    atexit.register(_cleanup)
    return clips

# ===================================================================
# Audio Mixing & Ducking
# ===================================================================

def _compat(obj, new_name, old_name, *args, **kwargs):
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
    return os.path.join(MUSIC_DIR, random.choice(music_files))


def prepare_audio(music_path, duration, has_voiceover=False):
    """Loops background music and applies volume ducking if narration is active."""
    src = AudioFileClip(music_path)
    start_offset = min(4.0, max(0.0, src.duration * 0.05))
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

    # Duck music if voice-over is talking so narration is crystal clear
    music_vol = 0.16 if has_voiceover else 0.28
    return _compat(audio, "with_volume_scaled", "volumex", music_vol)

# ===================================================================
# Video Production Pipeline
# ===================================================================

def build_video(telugu_text, english_text, explanation_text):
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    os.makedirs(THUMBNAIL_DIR, exist_ok=True)

    font_telugu_path = resolve_font_path(FONT_CANDIDATES_TELUGU, "telugu")
    font_latin_path = resolve_font_path(FONT_CANDIDATES_LATIN, "latin")

    total_words = count_words(telugu_text) + count_words(english_text)
    if explanation_text and _explanation_enabled():
        total_words += count_words(explanation_text)

    font_size = choose_font_size(total_words, VIDEO_SIZE)
    font_telugu = load_font(font_telugu_path, font_size)
    font_latin = load_font(font_latin_path, font_size)

    dummy_draw = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    raw_pages = build_segments(telugu_text, english_text, explanation_text,
                               font_telugu, font_latin, dummy_draw)
    if not raw_pages:
        raise ValueError("No text content provided to render.")

    # Render page line layers
    pages = []
    for p in raw_pages:
        default_font_path = font_telugu_path if p["font"] is font_telugu else font_latin_path
        line_layers, block_info = render_page_lines(p["lines"], p["font"], default_font_path)
        if not line_layers:
            continue
        pages.append({
            "lines": p["lines"],
            "raw_text": p["raw_text"],
            "font": p["font"],
            "line_layers": line_layers,
            "n_lines": len(line_layers),
            "block": block_info
        })

    if not pages:
        raise ValueError("No renderable text found after layout.")

    # Generate TTS audio narration (Edge-TTS)
    tts_audio_clips = create_tts_clips(pages)
    starts = schedule_pages(pages, tts_audio_clips)

    print(f"\nPrepared {len(pages)} slide(s) across {TOTAL_DURATION:.1f}s:")
    for i, (p, s) in enumerate(zip(pages, starts)):
        preview = " / ".join(p["lines"])
        has_tts = "Narration ON" if (tts_audio_clips and tts_audio_clips[i]) else "Music only"
        print(f"  Slide {i + 1}: {s:5.2f}s -> {s + p['duration']:5.2f}s "
              f"({p['duration']:4.2f}s, entrance {p['entrance']:.2f}s) [{has_tts}] {preview}")

    # Resolve Background (Scenery / Video / Celestial / Gradient)
    bg_provider, bg_mode = resolve_background()
    print(f"\nCinematic Background Mode: {bg_mode}")

    def make_frame(t):
        t = min(t, TOTAL_DURATION - 1e-3)
        idx = max(0, min(bisect_right(starts, t) - 1, len(pages) - 1))
        page = pages[idx]
        local_t = t - starts[idx]

        frame_img = bg_provider(t)

        # Page fade-out at end of page
        fo = page["fade_out"]
        page_alpha = 1.0
        if local_t > page["duration"] - fo:
            page_alpha = smooth_ease(max(0.0, (page["duration"] - local_t) / fo))

        if page_alpha > 0.01:
            lf = page["line_fade"]
            for li, (line_layer, l_start) in enumerate(zip(page["line_layers"], page["line_starts"])):
                lt = local_t - l_start
                if lt <= 0:
                    continue
                prog = min(1.0, lt / lf)
                # Buttery smooth Hermite easing
                l_alpha = smooth_ease(prog) * page_alpha
                if l_alpha <= 0.01:
                    continue
                # Gentle upward float
                rise = (1.0 - smooth_ease(prog)) * LINE_RISE_PIXELS
                arr = apply_line_alpha(line_layer["layer"], l_alpha, rise)
                frame_img.paste(Image.fromarray(arr), (line_layer["x"], line_layer["y"]), Image.fromarray(arr))

        return np.array(frame_img)

    clip = VideoClip(make_frame, duration=TOTAL_DURATION)
    clip = _compat(clip, "with_fps", "set_fps", FPS)

    # Composite audio (Music + Synchronized Narration)
    try:
        music_path = pick_music_file()
        has_narration = any(c is not None for c in tts_audio_clips)
        music_clip = prepare_audio(music_path, TOTAL_DURATION, has_voiceover=has_narration)

        timed_audio_clips = [music_clip]
        for c, s in zip(tts_audio_clips, starts):
            if c is not None:
                # Synchronize voice start with text float entrance
                c_timed = _compat(c, "with_start", "set_start", s + 0.3)
                timed_audio_clips.append(c_timed)

        composite_audio = CompositeAudioClip(timed_audio_clips)
        clip = _compat(clip, "with_audio", "set_audio", composite_audio)
    except FileNotFoundError as e:
        print(f"Audio notice: {e}. Rendering video without audio.")

    output_path = os.path.join(OUTPUT_DIR, "verse_video.mp4")
    print(f"\nEncoding {VIDEO_SIZE[0]}x{VIDEO_SIZE[1]} {FPS}fps video with libx264...")
    clip.write_videofile(
        output_path,
        fps=FPS,
        codec="libx264",
        audio_codec="aac",
        bitrate="12M",
        preset="medium",
        threads=4,
        ffmpeg_params=["-pix_fmt", "yuv420p"],
    )

    thumbnail_path = generate_thumbnail(telugu_text, english_text, font_telugu_path, font_latin_path)
    return output_path, thumbnail_path


def generate_thumbnail(telugu_text, english_text, font_telugu_path, font_latin_path):
    """Generates high-profile 1280x720 YouTube thumbnail."""
    thumb_size = (1280, 720)

    # Scenery background matching video
    path, _ = _find_matching_bg_file()
    if path and os.path.isfile(path):
        with Image.open(path) as img:
            bg_img = _apply_scrim(img.resize(thumb_size, _LANCZOS), dim_factor=0.35)
    else:
        bg_img = make_gradient_bg()(0).resize(thumb_size, _LANCZOS)

    display_text = telugu_text or english_text or "Daily Bible Verse"
    use_telugu = is_telugu(display_text)
    font_path = font_telugu_path if use_telugu else font_latin_path
    font_size = int(thumb_size[1] * 0.11)
    font = load_font(font_path, font_size)

    dummy_draw = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    safe_w = int(thumb_size[0] * 0.88)
    lines = wrap_text_to_lines(dummy_draw, display_text, font, safe_w)[:2]

    line_height = int(font_size * 1.42)
    block_height = line_height * len(lines)
    top = (thumb_size[1] - block_height) // 2

    draw = ImageDraw.Draw(bg_img)
    stroke_w = max(2, font_size // 24)

    for i, line in enumerate(lines):
        y = top + i * line_height
        is_ref = line.strip().startswith("(") and line.strip().endswith(")")
        text_fill = GOLD_ACCENT + (255,) if is_ref else CREAM_WHITE + (255,)

        shaped_layer, shaped_w, shaped_pad = _render_shaped_line(
            line, font_path, font_size, text_fill, STROKE_COLOR, stroke_w, (0, 0, 0, 240)
        )
        if shaped_layer is not None:
            x = (thumb_size[0] - shaped_w) / 2
            bg_img.paste(shaped_layer, (int(x - shaped_pad), int(y - shaped_pad // 2)), shaped_layer)
        else:
            w = draw.textlength(line, font=font)
            x = (thumb_size[0] - w) / 2
            draw.text((x + 3, y + 4), line, font=font, fill=(0, 0, 0, 240),
                      stroke_width=stroke_w + 2, stroke_fill=(0, 0, 0, 240))
            draw.text((x, y), line, font=font, fill=text_fill,
                      stroke_width=stroke_w, stroke_fill=STROKE_COLOR)

    # Golden badge
    label_font = load_font(font_latin_path, int(font_size * 0.36))
    label = "DAILY SCRIPTURE"
    draw.rounded_rectangle([32, thumb_size[1] - int(font_size * 0.36) - 48,
                           280, thumb_size[1] - 28], radius=8, fill=(18, 18, 24, 220))
    draw.text((44, thumb_size[1] - int(font_size * 0.36) - 44), label, font=label_font,
              fill=GOLD_ACCENT)

    timestamp = int(time.time())
    thumbnail_path = os.path.join(THUMBNAIL_DIR, f"thumbnail_{timestamp}.jpg")
    bg_img.convert("RGB").save(thumbnail_path, "JPEG", quality=95)
    return thumbnail_path

# ===================================================================
# Google Sheets & YouTube Upload
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
    """Fetch the FIRST unused row (strict queue: row 2, then 3, ...)."""
    range_ = f"{SHEET_TAB}!A2:D"
    result = call_with_retries(
        lambda: service.spreadsheets().values().get(spreadsheetId=SHEET_ID, range=range_).execute()
    )
    rows = result.get("values", [])
    available_count = 0
    for i, row in enumerate(rows):
        telugu = row[0] if len(row) > 0 else ""
        english = row[1] if len(row) > 1 else ""
        explanation = row[2] if len(row) > 2 else ""
        used = row[3] if len(row) > 3 else ""
        if (telugu or english) and used.strip().lower() != "used":
            available_count += 1
            print(f"{available_count} unused row(s) available out of {len(rows)} total.")
            print(f"Queue: selecting row {i + 2} (first unused).")
            return (i + 2, telugu.strip(), english.strip(), explanation.strip())

    print(f"0 unused row(s) available out of {len(rows)} total.")
    return None, None, None, None


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
    print("\n--- Running Local Test Render (Cinematic Scenery + Voiceover) ---")
    telugu_text = sanitize_text(
        "దేవుని ప్రేమ ఎంతో గొప్పది, కాబట్టి ఆయన తన అద్వితీయ కుమారుని అనుగ్రహించెను. "
        "(యోహాను 3:16)"
    )
    english_text = sanitize_text(
        "For God so loved the world that he gave his one and only Son. "
        "(John 3:16)"
    )
    video_path, thumbnail_path = build_video(telugu_text, english_text, "")
    print(f"\n==========================================")
    print(f"Test video created at:     {video_path}")
    print(f"Test thumbnail created at: {thumbnail_path}")
    print(f"==========================================\n")


def run_production():
    creds = get_user_credentials()
    sheets_service = get_sheets_service()

    row_number = None
    if TELUGU_OVERRIDE or ENGLISH_OVERRIDE:
        telugu_text, english_text, explanation_text = TELUGU_OVERRIDE, ENGLISH_OVERRIDE, EXPLANATION_OVERRIDE
        print("Using override text")
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

    print("Production run completed successfully.")


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
