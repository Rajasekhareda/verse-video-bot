"""
generate_video_pro.py
====================
Cinematic 45-second YouTube Bible-verse video generator.

Features:
    - Google Sheets integration: Column A = Telugu, B = English,
      C = explanation, D = "used" marker written back automatically
    - Word-by-word PowerPoint-style entrance animation,
      6-second hold, then a clean fade-away before the next page
    - Selectable backgrounds: gradient (default), image, GIF, or video
    - Telugu + English (plus any installed script) with no tofu
      boxes: bundled merged Noto Serif font first, per-script system
      font fallback via fontTools coverage checks
    - Background music loops to fill the full 45 seconds exactly
    - Optional ElevenLabs TTS narration synchronized with pages
    - YouTube thumbnail generation and private upload

Pipeline:
    Google Sheet -> render 45s video with word-by-word animated text ->
    loop music to exactly 45s -> upload to YouTube
"""

import argparse
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

# ================= SHEET LAYOUT =================
# Column A = Telugu verse text
# Column B = English verse text
# Column C = optional brief explanation/note (any language)
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
MAX_LINES = 3                      # hard cap per page

SAFE_MARGIN_X_RATIO = 0.09
SAFE_MARGIN_TOP_RATIO = 0.12
SAFE_MARGIN_BOTTOM_RATIO = 0.14
VERTICAL_BIAS = 0.0                # text block starts at TOP of safe area

SAFE_LEFT = int(VIDEO_SIZE[0] * SAFE_MARGIN_X_RATIO)
SAFE_RIGHT = int(VIDEO_SIZE[0] * (1 - SAFE_MARGIN_X_RATIO))
SAFE_TOP = int(VIDEO_SIZE[1] * SAFE_MARGIN_TOP_RATIO)
SAFE_BOTTOM = int(VIDEO_SIZE[1] * (1 - SAFE_MARGIN_BOTTOM_RATIO))
SAFE_TEXT_WIDTH = int((SAFE_RIGHT - SAFE_LEFT) * 0.96)

# ============ LINE-BY-LINE ANIMATION TIMING ============
# Each page: lines enter one-by-one from the top of the screen,
# the completed text holds, then fades away cleanly.
LINE_FADE = 0.5           # seconds for each line to fade in
LINE_STAGGER = 0.75       # seconds between consecutive line starts
LINE_RISE_PIXELS = 24     # each line rises slightly as it appears
ENTRANCE_CAP = 6.0        # max seconds for a page's full entrance
HOLD_SECONDS = 6.0        # text holds this long after entrance
PAGE_FADE_OUT = 0.9       # clean fade-away duration at page end
MIN_PAGE_DURATION = 3.0   # never squeeze a page below this

# Typography
SHADOW_COLOR = (0, 0, 0, 220)
STROKE_COLOR = (20, 20, 30, 200)
SHADOW_BLUR_RADIUS = 6
LINE_SPACING_MULTIPLIER = 1.45

# Cinematic text accents matched to each gradient palette
TEXT_ACCENTS = {
    "Midnight Purple": (232, 225, 255),   # soft lavender-white
    "Ocean Blue":      (214, 236, 255),   # ice-blue white
    "Wine Red":        (255, 226, 229),   # rose white
    "Emerald Teal":    (222, 255, 244),   # mint white
    "Sunset Amber":    (255, 236, 204),   # warm amber cream
    "Indigo Violet":   (228, 224, 255),   # periwinkle white
    "Midnight Slate":  (233, 240, 247),   # silver-blue white
    "Charcoal":        (250, 246, 238),   # warm cream
}
DEFAULT_TEXT_ACCENT = (255, 244, 224)  # warm cinematic cream-gold

# ============ BACKGROUNDS ============
# BACKGROUND_MODE: gradient | image | gif | video
BACKGROUND_MODE = (os.environ.get("BACKGROUND_MODE", "gradient").strip().lower()
                   or "gradient")
BACKGROUND_DIR = os.environ.get("BACKGROUND_DIR", "assets/backgrounds")
BACKGROUND_IMAGE = os.environ.get("BACKGROUND_IMAGE", "").strip()
BACKGROUND_GIF = os.environ.get("BACKGROUND_GIF", "").strip()
BACKGROUND_VIDEO = os.environ.get("BACKGROUND_VIDEO", "").strip()
IMAGE_DIM = 0.45          # darken still/gif backgrounds for text contrast
VIDEO_DIM = 0.50          # darken video backgrounds for text contrast
GIF_FRAME_CAP = 20        # max precomputed GIF frames (memory cap)

