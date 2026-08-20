from google.oauth2 import service_account
import json
import os
import sys
import re
import math
import random
import colorsys
import time
from ssl import SSLError
from http.client import IncompleteRead
import unicodedata
import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageFilter
from moviepy.editor import VideoClip, AudioFileClip
from google.oauth2.credentials import Credentials as UserCredentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from googleapiclient.errors import HttpError

# ================= SHEET LAYOUT =================
# Column A = Telugu verse text
# Column B = English verse text
# Column C = optional brief explanation/note (English or Telugu) — leave blank if none
# Column D = "used" marker, written automatically by this script
# ==================================================

SHEET_ID = os.environ["SHEET_ID"]
SHEET_TAB = os.environ.get("SHEET_TAB", "Sheet1")

MUSIC_DIR = os.environ.get("MUSIC_DIR", "assets/music")
CUSTOM_BG_DIR = os.environ.get("CUSTOM_BG_DIR", "assets/backgrounds")

# Cinematic serif fonts — Telugu script needs its own font file.
FONT_PATH_TELUGU = os.environ.get(
    "FONT_PATH_TELUGU", "/usr/share/fonts/truetype/noto/NotoSerifTelugu-Bold.ttf"
)

# English text (Columns B and C) now uses Poppins instead of Noto Serif.
# Poppins isn't a default system font, so it needs to actually be present —
# download "Poppins-Bold.ttf" from Google Fonts and place it in your repo at
# assets/fonts/Poppins-Bold.ttf (or point FONT_PATH_LATIN at wherever you put
# it). If the file isn't found, this safely falls back to Noto Serif instead
# of crashing the whole run.
_FONT_PATH_LATIN_REQUESTED = os.environ.get("FONT_PATH_LATIN", "assets/fonts/Poppins-Bold.ttf")
_FONT_PATH_LATIN_FALLBACK = "/usr/share/fonts/truetype/noto/NotoSerif-Bold.ttf"
if os.path.exists(_FONT_PATH_LATIN_REQUESTED):
    FONT_PATH_LATIN = _FONT_PATH_LATIN_REQUESTED
else:
    print(f"Warning: font not found at '{_FONT_PATH_LATIN_REQUESTED}', falling back to Noto Serif.")
    FONT_PATH_LATIN = _FONT_PATH_LATIN_FALLBACK

VIDEO_DURATION = 50           # ~50s — fits a single short verse on screen
MUSIC_START_OFFSET = 10
FPS = 24
VIDEO_SIZE = (3840, 2160)   # 4K UHD

# Every pixel-based constant below was tuned for a 1280x720 reference frame.
# RENDER_SCALE keeps borders, margins, stroke widths, and star sizes visually
# identical in proportion when the output resolution changes (e.g. to 4K).
RENDER_SCALE = VIDEO_SIZE[0] / 1280

# Telugu glyphs render visibly taller than Latin ones at the same point size.
# Sizes bumped up significantly from the previous 72pt/54pt baseline so the
# verse fills the frame much more at 4K and stays easy to read.
MAIN_FONT_SIZE_LATIN = int(140 * RENDER_SCALE)
MAIN_FONT_SIZE_TELUGU = int(105 * RENDER_SCALE)

STROKE_WIDTH = int(3 * RENDER_SCALE)
OUTLAY_STROKE_WIDTH = int(9 * RENDER_SCALE)   # thicker outer "outlay" border, drawn behind the gradient fill
SHADOW_OFFSET = int(4 * RENDER_SCALE)

# A manual line break placed by hand in the sheet — either as a real newline
# (Alt+Enter / Option+Return inside the cell) or as literal typed text "\n"
# (backslash + n) — always forces a new line at that exact spot, before any
# automatic width-based wrapping happens.
LINE_GAP_PT = 1.5               # fixed vertical gap between lines
PT_TO_PX = 96 / 72               # standard 1pt = 1/72in at 96dpi, for on-screen video text

SECONDS_PER_LINE = 2.5            # how long each fully-revealed line stays on screen
MAX_REVEAL_SECONDS = 10           # safety cap per phase's reveal window
TEXT_MARGIN_X = int(130 * RENDER_SCALE)
SAFE_TOP = int(110 * RENDER_SCALE)
SAFE_BOTTOM = int(110 * RENDER_SCALE)
TRANSITION_SECONDS = 1.3   # how long each scroll-out/scroll-in transition takes

