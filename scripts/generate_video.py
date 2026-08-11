import os
import sys
import re
import math
import random
import colorsys
import unicodedata

import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageFilter
from moviepy.editor import VideoClip, AudioFileClip

from google.oauth2.credentials import Credentials as UserCredentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

# ================= SHEET LAYOUT =================
# Column A = Telugu verse text
# Column B = English verse text
# Column C = optional explanation (English or Telugu) — leave blank if none
# Column D = "used" marker, written automatically by this script
# ==================================================

SHEET_ID    = os.environ["SHEET_ID"]
SHEET_TAB   = os.environ.get("SHEET_TAB", "Sheet1")
MUSIC_DIR   = os.environ.get("MUSIC_DIR",   "assets/music")
CUSTOM_BG_DIR = os.environ.get("CUSTOM_BG_DIR", "assets/backgrounds")

# --------------- FONTS ---------------
# Telugu always uses NotoSerifTelugu (system-installed by the workflow).
# English uses NotoSerif (also system-installed) — clean, elegant, cinematic.
# Poppins is not used; it caused boxes because it is not installed by default.
FONT_PATH_TELUGU = "/usr/share/fonts/truetype/noto/NotoSerifTelugu-Bold.ttf"
FONT_PATH_ENGLISH = "/usr/share/fonts/truetype/noto/NotoSerif-Bold.ttf"

# --------------- VIDEO ---------------
VIDEO_SIZE     = (1920, 1080)   # Full-HD (fast render; upscale in YT Studio if needed)
FPS            = 24
VIDEO_DURATION = 45             # total seconds

# RENDER_SCALE keeps every pixel-based measurement proportional to the resolution.
RENDER_SCALE = VIDEO_SIZE[0] / 1280

# --------------- TIMING ---------------
# Column A = 15 s, Column B = 12 s, Column C = 18 s.
# If a column is absent its share is redistributed so total always = VIDEO_DURATION.
COLUMN_DURATIONS = {"A": 15, "B": 12, "C": 18}
MUSIC_START_OFFSET = 10

# Line-by-line reveal: each column shows one line at a time, holds 3 s, then
# crossfades the next line in. Column A word-reveals its first line then switches
# to line-by-line for subsequent lines.
LINE_HOLD_SECONDS    = 3.0     # how long each line stays on screen
LINE_FADE_SECONDS    = 0.4     # crossfade duration between lines

# Column C paging (in case a single line is very long — handled by fit_text_block)
TRANSITION_SECONDS   = 1.0     # scroll transition between columns

# --------------- TEXT LAYOUT ---------------
TEXT_MARGIN_X  = int(120 * RENDER_SCALE)
SAFE_TOP       = int(100 * RENDER_SCALE)
SAFE_BOTTOM    = int(100 * RENDER_SCALE)
LINE_GAP_PT    = 1.5
PT_TO_PX       = 96 / 72

# --------------- FONT SIZES ---------------
FONT_SIZE_A    = int(68 * RENDER_SCALE)   # Column A  (Telugu)
FONT_SIZE_B    = int(72 * RENDER_SCALE)   # Column B  (English) — slightly bigger
FONT_SIZE_C    = int(56 * RENDER_SCALE)   # Column C  (explanation, slightly smaller)
FONT_SIZE_MIN  = int(24 * RENDER_SCALE)   # absolute floor

# --------------- TEXT STYLE ---------------
# Main verse (A & B): warm ivory → rich gold inlay, deep mahogany outlay
# Explanation (C):    soft cream → warm amber inlay, dark brown outlay
COLORS_A = ((255, 248, 220), (255, 200,  80), (60, 25,  5), (180, 110, 30))
COLORS_B = ((255, 255, 240), (255, 220, 100), (40, 20,  5), (160, 100, 20))
COLORS_C = ((255, 240, 200), (240, 180,  60), (50, 22,  5), (140,  85, 15))

SHADOW_OFFSET       = int(4 * RENDER_SCALE)
STROKE_INNER_WIDTH  = max(1, int(2 * RENDER_SCALE))
STROKE_OUTLAY_WIDTH = max(3, int(8 * RENDER_SCALE))

# --------------- NEON BORDER ---------------
NEON_MARGIN    = int(18 * RENDER_SCALE)
NEON_THICK     = int(4  * RENDER_SCALE)
NEON_GLOW      = int(10 * RENDER_SCALE)
NEON_SPEED     = 0.10
NEON_SEGMENTS  = 14
STARS_PER_SIDE = 5

