"""yt-dlp download jobs. Each job runs in a background thread and reports
its progress into the in-memory JOBS dict, which the page polls."""
import os
import re
import threading
from datetime import datetime, timezone

import yt_dlp
from yt_dlp.utils import download_range_func

from .config import Config

# imageio-ffmpeg bundles its own portable ffmpeg binary and downloads it on
# first use via pip -- no manual ffmpeg install / PATH editing needed.
try:
    import imageio_ffmpeg
    FFMPEG_PATH = imageio_ffmpeg.get_ffmpeg_exe()
except Exception:
    # Falls back to relying on a system ffmpeg already on PATH, if any.
    FFMPEG_PATH = None

# In-memory job tracker: job_id -> {"status": ..., "filename": ..., "error": ...,
# "progress": 0-100, "title": ...}
JOBS = {}

DOWNLOAD_SLOTS = threading.BoundedSemaphore(Config.MAX_PARALLEL_DOWNLOADS)

# Generic "is this a real URL" check. We don't restrict to one site anymore --
# yt-dlp itself supports YouTube, Facebook, Instagram, TikTok, X/Twitter,
# Vimeo, Reddit, SoundCloud, and hundreds of other sites. If a pasted link
# isn't from a supported site, yt-dlp will raise an error and the job will
# show that error to the user instead of silently failing here.
GENERIC_URL_RE = re.compile(r"^https?://[^\s]+\.[^\s]{2,}")


def is_valid_url(url: str) -> bool:
    return bool(url) and bool(GENERIC_URL_RE.match(url.strip()))


# Base options applied to every download. The extractor_args/player_client
# and user-agent below are a known workaround for the "HTTP Error 403:
# Forbidden" that YouTube periodically throws -- it happens when YouTube's
# web player signature check rejects the default client yt-dlp presents.
# Falling back to the android/ios client skips that check.
# NOTE: this only helps if yt-dlp itself is up to date. If you still get
# 403 errors after this, run:  pip install -U yt-dlp
COMMON_OPTS = {
    "quiet": True,
    "noplaylist": True,
    "extractor_args": {
        "youtube": {
            "player_client": ["android", "web"],
        }
    },
    "http_headers": {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    },
}
if FFMPEG_PATH:
    COMMON_OPTS["ffmpeg_location"] = FFMPEG_PATH


def start_download(job_id: str, url: str, fmt: str, limit_minutes: float | None, download_dir: str):
    JOBS[job_id] = {"status": "queued", "filename": None, "error": None,
                    "progress": 0, "title": None, "url": url}
    thread = threading.Thread(
        target=run_download, args=(job_id, url, fmt, limit_minutes, download_dir), daemon=True
    )
    thread.start()


def run_download(job_id: str, url: str, fmt: str, limit_minutes: float | None, download_dir: str):
    """fmt is 'mp3' or 'mp4'. limit_minutes, if set, downloads only the
    first N minutes of the source video instead of the whole thing."""
    with DOWNLOAD_SLOTS:
        _run_download(job_id, url, fmt, limit_minutes, download_dir)


def _make_progress_hook(job_id: str):
    def hook(d):
        job = JOBS[job_id]
        info = d.get("info_dict") or {}
        if info.get("title") and not job.get("title"):
            job["title"] = info["title"]
        if d.get("status") == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            if total:
                job["progress"] = round(d.get("downloaded_bytes", 0) * 100 / total, 1)
        elif d.get("status") == "finished":
            job["progress"] = 100
            job["status"] = "processing"
    return hook