# Per-column baseline duration at VIDEO_DURATION=240. These are multiplied by
# (VIDEO_DURATION / 45) so the per-column pacing stays the same as the
# original 45-second build regardless of the actual VIDEO_DURATION.
_BASE_COLUMN_DURATIONS = {"A": 15, "B": 12, "C": 18}

# Column C often has many lines. Rather than shrinking the font tiny to cram
# them all in at once, it's split into readable groups shown one at a time
# within its own time budget, at roughly this many seconds per group.
NOTE_SECONDS_PER_PAGE = 5
NOTE_PAGE_CROSSFADE_SECONDS = 0.5

NEON_BORDER_MARGIN = int(19 * RENDER_SCALE)
NEON_BORDER_THICKNESS = int(4 * RENDER_SCALE)
NEON_GLOW_THICKNESS = int(11 * RENDER_SCALE)
NEON_CYCLE_SPEED = 0.12
NEON_SEGMENTS_PER_SIDE = 14

STARS_PER_SIDE = 5

OUTPUT_DIR = "output"

# Manual-run controls (ignored on the automatic daily schedule).
PRIVACY_STATUS = os.environ.get("PRIVACY_STATUS", "private").strip().lower()
MUSIC_CHOICE = os.environ.get("MUSIC_CHOICE", "random").strip()
BACKGROUND_THEME = os.environ.get("BACKGROUND_THEME", "random").strip()
INCLUDE_EXPLANATION = os.environ.get("INCLUDE_EXPLANATION", "Auto").strip().lower()  # auto / yes / no
TELUGU_OVERRIDE = os.environ.get("TELUGU_OVERRIDE", "").strip()
ENGLISH_OVERRIDE = os.environ.get("ENGLISH_OVERRIDE", "").strip()
EXPLANATION_OVERRIDE = os.environ.get("EXPLANATION_OVERRIDE", "").strip()
# ===================================================

GRADIENT_PALETTES = {
    "Midnight Purple":  ((18, 12, 52),  (72, 22, 100)),   # deep royal purple
    "Ocean Blue":       ((8,  30, 70),  (20, 75, 130)),   # deep navy to sapphire
    "Wine Red":         ((45, 8,  20),  (110, 25, 50)),   # dark burgundy to ruby
    "Emerald Teal":     ((8,  42, 38),  (18, 100, 82)),   # dark forest to teal
    "Sunset Amber":     ((50, 22, 8),   (140, 65, 18)),   # dark copper to amber
    "Indigo Violet":    ((22, 14, 58),  (70, 45, 155)),   # deep indigo to violet
    "Midnight Slate":   ((14, 20, 38),  (28, 45, 80)),    # near-black to slate blue
    "Magenta Plum":     ((40, 10, 48),  (110, 22, 95)),   # deep plum to magenta
}

BASE_HASHTAGS = ["#BibleVerse", "#DailyVerse", "#Faith", "#God", "#Jesus", "#Scripture"]
TELUGU_HASHTAGS = ["#TeluguChristian", "#YesuKrishtu", "#Telugu"]
ENGLISH_HASHTAGS = ["#Christian", "#Gospel", "#WordOfGod"]


# ---------------- text/script helpers ----------------

def is_telugu(text):
    return any("\u0c00" <= ch <= "\u0c7f" for ch in text)
_PUNCT_MAP = {
    "\u2018": "'", "\u2019": "'",   # curly single quotes -> straight
    "\u201c": '"', "\u201d": '"',  # curly double quotes -> straight
    "\u2013": "-", "\u2014": "-",  # en/em dash -> hyphen
    "\u2026": "...",               # ellipsis character -> three dots
}

def sanitize_text(text):
    """Some fonts (like our English Poppins font) are missing glyphs for
    'smart' typographic punctuation, which renders as empty boxes. This
    swaps those characters for plain ASCII equivalents before drawing."""
    if text is None:
        return text
    for bad, good in _PUNCT_MAP.items():
        text = text.replace(bad, good)
    return text

def extract_reference_tag(text):
    """Looks for a trailing '(Book Chapter:Verse)' style reference embedded in
    the verse text itself, used only for hashtags — not displayed separately."""
    match = re.search(r"\(([^()]+)\)\s*$", text.strip())
    if not match:
        return None
    inner = match.group(1).strip()
    first_word = inner.split()[0] if inner.split() else None
    return first_word


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


# ---------------- neon border + stars (unchanged visual system) ----------------

