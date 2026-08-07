# HOW TO USE:
# 1. Save this file directly INSIDE your verse-video-bot project folder
#    (the same folder that has "scripts", "assets", ".github" in it).
# 2. Open PowerShell in that folder, run this first (once per new window):
#      Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
# 3. Then run:  .\update_project4.ps1

Write-Host "Writing updated scripts/generate_video.py..."
$pyContent = @'
import os
import sys
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

# ---------------- CONFIG ----------------
SHEET_ID = os.environ["SHEET_ID"]
SHEET_TAB = os.environ.get("SHEET_TAB", "Sheet1")

MUSIC_DIR = os.environ.get("MUSIC_DIR", "assets/music")
CUSTOM_BG_DIR = os.environ.get("CUSTOM_BG_DIR", "assets/backgrounds")

# Two fonts: Telugu script needs its own font, English uses DejaVu.
# Detected independently for the verse and the reference — no manual choice needed.
FONT_PATH_TELUGU = os.environ.get(
    "FONT_PATH_TELUGU", "/usr/share/fonts/truetype/noto/NotoSansTelugu-Bold.ttf"
)
FONT_PATH_LATIN = os.environ.get(
    "FONT_PATH_LATIN", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
)

VIDEO_DURATION = 45          # seconds, fixed length (extra room to add your own voiceover later)
MUSIC_START_OFFSET = 10      # start music 10s into the track
FPS = 24
VIDEO_SIZE = (1280, 720)

# Telugu glyphs render visibly taller than Latin ones at the same point size,
# so each script gets its own size to look visually consistent.
VERSE_FONT_SIZE_LATIN = 78
VERSE_FONT_SIZE_TELUGU = 58
VERSE_FONT_MIN_LATIN = 34    # verse text auto-shrinks down to this size if needed so long verses always fit on screen
VERSE_FONT_MIN_TELUGU = 26
REF_FONT_SIZE_LATIN = 52
REF_FONT_SIZE_TELUGU = 38

# Vertical space kept clear at the very top/bottom of the frame (border + breathing room)
SAFE_TOP = 110
SAFE_BOTTOM = 110

STROKE_WIDTH = 3             # outline thickness for the cinematic "engraved" text look
SECONDS_PER_WORD = 0.35      # word-by-word reveal speed (lower = faster)
MAX_REVEAL_SECONDS = 14      # cap so very long verses don't drag on forever
TEXT_MARGIN_X = 130          # keeps text well clear of the edges

# Neon border: positioned ~0.5cm inside the canvas edge (~19px at typical screen density)
NEON_BORDER_MARGIN = 19
NEON_BORDER_THICKNESS = 4
NEON_GLOW_THICKNESS = 11
NEON_CYCLE_SPEED = 0.12       # how fast the rainbow colors shift/move (higher = faster)
NEON_SEGMENTS_PER_SIDE = 14   # smoothness of the color flow along each edge

# Twinkling stars, placed just outside the neon border
STARS_PER_SIDE = 5

OUTPUT_DIR = "output"

# Manual-run controls (ignored on the automatic daily schedule, which always
# uses "random" + reads from the Sheet + uploads private).
PRIVACY_STATUS = os.environ.get("PRIVACY_STATUS", "private").strip().lower()
MUSIC_CHOICE = os.environ.get("MUSIC_CHOICE", "random").strip()
BACKGROUND_THEME = os.environ.get("BACKGROUND_THEME", "random").strip()
VERSE_OVERRIDE = os.environ.get("VERSE_OVERRIDE", "").strip()
REFERENCE_OVERRIDE = os.environ.get("REFERENCE_OVERRIDE", "").strip()
# -----------------------------------------

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


def is_telugu(text):
    """True if the text contains Telugu-script characters (Unicode range 0C00–0C7F)."""
    return any("\u0c00" <= ch <= "\u0c7f" for ch in text)


def hue_to_rgb(hue):
    r, g, b = colorsys.hsv_to_rgb(hue % 1.0, 1.0, 1.0)
    return (int(r * 255), int(g * 255), int(b * 255))


def dim(color, factor):
    return tuple(int(c * factor) for c in color)


