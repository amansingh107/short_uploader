import os
import sys
import json
import re
import subprocess
import argparse
from datetime import timedelta
import yt_dlp
from youtube_transcript_api import YouTubeTranscriptApi
import google.generativeai as genai
from upload_short import upload_short

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
DEFAULT_API_KEY = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY", "")


def get_video_id(url_or_id: str) -> str:
    """Extract YouTube video ID from URL or return ID directly."""
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
    raise ValueError(f"Could not extract video ID from '{url_or_id}'")


def download_video(url: str, output_path: str = "source_video.mp4") -> dict:
    """Download video using yt-dlp and return metadata."""
    print(f"\n[1/5] Checking/Downloading source video from: {url}")
    ydl_opts = {
        "format": "bestvideo[height<=1080]+bestaudio/best[height<=1080]/best",
        "merge_output_format": "mp4",
        "outtmpl": output_path,
        "overwrites": True,
        "quiet": False,
        "no_warnings": True,
    }
    if os.path.exists("cookies.txt") and os.path.getsize("cookies.txt") > 10:
        ydl_opts["cookiefile"] = "cookies.txt"
    
    if os.path.exists(output_path) and os.path.getsize(output_path) > 1024 * 1024:
        print(f"-> Source video '{output_path}' already exists locally ({os.path.getsize(output_path)/(1024*1024):.1f} MB). Skipping re-download.")
        with yt_dlp.YoutubeDL({"quiet": True}) as ydl:
            info = ydl.extract_info(url, download=False)
            return info

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        return info


def fetch_transcript(video_id: str) -> list[dict]:
    """Fetch transcript in Hindi, English or auto-generated."""
    print(f"\n[2/5] Fetching transcript for video ID: {video_id}")
    try:
        ytt = YouTubeTranscriptApi()
        transcript_list = ytt.list(video_id)
        
        target_transcript = None
        # Priority: Hindi or English, manually created or auto
        for code in ["hi", "en", "en-US", "en-GB"]:
            try:
                target_transcript = transcript_list.find_transcript([code])
                if target_transcript:
                    break
            except Exception:
                continue

        if not target_transcript:
            for t in transcript_list:
                target_transcript = t
                break

        if target_transcript:
            print(f"-> Found transcript in language: {target_transcript.language} ({target_transcript.language_code})")
            raw_data = target_transcript.fetch()
            return raw_data.to_raw_data()
    except Exception as e:
        print(f"Warning: Could not fetch transcript via API ({e}). Proceeding without transcript.")
    return []