def hue_to_rgb(hue):
    r, g, b = colorsys.hsv_to_rgb(hue % 1.0, 1.0, 1.0)
    return (int(r * 255), int(g * 255), int(b * 255))


def dim(color, factor):
    return tuple(int(c * factor) for c in color)


def build_border_points(w, h, margin):
    points = []
    steps = NEON_SEGMENTS_PER_SIDE
    x0, y0, x1, y1 = margin, margin, w - margin, h - margin
    for i in range(steps + 1):
        points.append((x0 + (x1 - x0) * i / steps, y0))
    for i in range(1, steps + 1):
        points.append((x1, y0 + (y1 - y0) * i / steps))
    for i in range(1, steps + 1):
        points.append((x1 - (x1 - x0) * i / steps, y1))
    for i in range(1, steps + 1):
        points.append((x0, y1 - (y1 - y0) * i / steps))
    return points


def draw_neon_border(draw, size, t):
    w, h = size
    points = build_border_points(w, h, NEON_BORDER_MARGIN)
    n = len(points) - 1
    time_offset = t * NEON_CYCLE_SPEED
    for i in range(n):
        p1, p2 = points[i], points[i + 1]
        hue = (i / n) + time_offset
        color = hue_to_rgb(hue)
        draw.line([p1, p2], fill=dim(color, 0.45), width=NEON_GLOW_THICKNESS)
        draw.line([p1, p2], fill=color, width=NEON_BORDER_THICKNESS)


def make_star_positions(size):
    w, h = size
    outer = max(NEON_BORDER_MARGIN - 10, 6)
    edge_margin = int(40 * RENDER_SCALE)
    stars = []

    def add_row(x_vals, y_vals):
        for x, y in zip(x_vals, y_vals):
            stars.append({"x": x, "y": y, "phase": random.uniform(0, math.tau), "speed": random.uniform(1.2, 2.4)})

    xs_top = np.linspace(edge_margin, w - edge_margin, STARS_PER_SIDE)
    add_row(xs_top, [outer] * STARS_PER_SIDE)
    add_row(xs_top, [h - outer] * STARS_PER_SIDE)
    ys_side = np.linspace(edge_margin, h - edge_margin, STARS_PER_SIDE)
    add_row([outer] * STARS_PER_SIDE, ys_side)
    add_row([w - outer] * STARS_PER_SIDE, ys_side)
    return stars


def draw_star_mark(draw, x, y, size, color):
    draw.line([x - size, y, x + size, y], fill=color, width=max(2, int(2 * RENDER_SCALE)))
    draw.line([x, y - size, x, y + size], fill=color, width=max(2, int(2 * RENDER_SCALE)))
    draw.ellipse([x - 2, y - 2, x + 2, y + 2], fill=color)


def draw_twinkling_stars(draw, stars, t):
    for s in stars:
        brightness = 0.4 + 0.6 * (0.5 + 0.5 * math.sin(s["speed"] * t + s["phase"]))
        size = (5 + 5 * brightness) * RENDER_SCALE
        shade = int(255 * brightness)
        draw_star_mark(draw, s["x"], s["y"], size, (shade, shade, shade))


# ---------------- background ----------------

def find_custom_background():
    if not os.path.isdir(CUSTOM_BG_DIR):
        return None
    for ext in (".jpg", ".jpeg", ".png"):
        path = os.path.join(CUSTOM_BG_DIR, "custom_background" + ext)
        if os.path.exists(path):
            return path
    return None


def apply_vignette(img, size):
    w, h = size
    vignette = Image.new("L", size, 0)
    vdraw = ImageDraw.Draw(vignette)
    vdraw.ellipse([-w * 0.3, -h * 0.3, w * 1.3, h * 1.3], fill=255)
    vignette = vignette.filter(ImageFilter.GaussianBlur(int(120 * RENDER_SCALE)))
    dark = Image.new("RGB", size, (0, 0, 0))
    return Image.composite(img, dark, vignette)


def load_custom_background(path, size):
    img = Image.open(path).convert("RGB")
    target_w, target_h = size
    src_w, src_h = img.size
    scale = max(target_w / src_w, target_h / src_h)
    new_w, new_h = int(src_w * scale), int(src_h * scale)
    img = img.resize((new_w, new_h), Image.LANCZOS)
    left = (new_w - target_w) // 2
    top = (new_h - target_h) // 2
    img = img.crop((left, top, left + target_w, top + target_h))
    return apply_vignette(img, size)


