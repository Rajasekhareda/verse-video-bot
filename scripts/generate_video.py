import os
import sys
import random
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

# Two fonts: Telugu script needs its own font, English uses DejaVu.
# Detected independently for the verse and the reference â€” no manual choice needed.
FONT_PATH_TELUGU = os.environ.get(
    "FONT_PATH_TELUGU", "/usr/share/fonts/truetype/noto/NotoSansTelugu-Bold.ttf"
)
FONT_PATH_LATIN = os.environ.get(
    "FONT_PATH_LATIN", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
)

VIDEO_DURATION = 45          # seconds, fixed length (extra room to add your own voiceover later)
MUSIC_START_OFFSET = 10      # start music 10s into the track
FPS = 24
VERSE_FONT_SIZE = 78         # bumped up for a bigger, bolder look
REF_FONT_SIZE = 52
SECONDS_PER_WORD = 0.35      # word-by-word reveal speed (lower = faster)
MAX_REVEAL_SECONDS = 14      # cap so very long verses don't drag on forever
VIDEO_SIZE = (1280, 720)
STROKE_WIDTH = 3             # outline thickness for the cinematic "engraved" text look
TEXT_MARGIN_X = 130          # keeps text well clear of the edges (~2cm inset look)

OUTPUT_DIR = "output"

# Manual-run controls (ignored on the automatic daily schedule, which always
# uses "random" + reads from the Sheet + uploads private).
PRIVACY_STATUS = os.environ.get("PRIVACY_STATUS", "private").strip().lower()
MUSIC_CHOICE = os.environ.get("MUSIC_CHOICE", "random").strip()
BACKGROUND_THEME = os.environ.get("BACKGROUND_THEME", "random").strip()
VERSE_OVERRIDE = os.environ.get("VERSE_OVERRIDE", "").strip()
REFERENCE_OVERRIDE = os.environ.get("REFERENCE_OVERRIDE", "").strip()
# -----------------------------------------

# A handful of eye-catching, hand-picked gradient color pairs, each with a name
# so they can be picked by name from the manual "Run workflow" screen.
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

# Hashtags always included, plus ones chosen based on verse language.
BASE_HASHTAGS = ["#BibleVerse", "#DailyVerse", "#Faith", "#God", "#Jesus", "#Scripture"]
TELUGU_HASHTAGS = ["#TeluguChristian", "#YesuKrishtu", "#Telugu"]
ENGLISH_HASHTAGS = ["#Christian", "#Gospel", "#WordOfGod"]


def is_telugu(text):
    """True if the text contains Telugu-script characters (Unicode range 0C00â€“0C7F)."""
    return any("\u0c00" <= ch <= "\u0c7f" for ch in text)


def draw_sparkle(draw, x, y, size, color):
    """A small four-point sparkle/star mark."""
    draw.line([x - size, y, x + size, y], fill=color, width=2)
    draw.line([x, y - size, x, y + size], fill=color, width=2)
    draw.ellipse([x - 3, y - 3, x + 3, y + 3], fill=color)