def select_clip_with_ai(
    title: str,
    duration: float,
    transcript: list[dict],
    api_key: str,
) -> dict:
    """Use Gemini 2.5 Flash to pick the most viral segment (30-55s) and generate metadata."""
    print("\n[3/5] Asking Gemini to find the optimal viral hook...")
    genai.configure(api_key=api_key)
    model = genai.GenerativeModel("gemini-2.5-flash")

    # Format transcript snippets
    transcript_text = ""
    if transcript:
        lines = []
        for item in transcript:
            start = item.get("start", 0)
            dur = item.get("duration", 0)
            text = item.get("text", "").replace("\n", " ").strip()
            if text and text != "[संगीत]":
                lines.append(f"[{start:.1f}s - {start+dur:.1f}s]: {text}")
        transcript_text = "\n".join(lines)

    prompt = f"""
You are an expert viral YouTube Shorts producer and media editor.
Task: Select the single best continuous highlight segment between 35 and 55 seconds from this video.

Video Details:
- Title: {title}
- Total Duration: {duration:.1f} seconds

Transcript with Timestamps:
{transcript_text if transcript_text else "No detailed transcript available. Analyze the title and select the most climactic 45-second portion (e.g. between 60s and 105s)."}

Requirements:
1. Target clip duration MUST be between 35 and 55 seconds (YouTube Shorts hard limit is 60s).
2. The start timestamp must hook the viewer immediately (strong statement, dramatic dialogue, or action).
3. The end timestamp must conclude smoothly or leave a compelling cliffhanger.
4. Generate a click-worthy viral Title ending with #shorts (max 70 characters).
5. Generate an engaging Description with relevant hashtags.
6. Generate 5-8 relevant SEO Tags.

You MUST reply ONLY with valid JSON in this exact structure:
{{
  "start_seconds": <float>,
  "end_seconds": <float>,
  "title": "<viral title #shorts>",
  "description": "<engaging description with hashtags>",
  "tags": ["tag1", "tag2", "tag3"]
}}
"""

    response = model.generate_content(prompt)
    response_text = response.text.strip()
    
    # Extract JSON if enclosed in markdown backticks
    if "```json" in response_text:
        response_text = response_text.split("```json")[1].split("```")[0].strip()
    elif "```" in response_text:
        response_text = response_text.split("```")[1].split("```")[0].strip()

    data = json.loads(response_text)
    
    # Enforce constraints
    start_sec = float(data.get("start_seconds", 0.0))
    end_sec = float(data.get("end_seconds", min(duration, start_sec + 50)))
    if end_sec - start_sec > 58:
        end_sec = start_sec + 55
    if end_sec > duration:
        end_sec = duration
        if end_sec - start_sec < 20 and duration >= 40:
            start_sec = max(0.0, end_sec - 45)

    data["start_seconds"] = round(start_sec, 2)
    data["end_seconds"] = round(end_sec, 2)
    data["duration"] = round(end_sec - start_sec, 2)

    print(f"-> Selected Clip: {data['start_seconds']}s to {data['end_seconds']}s (Duration: {data['duration']}s)")
    print(f"-> Title: {data['title']}")
    return data


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
    output_srt: str = "subtitles.srt",
) -> bool:
    """Generate a synchronized SRT subtitle file for the selected clip."""
    matching_entries = []
    for item in transcript:
        item_start = item.get("start", 0)
        item_end = item_start + item.get("duration", 0)
        text = item.get("text", "").replace("\n", " ").strip()
        
        # Check overlap
        if item_end > clip_start and item_start < clip_end:
            if text and text != "[संगीत]":
                rel_start = max(0.0, item_start - clip_start)
                rel_end = min(clip_end - clip_start, item_end - clip_start)
                if rel_end > rel_start:
                    matching_entries.append((rel_start, rel_end, text))

    if not matching_entries:
        print("-> No subtitle entries found in selected range.")
        return False

    with open(output_srt, "w", encoding="utf-8") as f:
        for idx, (s, e, text) in enumerate(matching_entries, start=1):
            f.write(f"{idx}\n")
            f.write(f"{format_srt_time(s)} --> {format_srt_time(e)}\n")
            f.write(f"{text}\n\n")

    print(f"-> Created subtitle file '{output_srt}' with {len(matching_entries)} subtitle cues.")
    return True