OUTPUT_DIR = "output"

# --------------- RUN CONTROLS (from GitHub Actions inputs) ---------------
PRIVACY_STATUS       = os.environ.get("PRIVACY_STATUS",       "private").strip().lower()
MUSIC_CHOICE         = os.environ.get("MUSIC_CHOICE",         "random").strip()
BACKGROUND_THEME     = os.environ.get("BACKGROUND_THEME",     "random").strip()
INCLUDE_EXPLANATION  = os.environ.get("INCLUDE_EXPLANATION",  "auto").strip().lower()
TELUGU_OVERRIDE      = os.environ.get("TELUGU_OVERRIDE",      "").strip()
ENGLISH_OVERRIDE     = os.environ.get("ENGLISH_OVERRIDE",     "").strip()
EXPLANATION_OVERRIDE = os.environ.get("EXPLANATION_OVERRIDE", "").strip()

# ===================== BACKGROUND PALETTES =====================
GRADIENT_PALETTES = {
    "Midnight Purple": ((14, 10, 42),  (65, 18,  95)),
    "Ocean Blue":      (( 6, 22, 60),  (16, 65, 120)),
    "Wine Red":        ((42,  6, 18),  (100, 20,  45)),
    "Emerald Teal":    (( 6, 38, 34),  (14, 90,  74)),
    "Sunset Amber":    ((48, 20,  6),  (130, 58,  14)),
    "Indigo Violet":   ((18, 12, 52),  (62, 40, 140)),
    "Midnight Slate":  ((12, 18, 34),  (24, 40,  72)),
    "Magenta Plum":    ((36,  8, 44),  (100, 18,  88)),
}

BASE_HASHTAGS    = ["#BibleVerse","#DailyVerse","#Faith","#God","#Jesus","#Scripture"]
TELUGU_HASHTAGS  = ["#TeluguChristian","#YesuKrishtu","#Telugu"]
ENGLISH_HASHTAGS = ["#Christian","#Gospel","#WordOfGod"]


# ==================== HELPERS ====================

def is_telugu(text):
    return any("\u0c00" <= ch <= "\u0c7f" for ch in text)

def extract_reference_tag(text):
    m = re.search(r"\(([^()]+)\)\s*$", (text or "").strip())
    if not m: return None
    words = m.group(1).strip().split()
    return words[0] if words else None

def generate_hashtags(telugu_text, english_text):
    tags = list(BASE_HASHTAGS)
    if is_telugu(telugu_text): tags += TELUGU_HASHTAGS
    if english_text:           tags += ENGLISH_HASHTAGS
    for src in (english_text, telugu_text):
        word = extract_reference_tag(src or "")
        if word:
            cleaned = "".join(ch for ch in word
                              if not unicodedata.category(ch).startswith(("P","Z","C","N")))
            tag = "#" + cleaned
            if tag != "#" and tag not in tags:
                tags.append(tag)
            break
    return tags[:10]


# ==================== NEON BORDER + STARS ====================

def hue_to_rgb(hue):
    r,g,b = colorsys.hsv_to_rgb(hue % 1.0, 1.0, 1.0)
    return (int(r*255), int(g*255), int(b*255))

def dimmed(color, f):
    return tuple(int(c*f) for c in color)

def border_points(w, h, m):
    pts, s = [], NEON_SEGMENTS
    x0,y0,x1,y1 = m,m,w-m,h-m
    for i in range(s+1): pts.append((x0+(x1-x0)*i/s, y0))
    for i in range(1,s+1): pts.append((x1, y0+(y1-y0)*i/s))
    for i in range(1,s+1): pts.append((x1-(x1-x0)*i/s, y1))
    for i in range(1,s+1): pts.append((x0, y1-(y1-y0)*i/s))
    return pts

def draw_neon_border(draw, size, t):
    pts = border_points(*size, NEON_MARGIN)
    n = len(pts)-1
    off = t * NEON_SPEED
    for i in range(n):
        c = hue_to_rgb((i/n)+off)
        draw.line([pts[i], pts[i+1]], fill=dimmed(c,0.4), width=NEON_GLOW)
        draw.line([pts[i], pts[i+1]], fill=c,             width=NEON_THICK)