def make_gradient_background(size):
    if BACKGROUND_THEME and BACKGROUND_THEME.lower() != "random" and BACKGROUND_THEME in GRADIENT_PALETTES:
        top_color, bottom_color = GRADIENT_PALETTES[BACKGROUND_THEME]
    else:
        top_color, bottom_color = random.choice(list(GRADIENT_PALETTES.values()))

    w, h = size
    top = np.array(top_color, dtype=float)
    bottom = np.array(bottom_color, dtype=float)
    t = np.linspace(0, 1, h).reshape(h, 1, 1)
    gradient = (top * (1 - t) + bottom * t).astype(np.uint8)
    gradient = np.repeat(gradient, w, axis=1)
    img = Image.fromarray(gradient, mode="RGB")
    return apply_vignette(img, size)


def make_background(size):
    wants_custom = BACKGROUND_THEME.lower() in ("custom", "custom image", "my custom image", "my uploaded image")
    if wants_custom:
        custom_path = find_custom_background()
        if custom_path:
            print(f"Using custom background: {custom_path}")
            return load_custom_background(custom_path, size)
        print("Custom background requested but none found — using a gradient instead.")
    return make_gradient_background(size)


def pick_music_file():
    music_files = sorted(f for f in os.listdir(MUSIC_DIR) if f.lower().endswith(".mp3"))
    if not music_files:
        raise FileNotFoundError(f"No .mp3 files found in {MUSIC_DIR}")
    if MUSIC_CHOICE and MUSIC_CHOICE.lower() != "random":
        for f in music_files:
            if f.lower() == MUSIC_CHOICE.lower():
                return os.path.join(MUSIC_DIR, f)
        print(f"Warning: '{MUSIC_CHOICE}' not found, picking randomly instead.")
    return os.path.join(MUSIC_DIR, random.choice(music_files))


# ---------------- Sheets / YouTube ----------------

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
        sa_info,
        scopes=["https://www.googleapis.com/auth/spreadsheets"]
    )
    return build("sheets", "v4", credentials=sa_creds)


def get_youtube_service(creds):
    return build("youtube", "v3", credentials=creds)


def fetch_next_row(service):
    """A = Telugu, B = English, C = optional explanation, D = 'used' marker."""
    range_ = f"{SHEET_TAB}!A2:D"
    result = call_with_retries(
        lambda: service.spreadsheets().values().get(spreadsheetId=SHEET_ID, range=range_).execute()
    )
    rows = result.get("values", [])
    for i, row in enumerate(rows):
        telugu = row[0] if len(row) > 0 else ""
        english = row[1] if len(row) > 1 else ""
        explanation = row[2] if len(row) > 2 else ""
        used = row[3] if len(row) > 3 else ""
        if (telugu or english) and used.strip().lower() != "used":
            row_number = i + 2
            return row_number, telugu.strip(), english.strip(), explanation.strip()
    return None, None, None, None


def mark_row_used(service, row_number):
    call_with_retries(lambda: service.spreadsheets().values().update(
        spreadsheetId=SHEET_ID,
        range=f"{SHEET_TAB}!D{row_number}",
        valueInputOption="RAW",
        body={"values": [["used"]]},
    ).execute())


# ---------------- text fitting + drawing ----------------

def text_width(draw, text, font):
    bbox = draw.textbbox((0, 0), text, font=font, stroke_width=STROKE_WIDTH)
    return bbox[2] - bbox[0]


def normalize_manual_breaks(text):
    """A break placed by hand in the sheet may arrive in several forms
    depending on how it was typed or pasted:
    - a real newline (Alt+Enter / Option+Return in the cell)
    - literal "\n" typed as two characters (backslash + n)
    - literal "\\n" — an extra escaped backslash, common when text is
      copy-pasted from a source that already escapes backslashes
    - a Windows-style "\r\n", or a stray lone "\r"
    All of these are normalized to a single real newline so downstream
    code only ever has to handle one case."""
    if text is None:
        return text
    text = text.replace("\\\\n", "\n")   # escaped backslash + n  -> break (handle first)
    text = text.replace("\\n", "\n")      # plain backslash + n    -> break
    text = text.replace("\r\n", "\n")     # Windows real newline   -> break
    text = text.replace("\r", "\n")       # stray carriage return  -> break
    return text


