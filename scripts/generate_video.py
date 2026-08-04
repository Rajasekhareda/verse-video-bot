import os
import sys
import random

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
# The script auto-detects which one to use per verse — no manual choice needed.
FONT_PATH_TELUGU = os.environ.get(
    "FONT_PATH_TELUGU", "/usr/share/fonts/truetype/noto/NotoSansTelugu-Bold.ttf"
)
FONT_PATH_LATIN = os.environ.get(
    "FONT_PATH_LATIN", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
)

VIDEO_DURATION = 45        # seconds, fixed length (extra room to add your own voiceover later)
MUSIC_START_OFFSET = 10    # start music 10s into the track
FPS = 24
VERSE_FONT_SIZE = 60
REF_FONT_SIZE = 40
TYPEWRITER_SECONDS = 18    # how long it takes to "type" the full verse
VIDEO_SIZE = (1280, 720)

OUTPUT_DIR = "output"
# -----------------------------------------

# A handful of eye-catching, hand-picked gradient color pairs.
# A new one is picked at random every run, so each video looks a little different.
GRADIENT_PALETTES = [
    ((15, 12, 45), (90, 30, 110)),     # midnight purple
    ((10, 25, 55), (30, 90, 130)),     # deep ocean blue
    ((40, 10, 30), (140, 40, 60)),     # wine red
    ((10, 35, 30), (30, 110, 90)),     # emerald teal
    ((45, 20, 10), (150, 80, 30)),     # sunset amber
    ((20, 15, 50), (80, 60, 150)),     # indigo violet
    ((5, 20, 25), (20, 80, 100)),      # deep teal
    ((35, 10, 45), (120, 30, 100)),    # magenta plum
]


def is_telugu(text):
    """True if the text contains Telugu-script characters (Unicode range 0C00–0C7F)."""
    return any("\u0c00" <= ch <= "\u0c7f" for ch in text)


def make_gradient_background(size):
    """Generates a fresh diagonal gradient with a soft vignette — different every run."""
    top_color, bottom_color = random.choice(GRADIENT_PALETTES)
    w, h = size
    top = np.array(top_color, dtype=float)
    bottom = np.array(bottom_color, dtype=float)

    t = np.linspace(0, 1, h).reshape(h, 1, 1)
    gradient = (top * (1 - t) + bottom * t).astype(np.uint8)
    gradient = np.repeat(gradient, w, axis=1)
    img = Image.fromarray(gradient, mode="RGB")

    # subtle vignette for a more polished, "designed" look
    vignette = Image.new("L", size, 0)
    vdraw = ImageDraw.Draw(vignette)
    vdraw.ellipse(
        [-w * 0.3, -h * 0.3, w * 1.3, h * 1.3], fill=255
    )
    vignette = vignette.filter(ImageFilter.GaussianBlur(120))
    dark = Image.new("RGB", size, (0, 0, 0))
    img = Image.composite(img, dark, vignette)

    return img


def get_user_credentials():
    """Single OAuth credential (your own Google account) used for both
    Sheets and YouTube — avoids needing a service account entirely."""
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


def render_frame(background, verse_text, reference_text, chars_to_show, show_reference, size, verse_font_path, ref_font_path):
    img = background.copy()
    draw = ImageDraw.Draw(img)
    verse_font = ImageFont.truetype(verse_font_path, VERSE_FONT_SIZE)
    ref_font = ImageFont.truetype(ref_font_path, REF_FONT_SIZE)

    visible_text = verse_text[:chars_to_show]
    max_width = size[0] - 160
    lines = wrap_text(draw, visible_text, verse_font, max_width)

    line_height = VERSE_FONT_SIZE + 14
    total_height = len(lines) * line_height
    y = (size[1] - total_height) // 2 - 40

    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=verse_font)
        w = bbox[2] - bbox[0]
        x = (size[0] - w) // 2
        draw.text((x + 2, y + 2), line, font=verse_font, fill=(0, 0, 0))
        draw.text((x, y), line, font=verse_font, fill=(255, 255, 255))
        y += line_height

    if show_reference and reference_text:
        bbox = draw.textbbox((0, 0), reference_text, font=ref_font)
        w = bbox[2] - bbox[0]
        x = (size[0] - w) // 2
        y_ref = y + 30
        draw.text((x + 2, y_ref + 2), reference_text, font=ref_font, fill=(0, 0, 0))
        draw.text((x, y_ref), reference_text, font=ref_font, fill=(255, 215, 0))

    return np.array(img.convert("RGB"))


def build_video(verse_text, reference_text):
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    background = make_gradient_background(VIDEO_SIZE)
    size = VIDEO_SIZE

    verse_font_path = FONT_PATH_TELUGU if is_telugu(verse_text) else FONT_PATH_LATIN
    ref_font_path = FONT_PATH_TELUGU if is_telugu(reference_text) else FONT_PATH_LATIN

    total_frames = VIDEO_DURATION * FPS
    n_chars = len(verse_text)

    frames = []
    for f in range(total_frames):
        t = f / FPS
        if t < TYPEWRITER_SECONDS:
            chars_to_show = int(n_chars * (t / TYPEWRITER_SECONDS))
            show_reference = False
        else:
            chars_to_show = n_chars
            show_reference = True
        frames.append(
            render_frame(background, verse_text, reference_text, chars_to_show, show_reference, size, verse_font_path, ref_font_path)
        )

    clip = ImageSequenceClip(frames, fps=FPS)

    music_files = [f for f in os.listdir(MUSIC_DIR) if f.lower().endswith(".mp3")]
    if not music_files:
        raise FileNotFoundError(f"No .mp3 files found in {MUSIC_DIR}")
    chosen_music = os.path.join(MUSIC_DIR, random.choice(music_files))

    audio = AudioFileClip(chosen_music).subclip(
        MUSIC_START_OFFSET, MUSIC_START_OFFSET + VIDEO_DURATION
    )
    clip = clip.set_audio(audio)

    output_path = os.path.join(OUTPUT_DIR, "verse_video.mp4")
    clip.write_videofile(output_path, fps=FPS, codec="libx264", audio_codec="aac")
    return output_path


def upload_to_youtube(youtube, video_path, verse_text, reference_text):
    title = f"{reference_text} | Daily Verse" if reference_text else "Daily Bible Verse"
    description = f"{verse_text}\n\n{reference_text}".strip()

    body = {
        "snippet": {
            "title": title[:100],
            "description": description,
            "categoryId": "22",
        },
        "status": {
            "privacyStatus": "private"  # private = you can review/edit further in YT Studio before publishing
        },
    }

    media = MediaFileUpload(video_path, chunksize=-1, resumable=True, mimetype="video/mp4")
    request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)
    response = None
    while response is None:
        status, response = request.next_chunk()
        if status:
            print(f"Upload progress: {int(status.progress() * 100)}%")
    print(f"Uploaded video ID: {response['id']}")
    return response["id"]


def main():
    creds = get_user_credentials()
    sheets_service = get_sheets_service(creds)
    row_number, verse_text, reference_text = fetch_next_verse(sheets_service)

    if not verse_text:
        print("No unused verses found in the sheet. Exiting.")
        sys.exit(0)

    print(f"Selected verse (row {row_number}): {verse_text} — {reference_text}")

    video_path = build_video(verse_text, reference_text)

    youtube_service = get_youtube_service(creds)
    upload_to_youtube(youtube_service, video_path, verse_text, reference_text)

    mark_verse_used(sheets_service, row_number)
    print("Done.")


if __name__ == "__main__":
    main()
