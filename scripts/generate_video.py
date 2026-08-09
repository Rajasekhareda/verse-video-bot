import os
import sys
import re
import math
import random
import colorsys
import unicodedata

import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageFilter
from moviepy.editor import ImageSequenceClip, AudioFileClip

from google.oauth2.credentials import Credentials as UserCredentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

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
FONT_PATH_LATIN = os.environ.get(
    "FONT_PATH_LATIN", "/usr/share/fonts/truetype/noto/NotoSerif-Bold.ttf"
)

VIDEO_DURATION = 45
MUSIC_START_OFFSET = 10
FPS = 24
VIDEO_SIZE = (1280, 720)

# Telugu glyphs render visibly taller than Latin ones at the same point size.
MAIN_FONT_SIZE_LATIN = 72
MAIN_FONT_SIZE_TELUGU = 54
NOTE_FONT_SIZE_LATIN = 44
NOTE_FONT_SIZE_TELUGU = 34

STROKE_WIDTH = 3
SECONDS_PER_WORD = 0.35
MAX_REVEAL_SECONDS = 12
TEXT_MARGIN_X = 130
SAFE_TOP = 110
SAFE_BOTTOM = 110
TRANSITION_SECONDS = 1.3   # how long each scroll-out/scroll-in transition takes

NEON_BORDER_MARGIN = 19
NEON_BORDER_THICKNESS = 4
NEON_GLOW_THICKNESS = 11
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
    "Midnight Purple": ((15, 12, 45), (90, 30, 110)),
    "Ocean Blue": ((10, 25, 55), (30, 90, 130)),
    "Wine Red": ((40, 10, 30), (140, 40, 60)),
    "Emerald Teal": ((10, 35, 30), (30, 110, 90)),
    "Sunset Amber": ((45, 20, 10), (150, 80, 30)),
    "Indigo Violet": ((20, 15, 50), (80, 60, 150)),
    "Deep Teal": ((5, 20, 25), (20, 80, 100)),
    "Magenta Plum": ((35, 10, 45), (120, 30, 100)),
}

BASE_HASHTAGS = ["#BibleVerse", "#DailyVerse", "#Faith", "#God", "#Jesus", "#Scripture"]
TELUGU_HASHTAGS = ["#TeluguChristian", "#YesuKrishtu", "#Telugu"]
ENGLISH_HASHTAGS = ["#Christian", "#Gospel", "#WordOfGod"]


# ---------------- text/script helpers ----------------

def is_telugu(text):
    return any("\u0c00" <= ch <= "\u0c7f" for ch in text)


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
    stars = []

    def add_row(x_vals, y_vals):
        for x, y in zip(x_vals, y_vals):
            stars.append({"x": x, "y": y, "phase": random.uniform(0, math.tau), "speed": random.uniform(1.2, 2.4)})

    xs_top = np.linspace(40, w - 40, STARS_PER_SIDE)
    add_row(xs_top, [outer] * STARS_PER_SIDE)
    add_row(xs_top, [h - outer] * STARS_PER_SIDE)
    ys_side = np.linspace(40, h - 40, STARS_PER_SIDE)
    add_row([outer] * STARS_PER_SIDE, ys_side)
    add_row([w - outer] * STARS_PER_SIDE, ys_side)
    return stars


def draw_star_mark(draw, x, y, size, color):
    draw.line([x - size, y, x + size, y], fill=color, width=2)
    draw.line([x, y - size, x, y + size], fill=color, width=2)
    draw.ellipse([x - 2, y - 2, x + 2, y + 2], fill=color)


def draw_twinkling_stars(draw, stars, t):
    for s in stars:
        brightness = 0.4 + 0.6 * (0.5 + 0.5 * math.sin(s["speed"] * t + s["phase"]))
        size = 5 + 5 * brightness
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
    vignette = vignette.filter(ImageFilter.GaussianBlur(120))
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


def get_sheets_service(creds):
    return build("sheets", "v4", credentials=creds)


def get_youtube_service(creds):
    return build("youtube", "v3", credentials=creds)


