import os
import sys
import random

import numpy as np
from PIL import Image, ImageDraw, ImageFont
from moviepy.editor import ImageSequenceClip, AudioFileClip

from google.oauth2.credentials import Credentials as UserCredentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

# ---------------- CONFIG ----------------
SHEET_ID = os.environ["SHEET_ID"]
SHEET_TAB = os.environ.get("SHEET_TAB", "Sheet1")

BACKGROUND_IMAGE = os.environ.get("BACKGROUND_IMAGE", "assets/background.jpg")
MUSIC_DIR = os.environ.get("MUSIC_DIR", "assets/music")
FONT_PATH = os.environ.get(
    "FONT_PATH", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
)

VIDEO_DURATION = 30        # seconds, fixed length
MUSIC_START_OFFSET = 10    # start music 10s into the track
FPS = 24
VERSE_FONT_SIZE = 60
REF_FONT_SIZE = 40
TYPEWRITER_SECONDS = 12    # how long it takes to "type" the full verse

OUTPUT_DIR = "output"
# -----------------------------------------


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


def render_frame(background, verse_text, reference_text, chars_to_show, show_reference, size):
    img = background.copy()
    draw = ImageDraw.Draw(img)
    verse_font = ImageFont.truetype(FONT_PATH, VERSE_FONT_SIZE)
    ref_font = ImageFont.truetype(FONT_PATH, REF_FONT_SIZE)

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
    background = Image.open(BACKGROUND_IMAGE).convert("RGB")
    size = background.size

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
            render_frame(background, verse_text, reference_text, chars_to_show, show_reference, size)
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
            "privacyStatus": "private"  # private = draft-like, review in YT Studio before publishing
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