def wrap_text(draw, text, font, max_width):
    """Wraps text to max_width. Any manual break ("\n") in the text always
    starts a new line first. If a single word is itself wider than
    max_width (common with long Telugu compound words), it's broken
    character-by-character so it can never run off the edge of the frame."""
    text = normalize_manual_breaks(text)
    lines = []
    for segment in text.split("\n"):
        words = segment.split()
        current = ""
        for word in words:
            test = f"{current} {word}".strip()
            if text_width(draw, test, font) <= max_width:
                current = test
                continue
            if current:
                lines.append(current)
                current = ""
            if text_width(draw, word, font) > max_width:
                piece = ""
                for ch in word:
                    test_piece = piece + ch
                    if text_width(draw, test_piece, font) <= max_width:
                        piece = test_piece
                    else:
                        if piece:
                            lines.append(piece)
                        piece = ch
                current = piece
            else:
                current = word
        lines.append(current)
    return lines


def compute_line_height(font):
    """Real line height: the font's own ascent+descent, plus a fixed 2pt
    gap — not a multiplier of font size, so the gap stays exactly 2pt
    (scaled for the render resolution) regardless of how much the font
    has been shrunk to fit."""
    ascent, descent = font.getmetrics()
    gap_px = LINE_GAP_PT * PT_TO_PX * RENDER_SCALE
    return int(round(ascent + descent + gap_px))

def fit_text_block(draw, full_text, font_path, initial_size, max_width, max_height, min_size=None):
    """Shrinks font until the full text wraps into a block that fits the
    available height. Floor defaults to a resolution-scaled 16pt, but a
    phase can pass a higher min_size (e.g. Column C's note text) so long
    text doesn't shrink far more than the main verse just because it has
    more words. If it still doesn't fit at the floor, it renders at the
    floor anyway (slight overflow) rather than shrinking further."""
    size = initial_size
    absolute_floor = min_size if min_size is not None else max(16, int(16 * RENDER_SCALE))
    while size >= absolute_floor:
        font = ImageFont.truetype(font_path, size)
        lines = wrap_text(draw, full_text, font, max_width)
        line_height = compute_line_height(font)
        if len(lines) * line_height <= max_height:
            return font, lines, line_height
        size -= 2
    font = ImageFont.truetype(font_path, absolute_floor)
    lines = wrap_text(draw, full_text, font, max_width)
    return font, lines, compute_line_height(font)


def make_vertical_gradient(size, color_top, color_bottom):
    w, h = size
    top = np.array(color_top, dtype=float)
    bottom = np.array(color_bottom, dtype=float)
    t = np.linspace(0, 1, h).reshape(h, 1, 1)
    grad = (top * (1 - t) + bottom * t).astype(np.uint8)
    grad = np.repeat(grad, w, axis=1)
    return Image.fromarray(grad, mode="RGB")