def render_short_video(
    input_video: str,
    start_seconds: float,
    duration: float,
    srt_file: str,
    output_video: str = "final_rendered_short.mp4",
) -> str:
    """Render 9:16 vertical video with blurred background and burned-in subtitles using FFmpeg."""
    print(f"\n[4/5] Rendering 9:16 Vertical Short with FFmpeg...")
    has_subtitles = srt_file and os.path.exists(srt_file) and os.path.getsize(srt_file) > 10

    # FFmpeg filter complex:
    # 1. Background: scale to fill 1080x1920, crop, boxblur
    # 2. Foreground: scale to width 1080 (keep aspect ratio)
    # 3. Overlay foreground over blurred background
    # 4. Burn in bold yellow subtitles with black outline using Nirmala UI / Arial
    if has_subtitles:
        # Escape path for FFmpeg subtitles filter on Windows
        srt_clean = srt_file.replace("\\", "/").replace(":", "\\:")
        filter_complex = (
            f"[0:v]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,boxblur=20:2[bg];"
            f"[0:v]scale=1080:-1[fg];"
            f"[bg][fg]overlay=(W-w)/2:(H-h)/2[composed];"
            f"[composed]subtitles='{srt_clean}':force_style='Fontname=Nirmala UI,FontSize=24,Bold=1,PrimaryColour=&H0000FFFF,OutlineColour=&H00000000,BorderStyle=1,Outline=3,Shadow=1,Alignment=2,MarginV=180'[v]"
        )
    else:
        filter_complex = (
            f"[0:v]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,boxblur=20:2[bg];"
            f"[0:v]scale=1080:-1[fg];"
            f"[bg][fg]overlay=(W-w)/2:(H-h)/2[v]"
        )

    cmd = [
        "ffmpeg", "-y",
        "-ss", str(start_seconds),
        "-t", str(duration),
        "-i", input_video,
        "-filter_complex", filter_complex,
        "-map", "[v]",
        "-map", "0:a",
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "19",
        "-c:a", "aac",
        "-b:a", "192k",
        output_video,
    ]

    print("Running FFmpeg render...")
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        print(f"FFmpeg error:\n{res.stderr}")
        raise RuntimeError("FFmpeg rendering failed.")

    size_mb = os.path.getsize(output_video) / (1024 * 1024)
    print(f"-> Render successful: '{output_video}' ({size_mb:.2f} MB, 1080x1920 vertical)")
    return output_video


def process_youtube_short(
    youtube_url: str,
    gemini_api_key: str = DEFAULT_API_KEY,
    privacy_status: str = "unlisted",
    auto_upload: bool = True,
):
    """End-to-end pipeline: Ingest -> AI Select -> Render 9:16 + Subs -> Upload."""
    video_id = get_video_id(youtube_url)
    source_video = "source_video.mp4"

    # Step 1: Download Video
    info = download_video(youtube_url, output_path=source_video)
    title = info.get("title", "YouTube Video")
    duration = float(info.get("duration", 120))

    # Step 2: Fetch Transcript
    transcript = fetch_transcript(video_id)

    # Step 3: AI Selection
    clip_meta = select_clip_with_ai(
        title=title,
        duration=duration,
        transcript=transcript,
        api_key=gemini_api_key,
    )

    # Step 4: Subtitles & Rendering
    srt_file = "clip_subtitles.srt"
    has_subs = generate_subtitles_srt(
        transcript=transcript,
        clip_start=clip_meta["start_seconds"],
        clip_end=clip_meta["end_seconds"],
        output_srt=srt_file,
    )

    rendered_short = "final_rendered_short.mp4"
    render_short_video(
        input_video=source_video,
        start_seconds=clip_meta["start_seconds"],
        duration=clip_meta["duration"],
        srt_file=srt_file if has_subs else "",
        output_video=rendered_short,
    )

    # Step 5: Upload to YouTube Shorts
    if auto_upload:
        print("\n[5/5] Uploading generated short to YouTube...")
        response = upload_short(
            video_file=rendered_short,
            title=clip_meta["title"],
            description=clip_meta["description"],
            tags=clip_meta["tags"],
            privacy_status=privacy_status,
        )
        return response
    else:
        print(f"\n[5/5] Skipped upload. Rendered video saved at '{rendered_short}'.")
        return clip_meta


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Automated AI YouTube Shorts Pipeline")
    parser.add_argument("--url", default="https://www.youtube.com/watch?v=pmWF2QtyvCI", help="Source YouTube URL")
    parser.add_argument("--key", default=DEFAULT_API_KEY, help="Google Gemini API Key")
    parser.add_argument("--privacy", default="unlisted", help="YouTube Privacy: unlisted, private, public")
    parser.add_argument("--no-upload", action="store_true", help="Render video only without uploading")

    args = parser.parse_args()
    process_youtube_short(
        youtube_url=args.url,
        gemini_api_key=args.key,
        privacy_status=args.privacy,
        auto_upload=not args.no_upload,
    )
