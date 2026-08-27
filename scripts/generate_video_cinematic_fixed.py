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

# Font paths - cinematic system
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

# Video settings - CINEMATIC DIRECTOR GRADE
FPS = 24
VIDEO_SIZE = (3840, 2160)   # 4K UHD
LINE_TIME = 2.0             # 2 seconds per line
SCROLL_SPEED = 100          # pixels per second for scroll up
RENDER_SCALE = VIDEO_SIZE[0] / 1280

# Neon border settings (from original professional version)
NEON_BORDER_MARGIN = int(19 * RENDER_SCALE)
NEON_BORDER_THICKNESS = int(4 * RENDER_SCALE)
NEON_GLOW_THICKNESS = int(11 * RENDER_SCALE)
NEON_CYCLE_SPEED = 0.12
NEON_SEGMENTS_PER_SIDE = 14
STARS_PER_SIDE = 5

# Text settings - LARGE & PROFESSIONAL
BASE_FONT_SIZE_TELUGU = int(120 * RENDER_SCALE * 1.5)  # 1.5x larger
BASE_FONT_SIZE_LATIN = int(160 * RENDER_SCALE * 1.5)   # 1.5x larger
TEXT_MARGIN_X = int(130 * RENDER_SCALE)
SAFE_TOP = int(150 * RENDER_SCALE)     # Start from top
SAFE_BOTTOM = int(150 * RENDER_SCALE)
LINE_SPACING = 0.3                     # Relative to font size

OUTPUT_DIR = "output"
THUMBNAIL_DIR = os.path.join(OUTPUT_DIR, "thumbnails")

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

# Neon colors - CINEMATIC PALETTE
NEON_COLORS_TELUGU = [
    (0, 255, 255),    # Cyan
    (255, 20, 147),   # Deep pink
    (138, 43, 226),   # Blue violet
]

NEON_COLORS_ENGLISH = [
    (255, 0, 255),    # Magenta
    (0, 255, 127),    # Spring green
    (255, 69, 0),     # Orange red
]

BASE_HASHTAGS = ["#BibleVerse", "#DailyVerse", "#Faith", "#God", "#Jesus", "#Scripture"]
TELUGU_HASHTAGS = ["#TeluguChristian", "#YesuKrishtu", "#Telugu"]
ENGLISH_HASHTAGS = ["#Christian", "#Gospel", "#WordOfGod"]

# ==================== CINEMATIC HELPER FUNCTIONS ====================

def is_telugu(text):
    return any("ఀ" <= ch <= "౿" for ch in text)

def sanitize_text(text):
    """Convert literal \n to actual line breaks and clean text"""
    if not text:
        return text
    # Fix literal \n strings to actual line breaks
    text = text.replace("\\n", "\n")
    text = text.replace("\\\\n", "\n")  # Handle escaped backslashes
    # Remove problematic characters
    text = text.replace("'", "'").replace("'", "'")
    text = text.replace(""", '"').replace(""", '"')
    return text.strip()

def split_and_wrap_text(text, font_size):
    """Intelligently split text into lines respecting \n breaks and screen width"""
    if not text:
        return []

    lines = []

    # Split by explicit \n breaks
    paragraphs = text.split('\n')

    for paragraph in paragraphs:
        if not paragraph.strip():
            continue

        # For Telugu/English mixed, we'll handle differently
        telugu = is_telugu(paragraph)
        font = ImageFont.truetype(FONT_PATH, font_size)

        # Estimate max characters per line based on font size
        if telugu:
            max_chars_per_line = 15  # Telugu characters are wider
        else:
            max_chars_per_line = 25

        # Simple word wrap
        words = paragraph.split()
        current_line = []
        current_length = 0

        for word in words:
            word_length = len(word)
            # If adding this word would exceed limit, start new line
            if current_length + word_length + (1 if current_line else 0) > max_chars_per_line:
                if current_line:
                    lines.append(" ".join(current_line))
                current_line = [word]
                current_length = word_length
            else:
                current_line.append(word)
                current_length += word_length + (1 if current_line else 0)

        # Add the last line
        if current_line:
            lines.append(" ".join(current_line))

    return lines

def hue_to_rgb(hue):
    """Convert hue to RGB for animated neon border"""
    r, g, b = colorsys.hsv_to_rgb(hue % 1.0, 1.0, 1.0)
    return (int(r * 255), int(g * 255), int(b * 255))

def dim(color, factor):
    """Dim a color"""
    return tuple(int(c * factor) for c in color)