def build_border_points(w, h, margin):
    """Perimeter points (clockwise from top-left) used to draw the neon border
    as a series of short colored segments, so colors can flow along it."""
    points = []
    steps = NEON_SEGMENTS_PER_SIDE
    x0, y0, x1, y1 = margin, margin, w - margin, h - margin

    for i in range(steps + 1):  # top edge, left -> right
        points.append((x0 + (x1 - x0) * i / steps, y0))
    for i in range(1, steps + 1):  # right edge, top -> bottom
        points.append((x1, y0 + (y1 - y0) * i / steps))
    for i in range(1, steps + 1):  # bottom edge, right -> left
        points.append((x1 - (x1 - x0) * i / steps, y1))
    for i in range(1, steps + 1):  # left edge, bottom -> top
        points.append((x0, y1 - (y1 - y0) * i / steps))

    return points


def draw_neon_border(draw, size, t):
    """Draws a glowing rainbow border whose colors flow around the edge over time."""
    w, h = size
    points = build_border_points(w, h, NEON_BORDER_MARGIN)
    n = len(points) - 1
    time_offset = t * NEON_CYCLE_SPEED

    for i in range(n):
        p1, p2 = points[i], points[i + 1]
        hue = (i / n) + time_offset
        color = hue_to_rgb(hue)
        # glow pass (soft, thicker, dimmer) then bright core pass on top
        draw.line([p1, p2], fill=dim(color, 0.45), width=NEON_GLOW_THICKNESS)
        draw.line([p1, p2], fill=color, width=NEON_BORDER_THICKNESS)


def make_star_positions(size):
    """Fixed star positions just outside the neon border, evenly spaced,
    at least STARS_PER_SIDE per side. Each gets a random twinkle phase/speed."""
    w, h = size
    outer = NEON_BORDER_MARGIN - 10  # sits between the true edge and the border
    outer = max(outer, 6)
    stars = []

    def add_row(x_vals, y_vals):
        for x, y in zip(x_vals, y_vals):
            stars.append({
                "x": x, "y": y,
                "phase": random.uniform(0, math.tau),
                "speed": random.uniform(1.2, 2.4),
            })

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


def find_custom_background():
    """Looks for an uploaded custom background at assets/backgrounds/custom_background.*
    Upload it directly on GitHub's website — no local file editing needed."""
    if not os.path.isdir(CUSTOM_BG_DIR):
        return None
    for ext in (".jpg", ".jpeg", ".png"):
        path = os.path.join(CUSTOM_BG_DIR, "custom_background" + ext)
        if os.path.exists(path):
            return path
    return None


def load_custom_background(path, size):
    """Cover-fit crop: fills the whole frame without distorting the image."""
    img = Image.open(path).convert("RGB")
    target_w, target_h = size
    src_w, src_h = img.size
    scale = max(target_w / src_w, target_h / src_h)
    new_w, new_h = int(src_w * scale), int(src_h * scale)
    img = img.resize((new_w, new_h), Image.LANCZOS)
    left = (new_w - target_w) // 2
    top = (new_h - target_h) // 2
    img = img.crop((left, top, left + target_w, top + target_h))

    # same soft vignette used on gradients, so text stays readable on any photo
    w, h = size
    vignette = Image.new("L", size, 0)
    vdraw = ImageDraw.Draw(vignette)
    vdraw.ellipse([-w * 0.3, -h * 0.3, w * 1.3, h * 1.3], fill=255)
    vignette = vignette.filter(ImageFilter.GaussianBlur(120))
    dark = Image.new("RGB", size, (0, 0, 0))
    img = Image.composite(img, dark, vignette)
    return img


def make_gradient_background(size):
    """Generates a diagonal gradient with a soft vignette. Picks a random themed
    palette unless a specific theme name was requested for a manual run."""
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

    vignette = Image.new("L", size, 0)
    vdraw = ImageDraw.Draw(vignette)
    vdraw.ellipse([-w * 0.3, -h * 0.3, w * 1.3, h * 1.3], fill=255)
    vignette = vignette.filter(ImageFilter.GaussianBlur(120))
    dark = Image.new("RGB", size, (0, 0, 0))
    img = Image.composite(img, dark, vignette)

    return img