# Manual-run controls
PRIVACY_STATUS = os.environ.get("PRIVACY_STATUS", "private").strip().lower()
MUSIC_CHOICE = os.environ.get("MUSIC_CHOICE", "random").strip()
BACKGROUND_THEME = os.environ.get("BACKGROUND_THEME", "random").strip()
INCLUDE_EXPLANATION = os.environ.get("INCLUDE_EXPLANATION", "Auto").strip().lower()
TELUGU_OVERRIDE = os.environ.get("TELUGU_OVERRIDE", "").strip()
ENGLISH_OVERRIDE = os.environ.get("ENGLISH_OVERRIDE", "").strip()
EXPLANATION_OVERRIDE = os.environ.get("EXPLANATION_OVERRIDE", "").strip()

FONT_PATH_TELUGU_ENV = os.environ.get("FONT_PATH_TELUGU", "").strip()
FONT_PATH_LATIN_ENV = os.environ.get("FONT_PATH_LATIN", "").strip()

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

# Bundled font covers Telugu + Latin (98 Telugu, 95 Latin glyphs verified)
_REPO_DIR = os.path.dirname(os.path.abspath(__file__))
_BUNDLED_FONT = os.path.join(_REPO_DIR, "NotoSerifMerged-Bold.ttf")

FONT_CANDIDATES_TELUGU = [p for p in [
    FONT_PATH_TELUGU_ENV,
    _BUNDLED_FONT,
    "/usr/share/fonts/truetype/noto/NotoSerifTelugu-Bold.ttf",
    "/usr/share/fonts/truetype/noto/NotoSansTelugu-Bold.ttf",
    "/usr/share/fonts/truetype/noto/NotoSansTelugu-Regular.ttf",
    r"C:\Windows\Fonts\NirmalaB.ttf",
    r"C:\Windows\Fonts\Nirmala.ttf",
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

# Script ranges for per-word font fallback (avoids tofu boxes for any
# language whose font is installed on the system)
_SCRIPT_RANGES = [
    ("Telugu", 0x0C00, 0x0C7F), ("Kannada", 0x0C80, 0x0CFF),
    ("Malayalam", 0x0D00, 0x0D7F), ("Tamil", 0x0B80, 0x0BFF),
    ("Bengali", 0x0980, 0x09FF), ("Gurmukhi", 0x0A00, 0x0A7F),
    ("Gujarati", 0x0A80, 0x0AFF), ("Oriya", 0x0B00, 0x0B7F),
    ("Devanagari", 0x0900, 0x097F), ("Sinhala", 0x0D80, 0x0DFF),
    ("Thai", 0x0E00, 0x0E7F), ("Lao", 0x0E80, 0x0EFF),
    ("Tibetan", 0x0F00, 0x0FFF), ("Myanmar", 0x1000, 0x109F),
    ("Georgian", 0x10A0, 0x10FF), ("Armenian", 0x0530, 0x058F),
    ("Hebrew", 0x0590, 0x05FF), ("Arabic", 0x0600, 0x06FF),
    ("Khmer", 0x1780, 0x17FF), ("Ethiopic", 0x1200, 0x137F),
    ("Cherokee", 0x13A0, 0x13FF), ("Greek", 0x0370, 0x03FF),
    ("Cyrillic", 0x0400, 0x04FF), ("Kana", 0x3040, 0x30FF),
    ("Hangul", 0xAC00, 0xD7AF), ("Han", 0x4E00, 0x9FFF),
]

# ===================================================================
# Text helpers
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
    "\u2018": "'", "\u2019": "'",
    "\u201c": '"', "\u201d": '"',
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
    """Normalize punctuation/whitespace but KEEP all Unicode letters.

    Any language must display without being mangled, so non-ASCII
    letters are never stripped or decomposed - the font fallback chain
    handles rendering them correctly.
    """
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
# Font resolution (bundled first, then system scan, per-script fallback)
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
    """True if the font file has glyphs for every character in text."""
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


def script_of(ch):
    cp = ord(ch)
    for name, lo, hi in _SCRIPT_RANGES:
        if lo <= cp <= hi:
            return name
    return None


_SCRIPT_FONT_CACHE = {}


def _scan_script_font(script_name):
    key = ("script", script_name)
    if key in _SCRIPT_FONT_CACHE:
        return _SCRIPT_FONT_CACHE[key]
    found = None
    for d in _FONT_SCAN_DIRS.get(platform.system(), []):
        if not os.path.isdir(d):
            continue
        for pattern in (f"*{script_name}*Bold*.ttf", f"*{script_name}*Serif*.ttf",
                        f"*{script_name}*.ttf", f"*{script_name}*.otf"):
            matches = glob.glob(os.path.join(d, "**", pattern), recursive=True)
            if matches:
                found = sorted(matches)[0]
                break
        if found:
            break
    _SCRIPT_FONT_CACHE[key] = found
    return found


def font_for_word(word, default_font, default_path):
    """Pick a font that actually covers the word's script (no tofu)."""
    if not word.strip() or not _HAS_FONTTOOLS or not default_path:
        return default_font
    if font_covers(default_path, word):
        return default_font
    scripts = sorted({s for s in (script_of(ch) for ch in word) if s})
    for script_name in scripts:
        path = _scan_script_font(script_name)
        if path and font_covers(path, word):
            size = getattr(default_font, "size", None)
            if size is None:
                return default_font
            return load_font(path, size)
    return default_font


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
    """Group lines into pages; merge a tiny trailing orphan page into
    the previous page when the block still fits so fragments like
    '3:16)' never become their own slide."""
    pages = [lines[i:i + max_lines] for i in range(0, len(lines), max_lines)]
    if len(pages) >= 2:
        last = pages[-1]
        words_in_last = sum(len(l.split()) for l in last)
        if words_in_last <= 1 and len(last) + len(pages[-2]) <= max_lines + 1:
            pages[-2] = pages[-2] + last
            pages.pop()
    return pages


def choose_font_size(total_words, video_size):
    """Readable cinematic sizing, ~75% larger than before."""
    h = video_size[1]
    base = int(h * 0.175)  # 0.10 * 1.75
    if total_words > 60:
        scale = 0.52
    elif total_words > 40:
        scale = 0.62
    elif total_words > 24:
        scale = 0.74
    elif total_words > 12:
        scale = 0.86
    else:
        scale = 1.0
    size = int(base * scale)
    return max(int(h * 0.08), min(size, int(h * 0.20)))


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
            if any(l.strip() for l in chunk):
                pages.append({"lines": chunk, "font": font})
    return pages


# ===================================================================
# Timing: line-by-line entrance (from top) + 6s hold + clean fade
# ===================================================================

def ease_out_cubic(p):
    p = max(0.0, min(1.0, p))
    return 1 - (1 - p) ** 3


def schedule_pages(pages):
    """Assign durations summing to exactly TOTAL_DURATION.

    Each page wants: entrance + HOLD_SECONDS + fade-out. When the
    budget is tight everything scales down proportionally (never below
    MIN_PAGE_DURATION when the math allows); with slack, holds stretch
    so the video is exactly 45 seconds.
    """
    for p in pages:
        nl = max(1, p["n_lines"])
        entrance = min(ENTRANCE_CAP, (nl - 1) * LINE_STAGGER + LINE_FADE)
        p["entrance"] = entrance
        p["fade_out"] = PAGE_FADE_OUT
        p["raw"] = entrance + HOLD_SECONDS + PAGE_FADE_OUT

    n = len(pages)
    scale = TOTAL_DURATION / sum(p["raw"] for p in pages)
    durs = [p["raw"] * scale for p in pages]

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
        p["fade_out"] = max(0.1, min(p["fade_out"] * f, d * 0.3))
        p["entrance"] = max(0.2, min(p["entrance"] * f, d - p["fade_out"] - 0.1))
        lf = max(0.08, min(LINE_FADE * f, p["entrance"] * 0.5))
        p["line_fade"] = lf
        nl = p["n_lines"]
        if nl > 1:
            stagger = max(0.01, (p["entrance"] - lf) / (nl - 1))
        else:
            stagger = 0.0
        p["line_starts"] = [i * stagger for i in range(nl)]
        starts.append(acc)
        acc += d
    return starts

# ===================================================================
# Backgrounds: gradient / image / gif / video (cover-fit + dim)
#              (No animated overlays: no flickering borders/stars)
# ===================================================================

_LANCZOS = getattr(getattr(Image, "Resampling", Image), "LANCZOS")
_VIGNETTE_MASK = None


def _vignette_mask():
    global _VIGNETTE_MASK
    if _VIGNETTE_MASK is None:
        mask = Image.new("L", VIDEO_SIZE, 0)
        d = ImageDraw.Draw(mask)
        d.ellipse([-VIDEO_SIZE[0] * 0.2, -VIDEO_SIZE[1] * 0.2,
                   VIDEO_SIZE[0] * 1.2, VIDEO_SIZE[1] * 1.2], fill=255)
        _VIGNETTE_MASK = mask.filter(
            ImageFilter.GaussianBlur(int(140 * VIDEO_SIZE[0] / 1920)))
    return _VIGNETTE_MASK


def _cover_resize(img):
    """Resize + center-crop so the image exactly fills VIDEO_SIZE."""
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


def _finish_still(img, dim):
    """Cover-fit, optionally dim, then vignette a still background."""
    img = _cover_resize(img.convert("RGB"))
    if dim > 0:
        img = Image.blend(img, Image.new("RGB", img.size, (0, 0, 0)), dim)
    return Image.composite(img, Image.new("RGB", img.size, (0, 0, 0)),
                           _vignette_mask())


_ACTIVE_PALETTE_NAME = [None]


def pick_gradient_palette():
    """Choose the gradient palette once per video: explicit theme,
    else random. Re-uses the active choice so background and text
    tint stay consistent (and the gradient never re-picks mid-video)."""
    name = None
    if BACKGROUND_THEME and BACKGROUND_THEME.lower() != "random" and BACKGROUND_THEME in GRADIENT_PALETTES:
        name = BACKGROUND_THEME
    if not name:
        name = _ACTIVE_PALETTE_NAME[0]
    if not name or name not in GRADIENT_PALETTES:
        name = random.choice(list(GRADIENT_PALETTES.keys()))
    _ACTIVE_PALETTE_NAME[0] = name
    return name, GRADIENT_PALETTES[name]


def text_accent_color():
    """Cinematic text color matched to the active gradient palette."""
    name = _ACTIVE_PALETTE_NAME[0]
    if name and name in TEXT_ACCENTS:
        return TEXT_ACCENTS[name] + (255,)
    return DEFAULT_TEXT_ACCENT + (255,)


def make_gradient_bg():
    """Static gradient background built once (no per-frame flicker)."""
    base = create_background()

    def provider(t):
        return base.copy()

    return provider


def create_background(t=None):
    """Gradient background (theme-aware) with vignette."""
    name, (top_color, bottom_color) = pick_gradient_palette()
    print(f"Gradient palette: {name}")

    background = Image.new("RGB", VIDEO_SIZE)
    draw = ImageDraw.Draw(background)
    for y in range(VIDEO_SIZE[1]):
        ratio = y / VIDEO_SIZE[1]
        r = int(top_color[0] * (1 - ratio) + bottom_color[0] * ratio)
        g = int(top_color[1] * (1 - ratio) + bottom_color[1] * ratio)
        b = int(top_color[2] * (1 - ratio) + bottom_color[2] * ratio)
        draw.line([(0, y), (VIDEO_SIZE[0], y)], fill=(r, g, b))

    return Image.composite(background, Image.new("RGB", VIDEO_SIZE, (0, 0, 0)),
                           _vignette_mask())


def _parse_duration_hint(im, default=100):
    """Best-effort GIF frame duration in ms."""
    d = im.info.get("duration")
    return d if d and d > 0 else default


def _find_bg_file(kind):
    """Resolve a background file path: explicit env var, then any
    matching extension inside BACKGROUND_DIR."""
    explicit = {"image": BACKGROUND_IMAGE, "gif": BACKGROUND_GIF,
                "video": BACKGROUND_VIDEO}[kind]
    if explicit and os.path.isfile(explicit):
        return explicit
    if explicit:
        print(f"WARNING: {kind} background '{explicit}' not found; searching {BACKGROUND_DIR}")
    if not os.path.isdir(BACKGROUND_DIR):
        return None
    exts = {"image": (".jpg", ".jpeg", ".png", ".webp", ".bmp"),
            "gif": (".gif",), "video": (".mp4", ".mov", ".mkv", ".webm", ".avi")}[kind]
    for f in sorted(os.listdir(BACKGROUND_DIR)):
        if f.lower().endswith(exts):
            return os.path.join(BACKGROUND_DIR, f)
    return None


def make_image_bg(path):
    """Single cover-fit dimmed still, used for every frame."""
    with Image.open(path) as im:
        base = _finish_still(im, IMAGE_DIM)

    def provider(t):
        return base.copy()

    return provider


def make_gif_bg(path):
    """Precompute cover-fit dimmed GIF frames, looped over time."""
    frames = []
    offsets = [0.0]
    with Image.open(path) as im:
        n_total = getattr(im, "n_frames", 1)
        step = max(1, math.ceil(n_total / GIF_FRAME_CAP))
        for i in range(0, n_total, step):
            im.seek(i)
            frames.append(_finish_still(im, IMAGE_DIM))
            offsets.append(offsets[-1] + max(0.02, _parse_duration_hint(im) / 1000.0))
            if len(frames) >= GIF_FRAME_CAP:
                break
    if not frames:
        base = create_background()

        def provider(t):
            return base.copy()

        return provider
    total = offsets[-1]

    def provider(t):
        tt = t % total if total > 0 else 0.0
        k = max(0, min(bisect_right(offsets, tt) - 1, len(frames) - 1))
        return frames[k].copy()

    return provider


def make_video_bg(path):
    """Loop a muted video file, resized/cropped per frame, dimmed.

    Frames are pulled lazily via VideoFileClip.get_frame and cached in
    small a FIFO so playback stays smooth without precomputing 45s of
    video in RAM.
    """
    src = VideoFileClip(path, audio=False)
    if hasattr(src, "resized"):
        src = src.resized(height=VIDEO_SIZE[1] if VIDEO_SIZE[1] <= VIDEO_SIZE[0] else VIDEO_SIZE[0])
    scale = max(VIDEO_SIZE[0] / src.w, VIDEO_SIZE[1] / src.h)
    if hasattr(src, "resized"):
        src = src.resized(scale) if abs(scale - 1.0) > 0.01 else src
    cache = {}
    cache_order = []

    def provider(t):
        tt = t % max(0.1, src.duration)
        key = int(tt * 5)  # 5 fps background is plenty; moviepy interpolates frames per call anyway
        if key in cache:
            frame = cache[key]
        else:
            frame = src.get_frame(key / 5.0)
            cache[key] = frame
            cache_order.append(key)
            if len(cache_order) > 40:
                old = cache_order.pop(0)
                cache.pop(old, None)
        img = Image.fromarray(frame).convert("RGB")
        img = _cover_resize(img)
        img = Image.blend(img, Image.new("RGB", img.size, (0, 0, 0)), VIDEO_DIM)
        return img

    return provider


def resolve_background():
    """Return (provider, mode_used) based on BACKGROUND_MODE."""
    mode = BACKGROUND_MODE
    if mode not in ("gradient", "image", "gif", "video"):
        print(f"WARNING: unknown BACKGROUND_MODE '{mode}'; using gradient.")
        mode = "gradient"

    if mode == "gradient":
        return make_gradient_bg(), "gradient"

    if mode == "image":
        p = _find_bg_file("image")
        if p:
            return make_image_bg(p), "image"
        print("WARNING: no image background found; falling back to gradient.")

    if mode == "gif":
        p = _find_bg_file("gif")
        if p:
            return make_gif_bg(p), "gif"
        print("WARNING: no GIF background found; falling back to gradient.")

    if mode == "video":
        p = _find_bg_file("video")
        if p:
            try:
                return make_video_bg(p), "video"
            except Exception as e:
                print(f"WARNING: video background failed ({e}); using gradient.")

    return make_gradient_bg(), "gradient"


def compute_block_top(block_height, safe_top=SAFE_TOP, safe_bottom=SAFE_BOTTOM, bias=VERTICAL_BIAS):
    zone_height = safe_bottom - safe_top
    desired_center = safe_top + zone_height * bias
    top = desired_center - block_height / 2
    return max(safe_top, min(top, safe_bottom - block_height))


# ===================================================================
# Page rendering: line-by-line entrance (from top) + hold + fade
# ===================================================================

def render_page_lines(lines, font, default_font_path):
    """Render each line of a page as its own RGBA layer positioned at
    the top of the safe area, so lines can animate in one-by-one.

    Returns (line_layers, block_info); each entry is a dict with
    {layer (PIL RGBA), x, y} pre-rendered at full opacity.
    """
    line_height = int(font.size * LINE_SPACING_MULTIPLIER)
    block_height = line_height * len(lines)
    top = compute_block_top(block_height)

    text_fill = text_accent_color()
    stroke_w = max(2, font.size // 24)
    pad_x = int(font.size * 0.6)
    pad_y = int(font.size * 0.9)
    line_layers = []
    max_line_width = 0.0

    measure = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    for i, line in enumerate(lines):
        if not line.strip():
            continue
        y = top + i * line_height
        # measure with the page font (wrap already used it)
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
        sdraw.text((tx + 2, ty + 2), line, font=font, fill=(0, 0, 0, 160))
        sdraw.text((tx, ty + font.size * 0.08), line, font=font, fill=SHADOW_COLOR)
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
    """Fade + shift a pre-rendered line layer for its entrance."""
    a = np.array(layer_img)
    if alpha < 1.0:
        a[..., 3] = (a[..., 3].astype(np.float32) * alpha).astype(np.uint8)
    if rise_px > 0:
        shifted = np.zeros_like(a)
        r = int(round(rise_px))
        if r > 0 and r < a.shape[0]:
            shifted[r:, :, :] = a[:-r, :, :]
        a = shifted
    return a


def composite_rgba_over_rgb(bg_rgb_arr, layer_rgba_arr):
    if layer_rgba_arr is None:
        return bg_rgb_arr
    alpha = layer_rgba_arr[..., 3:4].astype(np.float32) / 255.0
    fg = layer_rgba_arr[..., :3].astype(np.float32)
    bg = bg_rgb_arr.astype(np.float32)
    out = fg * alpha + bg * (1 - alpha)
    return out.astype(np.uint8)

# ===================================================================
# TTS: ElevenLabs integration (temp files - moviepy can't read BytesIO)
# ===================================================================

def generate_tts_audio(text, language, voice_id=None):
    """Generate audio using ElevenLabs TTS API; returns a temp mp3 path."""
    if not ELEVENLABS_API_KEY:
        return None
    if not text or not text.strip():
        return None

    voice_id = voice_id or "21m00Tcm4TlvDq8ikWAM"

    url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
    headers = {
        "xi-api-key": ELEVENLABS_API_KEY,
        "Content-Type": "application/json",
    }
    data = {
        "text": text,
        "model_id": "eleven_multilingual_v2",
        "voice_settings": {"stability": 0.5, "similarity_boost": 0.75},
    }

    try:
        print(f"Generating {language} TTS audio for: {text[:50]}...")
        response = requests.post(url, json=data, headers=headers, timeout=60)
        response.raise_for_status()
        fd, tmp_path = tempfile.mkstemp(suffix=".mp3")
        with os.fdopen(fd, "wb") as fh:
            fh.write(response.content)
        return tmp_path
    except Exception as e:
        print(f"ERROR generating TTS: {e}")
        return None


def create_tts_clips(pages, starts):
    """Create audio clips synchronized with text pages."""
    tts_clips = []
    for page, start in zip(pages, starts):
        text = " ".join(page["lines"])
        language = detect_language(text)
        tmp_path = generate_tts_audio(text, language)
        if tmp_path:
            try:
                audio_clip = AudioFileClip(tmp_path)
                audio_clip = audio_clip.with_start(start) if hasattr(audio_clip, "with_start") else audio_clip.set_start(start)
                tts_clips.append(audio_clip)
            except Exception as e:
                print(f"Error loading TTS audio: {e}")
            finally:
                pass  # temp file must outlive the clip; cleaned below

    if tts_clips:
        def _cleanup():
            for c in tts_clips:
                p = getattr(c, "filename", None) or (c.reader.filename if getattr(c, "reader", None) else None)
                if p and os.path.isfile(str(p)):
                    try:
                        os.remove(str(p))
                    except OSError:
                        pass
        atexit.register(_cleanup)
    return tts_clips if tts_clips else None


# ===================================================================
# Audio: music must play until the very end of the 45s
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
    """Return an audio clip of exactly `duration` seconds, looping if needed."""
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

    # Pick the gradient palette FIRST so text tint and background match
    pick_gradient_palette()

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
    raw_pages = build_segments(telugu_text, english_text, explanation_text,
                               font_telugu, font_latin, dummy_draw)
    if not raw_pages:
        raise ValueError("No text content provided to render.")

    pages = []
    for p in raw_pages:
        default_font_path = font_telugu_path if p["font"] is font_telugu else font_latin_path
        line_layers, block_info = render_page_lines(p["lines"], p["font"], default_font_path)
        n_lines = len(line_layers)
        if n_lines == 0:
            continue
        if block_info["max_width"] > SAFE_TEXT_WIDTH + 1:
            raise ValueError(
                f"Line width {block_info['max_width']:.0f}px exceeds safe width {SAFE_TEXT_WIDTH}px: {p['lines']!r}"
            )
        if block_info["top"] < SAFE_TOP - 1 or block_info["top"] + block_info["height"] > SAFE_BOTTOM + 1:
            raise ValueError(f"Text block falls outside the vertical safe area: {p['lines']!r}")
        pages.append({"lines": p["lines"], "font": p["font"], "line_layers": line_layers,
                      "n_lines": n_lines, "block": block_info})

    if not pages:
        raise ValueError("No renderable text found after layout.")

    starts = schedule_pages(pages)

    print(f"Prepared {len(pages)} page(s) across {TOTAL_DURATION:.1f}s "
          f"(line-by-line entrance, {HOLD_SECONDS}s hold, clean fade):")
    for i, (p, s) in enumerate(zip(pages, starts)):
        preview = " / ".join(p["lines"])
        print(f"  Page {i + 1}: {s:5.2f}s -> {s + p['duration']:5.2f}s "
              f"({p['duration']:4.2f}s, entrance {p['entrance']:.2f}s)  {preview}")

    bg_provider, bg_mode = resolve_background()
    print(f"Background mode: {bg_mode}")

    def make_frame(t):
        t = min(t, TOTAL_DURATION - 1e-3)
        idx = max(0, min(bisect_right(starts, t) - 1, len(pages) - 1))
        page = pages[idx]
        local_t = t - starts[idx]

        frame_img = bg_provider(t)

        # page-level fade-away factor at the very end
        fo = page["fade_out"]
        page_alpha = 1.0
        if local_t > page["duration"] - fo:
            page_alpha = ease_out_cubic(max(0.0, (page["duration"] - local_t) / fo))

        if page_alpha > 0.01:
            lf = page["line_fade"]
            for li, (line_layer, l_start) in enumerate(zip(page["line_layers"], page["line_starts"])):
                lt = local_t - l_start
                if lt <= 0:
                    continue
                prog = min(1.0, lt / lf)
                l_alpha = ease_out_cubic(prog) * page_alpha
                if l_alpha <= 0.01:
                    continue
                rise = (1 - ease_out_cubic(prog)) * LINE_RISE_PIXELS
                arr = apply_line_alpha(line_layer["layer"], l_alpha, rise)
                frame_img.paste(Image.fromarray(arr), (line_layer["x"], line_layer["y"]), Image.fromarray(arr))

        return np.array(frame_img)

    clip = VideoClip(make_frame, duration=TOTAL_DURATION)
    clip = _compat(clip, "with_fps", "set_fps", FPS)

    # Audio: music for the full 45s (+ optional page-synced TTS)
    try:
        music_path = pick_music_file()
        audio = prepare_audio(music_path, TOTAL_DURATION)
        tts_clips = create_tts_clips(pages, starts)
        if tts_clips:
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
    bg_img = _cover_resize(create_background().resize(
        (int(thumb_size[0] * 0.67), int(thumb_size[1] * 0.67)), _LANCZOS))

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
    text_fill = text_accent_color()
    for i, line in enumerate(lines):
        w = draw.textlength(line, font=font)
        x = (thumb_size[0] - w) / 2
        y = top + i * line_height
        draw.text((x, y), line, font=font, fill=text_fill,
                  stroke_width=stroke_w, stroke_fill=(0, 0, 0))

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
