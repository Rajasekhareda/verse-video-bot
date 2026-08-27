# 🚀 How to Use the Improved Verse Video Bot

## 📋 Quick Start Guide

### 1. **Test Locally (Optional)**
First, let's verify everything works by checking the script syntax:

```bash
# Navigate to the project
cd C:\Users\LENOVO\verse-video-bot

# Check that our improved script compiles correctly
python -m py_compile scripts\generate_video_pro.py
# (No output means success!)
```

### 2. **Set Up Environment Variables** 
For actual video generation, you'll need these set in your environment:
- `SHEET_ID` - Your Google Sheet ID
- `YT_REFRESH_TOKEN` - YouTube OAuth refresh token
- `YT_CLIENT_ID` - YouTube OAuth client ID
- `YT_CLIENT_SECRET` - YouTube OAuth client secret
- `GCP_SERVICE_ACCOUNT_JSON` - Google Service Account JSON

*(These are the same as your original setup)*

### 3. **Update GitHub Actions Workflow**
To use the improved version in your automated daily runs:

Edit `.github/workflows/generate-video.yml` and change:
```yaml
# OLD:
- run: python scripts/generate_video.py

# NEW:
- run: python scripts/generate_video_pro.py
```

### 4. **Generate Your First Video**
Once secrets are configured in GitHub:

1. Go to your repository on GitHub
2. Click the "Actions" tab
3. Select "Generate and Upload Verse Video" workflow
4. Click "Run workflow" → "Run workflow" button
5. Watch it generate a professional video with perfect timing!

### 5. **Find Your Outputs**
After the workflow completes:
- **Video**: Available as an artifact from the workflow run
- **Thumbnail**: Check the `output/thumbnails/` folder in the workflow artifacts
- **Logs**: See detailed console output in the workflow run

## 🎬 What Makes This Professional?

### Perfect Timing ⏱️
- **5-6 second minimum** display time per text segment
- **Word-count based duration** - longer verses get appropriate time
- **Contemplative pace** - 2-3 seconds per word for reflection

### Cinematic Visuals ✨
- **Professional neon glow** - multiple layers for realistic effect
- **Subtle pulsing animation** - text appears alive, not static
- **Smart color cycling** - attractive neon palette that evolves
- **Clean, full-screen presentation** - no distracting boxes or borders

### Optimal Readability 👓
- **Intelligent font sizing** - automatically fits 4K screens perfectly
- **Proper line spacing** - comfortable reading experience
- **Centered alignment** - natural focus point for viewers
- **Bilingual support** - Telugu and English displayed together when both exist

### YouTube Ready 📱
- **Auto-generated thumbnails** - 1280x720 professional quality
- **Matching styling** - thumbnails use same neon effects as video
- **Branded with "DAILY VERSE"** - consistent channel identity
- **High-quality export** - 4K resolution with optimal bitrate

## 🔧 Technical Specifications

- **Resolution**: 3840x2160 (4K UHD)
- **Frame Rate**: 24 fps (cinematic)
- **Audio**: Background music at 30% volume, starts at 5s
- **Format**: H.264 video, AAC audio in MP4 container
- **Thumbnail**: 1280x720 JPEG, YouTube optimized
- **Font**: Noto Serif (Latin & Telugu) for perfect glyph support
- **Effects**: Multi-layer neon glow with pulse animation

## 📞 Need Help?

If you encounter any issues:
1. Check the workflow logs for detailed error messages
2. Verify all required environment variables are set in GitHub Secrets
3. Ensure your Google Sheet is shared with the service account email
4. Confirm YouTube OAuth tokens are valid and not expired

The system is designed to be robust and create beautiful, professional verse videos every single time!