def fetch_next_row(service):
    """A = Telugu, B = English, C = optional explanation, D = 'used' marker."""
    range_ = f"{SHEET_TAB}!A2:D"
    result = service.spreadsheets().values().get(spreadsheetId=SHEET_ID, range=range_).execute()
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
    service.spreadsheets().values().update(
        spreadsheetId=SHEET_ID,
        range=f"{SHEET_TAB}!D{row_number}",
        valueInputOption="RAW",
        body={"values": [["used"]]},
    ).execute()


# ---------------- text fitting + drawing ----------------

def text_width(draw, text, font):
    bbox = draw.textbbox((0, 0), text, font=font, stroke_width=STROKE_WIDTH)
    return bbox[2] - bbox[0]


def wrap_text(draw, text, font, max_width):
    words = text.split()
    lines, current = [], ""
    for word in words:
        test = f"{current} {word}".strip()
        if text_width(draw, test, font) <= max_width:
            current = test
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def fit_text_block(draw, full_text, font_path, initial_size, max_width, max_height):
    """Shrinks font until the full text wraps into a block that fits the
    available height. Absolute floor of 16pt — always fits, no matter how long."""
    size = initial_size
    absolute_floor = 16
    while size >= absolute_floor:
        font = ImageFont.truetype(font_path, size)
        lines = wrap_text(draw, full_text, font, max_width)
        line_height = int(size * 1.3)
        if len(lines) * line_height <= max_height:
            return font, lines, line_height
        size -= 2
    font = ImageFont.truetype(font_path, absolute_floor)
    lines = wrap_text(draw, full_text, font, max_width)
    return font, lines, int(absolute_floor * 1.3)


def draw_cinematic_text(draw, text, font, x, y, fill, stroke_fill):
    draw.text((x + 4, y + 4), text, font=font, fill=(0, 0, 0))
    draw.text((x, y), text, font=font, fill=fill, stroke_width=STROKE_WIDTH, stroke_fill=stroke_fill)


