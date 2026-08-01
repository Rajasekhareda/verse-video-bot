# Daily Verse Video Bot

Reads an unused verse from your Google Sheet, renders it as a 30-second
typewriter-style video over a background image with music, and uploads it
to YouTube as a **private** video (so it shows up as a draft for you to
review/edit in YouTube Studio before publishing).

## What's in this folder

```
.github/workflows/generate-video.yml   -> GitHub Actions schedule
scripts/generate_video.py               -> main pipeline script
assets/background.jpg                   -> PLACEHOLDER background (replace with yours)
assets/music/track1.mp3                 -> your uploaded music file
assets/music/track2.mp3                 -> your uploaded music file
requirements.txt                        -> Python dependencies
```

## Setup steps

### 1. Push this folder to your GitHub repo
Commit and push everything in this folder, including `assets/music/*.mp3`
(GitHub allows files under 100MB — both tracks are under that limit).

### 2. Replace the placeholder background
Swap `assets/background.jpg` with your own YouTube-made image. Keep the
filename the same, or update `BACKGROUND_IMAGE` in the workflow env vars.

### 3. Confirm your GitHub Secrets
This script expects these exact secret names in
**Repo Settings → Secrets and variables → Actions**:

| Secret name              | What it is                                      |
|---------------------------|--------------------------------------------------|
| `SHEET_ID`                 | `1JdImkWazXD23T52brRN0OYT8FP6S9-G2d_ivBvcduMc`   |
| `GCP_SERVICE_ACCOUNT_JSON` | Full JSON content of your service account key    |
| `YT_CLIENT_ID`             | From your OAuth client_secret.json                |
| `YT_CLIENT_SECRET`         | From your OAuth client_secret.json                |
| `YT_REFRESH_TOKEN`         | From your earlier `get_sheets_token.py` run       |

If the secrets you added earlier used different names, either rename them
in GitHub to match the table above, or edit the `env:` block in
`.github/workflows/generate-video.yml` to match your existing names.

**Important — sharing permissions:**
- Share your Google Sheet with your **service account's email**
  (found inside the service account JSON, field `client_email`) as an
  **Editor** — otherwise the script can't read/write it.
- The YouTube upload uses your **personal account's OAuth refresh token**
  (not the service account), so it uploads to your own channel.

### 4. Sheet layout expected
- Column A = verse text
- Column B = reference (e.g. "John 3:16")
- Column C = left blank; the script writes `used` here after a verse is
  posted, so it won't repeat
- Row 1 = header (skipped automatically)

### 5. Test it
Go to the **Actions** tab in your repo → select **"Generate and Upload
Verse Video"** → click **Run workflow** to trigger it manually before
waiting for the daily schedule.

### 6. Adjust the schedule
Edit the `cron` line in `.github/workflows/generate-video.yml`. It's
currently set to run daily at 06:00 UTC.

## Customizing later
- **Typewriter speed**: change `TYPEWRITER_SECONDS` in `generate_video.py`
- **Video length**: change `VIDEO_DURATION` (currently 30 seconds)
- **Where music starts**: change `MUSIC_START_OFFSET` (currently 10 seconds)
- **Font/colors/sizes**: see `VERSE_FONT_SIZE`, `REF_FONT_SIZE`, and the
  `fill=(...)` colors inside `render_frame()`

## ⚠️ Copyright note
The two uploaded music tracks appear to be commercial worship recordings
(not stock/royalty-free). If you don't hold rights to redistribute them,
YouTube's Content ID may mute, claim, or restrict the videos. Worth
confirming licensing before this runs unattended on a schedule.
