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

# ================= CONFIGURATION =================
SHEET_ID = os.environ["SHEET_ID"]
SHEET_TAB = os.environ.get("SHEET_TAB", "Sheet1")
MUSIC_DIR = os.environ.get("MUSIC_DIR", "assets/music")
CUSTOM_BG_DIR = os.environ.get("CUSTOM_BG_DIR", "assets/backgrounds")
OUTPUT_DIR = "output"
THUMBNAIL_DIR = os.path.join(OUTPUT_DIR, "thumbnails")

# Video settings - 4K UHD
FPS = 24
VIDEO_SIZE = (3840, 2160)
MIN_DISPLAY_TIME = 5.0
MAX_DISPLAY_TIME = 6.0

# Font paths - will use merged font from original script
try:
    from fontTools.merge import Merger
    def _build_merged_font():
        cache_path = "/tmp/NotoSerifMerged-Bold.ttf"
        if os.path.exists(cache_path):
            return cache_path
        latin_src = "/usr/share/fonts/truetype/noto/NotoSerif-Bold.ttf"
        telugu_src = "/usr/share/fonts/truetype/noto/NotoSerifTelugu-Bold.ttf"
        merged = Merger().merge([latin_src, telugu_src])
        merged.save(cache_path)
        return cache_path
    FONT_PATH = _build_merged_font()
except:
    FONT_PATH = "/usr/share/fonts/truetype/noto/NotoSerif-Bold.ttf"

# Neon colors
NEON_COLORS = [(0, 255, 255), (255, 0, 255), (0, 255, 127), (255, 20, 147), (30, 144, 255)]

# Environment variables
PRIVACY_STATUS = os.environ.get("PRIVACY_STATUS", "private").strip().lower()
MUSIC_CHOICE = os.environ.get("MUSIC_CHOICE", "random").strip()
BACKGROUND_THEME = os.environ.get("BACKGROUND_THEME", "random").strip()
TELUGU_OVERRIDE = os.environ.get("TELUGU_OVERRIDE", "").strip()
ENGLISH_OVERRIDE = os.environ.get("ENGLISH_OVERRIDE", "").strip()

GRADIENT_PALETTES = {
    "Midnight Purple": ((18, 12, 52), (72, 22, 100)),
    "Ocean Blue": ((8, 30, 70), (20, 75, 130)),
    "Wine Red": ((45, 8, 20), (110, 25, 50)),
    "Emerald Teal": ((8, 42, 38), (18, 100, 82)),
    "Sunset Amber": ((50, 22, 8), (140, 65, 18)),
    "Indigo Violet": ((22, 14, 58), (70, 45, 155)),
    "Midnight Slate": ((14, 20, 38), (28, 45, 80)),
    "Magenta Plum": ((40, 10, 48), (110, 22, 95)),
}

BASE_HASHTAGS = ["#BibleVerse", "#DailyVerse", "#Faith", "#God", "#Jesus", "#Scripture"]
TELUGU_HASHTAGS = ["#TeluguChristian", "#YesuKrishtu", "#Telugu"]
ENGLISH_HASHTAGS = ["#Christian", "#Gospel", "#WordOfGod"]

def is_telugu(text):
    return any("ఀ" <= ch <= "౿" for ch in text)

def sanitize_text(text):
    if not text:
        return text
    # Remove problematic characters
    text = text.replace("'", "'").replace("'", "'")
    text = text.replace(""", '"').replace(""", '"')
    return text

def count_words(text):
    if not text:
        return 0
    words = re.findall(r'\b\w+\b', text)
    return len(words)

def calculate_display_duration(text):
    word_count = count_words(text)
    base_time = MIN_DISPLAY_TIME
    word_time = word_count * 0.3  # 0.3 seconds per word
    total_time = base_time + word_time
    return max(MIN_DISPLAY_TIME, min(total_time, 15.0))  # Cap at 15 seconds

def create_background():
    """Create gradient background - cached"""
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

    # Simple vignette
    vignette = Image.new("L", VIDEO_SIZE, 0)
    vdraw = ImageDraw.Draw(vignette)
    vdraw.ellipse([-VIDEO_SIZE[0]*0.2, -VIDEO_SIZE[1]*0.2, VIDEO_SIZE[0]*1.2, VIDEO_SIZE[1]*1.2], fill=255)
    vignette = vignette.filter(ImageFilter.GaussianBlur(200))
    dark = Image.new("RGB", VIDEO_SIZE, (0, 0, 0))
    background = Image.composite(background, dark, vignette)

    return background

