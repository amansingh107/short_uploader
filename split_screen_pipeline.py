import os
import sys
import json
import time
import random
import subprocess
import argparse
import re
from datetime import timedelta
import warnings
warnings.filterwarnings("ignore")

import yt_dlp
from youtube_transcript_api import YouTubeTranscriptApi
import google.generativeai as genai

# Force UTF-8 stdout encoding on Windows
if sys.stdout.encoding != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

# Upload module
try:
    from upload_short import upload_short
    UPLOAD_AVAILABLE = True
except ImportError:
    UPLOAD_AVAILABLE = False

def load_env():
    """Load key-values from .env if present."""
    if os.path.exists(".env"):
        try:
            with open(".env", "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, v = line.split("=", 1)
                        if k.strip() not in os.environ:
                            os.environ[k.strip()] = v.strip().strip("'\"")
        except Exception:
            pass

load_env()
DEFAULT_GEMINI_KEY = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY", "")
DEFAULT_GAMEPLAY_URL = "https://www.youtube.com/watch?v=bdmG3oh-ZD8"  # Subway Surfers


def get_video_id(url_or_id: str) -> str:
    """Extract YouTube video ID from URL or return directly."""
    if not url_or_id:
        return None
    if len(url_or_id) == 11 and re.match(r"^[A-Za-z0-9_-]{11}$", url_or_id):
        return url_or_id
    patterns = [
        r"(?:v=|\/)([0-9A-Za-z_-]{11}).*",
        r"youtu\.be\/([0-9A-Za-z_-]{11})",
        r"embed\/([0-9A-Za-z_-]{11})",
        r"shorts\/([0-9A-Za-z_-]{11})",
    ]
    for pattern in patterns:
        match = re.search(pattern, url_or_id)
        if match:
            return match.group(1)
    return None


def get_video_duration(video_path: str) -> float:
    """Get exact video duration using ffprobe."""
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        video_path,
    ]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(f"Failed to probe duration for '{video_path}': {res.stderr}")
    return float(res.stdout.strip())


def ensure_video(url_or_path: str, prefix: str = "video", cookiefile: str = "cookies.txt") -> str:
    """Download video if URL is provided, or return verified local file path."""
    if os.path.exists(url_or_path):
        print(f"Using local file: {url_or_path}")
        return url_or_path

    v_id = get_video_id(url_or_path)
    output_path = f"{prefix}_{v_id}.mp4" if v_id else f"{prefix}.mp4"

    if os.path.exists(output_path) and os.path.getsize(output_path) > 1024 * 1024:
        print(f"Found cached video '{output_path}' ({os.path.getsize(output_path)/(1024*1024):.1f} MB). Skipping re-download.")
        return output_path

    print(f"Downloading from URL: {url_or_path} -> {output_path}...")
    ydl_opts = {
        "format": "bestvideo[height<=1080]+bestaudio/bestvideo+bestaudio/best[height<=1080]/best/bv*+ba/b",
        "merge_output_format": "mp4",
        "outtmpl": output_path,
        "overwrites": True,
        "quiet": False,
        "no_warnings": False,
        "socket_timeout": 30,
        "retries": 10,
        "fragment_retries": 10,
        "remote_components": ["ejs:github"],
    }
    if os.path.exists(cookiefile) and os.path.getsize(cookiefile) > 10:
        print(f"-> Using cookies from '{cookiefile}' for authentication.")
        ydl_opts["cookiefile"] = cookiefile

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url_or_path])
    except Exception as e:
        print(f"Primary format download failed ({e}). Retrying with flexible fallback clients...")
        ydl_opts_fallback = dict(ydl_opts)
        ydl_opts_fallback["format"] = "b/bv*+ba/best"
        ydl_opts_fallback["extractor_args"] = {"youtube": {"player_client": ["android", "ios", "web"]}}
        with yt_dlp.YoutubeDL(ydl_opts_fallback) as ydl:
            ydl.download([url_or_path])

    return output_path


def fetch_transcript(video_id: str) -> list[dict]:
    """Fetch transcript from YouTube in Hindi, English, or auto-generated."""
    if not video_id:
        return []
    try:
        ytt = YouTubeTranscriptApi()
        transcript_list = ytt.list(video_id)
        
        target = None
        for code in ["hi", "en", "en-US", "en-GB"]:
            try:
                target = transcript_list.find_transcript([code])
                if target:
                    break
            except Exception:
                continue

        if not target:
            for t in transcript_list:
                target = t
                break

        if target:
            print(f"-> Retrieved transcript: {target.language} ({target.language_code})")
            return target.fetch().to_raw_data()
    except Exception as e:
        print(f"Warning: Could not fetch transcript via API ({e}).")
    return []