def draw_cinematic_text(img, draw, text, font, x, y, fill_top, fill_bottom, outlay_fill, inner_edge, block_top, block_height):
    """Cinematic text: drop shadow -> thick solid outlay border -> a
    vertical-gradient inlay fill clipped to the glyph shapes -> a thin
    crisp inner edge on top for definition.
    
    block_top / block_height define the vertical span of the WHOLE text block
    so the gradient runs from fill_top at the first line to fill_bottom at the
    last — making the color shift clearly visible rather than landing on one
    washed-out mid-point of a full-frame gradient."""
    # drop shadow
    draw.text((x + SHADOW_OFFSET, y + SHADOW_OFFSET), text, font=font, fill=(0, 0, 0))

    # outlay: thick solid border
    draw.text((x, y), text, font=font, fill=outlay_fill,
               stroke_width=OUTLAY_STROKE_WIDTH, stroke_fill=outlay_fill)

    # thin crisp inner edge
    draw.text((x, y), text, font=font, fill=inner_edge,
               stroke_width=max(1, STROKE_WIDTH // 3), stroke_fill=inner_edge)

    # inlay: gradient built across the BLOCK height only, then pasted at
    # the correct y-offset inside the full frame so each line gets its
    # proportional color slice and the result is clearly visible gold.
    mask_img = Image.new("L", img.size, 0)
    mask_draw = ImageDraw.Draw(mask_img)
    mask_draw.text((x, y), text, font=font, fill=255)

    bh = max(block_height, 1)
    grad_strip = Image.new("RGB", (img.size[0], bh))
    gd = ImageDraw.Draw(grad_strip)
    top_c = np.array(fill_top, dtype=float)
    bot_c = np.array(fill_bottom, dtype=float)
    for row in range(bh):
        t = row / bh
        color = tuple((top_c * (1 - t) + bot_c * t).astype(np.uint8).tolist())
        gd.line([(0, row), (img.size[0], row)], fill=color)

    # paste the strip onto a full-frame canvas at block_top so coordinates align
    gradient = Image.new("RGB", img.size, (0, 0, 0))
    gradient.paste(grad_strip, (0, block_top))
    img.paste(gradient, (0, 0), mask_img)


def draw_text_block(img, draw, lines, font, line_height, size, y_offset, colors):
    fill_top, fill_bottom, outlay_fill, inner_edge = colors
    total_height = len(lines) * line_height
    block_top = int((size[1] - total_height) // 2 - 40 + y_offset)
    y = block_top
    for line in lines:
        w = text_width(draw, line, font)
        x = max(TEXT_MARGIN_X, (size[0] - w) // 2)
        draw_cinematic_text(img, draw, line, font, x, y,
                            fill_top, fill_bottom, outlay_fill, inner_edge,
                            block_top, total_height)
        y += line_height


# ---------------- scene / phase building ----------------

def build_phase(text, style, size, column):
    """Prepares one phase: picks font/size/colors by script + style, and fits
    the full text to the whole available frame (each phase gets the screen
    to itself, so nothing ever fights another block for space)."""
    telugu = is_telugu(text)
    font_path = FONT_PATH_TELUGU if telugu else FONT_PATH_LATIN
    initial_size = MAIN_FONT_SIZE_TELUGU if telugu else MAIN_FONT_SIZE_LATIN

    if style == "main":
        # Inlay: soft ivory fading to rich gold. Outlay: deep espresso-bronze
        # (replaces the previous green outline) for a premium, cinematic,
        # engraved-plaque look that reads clearly on any background.
        colors = ((255, 248, 225), (255, 209, 112), (25, 15, 8), (128, 82, 24))
    else:  # "note" — the optional explanation, same size as main, distinct palette
        # Inlay: warm amber-gold, slightly deeper than the main text so the
        # two remain easy to tell apart. Same espresso-bronze outlay for
        # visual consistency across the whole video.
        colors = ((255, 233, 186), (224, 160, 64), (25, 15, 8), (107, 66, 18))

    max_width = size[0] - (TEXT_MARGIN_X * 2)
    max_height = size[1] - SAFE_TOP - SAFE_BOTTOM

       # Note text (Column C): 10% smaller than the main verse text (per
    # request), with a floor of 80% of that reduced size so it stays
    # readable even when it can't fully fit.
    if style == "note":
        initial_size = int(initial_size * 0.9)
        min_size = int(initial_size * 0.8)
    else:
        # Main verse text (Columns A/B) now has a real floor too, so a
        # long English verse never shrinks to an unreadable size just
        # because it has more words than the Telugu version.
        min_size = int(initial_size * 0.55)

    probe_img = Image.new("RGB", size)
    probe_draw = ImageDraw.Draw(probe_img)
    font, lines, line_height = fit_text_block(
        probe_draw, text, font_path, initial_size, max_width, max_height, min_size=min_size
    )

    return {
        "text": text, "font": font, "lines": lines, "line_height": line_height,
        "colors": colors, "column": column,
    }


def render_video_frame(background, size, phases, t, stars):
    img = background.copy()
    draw = ImageDraw.Draw(img)

    draw_neon_border(draw, size, t)
    draw_twinkling_stars(draw, stars, t)

    num_phases = len(phases)

    # Each phase has its own fixed duration (Column A/B/C timing) rather than
    # an equal split — find which phase "t" currently falls into.
    idx = 0
    elapsed = 0.0
    for i, ph in enumerate(phases):
        if t < elapsed + ph["duration"] or i == num_phases - 1:
            idx = i
            break
        elapsed += ph["duration"]
    phase_duration = phases[idx]["duration"]
    tl = t - elapsed
    phase = phases[idx]

    in_transition = tl >= (phase_duration - TRANSITION_SECONDS) and idx < num_phases - 1
    reveal_duration = phase.get("reveal_duration", 0)

    if in_transition:
        progress = (tl - (phase_duration - TRANSITION_SECONDS)) / TRANSITION_SECONDS
        progress = max(0.0, min(1.0, progress))
        ease = progress * progress * (3 - 2 * progress)  # smoothstep

        out_offset = -ease * (size[1])
        draw_text_block(img, draw, phase["lines"], phase["font"], phase["line_height"], size, out_offset, phase["colors"])

        next_phase = phases[idx + 1]
        # If the incoming phase is paged (Column C), scroll in showing its
        # first page/group only — not the entire unwrapped block.
        next_lines = next_phase["pages"][0] if "pages" in next_phase else next_phase["lines"]
        in_offset = (1 - ease) * size[1]
        draw_text_block(img, draw, next_lines, next_phase["font"], next_phase["line_height"], size, in_offset, next_phase["colors"])

    elif idx == 0 and tl < reveal_duration:
        # Line-by-line reveal: each fully-formed line of the phase is shown
        # one at a time, dwelling for SECONDS_PER_LINE before the next line
        # joins it. We rebuild the wrap from the visible-prefix text so each
        # line drops in already wrapped at the same width the final block
        # uses — no reflow jump when the last line arrives.
        all_lines = phase["lines"]
        progress = tl / reveal_duration if reveal_duration > 0 else 1.0
        n_to_show = min(len(all_lines), max(1, int(progress * len(all_lines)) + 1))
        visible_lines = all_lines[:n_to_show]
        draw_text_block(img, draw, visible_lines, phase["font"], phase["line_height"], size, 0, phase["colors"])

    elif "pages" in phase:
        # Column C: cycle through its grouped lines sequentially within its
        # own time budget, with a short crossfade between groups.
        pages = phase["pages"]
        page_duration = phase["page_duration"]
        num_pages = len(pages)
        page_idx = min(int(tl // page_duration), num_pages - 1)
        local = tl - page_idx * page_duration
        crossfade = min(NOTE_PAGE_CROSSFADE_SECONDS, page_duration * 0.3)

        if page_idx > 0 and local < crossfade:
            alpha = local / crossfade if crossfade > 0 else 1.0
            prev_img = img.copy()
            prev_draw = ImageDraw.Draw(prev_img)
            draw_text_block(prev_img, prev_draw, pages[page_idx - 1], phase["font"], phase["line_height"], size, 0, phase["colors"])
            curr_img = img.copy()
            curr_draw = ImageDraw.Draw(curr_img)
            draw_text_block(curr_img, curr_draw, pages[page_idx], phase["font"], phase["line_height"], size, 0, phase["colors"])
            img = Image.blend(prev_img, curr_img, alpha)
        else:
            draw_text_block(img, draw, pages[page_idx], phase["font"], phase["line_height"], size, 0, phase["colors"])

    else:
        draw_text_block(img, draw, phase["lines"], phase["font"], phase["line_height"], size, 0, phase["colors"])

    return np.array(img.convert("RGB"))


def build_video(telugu_text, english_text, explanation_text):
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    size = VIDEO_SIZE
    background = make_background(size)
    stars = make_star_positions(size)

    phase_specs = []
    if telugu_text:
        phase_specs.append((telugu_text, "main", "A"))
    if english_text:
        phase_specs.append((english_text, "main", "B"))

    include_explanation = bool(explanation_text) and INCLUDE_EXPLANATION != "no"
    if INCLUDE_EXPLANATION == "yes" and not explanation_text:
        include_explanation = False
    if include_explanation:
        phase_specs.append((explanation_text, "note", "C"))

    if not phase_specs:
        raise ValueError("No text to render — Telugu and English are both empty.")

    phases = [build_phase(text, style, size, column) for text, style, column in phase_specs]

    # Per-column durations scale with VIDEO_DURATION so the original
    # 45s pacing is preserved regardless of how long the final clip is.
    # If a column is missing, the remaining columns are scaled up so the
    # video always totals exactly VIDEO_DURATION.
    intended_total = sum(_BASE_COLUMN_DURATIONS[p["column"]] for p in phases)
    scale = VIDEO_DURATION / intended_total if intended_total > 0 else 1
    for p in phases:
        p["duration"] = _BASE_COLUMN_DURATIONS[p["column"]] * scale

    # The first phase reveals its text line-by-line. The reveal window is
    # one line per SECONDS_PER_LINE, scaled to a comfortable fraction of
    # the phase's own duration (no word-count cap) — long verses simply
    # take longer to reveal, which is what we want.
    n_lines_first = max(1, len(phases[0]["lines"]))
    line_based = n_lines_first * SECONDS_PER_LINE
    phases[0]["reveal_duration"] = min(line_based, phases[0]["duration"] - TRANSITION_SECONDS)

    # Column C (the note/explanation) often has many lines. Rather than
    # cramming them all into one shrunken block, group them into readable
    # "pages" shown sequentially within Column C's own time budget.
    for p in phases:
        if p["column"] != "C":
            continue
        total_lines = len(p["lines"])
        target_pages = max(1, round(p["duration"] / NOTE_SECONDS_PER_PAGE))
        num_pages = max(1, min(target_pages, total_lines))
        lines_per_page = math.ceil(total_lines / num_pages) if total_lines else 1
        pages = [p["lines"][i:i + lines_per_page] for i in range(0, total_lines, lines_per_page)] or [[]]
        p["pages"] = pages
        p["page_duration"] = p["duration"] / len(pages)

    # Frames are rendered on demand by moviepy (one at a time) rather than all
    # built into a Python list up front. At 4K, holding every frame in memory
    # simultaneously would need ~25GB+ of RAM for a 45s clip — this streams
    # instead, so memory use stays flat regardless of resolution or duration.
    def make_frame(t):
        return render_video_frame(background, size, phases, t, stars)
    clip = VideoClip(make_frame, duration=VIDEO_DURATION).set_fps(FPS)
    chosen_music = pick_music_file()
    music_source = AudioFileClip(chosen_music)
    end_time = min(MUSIC_START_OFFSET + VIDEO_DURATION, music_source.duration)
    audio = music_source.subclip(MUSIC_START_OFFSET, end_time)
    clip = clip.set_audio(audio)
    output_path = os.path.join(OUTPUT_DIR, "verse_video.mp4")
    clip.write_videofile(
        output_path, fps=FPS, codec="libx264", audio_codec="aac",
        bitrate="40M", preset="medium",
        ffmpeg_params=["-pix_fmt", "yuv420p"],
    )
    return output_path


def call_with_retries(func, max_retries=5, base_delay=5):
    """Retries on dropped connections (common on GitHub Actions runners
    talking to Google's servers) with increasing wait time between tries.
    Also retries on transient HTTP errors from Google's API itself (503
    'service unavailable', 500, 502, 504, and 429 rate-limit) — but NOT on
    real errors like 403/404, which will never succeed on retry."""
    RETRYABLE_HTTP_STATUSES = {429, 500, 502, 503, 504}
    for attempt in range(1, max_retries + 1):
        try:
            return func()
        except HttpError as e:
            status = e.resp.status if getattr(e, "resp", None) else None
            if status not in RETRYABLE_HTTP_STATUSES or attempt == max_retries:
                raise
            delay = base_delay * (2 ** (attempt - 1))
            print(f"Google API returned {status} — retrying in {delay}s (attempt {attempt}/{max_retries})...")
            time.sleep(delay)
        except (SSLError, ConnectionError, IncompleteRead, TimeoutError) as e:
            if attempt == max_retries:
                raise
            delay = base_delay * (2 ** (attempt - 1))
            print(f"Network hiccup ({e}) — retrying in {delay}s (attempt {attempt}/{max_retries})...")
            time.sleep(delay)


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
def main():
    creds = get_user_credentials()
    sheets_service = get_sheets_service()

    row_number = None
    if TELUGU_OVERRIDE or ENGLISH_OVERRIDE:
        telugu_text, english_text, explanation_text = TELUGU_OVERRIDE, ENGLISH_OVERRIDE, EXPLANATION_OVERRIDE
        print(f"Using override text — Telugu: {telugu_text!r}  English: {english_text!r}  Explanation: {explanation_text!r}")
    else:
        row_number, telugu_text, english_text, explanation_text = fetch_next_row(sheets_service)
        if not telugu_text and not english_text:
            print("No unused rows found in the sheet. Exiting.")
            sys.exit(0)
        print(f"Selected row {row_number} — Telugu: {telugu_text!r}  English: {english_text!r}  Explanation: {explanation_text!r}")
    telugu_text = sanitize_text(telugu_text)
    english_text = sanitize_text(english_text)
    explanation_text = sanitize_text(explanation_text)
    video_path = build_video(telugu_text, english_text, explanation_text)

    youtube_service = get_youtube_service(creds)
    upload_to_youtube(youtube_service, video_path, telugu_text, english_text)

    if row_number is not None:
        mark_row_used(sheets_service, row_number)

    print("Done.")


if __name__ == "__main__":
    main()