def draw_text_block(draw, lines, font, line_height, size, y_offset, fill, stroke_fill):
    total_height = len(lines) * line_height
    y = (size[1] - total_height) // 2 - 40 + y_offset
    for line in lines:
        w = text_width(draw, line, font)
        x = max(TEXT_MARGIN_X, (size[0] - w) // 2)
        draw_cinematic_text(draw, line, font, x, y, fill, stroke_fill)
        y += line_height


# ---------------- scene / phase building ----------------

def build_phase(text, style, size):
    """Prepares one phase: picks font/size/color by script + style, and fits
    the full text to the whole available frame (each phase gets the screen
    to itself, so nothing ever fights another block for space)."""
    telugu = is_telugu(text)
    font_path = FONT_PATH_TELUGU if telugu else FONT_PATH_LATIN

    if style == "main":
        initial_size = MAIN_FONT_SIZE_TELUGU if telugu else MAIN_FONT_SIZE_LATIN
        fill, stroke_fill = (255, 255, 255), (10, 10, 20)
    else:  # "note" — the optional explanation, styled a little differently
        initial_size = NOTE_FONT_SIZE_TELUGU if telugu else NOTE_FONT_SIZE_LATIN
        fill, stroke_fill = (255, 215, 0), (60, 40, 0)

    max_width = size[0] - (TEXT_MARGIN_X * 2)
    max_height = size[1] - SAFE_TOP - SAFE_BOTTOM

    probe_img = Image.new("RGB", size)
    probe_draw = ImageDraw.Draw(probe_img)
    font, lines, line_height = fit_text_block(probe_draw, text, font_path, initial_size, max_width, max_height)

    return {
        "text": text, "font": font, "lines": lines, "line_height": line_height,
        "fill": fill, "stroke_fill": stroke_fill,
    }


def render_video_frame(background, size, phases, t, stars):
    img = background.copy()
    draw = ImageDraw.Draw(img)

    draw_neon_border(draw, size, t)
    draw_twinkling_stars(draw, stars, t)

    num_phases = len(phases)
    phase_duration = VIDEO_DURATION / num_phases
    idx = min(int(t // phase_duration), num_phases - 1)
    tl = t - idx * phase_duration
    phase = phases[idx]

    in_transition = tl >= (phase_duration - TRANSITION_SECONDS) and idx < num_phases - 1
    reveal_duration = phase.get("reveal_duration", 0)

    if in_transition:
        progress = (tl - (phase_duration - TRANSITION_SECONDS)) / TRANSITION_SECONDS
        progress = max(0.0, min(1.0, progress))
        ease = progress * progress * (3 - 2 * progress)  # smoothstep

        out_offset = -ease * (size[1])
        draw_text_block(draw, phase["lines"], phase["font"], phase["line_height"], size, out_offset, phase["fill"], phase["stroke_fill"])

        next_phase = phases[idx + 1]
        in_offset = (1 - ease) * size[1]
        draw_text_block(draw, next_phase["lines"], next_phase["font"], next_phase["line_height"], size, in_offset, next_phase["fill"], next_phase["stroke_fill"])

    elif idx == 0 and tl < reveal_duration:
        words = phase["text"].split()
        n_words = len(words)
        words_to_show = min(n_words, int(tl / SECONDS_PER_WORD) + 1) if SECONDS_PER_WORD > 0 else n_words
        visible_text = " ".join(words[:words_to_show])
        max_width = size[0] - (TEXT_MARGIN_X * 2)
        lines = wrap_text(draw, visible_text, phase["font"], max_width)
        draw_text_block(draw, lines, phase["font"], phase["line_height"], size, 0, phase["fill"], phase["stroke_fill"])

    else:
        draw_text_block(draw, phase["lines"], phase["font"], phase["line_height"], size, 0, phase["fill"], phase["stroke_fill"])

    return np.array(img.convert("RGB"))


def build_video(telugu_text, english_text, explanation_text):
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    size = VIDEO_SIZE
    background = make_background(size)
    stars = make_star_positions(size)

    phase_specs = []
    if telugu_text:
        phase_specs.append((telugu_text, "main"))
    if english_text:
        phase_specs.append((english_text, "main"))

    include_explanation = bool(explanation_text) and INCLUDE_EXPLANATION != "no"
    if INCLUDE_EXPLANATION == "yes" and not explanation_text:
        include_explanation = False
    if include_explanation:
        phase_specs.append((explanation_text, "note"))

    if not phase_specs:
        raise ValueError("No text to render — Telugu and English are both empty.")

    phases = [build_phase(text, style, size) for text, style in phase_specs]

    n_words_first = len(phases[0]["text"].split())
    phase_duration = VIDEO_DURATION / len(phases)
    phases[0]["reveal_duration"] = min(n_words_first * SECONDS_PER_WORD, MAX_REVEAL_SECONDS, phase_duration * 0.6)

    total_frames = VIDEO_DURATION * FPS
    frames = [render_video_frame(background, size, phases, f / FPS, stars) for f in range(total_frames)]

    clip = ImageSequenceClip(frames, fps=FPS)

    chosen_music = pick_music_file()
    audio = AudioFileClip(chosen_music).subclip(MUSIC_START_OFFSET, MUSIC_START_OFFSET + VIDEO_DURATION)
    clip = clip.set_audio(audio)

    output_path = os.path.join(OUTPUT_DIR, "verse_video.mp4")
    clip.write_videofile(output_path, fps=FPS, codec="libx264", audio_codec="aac")
    return output_path


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
        status, response = request.next_chunk()
        if status:
            print(f"Upload progress: {int(status.progress() * 100)}%")
    print(f"Uploaded video ID: {response['id']} (privacy: {privacy})")
    return response["id"]


def main():
    creds = get_user_credentials()
    sheets_service = get_sheets_service(creds)

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

    video_path = build_video(telugu_text, english_text, explanation_text)

    youtube_service = get_youtube_service(creds)
    upload_to_youtube(youtube_service, video_path, telugu_text, english_text)

    if row_number is not None:
        mark_row_used(sheets_service, row_number)

    print("Done.")


if __name__ == "__main__":
    main()