def make_stars(size):
    w,h = size
    outer = max(NEON_MARGIN-10, 6)
    em    = int(40*RENDER_SCALE)
    stars = []
    def add(xs,ys):
        for x,y in zip(xs,ys):
            stars.append({"x":x,"y":y,
                          "phase":random.uniform(0,math.tau),
                          "speed":random.uniform(1.2,2.4)})
    xs = np.linspace(em, w-em, STARS_PER_SIDE)
    add(xs, [outer]*STARS_PER_SIDE); add(xs, [h-outer]*STARS_PER_SIDE)
    ys = np.linspace(em, h-em, STARS_PER_SIDE)
    add([outer]*STARS_PER_SIDE, ys); add([w-outer]*STARS_PER_SIDE, ys)
    return stars

def draw_stars(draw, stars, t):
    for s in stars:
        b = 0.4+0.6*(0.5+0.5*math.sin(s["speed"]*t+s["phase"]))
        sz = (4+5*b)*RENDER_SCALE
        sh = int(255*b)
        c  = (sh,sh,sh)
        x,y = s["x"], s["y"]
        draw.line([x-sz,y,x+sz,y], fill=c, width=max(1,int(2*RENDER_SCALE)))
        draw.line([x,y-sz,x,y+sz], fill=c, width=max(1,int(2*RENDER_SCALE)))
        draw.ellipse([x-2,y-2,x+2,y+2], fill=c)


# ==================== BACKGROUND ====================

def apply_vignette(img, size):
    w,h = size
    vig  = Image.new("L", size, 0)
    vd   = ImageDraw.Draw(vig)
    vd.ellipse([-w*.3,-h*.3,w*1.3,h*1.3], fill=255)
    vig  = vig.filter(ImageFilter.GaussianBlur(int(100*RENDER_SCALE)))
    dark = Image.new("RGB", size, (0,0,0))
    return Image.composite(img, dark, vig)

def make_background(size):
    wants_custom = BACKGROUND_THEME.lower() in ("custom","custom image")
    if wants_custom:
        for ext in (".jpg",".jpeg",".png"):
            p = os.path.join(CUSTOM_BG_DIR, "custom_background"+ext)
            if os.path.exists(p):
                img = Image.open(p).convert("RGB")
                tw,th = size
                sw,sh = img.size
                scale = max(tw/sw, th/sh)
                img = img.resize((int(sw*scale),int(sh*scale)), Image.LANCZOS)
                l=(img.width-tw)//2; t=(img.height-th)//2
                img = img.crop((l,t,l+tw,t+th))
                return apply_vignette(img, size)
        print("Custom BG not found — using gradient.")

    if BACKGROUND_THEME in GRADIENT_PALETTES:
        top_c, bot_c = GRADIENT_PALETTES[BACKGROUND_THEME]
    else:
        top_c, bot_c = random.choice(list(GRADIENT_PALETTES.values()))

    w,h = size
    top = np.array(top_c, dtype=float)
    bot = np.array(bot_c, dtype=float)
    t_  = np.linspace(0,1,h).reshape(h,1,1)
    grad = (top*(1-t_)+bot*t_).astype(np.uint8)
    grad = np.repeat(grad, w, axis=1)
    img  = Image.fromarray(grad, "RGB")
    return apply_vignette(img, size)

def pick_music():
    files = sorted(f for f in os.listdir(MUSIC_DIR) if f.lower().endswith(".mp3"))
    if not files: raise FileNotFoundError(f"No mp3 in {MUSIC_DIR}")
    if MUSIC_CHOICE.lower() != "random":
        for f in files:
            if f.lower() == MUSIC_CHOICE.lower():
                return os.path.join(MUSIC_DIR, f)
    return os.path.join(MUSIC_DIR, random.choice(files))


# ==================== SHEETS / YOUTUBE ====================

def get_creds():
    return UserCredentials(
        None,
        refresh_token   = os.environ["YT_REFRESH_TOKEN"],
        client_id       = os.environ["YT_CLIENT_ID"],
        client_secret   = os.environ["YT_CLIENT_SECRET"],
        token_uri       = "https://oauth2.googleapis.com/token",
        scopes          = ["https://www.googleapis.com/auth/spreadsheets",
                           "https://www.googleapis.com/auth/youtube.upload"],
    )