def _run_download(job_id: str, url: str, fmt: str, limit_minutes: float | None, download_dir: str):
    JOBS[job_id]["status"] = "downloading"
    unique_name = f"{job_id}"
    outtmpl = os.path.join(download_dir, f"{unique_name}.%(ext)s")

    try:
        ydl_opts = dict(COMMON_OPTS)
        ydl_opts["outtmpl"] = outtmpl
        ydl_opts["progress_hooks"] = [_make_progress_hook(job_id)]

        if fmt == "mp3":
            ydl_opts.update({
                "format": "bestaudio/best",
                "postprocessors": [
                    {
                        "key": "FFmpegExtractAudio",
                        "preferredcodec": "mp3",
                        "preferredquality": "192",
                    }
                ],
            })
        else:  # mp4
            ydl_opts.update({
                "format": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
                "merge_output_format": "mp4",
            })

        if limit_minutes and limit_minutes > 0:
            end_seconds = int(limit_minutes * 60)
            # Only downloads/keeps the 0:00-end_seconds slice instead of
            # pulling the full video then trimming -- much faster on long videos.
            ydl_opts["download_ranges"] = download_range_func(None, [(0, end_seconds)])
            ydl_opts["force_keyframes_at_cuts"] = True

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            title = info.get("title", "download")

        # Where the audio came from, kept so a Hugging Face push can credit
        # the original video/page (see huggingface.hf_row).
        JOBS[job_id]["source"] = {
            "id": info.get("id"),
            "url": info.get("webpage_url") or url,
            "platform": info.get("extractor_key") or info.get("extractor"),
            "channel": info.get("channel") or info.get("uploader"),
            "channel_url": info.get("channel_url") or info.get("uploader_url"),
            "upload_date": info.get("upload_date"),
            "duration": info.get("duration"),
            "clip_minutes": limit_minutes,
        }

        # Find the actual produced file (extension is set by postprocessor/merge)
        expected_ext = "mp3" if fmt == "mp3" else "mp4"
        produced_file = f"{unique_name}.{expected_ext}"
        produced_path = os.path.join(download_dir, produced_file)

        if not os.path.exists(produced_path):
            # fallback: search for any file starting with unique_name
            candidates = [f for f in os.listdir(download_dir) if f.startswith(unique_name)]
            if candidates:
                produced_file = candidates[0]
            else:
                raise FileNotFoundError("Converted file was not found after download.")

        JOBS[job_id]["status"] = "done"
        JOBS[job_id]["filename"] = produced_file
        JOBS[job_id]["title"] = title
        JOBS[job_id]["format"] = expected_ext
        JOBS[job_id]["finished_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")

    except Exception as e:
        msg = str(e)
        low = msg.lower()
        if "login" in low or "private" in low or "rate-limit" in low:
            msg += " (This link may be private, age-restricted, or require login -- those can't be downloaded.)"
        elif "403" in msg or "forbidden" in low:
            msg += " (Try running 'pip install -U yt-dlp' to update to the latest version, then retry.)"
        elif "ffmpeg" in low:
            msg += " (Try running 'pip install -U imageio-ffmpeg' and restarting the app.)"
        JOBS[job_id]["status"] = "error"
        JOBS[job_id]["error"] = msg


def expand_playlist(url: str) -> dict:
    """Turn a playlist link into {"title", "videos": [{"url", "title"}]}.
    A plain video link comes back as a one-item list. Raises on failure."""
    ydl_opts = dict(COMMON_OPTS)
    ydl_opts.update({
        "noplaylist": False,
        # Only list the entries; don't resolve every video (that's slow).
        "extract_flat": "in_playlist",
        "skip_download": True,
    })
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)

    entries = info.get("entries")
    if entries is None:
        return {"title": info.get("title"), "videos": [{"url": url, "title": info.get("title")}]}

    videos = []
    for entry in entries:
        if not entry:
            continue
        title = entry.get("title")
        # Deleted/private videos still appear in playlists but can't be downloaded.
        if title in ("[Deleted video]", "[Private video]"):
            continue
        video_url = entry.get("url") or entry.get("webpage_url")
        if not video_url and entry.get("id"):
            video_url = f"https://www.youtube.com/watch?v={entry['id']}"
        if video_url:
            videos.append({"url": video_url, "title": title})

    return {"title": info.get("title"), "videos": videos}