def select_multiple_clips_with_ai(
    title: str,
    duration: float,
    transcript: list[dict],
    num_clips: int = 6,
    api_key: str = DEFAULT_GEMINI_KEY,
) -> list[dict]:
    """Use Gemini with retries to find the top N viral moments, titles, hashtags, and descriptions."""
    print(f"\n[AI Brain] Asking Gemini to select {num_clips} viral moments and generate metadata...")
    genai.configure(api_key=api_key)

    # Format transcript
    transcript_text = ""
    if transcript:
        lines = []
        for item in transcript:
            start = item.get("start", 0)
            dur = item.get("duration", 0)
            text = item.get("text", "").replace("\n", " ").strip()
            if text and text != "[संगीत]":
                lines.append(f"[{start:.1f}s - {start+dur:.1f}s]: {text}")
        transcript_text = "\n".join(lines[:350])

    max_clip_len = 50.0
    min_clip_len = 25.0

    prompt = f"""
You are an elite YouTube Shorts producer and media editor.
Select the top {num_clips} most viral, funny, relatable, high-retention highlights from this video.

Video Details:
- Title: {title}
- Total Duration: {duration:.1f} seconds

Transcript with Timestamps:
{transcript_text if transcript_text else "No transcript available. Estimate high-impact timestamps across the video duration."}

Requirements for each clip:
1. Clip duration MUST be between {min_clip_len:.0f} and {max_clip_len:.0f} seconds (strictly under 58 seconds).
2. Each clip must have an engaging conversation hook, funny moment, or dramatic insight.
3. Spread out the highlights across the video (don't cram them all in one spot).
4. Generate a click-worthy viral Title ending with #shorts (max 65 chars).
5. Generate an engaging Description with 3-5 hashtags (#shorts #trending #viral).
6. Generate 5-8 SEO Tags.

Respond ONLY with a JSON array of {num_clips} objects:
[
  {{
    "clip_number": 1,
    "start_seconds": 15.0,
    "end_seconds": 55.0,
    "title": "Shocking Truth About Modern Dating #shorts",
    "description": "This hit too close to home... Like and subscribe!\\n\\n#shorts #relatable #trending",
    "tags": ["shorts", "dating", "relationships", "viral"]
  }}
]
"""

    clips = None
    for attempt in range(4):
        try:
            model = genai.GenerativeModel("gemini-2.5-flash")
            response = model.generate_content(prompt)
            text = response.text.strip()
            if "```json" in text:
                text = text.split("```json")[1].split("```")[0].strip()
            elif "```" in text:
                text = text.split("```")[1].split("```")[0].strip()

            clips = json.loads(text)
            if isinstance(clips, list) and len(clips) > 0:
                print(f"-> Gemini selected {len(clips)} highlights successfully!")
                break
        except Exception as e:
            err_str = str(e)
            if "429" in err_str or "quota" in err_str.lower():
                wait_sec = 25 * (attempt + 1)
                print(f"Rate limited by Gemini (429). Retrying in {wait_sec} seconds (Attempt {attempt+1}/4)...")
                time.sleep(wait_sec)
            else:
                print(f"Gemini call error: {e}")
                time.sleep(5)

    if not clips:
        print("Fallback: Creating evenly distributed highlight segments across video.")
        step = max(10.0, (duration - 45.0) / max(1, num_clips - 1))
        clips = []
        for i in range(num_clips):
            start = round(min(duration - 40.0, i * step), 2)
            clips.append({
                "clip_number": i + 1,
                "start_seconds": start,
                "end_seconds": min(duration, start + 38.0),
                "title": f"{title[:40]} - Part {i+1} #shorts",
                "description": f"Highlight #{i+1} from {title}!\n\n#shorts #trending #viral",
                "tags": ["shorts", "viral", "trending", "clip"],
            })

    # Validate timestamps
    validated = []
    for idx, c in enumerate(clips[:num_clips], 1):
        s = float(c.get("start_seconds", (idx - 1) * 35))
        e = float(c.get("end_seconds", s + 40))
        if e - s > 58.0:
            e = s + 52.0
        if e > duration:
            e = duration
            if e - s < 15.0 and duration >= 30:
                s = max(0.0, e - 30.0)

        c["start_seconds"] = round(s, 2)
        c["end_seconds"] = round(e, 2)
        c["duration"] = round(e - s, 2)
        c["clip_number"] = idx
        validated.append(c)

    return validated