def fetch_next_row(service):
    r = service.spreadsheets().values().get(
        spreadsheetId=SHEET_ID, range=f"{SHEET_TAB}!A2:D").execute()
    for i, row in enumerate(r.get("values",[])):
        te  = (row[0] if len(row)>0 else "").strip()
        en  = (row[1] if len(row)>1 else "").strip()
        exp = (row[2] if len(row)>2 else "").strip()
        used= (row[3] if len(row)>3 else "").strip().lower()
        if (te or en) and used != "used":
            return i+2, te, en, exp
    return None, None, None, None

def mark_used(service, row):
    service.spreadsheets().values().update(
        spreadsheetId=SHEET_ID, range=f"{SHEET_TAB}!D{row}",
        valueInputOption="RAW", body={"values":[["used"]]}).execute()


# ==================== TEXT UTILITIES ====================

def normalize_breaks(text):
    if not text: return text
    text = text.replace("\\\\n","\n").replace("\\n","\n")
    text = text.replace("\r\n","\n").replace("\r","\n")
    return text

def text_w(draw, text, font):
    bb = draw.textbbox((0,0), text, font=font, stroke_width=STROKE_INNER_WIDTH)
    return bb[2]-bb[0]

def wrap(draw, text, font, max_w):
    """Respect manual \n breaks, then auto-wrap each segment by pixel width."""
    text  = normalize_breaks(text)
    lines = []
    for seg in text.split("\n"):
        words, cur = seg.split(), ""
        for w in words:
            test = f"{cur} {w}".strip()
            if text_w(draw, test, font) <= max_w:
                cur = test
            else:
                if cur: lines.append(cur)
                cur = w
        lines.append(cur)
    return lines

def line_height(font):
    asc, dsc = font.getmetrics()
    return int(round(asc + dsc + LINE_GAP_PT * PT_TO_PX * RENDER_SCALE))

def fit_font(draw, text, path, initial, min_sz, max_w, max_h):
    """Shrink font until full text fits on screen, or stop at min_sz."""
    sz = initial
    while sz >= min_sz:
        f   = ImageFont.truetype(path, sz)
        ls  = wrap(draw, text, f, max_w)
        lh  = line_height(f)
        if len(ls)*lh <= max_h:
            return f, ls, lh
        sz -= 2
    f  = ImageFont.truetype(path, min_sz)
    ls = wrap(draw, text, f, max_w)
    return f, ls, line_height(f)


# ==================== CINEMATIC TEXT DRAWING ====================
# The gradient is built only over the text-block's height (not the full
# frame), so the gold shift is always clearly visible.

def _make_gradient_strip(w, h, top_c, bot_c):
    """Build a gradient image exactly h pixels tall, full width w."""
    top = np.array(top_c, dtype=float)
    bot = np.array(bot_c, dtype=float)
    t   = np.linspace(0,1,max(h,1)).reshape(-1,1,1)
    arr = (top*(1-t)+bot*t).astype(np.uint8)
    arr = np.repeat(arr, w, axis=1)
    return Image.fromarray(arr, "RGB")

def draw_line_cinematic(img, draw, text, font, x, y, colors, block_top, block_h):
    """Draw one line with: shadow → outlay border → inner edge → gradient inlay."""
    ft, fb, outlay, inner = colors
    # 1. drop shadow
    draw.text((x+SHADOW_OFFSET, y+SHADOW_OFFSET), text, font=font, fill=(0,0,0))
    # 2. thick outlay border
    draw.text((x,y), text, font=font, fill=outlay,
              stroke_width=STROKE_OUTLAY_WIDTH, stroke_fill=outlay)
    # 3. thin crisp inner ring
    draw.text((x,y), text, font=font, fill=inner,
              stroke_width=STROKE_INNER_WIDTH, stroke_fill=inner)
    # 4. gradient inlay — built over block height so shift is visible
    mask = Image.new("L", img.size, 0)
    ImageDraw.Draw(mask).text((x,y), text, font=font, fill=255)
    bh   = max(block_h, 1)
    strip = _make_gradient_strip(img.width, bh, ft, fb)
    canvas = Image.new("RGB", img.size, (0,0,0))
    canvas.paste(strip, (0, block_top))
    img.paste(canvas, (0,0), mask)

