"""yt-dlp download jobs. Each job runs in a background thread and reports
its progress into the in-memory JOBS dict, which the page polls."""
import os
import re
import threading
import time
from datetime import datetime, timezone

import yt_dlp
from yt_dlp.utils import download_range_func

from . import cookies
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


# Characters Windows doesn't allow in file names, plus control characters.
# Everything else is kept, so Khmer, Thai, Chinese, etc. titles stay readable
# (werkzeug's secure_filename would strip them down to nothing).
BAD_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
WINDOWS_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
                    *(f"LPT{i}" for i in range(1, 10))}
# Keeps the full path short enough for Windows even in a deep folder.
MAX_TITLE_LENGTH = 120


def title_to_filename(title: str | None) -> str:
    """A file name (without extension) made from a video title."""
    name = BAD_FILENAME_CHARS.sub(" ", title or "")
    name = re.sub(r"\s+", " ", name).strip()[:MAX_TITLE_LENGTH]
    # Windows drops trailing dots/spaces, which would change the name.
    name = name.rstrip(". ")
    if not name or name.upper() in WINDOWS_RESERVED:
        name = "audio"
    return name


# Two downloads of videos with the same title can finish at the same time.
RENAME_LOCK = threading.Lock()


def rename_to_title(download_dir: str, filename: str, title: str | None) -> str:
    """Rename a finished download to "<title>.<ext>" ("<title> (2).<ext>" if
    that's taken) and return the new file name."""
    ext = os.path.splitext(filename)[1]
    base = title_to_filename(title)
    with RENAME_LOCK:
        name, n = f"{base}{ext}", 2
        while os.path.exists(os.path.join(download_dir, name)):
            name, n = f"{base} ({n}){ext}", n + 1
        os.replace(os.path.join(download_dir, filename), os.path.join(download_dir, name))
    return name


# Base options applied to every download. YouTube clients and headers are
# left to yt-dlp's own defaults, which track YouTube's changes; forcing a
# client or user-agent here goes stale and causes 403s / bot checks.
# YouTube also needs a JavaScript runtime to solve its player challenges
# (scripts come from the yt-dlp-ejs package in yt-dlp[default]). yt-dlp only
# looks for Deno by default, so also allow Node and Bun if installed.
# If YouTube errors appear, first run:  pip install -U "yt-dlp[default]"
COMMON_OPTS = {
    "quiet": True,
    "noplaylist": True,
    # Keep the real file times (not the video's upload date) so
    # delete_old_files can tell how long a file has been sitting there.
    "updatetime": False,
    "js_runtimes": {"deno": {}, "node": {}, "bun": {}},
}
if FFMPEG_PATH:
    COMMON_OPTS["ffmpeg_location"] = FFMPEG_PATH
# Cookies aren't in COMMON_OPTS: they're looked up on every download (see
# cookies.ydl_options) so a cookies.txt uploaded on the page works right away.


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

        with cookies.ydl_options() as cookie_opts, yt_dlp.YoutubeDL({**ydl_opts, **cookie_opts}) as ydl:
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

        # yt-dlp saved it under the job id (unique while downloading); name
        # it after the video now that it's done.
        produced_file = rename_to_title(download_dir, produced_file, title)

        JOBS[job_id]["status"] = "done"
        JOBS[job_id]["filename"] = produced_file
        JOBS[job_id]["title"] = title
        JOBS[job_id]["format"] = expected_ext
        JOBS[job_id]["finished_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        JOBS[job_id]["done_at"] = time.time()

    except Exception as e:
        msg = str(e)
        low = msg.lower()
        cookie_hint = cookies.explain_error(msg)
        if cookie_hint:
            msg += f" ({cookie_hint})"
        elif "login" in low or "private" in low or "rate-limit" in low:
            msg += " (This link may be private, age-restricted, or require login -- those can't be downloaded.)"
        elif "403" in msg or "forbidden" in low:
            msg += " (Try running 'pip install -U yt-dlp' to update to the latest version, then retry.)"
        elif "ffmpeg" in low:
            msg += " (Try running 'pip install -U imageio-ffmpeg' and restarting the app.)"
        JOBS[job_id]["status"] = "error"
        JOBS[job_id]["error"] = msg


def delete_old_files(download_dir: str, max_age_seconds: float):
    """Files are only kept until the user saves them from the page. Delete
    finished files older than max_age_seconds (their jobs become "expired"),
    and any other file in the folder that old which no running job is using:
    zips, and leftovers from earlier runs (JOBS is in memory, so those can
    never be downloaded again). max_age_seconds=0 clears all of those."""
    now = time.time()
    keep = set()
    for job_id, job in list(JOBS.items()):
        if job["status"] == "done":
            if now - job.get("done_at", now) >= max_age_seconds:
                try:
                    os.remove(os.path.join(download_dir, job["filename"]))
                except OSError:
                    pass
                job["status"] = "expired"
                job["error"] = "This file was deleted from the server. Download it again."
            else:
                keep.add(job["filename"])
        elif job["status"] in ("queued", "downloading", "processing"):
            keep.add(job_id)  # its files start with the job id while in progress

    try:
        names = os.listdir(download_dir)
    except OSError:
        return
    for name in names:
        if name in keep or name.split(".")[0] in keep:
            continue
        path = os.path.join(download_dir, name)
        try:
            if os.path.isfile(path) and now - os.path.getmtime(path) >= max_age_seconds:
                os.remove(path)
        except OSError:
            pass  # e.g. a zip still being sent (Windows locks it); next time


def start_cleanup(download_dir: str, max_age_seconds: float, every_seconds: float = 60):
    """Empty the folder of leftovers now, then delete old files every minute."""
    delete_old_files(download_dir, 0)

    def loop():
        while True:
            time.sleep(every_seconds)
            delete_old_files(download_dir, max_age_seconds)

    threading.Thread(target=loop, daemon=True).start()


def expand_playlist(url: str, limit: int | None = None, start: int = 1) -> dict:
    """Turn a playlist link into {"title", "total", "videos": [{"url", "title"}],
    "next_start", "has_more"}. start is the 1-based playlist position to begin
    at and limit, if set, keeps only N videos from there, so a long playlist can
    be fetched in batches by passing the previous "next_start". "total" is how
    many the whole playlist has (None if unknown). A plain video link comes back
    as a one-item list. Raises on failure."""
    ydl_opts = dict(COMMON_OPTS)
    ydl_opts.update({
        "noplaylist": False,
        # Only list the entries; don't resolve every video (that's slow).
        "extract_flat": "in_playlist",
        "skip_download": True,
    })
    if start > 1:
        ydl_opts["playliststart"] = start
    if limit:
        # playlistend is a position in the playlist, not a count.
        ydl_opts["playlistend"] = start + limit - 1
    with cookies.ydl_options() as cookie_opts, yt_dlp.YoutubeDL({**ydl_opts, **cookie_opts}) as ydl:
        info = ydl.extract_info(url, download=False)

    entries = info.get("entries")
    if entries is None:
        return {"title": info.get("title"), "total": 1, "videos": [{"url": url, "title": info.get("title")}],
                "next_start": 2, "has_more": False}

    videos = []
    seen = 0  # playlist positions read, including skipped ones
    for entry in entries:
        seen += 1
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

    total = info.get("playlist_count")
    next_start = start + seen
    if total:
        has_more = next_start <= total
    else:
        # Unknown size: a full batch means there may be more.
        has_more = bool(limit) and seen >= limit
    return {"title": info.get("title"), "total": total, "videos": videos,
            "next_start": next_start, "has_more": has_more}