def format_srt_time(seconds: float) -> str:
    """Format seconds into HH:MM:SS,mmm for SRT."""
    td = timedelta(seconds=max(0.0, seconds))
    total_seconds = int(td.total_seconds())
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    secs = total_seconds % 60
    millis = int((seconds - int(seconds)) * 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def generate_subtitles_srt(
    transcript: list[dict],
    clip_start: float,
    clip_end: float,
    output_srt: str,
) -> bool:
    """Generate synchronized subtitles for the specific clip segment."""
    matching = []
    for item in transcript:
        item_start = item.get("start", 0)
        item_end = item_start + item.get("duration", 0)
        text = item.get("text", "").replace("\n", " ").strip()
        if item_end > clip_start and item_start < clip_end and text and text != "[संगीत]":
            rel_start = max(0.0, item_start - clip_start)
            rel_end = min(clip_end - clip_start, item_end - clip_start)
            if rel_end > rel_start:
                matching.append((rel_start, rel_end, text))

    if not matching:
        return False

    with open(output_srt, "w", encoding="utf-8") as f:
        for idx, (s, e, text) in enumerate(matching, 1):
            f.write(f"{idx}\n{format_srt_time(s)} --> {format_srt_time(e)}\n{text}\n\n")

    return True


def render_split_short_with_subtitles(
    main_video: str,
    gameplay_video: str,
    main_start: float,
    gameplay_start: float,
    duration: float,
    srt_file: str,
    output_path: str,
) -> str:
    """
    Render 1080x1920 (9:16) split screen:
    - Top half (1080x960): Real content + dialogue subtitles + original audio
    - Bottom half (1080x960): Random gameplay (video only, audio muted)
    """
    has_subs = srt_file and os.path.exists(srt_file) and os.path.getsize(srt_file) > 10

    if has_subs:
        srt_clean = srt_file.replace("\\", "/").replace(":", "\\:")
        filter_complex = (
            f"[0:v]scale=1080:960:force_original_aspect_ratio=increase,crop=1080:960[top_raw];"
            f"[top_raw]subtitles='{srt_clean}':force_style='Fontname=Nirmala UI,FontSize=22,Bold=1,PrimaryColour=&H0000FFFF,OutlineColour=&H00000000,BorderStyle=1,Outline=2,Shadow=1,Alignment=2,MarginV=30'[top];"
            f"[1:v]scale=1080:960:force_original_aspect_ratio=increase,crop=1080:960[bottom];"
            f"[top][bottom]vstack=inputs=2[v]"
        )
    else:
        filter_complex = (
            "[0:v]scale=1080:960:force_original_aspect_ratio=increase,crop=1080:960[top];"
            "[1:v]scale=1080:960:force_original_aspect_ratio=increase,crop=1080:960[bottom];"
            "[top][bottom]vstack=inputs=2[v]"
        )

    cmd = [
        "ffmpeg", "-y",
        "-ss", str(main_start),
        "-t", str(duration),
        "-i", main_video,
        "-ss", str(gameplay_start),
        "-t", str(duration),
        "-i", gameplay_video,
        "-filter_complex", filter_complex,
        "-map", "[v]",
        "-map", "0:a",  # Audio strictly from main video
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "20",
        "-c:a", "aac",
        "-b:a", "192k",
        output_path,
    ]

    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(f"FFmpeg failed to render '{output_path}':\n{res.stderr}")

    return output_path


def resolve_token_files(tokens_arg: list[str] = None, tokens_dir: str = "tokens") -> list[str]:
    """Find all target account token files (supports multi-account uploading)."""
    if tokens_arg:
        valid = [t for t in tokens_arg if os.path.exists(t)]
        if valid:
            return valid

    if os.path.exists(tokens_dir):
        found = [os.path.join(tokens_dir, f) for f in os.listdir(tokens_dir) if f.endswith(".json")]
        if found:
            return sorted(found)

    if os.path.exists("token.json"):
        return ["token.json"]

    return []


def generate_batch_shorts(
    main_input: str,
    gameplay_input: str = None,
    num_shorts: int = 6,
    gemini_key: str = DEFAULT_GEMINI_KEY,
    output_dir: str = "outputs",
    auto_upload: bool = True,
    privacy_status: str = "unlisted",
    token_files: list[str] = None,
):
    """Main pipeline: Gemini AI Highlight Selection -> Random Gameplay Cut -> 9:16 Split Render -> Upload."""
    os.makedirs(output_dir, exist_ok=True)

    # Normalize privacy status
    if privacy_status.lower() not in ["public", "private", "unlisted"]:
        privacy_status = "unlisted"

    print("=" * 65)
    print(f"[SPLIT-SCREEN PIPELINE] Generating {num_shorts} Shorts (Top: Main, Bottom: Gameplay)")
    print(f"Auto Upload: {auto_upload} | Privacy: {privacy_status}")
    print("=" * 65)

    # 1. Main Video & Transcript
    main_video = ensure_video(main_input, prefix="main")
    main_duration = get_video_duration(main_video)
    video_id = get_video_id(main_input)
    transcript = fetch_transcript(video_id) if video_id else []

    ydl_info_opts = {"quiet": True}
    if os.path.exists("cookies.txt") and os.path.getsize("cookies.txt") > 10:
        ydl_info_opts["cookiefile"] = "cookies.txt"

    with yt_dlp.YoutubeDL(ydl_info_opts) as ydl:
        try:
            info = ydl.extract_info(main_input, download=False)
            video_title = info.get("title", "Highlight Clip")
        except Exception:
            video_title = os.path.splitext(os.path.basename(main_video))[0]

    print(f"-> Main Video: {main_video} ({main_duration:.1f}s)")
    print(f"-> Video Title: {video_title}")

    # 2. Gameplay Video (Background)
    if not gameplay_input:
        gameplay_input = DEFAULT_GAMEPLAY_URL
    gameplay_video = ensure_video(gameplay_input, prefix="gameplay")
    gameplay_duration = get_video_duration(gameplay_video)
    print(f"-> Gameplay Background: {gameplay_video} ({gameplay_duration:.1f}s)")

    # 3. Gemini AI Multi-Clip Selection (Hook detection, titles, descriptions, tags)
    clips = select_multiple_clips_with_ai(
        title=video_title,
        duration=main_duration,
        transcript=transcript,
        num_clips=num_shorts,
        api_key=gemini_key,
    )

    print(f"\n[AI Selected {len(clips)} Highlights]:")
    for c in clips:
        print(f"  #{c['clip_number']}: [{c['start_seconds']}s -> {c['end_seconds']}s] ({c['duration']}s)")
        print(f"      Title: {c['title']}")

    # 4. Render each clip
    rendered_files = []
    for c in clips:
        idx = c["clip_number"]
        dur = c["duration"]
        main_start = c["start_seconds"]

        # Random slice for bottom gameplay
        max_gameplay_start = max(0.0, gameplay_duration - dur - 2.0)
        gameplay_start = round(random.uniform(0.0, max_gameplay_start), 2)

        # Generate subtitles if transcript available
        srt_file = os.path.join(output_dir, f"sub_{idx:02d}.srt")
        has_subs = generate_subtitles_srt(
            transcript=transcript,
            clip_start=main_start,
            clip_end=c["end_seconds"],
            output_srt=srt_file,
        )

        out_name = os.path.join(output_dir, f"short_{idx:02d}.mp4")
        print(f"\n[{idx}/{num_shorts}] Rendering Short #{idx}:")
        print(f"   AI Hook Cut        : {main_start:.1f}s -> {c['end_seconds']:.1f}s")
        print(f"   Gameplay Random Cut: {gameplay_start:.1f}s -> {gameplay_start + dur:.1f}s")
        print(f"   Subtitles          : {'Yes (Burned-in)' if has_subs else 'None'}")

        render_split_short_with_subtitles(
            main_video=main_video,
            gameplay_video=gameplay_video,
            main_start=main_start,
            gameplay_start=gameplay_start,
            duration=dur,
            srt_file=srt_file if has_subs else "",
            output_path=out_name,
        )

        file_size = os.path.getsize(out_name) / (1024 * 1024)
        print(f"   [OK] Rendered: {out_name} ({file_size:.2f} MB, 1080x1920)")
        c["file_path"] = out_name
        rendered_files.append(c)

    print("\n" + "=" * 65)
    print(f"[SUCCESS] ALL {len(rendered_files)} SHORTS GENERATED in '{output_dir}/'!")
    print("=" * 65)

    # 5. Upload using AI Metadata (Supports Multi-Account)
    if auto_upload:
        if not UPLOAD_AVAILABLE:
            raise RuntimeError("Upload module 'upload_short.py' could not be loaded. Please ensure google-api-python-client is installed.")

        tokens_to_use = resolve_token_files(tokens_arg=token_files)
        if not tokens_to_use:
            err_msg = (
                "\n" + "!" * 70 + "\n"
                "[AUTO-UPLOAD ERROR] '--upload' was specified, but NO valid token files were found!\n"
                "Searched in 'tokens/' directory and 'token.json'.\n\n"
                "If running in GitHub Actions, ensure you added your OAuth tokens to Repository Secrets:\n"
                "  1. Go to GitHub repo -> Settings -> Secrets and variables -> Actions\n"
                "  2. Add 'TOKEN_ACCOUNT1_JSON' (paste content of tokens/account1.json)\n"
                "  3. Add 'TOKEN_ACCOUNT2_JSON' (paste content of tokens/account2.json)\n"
                + "!" * 70 + "\n"
            )
            print(err_msg)
            raise RuntimeError("Auto-upload requested but no account token files found.")

        print(f"\n[UPLOADING] Publishing {len(rendered_files)} Shorts across {len(tokens_to_use)} Account(s)...")
        for t in tokens_to_use:
            print(f"   -> Target: {t}")

        uploaded_shorts = []
        upload_failures = []
        for c in rendered_files:
            for token_path in tokens_to_use:
                acc_name = os.path.splitext(os.path.basename(token_path))[0]
                # Match client secret per account if available
                secret_candidates = [
                    f"client_secret_{acc_name}.json",
                    f"client_{acc_name}.json",
                    "client_secret.json",
                ]
                acc_secret = next((s for s in secret_candidates if os.path.exists(s)), "client_secret.json")

                try:
                    print(f"\nUploading Part #{c['clip_number']} to [{acc_name}] (using secret: {acc_secret}): {c['title']}")
                    resp = upload_short(
                        video_file=c["file_path"],
                        title=c["title"],
                        description=c["description"],
                        tags=c["tags"],
                        privacy_status=privacy_status,
                        token_file=token_path,
                        client_secret_file=acc_secret,
                    )
                    uploaded_shorts.append({
                        "clip": c["clip_number"],
                        "account": acc_name,
                        "video_id": resp.get("id"),
                        "title": c["title"],
                        "privacy": resp.get("status", {}).get("privacyStatus", privacy_status),
                    })
                except Exception as e:
                    err_info = f"Short #{c['clip_number']} on [{acc_name}]: {e}"
                    print(f"[UPLOAD ERROR] {err_info}")
                    upload_failures.append(err_info)

        print("\n" + "=" * 65)
        print(f"UPLOAD SUMMARY: {len(uploaded_shorts)} succeeded, {len(upload_failures)} failed.")
        for u in uploaded_shorts:
            print(f"  [{u['account']}] Short #{u['clip']} ({u['privacy']}): https://youtube.com/shorts/{u['video_id']}")
        print("=" * 65)

        if len(uploaded_shorts) == 0 and len(rendered_files) > 0:
            raise RuntimeError(f"All uploads failed! Errors:\n" + "\n".join(upload_failures))
    else:
        print("\nTip: Videos are ready in outputs/. Pass '--upload' when ready to publish to YouTube.")

    return rendered_files


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="AI Split-Screen Shorts Pipeline")
    parser.add_argument("--url", required=True, help="Main video file path or YouTube URL")
    parser.add_argument("--gameplay", default=DEFAULT_GAMEPLAY_URL, help="Gameplay background file or YouTube URL")
    parser.add_argument("--count", type=int, default=6, help="Number of shorts to generate (default: 6)")
    parser.add_argument("--key", default=DEFAULT_GEMINI_KEY, help="Google Gemini API Key")
    parser.add_argument("--output-dir", default="outputs", help="Output directory for generated MP4s")
    parser.add_argument("--upload", action="store_true", help="Automatically upload generated shorts to YouTube")
    parser.add_argument("--privacy", default="unlisted", help="YouTube upload privacy: unlisted, private, public")
    parser.add_argument("--tokens", nargs="+", default=None, help="Specific token JSON files (e.g. tokens/account1.json tokens/account2.json)")

    args = parser.parse_args()
    auto_upload_flag = args.upload or os.environ.get("AUTO_UPLOAD", "").lower() in ("true", "1", "yes")
    generate_batch_shorts(
        main_input=args.url,
        gameplay_input=args.gameplay,
        num_shorts=args.count,
        gemini_key=args.key,
        output_dir=args.output_dir,
        auto_upload=auto_upload_flag,
        privacy_status=args.privacy,
        token_files=args.tokens,
    )