def create_text_image(text, font_size, color):
    """Pre-render text with neon glow effect - MUCH FASTER"""
    img = Image.new("RGBA", VIDEO_SIZE, (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    try:
        font = ImageFont.truetype(FONT_PATH, font_size)
    except:
        font = ImageFont.load_default()

    # Calculate text position
    bbox = draw.textbbox((0, 0), text, font=font)
    text_width = bbox[2] - bbox[0]
    text_height = bbox[3] - bbox[1]
    x = (VIDEO_SIZE[0] - text_width) // 2
    y = (VIDEO_SIZE[1] - text_height) // 2

    # Simple glow effect (fewer layers = faster)
    for i in range(10, 0, -3):
        alpha = int(40 * (10 - i) / 10)
        glow_color = (*color, alpha)
        draw.text((x, y), text, font=font, fill=glow_color, stroke_width=i, stroke_fill=glow_color)

    # Main text
    draw.text((x, y), text, font=font, fill=(*color, 255), stroke_width=2, stroke_fill=(*color, 200))

    return img

def calculate_font_size(text):
    """Smart font sizing based on text length"""
    word_count = count_words(text)
    if word_count <= 5:
        return 180
    elif word_count <= 10:
        return 140
    elif word_count <= 15:
        return 120
    else:
        return 100

def build_video(telugu_text, english_text):
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    os.makedirs(THUMBNAIL_DIR, exist_ok=True)

    print("Creating background...")
    background = create_background()
    background_array = np.array(background)

    # Calculate durations
    telugu_duration = calculate_display_duration(telugu_text) if telugu_text else 0
    english_duration = calculate_display_duration(english_text) if english_text else 0
    video_duration = max(telugu_duration + english_duration, 10)

    print(f"Video duration: {video_duration:.1f}s (Telugu: {telugu_duration:.1f}s, English: {english_duration:.1f}s)")

    # PRE-RENDER text images (CRITICAL OPTIMIZATION)
    print("Pre-rendering text layers...")
    text_images = []

    if telugu_text:
        telugu_font_size = calculate_font_size(telugu_text)
        telugu_img = create_text_image(telugu_text, telugu_font_size, NEON_COLORS[0])
        text_images.append(('telugu', np.array(telugu_img), 0, telugu_duration))

    if english_text:
        english_font_size = calculate_font_size(english_text)
        english_img = create_text_image(english_text, english_font_size, NEON_COLORS[2])
        text_images.append(('english', np.array(english_img), telugu_duration, video_duration))

    print("Text layers ready. Starting video render...")

    def make_frame(t):
        """Super fast - just composite pre-rendered images"""
        frame = background_array.copy()

        for name, text_array, start_time, end_time in text_images:
            if start_time <= t < end_time:
                # Simple fade in/out
                fade_duration = 0.5
                alpha = 1.0
                if t < start_time + fade_duration:
                    alpha = (t - start_time) / fade_duration
                elif t > end_time - fade_duration:
                    alpha = (end_time - t) / fade_duration

                # Composite with alpha
                if alpha > 0:
                    text_rgb = text_array[:, :, :3]
                    text_alpha = (text_array[:, :, 3:4] * alpha / 255.0)
                    frame = (frame * (1 - text_alpha) + text_rgb * text_alpha).astype(np.uint8)

        return frame

    clip = VideoClip(make_frame, duration=video_duration).set_fps(FPS)

    # Add music
    chosen_music = pick_music_file()
    music_source = AudioFileClip(chosen_music)
    end_time = min(5 + video_duration, music_source.duration)
    audio = music_source.subclip(5, end_time).volumex(0.3)
    clip = clip.set_audio(audio)

    output_path = os.path.join(OUTPUT_DIR, "verse_video.mp4")
    print(f"Writing video to {output_path}...")
    clip.write_videofile(
        output_path,
        fps=FPS,
        codec="libx264",
        audio_codec="aac",
        bitrate="12M",
        preset="faster",
        threads=4,
        ffmpeg_params=["-pix_fmt", "yuv420p"],
    )

    # Generate thumbnail
    thumbnail_path = generate_thumbnail(telugu_text, english_text)

    return output_path, thumbnail_path

def generate_thumbnail(telugu_text, english_text):
    """Quick thumbnail generation"""
    thumb_size = (1280, 720)
    bg = create_background()
    bg = bg.resize(thumb_size, Image.LANCZOS)

    display_text = f"{telugu_text}\n\n{english_text}" if telugu_text and english_text else (telugu_text or english_text)

    draw = ImageDraw.Draw(bg)
    try:
        font = ImageFont.truetype(FONT_PATH, 80)
    except:
        font = ImageFont.load_default()

    bbox = draw.textbbox((0, 0), display_text, font=font)
    text_width = bbox[2] - bbox[0]
    text_height = bbox[3] - bbox[1]
    x = (thumb_size[0] - text_width) // 2
    y = (thumb_size[1] - text_height) // 2

    # Simple glow
    for i in range(8, 0, -2):
        draw.text((x, y), display_text, font=font, fill=(0, 255, 255, 100), stroke_width=i, stroke_fill=(0, 255, 255, 50))
    draw.text((x, y), display_text, font=font, fill=(0, 255, 255, 255))

    timestamp = int(time.time())
    thumbnail_path = os.path.join(THUMBNAIL_DIR, f"thumbnail_{timestamp}.jpg")
    bg.save(thumbnail_path, "JPEG", quality=90)

    return thumbnail_path

def pick_music_file():
    music_files = sorted(f for f in os.listdir(MUSIC_DIR) if f.lower().endswith(".mp3"))
    if not music_files:
        raise FileNotFoundError(f"No .mp3 files found in {MUSIC_DIR}")
    if MUSIC_CHOICE and MUSIC_CHOICE.lower() != "random":
        for f in music_files:
            if f.lower() == MUSIC_CHOICE.lower():
                return os.path.join(MUSIC_DIR, f)
    return os.path.join(MUSIC_DIR, random.choice(music_files))

def get_user_credentials():
    return UserCredentials(
        None,
        refresh_token=os.environ["YT_REFRESH_TOKEN"],
        client_id=os.environ["YT_CLIENT_ID"],
        client_secret=os.environ["YT_CLIENT_SECRET"],
        token_uri="https://oauth2.googleapis.com/token",
        scopes=["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/youtube.upload"],
    )

def get_sheets_service():
    sa_info = json.loads(os.environ["GCP_SERVICE_ACCOUNT_JSON"])
    sa_creds = service_account.Credentials.from_service_account_info(
        sa_info, scopes=["https://www.googleapis.com/auth/spreadsheets"]
    )
    return build("sheets", "v4", credentials=sa_creds)

def get_youtube_service(creds):
    return build("youtube", "v3", credentials=creds)

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
        used = row[3] if len(row) > 3 else ""
        if (telugu or english) and used.strip().lower() != "used":
            available.append((i + 2, telugu.strip(), english.strip()))

    print(f"{len(available)} unused row(s) available.")
    if not available:
        return None, None, None
    return random.choice(available)

def mark_row_used(service, row_number):
    call_with_retries(lambda: service.spreadsheets().values().update(
        spreadsheetId=SHEET_ID,
        range=f"{SHEET_TAB}!D{row_number}",
        valueInputOption="RAW",
        body={"values": [["used"]]},
    ).execute())
    print(f"Marked row {row_number} as used.")

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
            print(f"Retrying in {delay}s...")
            time.sleep(delay)
        except (SSLError, ConnectionError, IncompleteRead, TimeoutError) as e:
            if attempt == max_retries:
                raise
            delay = base_delay * (2 ** (attempt - 1))
            print(f"Network error, retrying in {delay}s...")
            time.sleep(delay)

def upload_to_youtube(youtube, video_path, telugu_text, english_text):
    base_text = english_text or telugu_text
    title = (base_text[:80] + "...") if len(base_text) > 80 else base_text
    if not title:
        title = "Daily Bible Verse"

    description = f"{telugu_text}\n\n{english_text}\n\n" + " ".join(BASE_HASHTAGS + TELUGU_HASHTAGS + ENGLISH_HASHTAGS)

    privacy = PRIVACY_STATUS if PRIVACY_STATUS in ("private", "public", "unlisted") else "private"

    body = {
        "snippet": {
            "title": title[:100],
            "description": description,
            "categoryId": "22",
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
    print(f"Uploaded video ID: {response['id']}")
    return response["id"]

def main():
    creds = get_user_credentials()
    sheets_service = get_sheets_service()

    row_number = None
    if TELUGU_OVERRIDE or ENGLISH_OVERRIDE:
        telugu_text, english_text = TELUGU_OVERRIDE, ENGLISH_OVERRIDE
        print(f"Using override text")
    else:
        row_number, telugu_text, english_text = fetch_next_row(sheets_service)
        if not telugu_text and not english_text:
            print("No unused rows found.")
            sys.exit(0)
        print(f"Selected row {row_number}")

    telugu_text = sanitize_text(telugu_text)
    english_text = sanitize_text(english_text)

    video_path, thumbnail_path = build_video(telugu_text, english_text)
    print(f"Video: {video_path}")
    print(f"Thumbnail: {thumbnail_path}")

    youtube_service = get_youtube_service(creds)
    upload_to_youtube(youtube_service, video_path, telugu_text, english_text)

    if row_number is not None:
        mark_row_used(sheets_service, row_number)

    print("Done!")

if __name__ == "__main__":
    main()