def make_background(size):
    """Uses your uploaded custom image if the theme requests it and one exists;
    otherwise generates a gradient (random or a named palette)."""
    wants_custom = BACKGROUND_THEME.lower() in ("custom", "custom image", "my custom image", "my uploaded image")
    if wants_custom:
        custom_path = find_custom_background()
        if custom_path:
            print(f"Using custom background: {custom_path}")
            return load_custom_background(custom_path, size)
        print("Custom background requested but none found — falling back to gradient.")

    return make_gradient_background(size)


def pick_music_file():
    music_files = sorted(f for f in os.listdir(MUSIC_DIR) if f.lower().endswith(".mp3"))
    if not music_files:
        raise FileNotFoundError(f"No .mp3 files found in {MUSIC_DIR}")

    if MUSIC_CHOICE and MUSIC_CHOICE.lower() != "random":
        for f in music_files:
            if f.lower() == MUSIC_CHOICE.lower():
                return os.path.join(MUSIC_DIR, f)
        print(f"Warning: '{MUSIC_CHOICE}' not found in {MUSIC_DIR}, picking randomly instead.")

    return os.path.join(MUSIC_DIR, random.choice(music_files))


def generate_hashtags(reference_text, verse_text):
    tags = list(BASE_HASHTAGS)
    tags += TELUGU_HASHTAGS if is_telugu(verse_text) else ENGLISH_HASHTAGS

    if reference_text:
        book = reference_text.split()[0]
        cleaned = "".join(
            ch for ch in book if not unicodedata.category(ch).startswith(("P", "Z", "C", "N"))
        )
        book_tag = "#" + cleaned
        if book_tag != "#" and book_tag not in tags:
            tags.append(book_tag)

    return tags[:10]


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


def fetch_next_verse(service):
    range_ = f"{SHEET_TAB}!A2:C"
    result = (
        service.spreadsheets()
        .values()
        .get(spreadsheetId=SHEET_ID, range=range_)
        .execute()
    )
    rows = result.get("values", [])
    for i, row in enumerate(rows):
        verse = row[0] if len(row) > 0 else ""
        reference = row[1] if len(row) > 1 else ""
        used = row[2] if len(row) > 2 else ""
        if verse and used.strip().lower() != "used":
            row_number = i + 2
            return row_number, verse.strip(), reference.strip()
    return None, None, None


def mark_verse_used(service, row_number):
    service.spreadsheets().values().update(
        spreadsheetId=SHEET_ID,
        range=f"{SHEET_TAB}!C{row_number}",
        valueInputOption="RAW",
        body={"values": [["used"]]},
    ).execute()


def text_width(draw, text, font):
    """Width of text INCLUDING the stroke outline, so wrapping/centering
    never lets the visible (stroked) text run past the margin."""
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


def draw_cinematic_text(draw, text, font, x, y):
    draw.text((x + 4, y + 4), text, font=font, fill=(0, 0, 0))
    draw.text((x, y), text, font=font, fill=(255, 255, 255), stroke_width=STROKE_WIDTH, stroke_fill=(10, 10, 20))


def fit_verse_font(draw, full_text, font_path, initial_size, min_size, max_width, max_height):
    """Shrinks the verse font (in 2pt steps) until the FULL verse text wraps
    into a block that fits the available height. Computed once from the full
    text, so the size stays constant through the word-by-word reveal."""
    size = initial_size
    while size >= min_size:
        font = ImageFont.truetype(font_path, size)
        lines = wrap_text(draw, full_text, font, max_width)
        line_height = size + 16
        total_height = len(lines) * line_height
        if total_height <= max_height:
            return font
        size -= 2
    return ImageFont.truetype(font_path, min_size)