def build_border_points(w, h, margin):
    """Build points for animated neon border"""
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
    """Draw animated rainbow neon border"""
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
    """Create twinkling star positions"""
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
    """Draw a twinkling star"""
    draw.line([x - size, y, x + size, y], fill=color, width=max(2, int(2 * RENDER_SCALE)))
    draw.line([x, y - size, x, y + size], fill=color, width=max(2, int(2 * RENDER_SCALE)))
    draw.ellipse([x - 2, y - 2, x + 2, y + 2], fill=color)

def draw_twinkling_stars(draw, stars, t):
    """Draw all twinkling stars"""
    for s in stars:
        brightness = 0.4 + 0.6 * (0.5 + 0.5 * math.sin(s["speed"] * t + s["phase"]))
        size = (5 + 5 * brightness) * RENDER_SCALE
        shade = int(255 * brightness)
        draw_star_mark(draw, s["x"], s["y"], size, (shade, shade, shade))

def create_background():
    """Create cinematic gradient background"""
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

    # Cinematic vignette
    vignette = Image.new("L", VIDEO_SIZE, 0)
    vdraw = ImageDraw.Draw(vignette)
    vdraw.ellipse([-VIDEO_SIZE[0]*0.3, -VIDEO_SIZE[1]*0.3, VIDEO_SIZE[0]*1.3, VIDEO_SIZE[1]*1.3], fill=255)
    vignette = vignette.filter(ImageFilter.GaussianBlur(int(200 * RENDER_SCALE)))
    dark = Image.new("RGB", VIDEO_SIZE, (0, 0, 0))
    background = Image.composite(background, dark, vignette)

    return background

