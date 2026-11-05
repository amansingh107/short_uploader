# Automated AI Split-Screen Shorts Pipeline

An automated pipeline that converts YouTube videos (podcasts, streams, devlogs) into viral 9:16 split-screen YouTube Shorts with burned-in subtitles and random background gameplay, with multi-account publishing support.

---

## Architecture
- **Top Half (1080x960):** Content video with synchronized dialogue subtitles and audio.
- **Bottom Half (1080x960):** Randomly sliced gameplay video (e.g. Subway Surfers, Minecraft Parkour) with audio muted.
- **AI Brain (Gemini 2.5 Flash):** Analyzes dialogue transcripts to detect viral hooks, generate clickable titles (`#shorts`), hashtag-rich descriptions, and SEO tags.
- **Multi-Account Uploading:** Publishes generated shorts across multiple YouTube accounts using independent Google Cloud OAuth credentials and quotas.

---

## Running in the Cloud via GitHub Actions

1. Go to the **Actions** tab of this repository.
2. Select **Automated Split-Screen Shorts Pipeline**.
3. Click **Run workflow**:
   - `main_url`: The YouTube video URL to clip.
   - `gameplay_url`: The bottom gameplay video (defaults to Subway Surfers).
   - `count`: Number of shorts to generate (default: 6).
   - `privacy`: `unlisted`, `public`, or `private`.
   - `upload`: Set to true to publish to your connected YouTube channels.
4. Rendered `.mp4` video files are also available as downloadable artifacts in the workflow run.

---

## Local Usage

### 1. Install Dependencies
```bash
pip install -r requirements.txt
```

### 2. Connect YouTube Accounts
```bash
python auth_account.py --name account1
python auth_account.py --name account2
```

### 3. Generate & Upload Shorts
```bash
python split_screen_pipeline.py \
  --url "https://www.youtube.com/watch?v=VIDEO_ID" \
  --gameplay "https://www.youtube.com/watch?v=GAMEPLAY_ID" \
  --count 6 \
  --upload \
  --privacy "unlisted"
```