def add_sparkle_border(img):
    """Adds an elegant gold rounded border with sparkle accents around the
    frame edge â€” computed once per video, not per-frame, so it stays fast."""
    draw = ImageDraw.Draw(img)
    w, h = img.size
    margin = 34
    gold = (255, 215, 0)

    draw.rounded_rectangle(
        [margin, margin, w - margin, h - margin], radius=24, outline=gold, width=3
    )

    sparkle_spots = [
        (margin, margin), (w - margin, margin),
        (margin, h - margin), (w - margin, h - margin),
        (w // 2, margin), (w // 2, h - margin),
    ]
    for (sx, sy) in sparkle_spots:
        draw_sparkle(draw, sx, sy, 10, (255, 255, 255))

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


def pick_music_file():
    music_files = sorted(f for f in os.listdir(MUSIC_DIR) if f.lower().endswith(".mp3"))
    if not music_files:
        raise FileNotFoundError(f"No .mp3 files found in {MUSIC_DIR}")

    if MUSIC_CHOICE and MUSIC_CHOICE.lower() != "random":
        for f in music_files:
            if f.lower() == MUSIC_CHOICE.lower():
                return os.path.join(MUSIC_DIR, f)
        # requested file not found â€” fall back to random rather than crash
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

    return tags[:10]  # keep it reasonable, avoid hashtag spam


def get_user_credentials():
    """Single OAuth credential (your own Google account) used for both
    Sheets and YouTube â€” avoids needing a service account entirely."""
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
    """Column A = verse text, B = reference, C = 'used' marker (written by this script)."""
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
            row_number = i + 2  # +2: header row + 0-index offset
            return row_number, verse.strip(), reference.strip()
    return None, None, None


def mark_verse_used(service, row_number):
    service.spreadsheets().values().update(
        spreadsheetId=SHEET_ID,
        range=f"{SHEET_TAB}!C{row_number}",
        valueInputOption="RAW",
        body={"values": [["used"]]},
    ).execute()


def wrap_text(draw, text, font, max_width):
    words = text.split()
    lines, current = [], ""
    for word in words:
        test = f"{current} {word}".strip()
        bbox = draw.textbbox((0, 0), test, font=font)
        if bbox[2] - bbox[0] <= max_width:
            current = test
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def draw_cinematic_text(draw, text, font, x, y):
    """Draws text with an offset drop-shadow plus a stroke outline for a
    bold, engraved 'cinematic' depth look."""
    draw.text((x + 4, y + 4), text, font=font, fill=(0, 0, 0))
    draw.text((x, y), text, font=font, fill=(255, 255, 255), stroke_width=STROKE_WIDTH, stroke_fill=(10, 10, 20))


def render_frame(background, verse_text, reference_text, words_to_show, show_reference, size, verse_font_path, ref_font_path):
    img = background.copy()
    draw = ImageDraw.Draw(img)
    verse_font = ImageFont.truetype(verse_font_path, VERSE_FONT_SIZE)
    ref_font = ImageFont.truetype(ref_font_path, REF_FONT_SIZE)

    words = verse_text.split()
    visible_text = " ".join(words[:words_to_show])
    max_width = size[0] - (TEXT_MARGIN_X * 2)
    lines = wrap_text(draw, visible_text, verse_font, max_width)

    line_height = VERSE_FONT_SIZE + 16
    total_height = len(lines) * line_height
    y = (size[1] - total_height) // 2 - 40

    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=verse_font)
        w = bbox[2] - bbox[0]
        x = max(TEXT_MARGIN_X, (size[0] - w) // 2)
        draw_cinematic_text(draw, line, verse_font, x, y)
        y += line_height

    if show_reference and reference_text:
        bbox = draw.textbbox((0, 0), reference_text, font=ref_font)
        w = bbox[2] - bbox[0]
        x = max(TEXT_MARGIN_X, (size[0] - w) // 2)
        y_ref = y + 30
        draw.text((x + 3, y_ref + 3), reference_text, font=ref_font, fill=(0, 0, 0))
        draw.text((x, y_ref), reference_text, font=ref_font, fill=(255, 215, 0), stroke_width=2, stroke_fill=(60, 40, 0))

    return np.array(img.convert("RGB"))


def build_video(verse_text, reference_text):
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    background = make_gradient_background(VIDEO_SIZE)
    background = add_sparkle_border(background)
    size = VIDEO_SIZE

    verse_font_path = FONT_PATH_TELUGU if is_telugu(verse_text) else FONT_PATH_LATIN
    ref_font_path = FONT_PATH_TELUGU if is_telugu(reference_text) else FONT_PATH_LATIN

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
            render_frame(background, verse_text, reference_text, words_to_show, show_reference, size, verse_font_path, ref_font_path)
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
        print(f"Using override text: {verse_text} â€” {reference_text}")
    else:
        row_number, verse_text, reference_text = fetch_next_verse(sheets_service)
        if not verse_text:
            print("No unused verses found in the sheet. Exiting.")
            sys.exit(0)
        print(f"Selected verse (row {row_number}): {verse_text} â€” {reference_text}")

    video_path = build_video(verse_text, reference_text)

    youtube_service = get_youtube_service(creds)
    upload_to_youtube(youtube_service, video_path, verse_text, reference_text)

    if row_number is not None:
        mark_verse_used(sheets_service, row_number)

    print("Done.")


if __name__ == "__main__":
    main()