def draw_block(img, draw, lines, font, lh, size, y_offset, colors, alpha=1.0):
    """Draw a block of lines; alpha for crossfade blending."""
    total_h = len(lines) * lh
    block_top = int((size[1]-total_h)//2 - 30 + y_offset)
    y = block_top
    if alpha < 1.0:
        tmp = img.copy()
        tmp_draw = ImageDraw.Draw(tmp)
        for line in lines:
            w = text_w(tmp_draw, line, font)
            x = max(TEXT_MARGIN_X, (size[0]-w)//2)
            draw_line_cinematic(tmp, tmp_draw, line, font, x, y, colors, block_top, total_h)
            y += lh
        return Image.blend(img, tmp, alpha)
    else:
        for line in lines:
            w = text_w(draw, line, font)
            x = max(TEXT_MARGIN_X, (size[0]-w)//2)
            draw_line_cinematic(img, draw, line, font, x, y, colors, block_top, total_h)
            y += lh
        return img


# ==================== PHASE BUILDING ====================

def build_phase(text, col, size):
    """Return a phase dict with pre-fitted font and line list."""
    telugu   = is_telugu(text)
    path     = FONT_PATH_TELUGU if telugu else FONT_PATH_ENGLISH
    init_sz  = {"A": FONT_SIZE_A, "B": FONT_SIZE_B, "C": FONT_SIZE_C}[col]
    colors   = {"A": COLORS_A,    "B": COLORS_B,    "C": COLORS_C}[col]
    max_w    = size[0] - TEXT_MARGIN_X*2
    max_h    = size[1] - SAFE_TOP - SAFE_BOTTOM

    probe    = Image.new("RGB", size)
    pd       = ImageDraw.Draw(probe)
    font, lines, lh = fit_font(pd, text, path, init_sz, FONT_SIZE_MIN, max_w, max_h)

    # Assign fixed duration; scaled later if columns are missing.
    duration = COLUMN_DURATIONS[col]
    return {"col":col,"text":text,"font":font,"lines":lines,"lh":lh,
            "colors":colors,"duration":duration}


# ==================== LINE-BY-LINE REVEAL ====================

def lines_state(phase, t_local):
    """Return (current_lines, next_lines, fade_alpha) for line-by-line reveal.
    Each line holds LINE_HOLD_SECONDS then crossfades to the next line."""
    lines = phase["lines"]
    n     = len(lines)
    if n == 0: return [], [], 1.0

    slot_dur  = LINE_HOLD_SECONDS + LINE_FADE_SECONDS
    total_seq = n * slot_dur
    # Clamp so the last line stays on for the rest of the phase.
    t_clamped = min(t_local, total_seq - LINE_FADE_SECONDS)

    idx   = min(int(t_clamped // slot_dur), n-1)
    tl    = t_clamped - idx*slot_dur

    if tl >= LINE_HOLD_SECONDS and idx < n-1:
        alpha = (tl - LINE_HOLD_SECONDS) / LINE_FADE_SECONDS
        alpha = max(0.0, min(1.0, alpha))
        return [lines[idx]], [lines[idx+1]], alpha
    else:
        return [lines[idx]], [], 1.0


# ==================== FRAME RENDERER ====================

def render_frame(bg, size, phases, t, stars):
    img  = bg.copy()
    draw = ImageDraw.Draw(img)

    draw_neon_border(draw, size, t)
    draw_stars(draw, stars, t)

    # Locate which phase is active.
    idx, elapsed = 0, 0.0
    for i, ph in enumerate(phases):
        if t < elapsed + ph["duration"] or i == len(phases)-1:
            idx = i; break
        elapsed += ph["duration"]

    phase   = phases[idx]
    t_local = t - elapsed
    ph_dur  = phase["duration"]

    # Check if we're in the inter-column scroll transition.
    in_scroll = (t_local >= ph_dur - TRANSITION_SECONDS) and idx < len(phases)-1

    if in_scroll:
        prog  = (t_local-(ph_dur-TRANSITION_SECONDS)) / TRANSITION_SECONDS
        ease  = prog*prog*(3-2*prog)
        # Current column scrolls out upward.
        cur_lines, nxt_lines, _ = lines_state(phase, ph_dur - TRANSITION_SECONDS)
        out_off = -ease * size[1]
        img = draw_block(img, draw, cur_lines, phase["font"], phase["lh"],
                         size, out_off, phase["colors"])
        # Next column scrolls in from below.
        nxt = phases[idx+1]
        nxt_first = [nxt["lines"][0]] if nxt["lines"] else []
        in_off  = (1-ease) * size[1]
        draw2   = ImageDraw.Draw(img)
        img = draw_block(img, draw2, nxt_first, nxt["font"], nxt["lh"],
                         size, in_off, nxt["colors"])
    else:
        cur, nxt, alpha = lines_state(phase, t_local)
        img = draw_block(img, draw, cur, phase["font"], phase["lh"],
                         size, 0, phase["colors"])
        if nxt and alpha < 1.0:
            draw2 = ImageDraw.Draw(img)
            img   = draw_block(img, draw2, nxt, phase["font"], phase["lh"],
                                size, 0, phase["colors"], alpha=alpha)

    return np.array(img.convert("RGB"))


# ==================== VIDEO BUILD ====================

def build_video(telugu_text, english_text, explanation_text):
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    size = VIDEO_SIZE
    bg   = make_background(size)
    strs = make_stars(size)

    specs = []
    if telugu_text:      specs.append((telugu_text,      "A"))
    if english_text:     specs.append((english_text,     "B"))

    inc_exp = bool(explanation_text) and INCLUDE_EXPLANATION != "no"
    if INCLUDE_EXPLANATION == "yes" and not explanation_text: inc_exp = False
    if inc_exp:          specs.append((explanation_text, "C"))

    if not specs: raise ValueError("No text to render.")

    phases = [build_phase(text, col, size) for text, col in specs]

    # Scale durations so they always total exactly VIDEO_DURATION.
    intended = sum(p["duration"] for p in phases)
    scale    = VIDEO_DURATION / intended if intended else 1
    for p in phases:
        p["duration"] *= scale

    def make_frame(t):
        return render_frame(bg, size, phases, t, strs)

    clip  = VideoClip(make_frame, duration=VIDEO_DURATION).set_fps(FPS)
    music = AudioFileClip(pick_music()).subclip(MUSIC_START_OFFSET,
                                               MUSIC_START_OFFSET+VIDEO_DURATION)
    clip  = clip.set_audio(music)

    out = os.path.join(OUTPUT_DIR, "verse_video.mp4")
    clip.write_videofile(out, fps=FPS, codec="libx264", audio_codec="aac",
                         bitrate="12M", preset="fast",
                         ffmpeg_params=["-pix_fmt","yuv420p"])
    return out


# ==================== YOUTUBE UPLOAD ====================

def upload(youtube, path, telugu_text, english_text):
    src   = english_text or telugu_text
    title = re.sub(r"\([^()]*\)\s*$","",src).strip()[:80]
    if not title: title = "Daily Bible Verse"

    tags  = generate_hashtags(telugu_text, english_text)
    desc  = f"{telugu_text}\n\n{english_text}\n\n"+" ".join(tags)
    priv  = PRIVACY_STATUS if PRIVACY_STATUS in ("private","public","unlisted") else "private"

    body  = {"snippet":{"title":title[:100],"description":desc,
                         "categoryId":"22","tags":[t.lstrip("#") for t in tags]},
             "status":{"privacyStatus":priv}}
    media = MediaFileUpload(path, chunksize=-1, resumable=True, mimetype="video/mp4")
    req   = youtube.videos().insert(part="snippet,status",body=body,media_body=media)
    resp  = None
    while resp is None:
        st, resp = req.next_chunk()
        if st: print(f"Upload {int(st.progress()*100)}%")
    print(f"Uploaded: {resp['id']} ({priv})")
    return resp["id"]


# ==================== MAIN ====================

def main():
    creds   = get_creds()
    sheets  = build("sheets","v4",credentials=creds)
    youtube = build("youtube","v3",credentials=creds)

    row_number = None
    if TELUGU_OVERRIDE or ENGLISH_OVERRIDE:
        te, en, exp = TELUGU_OVERRIDE, ENGLISH_OVERRIDE, EXPLANATION_OVERRIDE
        print(f"Override: te={te!r}  en={en!r}  exp={exp!r}")
    else:
        row_number, te, en, exp = fetch_next_row(sheets)
        if not te and not en:
            print("No unused rows. Exiting.")
            sys.exit(0)
        print(f"Row {row_number}: te={te!r}  en={en!r}  exp={exp!r}")

    path = build_video(te, en, exp)
    upload(youtube, path, te, en)
    if row_number:
        mark_used(sheets, row_number)
    print("Done.")

if __name__ == "__main__":
    main()