def render_frame(background, verse_text, reference_text, words_to_show, show_reference,
                  size, verse_font, ref_font, t, stars):
    img = background.copy()
    draw = ImageDraw.Draw(img)

    draw_neon_border(draw, size, t)
    draw_twinkling_stars(draw, stars, t)

    words = verse_text.split()
    visible_text = " ".join(words[:words_to_show])
    max_width = size[0] - (TEXT_MARGIN_X * 2)
    lines = wrap_text(draw, visible_text, verse_font, max_width)

    line_height = verse_font.size + 16
    total_height = len(lines) * line_height
    y = (size[1] - total_height) // 2 - 40

    for line in lines:
        w = text_width(draw, line, verse_font)
        x = max(TEXT_MARGIN_X, (size[0] - w) // 2)
        draw_cinematic_text(draw, line, verse_font, x, y)
        y += line_height

    if show_reference and reference_text:
        w = text_width(draw, reference_text, ref_font)
        x = max(TEXT_MARGIN_X, (size[0] - w) // 2)
        y_ref = y + 30
        draw.text((x + 3, y_ref + 3), reference_text, font=ref_font, fill=(0, 0, 0))
        draw.text((x, y_ref), reference_text, font=ref_font, fill=(255, 215, 0), stroke_width=2, stroke_fill=(60, 40, 0))

    return np.array(img.convert("RGB"))


def build_video(verse_text, reference_text):
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    background = make_background(VIDEO_SIZE)
    size = VIDEO_SIZE
    stars = make_star_positions(size)

    verse_is_telugu = is_telugu(verse_text)
    ref_is_telugu = is_telugu(reference_text)

    verse_font_path = FONT_PATH_TELUGU if verse_is_telugu else FONT_PATH_LATIN
    ref_font_path = FONT_PATH_TELUGU if ref_is_telugu else FONT_PATH_LATIN
    verse_font_size = VERSE_FONT_SIZE_TELUGU if verse_is_telugu else VERSE_FONT_SIZE_LATIN
    verse_font_min = VERSE_FONT_MIN_TELUGU if verse_is_telugu else VERSE_FONT_MIN_LATIN
    ref_font_size = REF_FONT_SIZE_TELUGU if ref_is_telugu else REF_FONT_SIZE_LATIN

    ref_font = ImageFont.truetype(ref_font_path, ref_font_size)

    # Reserve vertical room for the reference line, then auto-shrink the verse
    # font (if needed) so the FULL verse always fits on screen — no more cutoff.
    max_width = size[0] - (TEXT_MARGIN_X * 2)
    reference_block_height = (ref_font_size + 30 + 20) if reference_text else 0
    max_verse_height = size[1] - SAFE_TOP - SAFE_BOTTOM - reference_block_height

    probe_img = Image.new("RGB", size)
    probe_draw = ImageDraw.Draw(probe_img)
    verse_font = fit_verse_font(
        probe_draw, verse_text, verse_font_path, verse_font_size, verse_font_min,
        max_width, max_verse_height
    )

    words = verse_text.split()
    n_words = len(words)
    reveal_duration = min(n_words * SECONDS_PER_WORD, MAX_REVEAL_SECONDS)

    total_frames = VIDEO_DURATION * FPS

    frames = []
    for f in range(total_frames):
        t = f / FPS
        if t < reveal_duration:
            words_to_show = min(n_words, int(t / SECONDS_PER_WORD) + 1)
            show_reference = False
        else:
            words_to_show = n_words
            show_reference = True
        frames.append(
            render_frame(background, verse_text, reference_text, words_to_show, show_reference,
                         size, verse_font, ref_font, t, stars)
        )

    clip = ImageSequenceClip(frames, fps=FPS)

    chosen_music = pick_music_file()
    audio = AudioFileClip(chosen_music).subclip(
        MUSIC_START_OFFSET, MUSIC_START_OFFSET + VIDEO_DURATION
    )
    clip = clip.set_audio(audio)

    output_path = os.path.join(OUTPUT_DIR, "verse_video.mp4")
    clip.write_videofile(output_path, fps=FPS, codec="libx264", audio_codec="aac")
    return output_path


def upload_to_youtube(youtube, video_path, verse_text, reference_text):
    title = f"{reference_text} | Daily Verse" if reference_text else "Daily Bible Verse"
    hashtags = generate_hashtags(reference_text, verse_text)
    description = f"{verse_text}\n\n{reference_text}\n\n" + " ".join(hashtags)

    privacy = PRIVACY_STATUS if PRIVACY_STATUS in ("private", "public", "unlisted") else "private"

    body = {
        "snippet": {
            "title": title[:100],
            "description": description,
            "categoryId": "22",
            "tags": [t.lstrip("#") for t in hashtags],
        },
        "status": {
            "privacyStatus": privacy
        },
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
    if VERSE_OVERRIDE:
        verse_text, reference_text = VERSE_OVERRIDE, REFERENCE_OVERRIDE
        print(f"Using override text: {verse_text} — {reference_text}")
    else:
        row_number, verse_text, reference_text = fetch_next_verse(sheets_service)
        if not verse_text:
            print("No unused verses found in the sheet. Exiting.")
            sys.exit(0)
        print(f"Selected verse (row {row_number}): {verse_text} — {reference_text}")

    video_path = build_video(verse_text, reference_text)

    youtube_service = get_youtube_service(creds)
    upload_to_youtube(youtube_service, video_path, verse_text, reference_text)

    if row_number is not None:
        mark_verse_used(sheets_service, row_number)

    print("Done.")


if __name__ == "__main__":
    main()

'@
Set-Content -Path "scripts\generate_video.py" -Value $pyContent -Encoding utf8

Write-Host "Writing updated .github/workflows/generate-video.yml..."
$ymlContent = @'
name: Generate and Upload Verse Video

on:
  schedule:
    - cron: "0 6 * * *"   # daily at 06:00 UTC — fully automatic run: random theme/music, reads Sheet, uploads private
  workflow_dispatch:
    inputs:
      privacy:
        description: "Who can see the uploaded video"
        required: true
        type: choice
        options:
          - private
          - unlisted
          - public
        default: private
      background_theme:
        description: "Background style — pick 'Custom Image' to use your own uploaded photo"
        required: true
        type: choice
        options:
          - Random
          - Custom Image
          - Midnight Purple
          - Ocean Blue
          - Wine Red
          - Emerald Teal
          - Sunset Amber
          - Indigo Violet
          - Deep Teal
          - Magenta Plum
        default: Random
      music_choice:
        description: "Which music track to use"
        required: true
        type: choice
        options:
          - Random
          - track1.mp3
          - track2.mp3
        default: Random
      verse_override:
        description: "Optional: type a verse here to test with, instead of pulling from the Sheet"
        required: false
        type: string
      reference_override:
        description: "Optional: reference to go with the verse_override above (e.g. John 3:16)"
        required: false
        type: string

jobs:
  generate-video:
    runs-on: ubuntu-latest
    steps:
      - name: Checkout repo
        uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.11"

      - name: Install ffmpeg and fonts
        run: |
          sudo apt-get update
          sudo apt-get install -y ffmpeg fonts-dejavu-core fonts-noto-core

      - name: Install Python dependencies
        run: pip install -r requirements.txt

      - name: Run video generation and upload
        env:
          SHEET_ID: ${{ secrets.SHEET_ID }}
          YT_CLIENT_ID: ${{ secrets.YT_CLIENT_ID }}
          YT_CLIENT_SECRET: ${{ secrets.YT_CLIENT_SECRET }}
          YT_REFRESH_TOKEN: ${{ secrets.YT_REFRESH_TOKEN }}
          PRIVACY_STATUS: ${{ inputs.privacy || 'private' }}
          BACKGROUND_THEME: ${{ inputs.background_theme || 'Random' }}
          MUSIC_CHOICE: ${{ inputs.music_choice || 'Random' }}
          VERSE_OVERRIDE: ${{ inputs.verse_override || '' }}
          REFERENCE_OVERRIDE: ${{ inputs.reference_override || '' }}
        run: python scripts/generate_video.py

'@
Set-Content -Path ".github\workflows\generate-video.yml" -Value $ymlContent -Encoding utf8

Write-Host "Committing and pushing to GitHub..."
git add .
git commit -m "Auto-fit text sizing (fixes long-verse cutoff), custom background image support"
git push

Write-Host "Done! Go to the Actions tab on GitHub to run the workflow."