def create_text_layer(text_lines, font_size, neon_color, y_offset=0):
    """Create a text layer with neon effects"""
    img = Image.new("RGBA", VIDEO_SIZE, (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    try:
        font = ImageFont.truetype(FONT_PATH, font_size)
    except:
        font = ImageFont.load_default()

    line_height = int(font_size * (1 + LINE_SPACING))
    current_y = SAFE_TOP + y_offset

    for line in text_lines:
        bbox = draw.textbbox((0, 0), line, font=font)
        text_width = bbox[2] - bbox[0]
        x = (VIDEO_SIZE[0] - text_width) // 2

        # Neon glow effect
        for i in range(12, 0, -3):
            alpha = int(50 * (12 - i) / 12)
            glow_color = (*neon_color, alpha)
            draw.text((x, current_y), line, font=font, fill=glow_color, stroke_width=i, stroke_fill=glow_color)

        # Main text with bright neon core
        draw.text((x, current_y), line, font=font, fill=(*neon_color, 255), stroke_width=3, stroke_fill=(*neon_color, 220))

        current_y += line_height

    return img

class CinematicAnimation:
    """Professional line-by-line scrolling animation"""
    def __init__(self, telugu_text, english_text):
        self.telugu_lines = []
        self.english_lines = []
        self.all_lines = []

        # Process Telugu text
        if telugu_text:
            self.telugu_font_size = BASE_FONT_SIZE_TELUGU
            self.telugu_lines = split_and_wrap_text(telugu_text, self.telugu_font_size)
            for line in self.telugu_lines:
                self.all_lines.append({
                    'text': line,
                    'font_size': self.telugu_font_size,
                    'color': random.choice(NEON_COLORS_TELUGU),
                    'lang': 'telugu'
                })

        # Process English text
        if english_text:
            self.english_font_size = BASE_FONT_SIZE_LATIN
            self.english_lines = split_and_wrap_text(english_text, self.english_font_size)
            for line in self.english_lines:
                self.all_lines.append({
                    'text': line,
                    'font_size': self.english_font_size,
                    'color': random.choice(NEON_COLORS_ENGLISH),
                    'lang': 'english'
                })

        self.total_lines = len(self.all_lines)
        self.video_duration = self.total_lines * LINE_TIME + 1.0

        # Pre-render all text layers
        self.rendered_layers = []
        self.create_layers()

    def create_layers(self):
        """Pre-render each line"""
        for idx, line_data in enumerate(self.all_lines):
            layer = create_text_layer([line_data['text']],
                                     line_data['font_size'],
                                     line_data['color'])
            self.rendered_layers.append({
                'array': np.array(layer),
                'start_time': idx * LINE_TIME,
                'y_position': 0,
                'scroll_offset': 0
            })

    def calculate_frame(self, t, background_array, draw, stars):
        """Calculate frame at time t with scrolling animation"""
        frame = background_array.copy()

        # Draw neon border
        draw_neon_border(draw, VIDEO_SIZE, t)

        # Draw twinkling stars
        draw_twinkling_stars(draw, stars, t)

        # Determine which lines should be visible
        visible_lines = []
        for idx, line_data in enumerate(self.rendered_layers):
            if t >= line_data['start_time']:
                # Line is active - calculate scroll offset
                time_active = t - line_data['start_time']
                scroll_offset = int(time_active * SCROLL_SPEED)

                # Lines that appeared earlier scroll more
                if idx > 0:
                    earlier_lines = self.rendered_layers[:idx]
                    for earlier_idx, earlier_line in enumerate(earlier_lines):
                        if t >= earlier_line['start_time']:
                            # Earlier lines get cumulative scroll
                            earlier_time_active = t - earlier_line['start_time']
                            earlier_scroll = int(earlier_time_active * SCROLL_SPEED)
                            scroll_offset -= int(earlier_scroll * 0.5)  # Slightly less for earlier lines

                visible_lines.append({
                    'array': line_data['array'],
                    'scroll_offset': min(scroll_offset, VIDEO_SIZE[1] // 2)
                })

        # Composite visible lines (in reverse order so earlier lines appear under)
        for line in reversed(visible_lines):
            text_rgb = line['array'][:, :, :3]
            text_alpha = line['array'][:, :, 3:4] / 255.0

            # Apply scroll offset (move text up)
            y_offset = line['scroll_offset']

            # Create mask for this line's position
            mask = text_alpha > 0

            # Apply scrolling
            if y_offset > 0:
                # Shift text up by y_offset pixels
                shifted_text_rgb = np.roll(text_rgb, -y_offset, axis=0)
                shifted_mask = np.roll(mask, -y_offset, axis=0)

                # Fill top part with zeros
                shifted_text_rgb[:y_offset, :, :] = 0
                shifted_mask[:y_offset, :, :] = 0

                # Composite
                frame = frame * (1 - shifted_mask) + shifted_text_rgb * shifted_mask
            else:
                frame = frame * (1 - mask) + text_rgb * mask

        return frame.astype(np.uint8)

def build_video(telugu_text, english_text):
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    os.makedirs(THUMBNAIL_DIR, exist_ok=True)

    print("🎬 CINEMATIC VIDEO PRODUCTION STARTING...")
    print(f"Telugu: {len(telugu_text or '')} chars")
    print(f"English: {len(english_text or '')} chars")

    # Create cinematic animation system
    animation = CinematicAnimation(telugu_text, english_text)

    print(f"🎬 Rendering {animation.total_lines} lines over {animation.video_duration:.1f}s")
    print(f"Line timing: {LINE_TIME}s per line")

    # Create background
    background = create_background()
    stars = make_star_positions(VIDEO_SIZE)

    # Prepare for rendering
    background_array = np.array(background)
    background_img = background.copy()

    def make_frame(t):
        # Create fresh draw for each frame
        img = background_img.copy()
        draw = ImageDraw.Draw(img)

        return animation.calculate_frame(t, background_array, draw, stars)

    clip = VideoClip(make_frame, duration=animation.video_duration).set_fps(FPS)

    # Add music
    chosen_music = pick_music_file()
    music_source = AudioFileClip(chosen_music)
    end_time = min(3 + animation.video_duration, music_source.duration)
    audio = music_source.subclip(3, end_time).volumex(0.25)  # Lower volume for cinematic feel
    clip = clip.set_audio(audio)

    output_path = os.path.join(OUTPUT_DIR, "verse_video.mp4")
    print(f"🎬 Writing 4K cinematic video to {output_path}...")

    clip.write_videofile(
        output_path,
        fps=FPS,
        codec="libx264",
        audio_codec="aac",
        bitrate="16M",
        preset="faster",
        threads=6,
        ffmpeg_params=["-pix_fmt", "yuv420p", "-profile:v", "high", "-level", "5.2"],
    )

    # Generate thumbnail
    thumbnail_path = generate_thumbnail(telugu_text, english_text)

    return output_path, thumbnail_path

def generate_thumbnail(telugu_text, english_text):
    """Generate YouTube thumbnail with first line"""
    thumb_size = (1280, 720)

    # Create background matching video
    bg = create_background()
    bg = bg.resize(thumb_size, Image.LANCZOS)

    # Get first line for thumbnail
    display_text = ""
    if telugu_text:
        telugu_lines = split_and_wrap_text(telugu_text, BASE_FONT_SIZE_TELUGU // 2)
        if telugu_lines:
            display_text = telugu_lines[0]
    if english_text and not display_text:
        english_lines = split_and_wrap_text(english_text, BASE_FONT_SIZE_LATIN // 2)
        if english_lines:
            display_text = english_lines[0]

    if len(display_text) > 60:
        display_text = display_text[:57] + "..."

    draw = ImageDraw.Draw(bg)
    try:
        font = ImageFont.truetype(FONT_PATH, 90)
    except:
        font = ImageFont.load_default()

    bbox = draw.textbbox((0, 0), display_text, font=font)
    text_width = bbox[2] - bbox[0]
    x = (thumb_size[0] - text_width) // 2
    y = (thumb_size[1] // 2) - 50

    # Neon effect for thumbnail
    neon_color = NEON_COLORS_TELUGU[0]
    for i in range(10, 0, -2):
        draw.text((x, y), display_text, font=font, fill=(*neon_color, 100),
                  stroke_width=i, stroke_fill=(*neon_color, 50))
    draw.text((x, y), display_text, font=font, fill=(*neon_color, 255),
              stroke_width=2, stroke_fill=(*neon_color, 200))

    # "DAILY VERSE" badge
    try:
        badge_font = ImageFont.truetype(FONT_PATH, 40)
    except:
        badge_font = ImageFont.load_default()

    badge_text = "DAILY VERSE"
    badge_bbox = draw.textbbox((0, 0), badge_text, font=badge_font)
    badge_width = badge_bbox[2] - badge_bbox[0]
    badge_x = 20
    badge_y = thumb_size[1] - 60

    # Badge background
    draw.rectangle([badge_x-10, badge_y-10, badge_x+badge_width+10, badge_y+40],
                   fill=(0, 0, 0, 180))
    draw.text((badge_x, badge_y), badge_text, font=badge_font, fill=(255, 215, 0, 255))

    timestamp = int(time.time())
    thumbnail_path = os.path.join(THUMBNAIL_DIR, f"thumbnail_{timestamp}.jpg")
    bg.save(thumbnail_path, "JPEG", quality=95)

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
    """Get next UNUSED row - check column D for 'used'"""
    range_ = f"{SHEET_TAB}!A2:D"
    result = call_with_retries(
        lambda: service.spreadsheets().values().get(spreadsheetId=SHEET_ID, range=range_).execute()
    )
    rows = result.get("values", [])
    available = []

    for i, row in enumerate(rows):
        telugu = row[0] if len(row) > 0 else ""
        english = row[1] if len(row) > 1 else ""
        used = row[3].strip().lower() if len(row) > 3 else ""

        if (telugu or english) and used != "used":
            available.append((i + 2, telugu.strip(), english.strip()))

    print(f"Available rows: {len(available)}/{len(rows)}")
    if not available:
        return None, None, None
    return available[0]  # Take first available

def mark_row_used(service, row_number):
    call_with_retries(lambda: service.spreadsheets().values().update(
        spreadsheetId=SHEET_ID,
        range=f"{SHEET_TAB}!D{row_number}",
        valueInputOption="RAW",
        body={"values": [["used"]]},
    ).execute())
    print(f"✓ Marked row {row_number} as used")

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
            time.sleep(delay)
        except (SSLError, ConnectionError, IncompleteRead, TimeoutError) as e:
            if attempt == max_retries:
                raise
            delay = base_delay * (2 ** (attempt - 1))
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
            print(f"Upload: {int(status.progress() * 100)}%")
    print(f"✓ Uploaded: {response['id']}")
    return response["id"]

def main():
    creds = get_user_credentials()
    sheets_service = get_sheets_service()

    row_number = None
    if TELUGU_OVERRIDE or ENGLISH_OVERRIDE:
        telugu_text, english_text = TELUGU_OVERRIDE, ENGLISH_OVERRIDE
        print("Using override text")
    else:
        row_number, telugu_text, english_text = fetch_next_row(sheets_service)
        if not telugu_text and not english_text:
            print("No unused rows found")
            sys.exit(0)
        print(f"Selected row {row_number}")

    # Sanitize text
    telugu_text = sanitize_text(telugu_text)
    english_text = sanitize_text(english_text)

    print("🎬 Starting cinematic video production...")
    video_path, thumbnail_path = build_video(telugu_text, english_text)
    print(f"✅ Video: {video_path}")
    print(f"✅ Thumbnail: {thumbnail_path}")

    youtube_service = get_youtube_service(creds)
    upload_to_youtube(youtube_service, video_path, telugu_text, english_text)

    if row_number is not None:
        mark_row_used(sheets_service, row_number)

    print("✅ CINEMATIC PRODUCTION COMPLETE!")

if __name__ == "__main__":
    main()
