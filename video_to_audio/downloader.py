"""yt-dlp download jobs. Each job runs in a background thread and reports
its progress into the in-memory JOBS dict, which the page polls."""
import csv
import io
import os
import re
import threading
import time
from datetime import datetime, timezone
from queue import Queue
from urllib.parse import parse_qs, urlsplit, urlunsplit

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
DOWNLOAD_QUEUE = Queue()
DOWNLOAD_WORKERS_LOCK = threading.Lock()
DOWNLOAD_WORKERS_STARTED = False
GENERIC_URL_RE = re.compile(r"^https?://[^\s]+\.[^\s]{2,}")


def is_valid_url(url: str) -> bool:
    return bool(url) and bool(GENERIC_URL_RE.match(url.strip()))


# A Facebook page or one of its tabs, e.g. facebook.com/www.mptc.gov.kh/reels/.
# yt-dlp only downloads single Facebook videos/reels; it can't list a page.
FACEBOOK_PAGE_PATH = re.compile(r"/(?!reel/|watch/?$|share/|story\.php|video\.php)[^/]+/?"
                                r"(?:reels|videos)?/?")


def check_facebook_page(url: str):
    """Raise a clear error for a Facebook page link instead of yt-dlp's
    "Unsupported URL"."""
    parts = urlsplit(url)
    host = parts.hostname or ""
    if (host == "facebook.com" or host.endswith(".facebook.com")) and not parts.query \
            and FACEBOOK_PAGE_PATH.fullmatch(parts.path):
        raise ValueError("This is a Facebook page, not a single video. yt-dlp can't list all of a "
                         "page's reels. Open each reel (facebook.com/reel/<id>) and paste its link, or "
                         "put the reel links in a .txt file (one per line) and use Import links.")


def prepare_download_url(url: str, fmt: str) -> str:
    """Let yt-dlp extract a TikTok photo post's sound through its video URL."""
    check_facebook_page(url)
    parts = urlsplit(url)
    if parts.hostname not in {"tiktok.com", "www.tiktok.com", "m.tiktok.com"}:
        return url
    if not re.fullmatch(r"/@[\w.-]+/photo/\d+/?", parts.path):
        return url
    if fmt != "mp3":
        raise ValueError("This TikTok link is a photo/slideshow post. Choose MP3 audio "
                         "to download its sound. Exporting the photos as an MP4 slideshow "
                         "isn't supported.")
    return urlunsplit((parts.scheme, "www.tiktok.com", parts.path.replace("/photo/", "/video/", 1),
                       parts.query, parts.fragment))


def audio_format(url: str) -> str:
    """yt-dlp format selector for MP3 downloads.

    TikTok lists the post's background music as a separate "audio" format. For a
    video post that is often only the song (and can be shorter than the video),
    so take the audio from the video itself. Photo posts only have that music.
    """
    host = urlsplit(url).hostname or ""
    if (host == "tiktok.com" or host.endswith(".tiktok.com")) and "/photo/" not in url:
        return "bestaudio[format_id!=audio]/best[format_id!=audio]/best"
    return "bestaudio/best"


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


# Timed captions (subtitles) saved next to the audio/video when asked for.
# TikTok serves them as WebVTT or SRT (SRT is converted to VTT); "all" keeps
# every language the post has, e.g. the original plus TikTok's translations.
# YouTube's auto-generated captions are left out: they come in 100+
# machine-translated languages.
CAPTION_EXTS = (".vtt", ".srt", ".json")
CAPTION_OPTS = {
    "writesubtitles": True,
    "subtitleslangs": ["all", "-live_chat"],
    "subtitlesformat": "vtt/srt/best",
}
CAPTION_CONVERTER = {"key": "FFmpegSubtitlesConvertor", "format": "vtt", "when": "before_dl"}


def video_key(url: str) -> str:
    """The same video whatever form its link takes, so it isn't downloaded
    twice: watch?v=ID, youtu.be/ID, shorts/ID (with or without &t=, &list=...),
    TikTok video/photo posts and Facebook reels. Other links are compared
    without their #fragment. Mirrors videoId() in static/js/app.js."""
    try:
        parts = urlsplit(url.strip())
        host = re.sub(r"^(www|m|music|web)\.", "", (parts.hostname or "").lower())
    except ValueError:
        return url.strip()
    query = parse_qs(parts.query)
    if host == "youtu.be":
        return "yt:" + parts.path.lstrip("/").split("/")[0]
    if host == "youtube.com":
        if query.get("v"):
            return "yt:" + query["v"][0]
        match = re.match(r"/(shorts|live|embed)/([^/]+)", parts.path)
        if match:
            return "yt:" + match[2]
    if host == "tiktok.com":
        match = re.fullmatch(r"/@[^/]+/(?:video|photo)/(\d+)/?", parts.path)
        if match:
            return "tiktok:" + match[1]
    if host == "facebook.com" or host.endswith(".facebook.com"):
        match = re.fullmatch(r"/reel/(\d+)/?", parts.path)
        if match:
            return "fb:" + match[1]
        if parts.path.rstrip("/") == "/watch" and query.get("v"):
            return "fb:" + query["v"][0]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, parts.query, ""))


# Held while checking for a duplicate and registering the new job, so two
# requests for the same video at the same moment can't both start one.
DUPLICATE_LOCK = threading.Lock()
ACTIVE_STATUSES = ("queued", "downloading", "processing")


def find_duplicate(url: str, fmt: str, limit_minutes: float | None, download_dir: str,
                   captions: bool = False) -> str | None:
    """The id of a job already downloading or holding this same video in the
    same format, clip length and captions setting, or None. A finished job
    only counts while its file is still on the server."""
    key = video_key(url)
    for job_id, job in list(JOBS.items()):
        if (job.get("key") != key or job.get("fmt") != fmt or job.get("limit_minutes") != limit_minutes
                or bool(job.get("want_captions")) != bool(captions)):
            continue
        if job["status"] in ACTIVE_STATUSES:
            return job_id
        if job["status"] == "done" and all(
                os.path.exists(os.path.join(download_dir, name)) for name in job_files(job)):
            return job_id
    return None


def start_download(job_id: str, url: str, fmt: str, limit_minutes: float | None, download_dir: str,
                   captions: bool = False) -> str:
    """Queue a download and return its job id. If the same video is already
    queued, downloading or downloaded (same format, clip and captions), no new
    download starts and that job's id is returned instead."""
    with DUPLICATE_LOCK:
        existing = find_duplicate(url, fmt, limit_minutes, download_dir, captions)
        if existing:
            return existing
        JOBS[job_id] = {"status": "queued", "filename": None, "error": None,
                        "progress": 0, "title": None, "url": url, "want_captions": captions,
                        "key": video_key(url), "fmt": fmt, "limit_minutes": limit_minutes}
    # A large CSV uses a fixed number of workers rather than a thread per URL.
    global DOWNLOAD_WORKERS_STARTED
    with DOWNLOAD_WORKERS_LOCK:
        if not DOWNLOAD_WORKERS_STARTED:
            for index in range(max(1, Config.MAX_PARALLEL_DOWNLOADS)):
                threading.Thread(target=_download_worker, args=(DOWNLOAD_QUEUE,), daemon=True,
                                 name=f"audio-download-{index}").start()
            DOWNLOAD_WORKERS_STARTED = True
    DOWNLOAD_QUEUE.put((job_id, url, fmt, limit_minutes, download_dir, captions))
    return job_id


def _download_worker(queue):
    while True:
        args = queue.get()
        try:
            if args is None:
                return
            run_download(*args)
        except Exception as exc:
            # A failed job must not stop this worker or strand the rest of a batch.
            if args[0] in JOBS:
                JOBS[args[0]].update(status="error", error=str(exc))
        finally:
            queue.task_done()


def run_download(job_id: str, url: str, fmt: str, limit_minutes: float | None, download_dir: str,
                 captions: bool = False):
    """fmt is 'mp3' or 'mp4'. limit_minutes, if set, downloads only the
    first N minutes of the source video instead of the whole thing.
    captions also saves the video's timed captions as .vtt files."""
    with DOWNLOAD_SLOTS:
        _run_download(job_id, url, fmt, limit_minutes, download_dir, captions)


# Facebook titles reels as "9.9K views · 340 reactions | <caption> | <page>".
FB_STATS_PREFIX = re.compile(
    r"^[\d.,]+\s*[KMB]?\s+views?(\s*·\s*[\d.,]+\s*[KMB]?\s+reactions?)?\s*\|\s*", re.I)


def clean_title(info: dict) -> str | None:
    """The video's title, minus the view/reaction counts and page name that
    Facebook packs into it, so file names and dataset rows get the caption."""
    title = info.get("title")
    if not title or not (info.get("extractor_key") or info.get("ie_key") or "").startswith("Facebook"):
        return title
    cleaned = FB_STATS_PREFIX.sub("", title)
    uploader = info.get("uploader") or info.get("channel")
    if uploader and cleaned.endswith(f" | {uploader}"):
        cleaned = cleaned[:-len(uploader) - 3]
    return cleaned.strip() or title


def video_card(info: dict) -> dict:
    """What the page shows next to a video: thumbnail, duration (seconds) and
    channel. Works for full video info and for flat playlist entries."""
    thumbnail = info.get("thumbnail")
    if not thumbnail and info.get("thumbnails"):
        thumbnail = info["thumbnails"][-1].get("url")
    # Flat YouTube entries list huge or odd-sized thumbnails; this one is a
    # small 16:9 image that always exists.
    if info.get("id") and (info.get("ie_key") or info.get("extractor_key")) == "Youtube":
        thumbnail = f"https://i.ytimg.com/vi/{info['id']}/mqdefault.jpg"
    return {"thumbnail": thumbnail, "duration": info.get("duration"),
            "channel": info.get("channel") or info.get("uploader")}


def _make_progress_hook(job_id: str):
    def hook(d):
        job = JOBS[job_id]
        info = d.get("info_dict") or {}
        if info.get("title") and not job.get("title"):
            job["title"] = clean_title(info)
        if info and not job.get("card"):
            job["card"] = video_card(info)
        if d.get("status") == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            if total:
                job["progress"] = round(d.get("downloaded_bytes", 0) * 100 / total, 1)
        elif d.get("status") == "finished":
            job["progress"] = 100
            job["status"] = "processing"
    return hook


def _run_download(job_id: str, url: str, fmt: str, limit_minutes: float | None, download_dir: str,
                  captions: bool = False):
    JOBS[job_id]["status"] = "downloading"
    unique_name = f"{job_id}"
    outtmpl = os.path.join(download_dir, f"{unique_name}.%(ext)s")

    try:
        extract_url = prepare_download_url(url, fmt)
        ydl_opts = dict(COMMON_OPTS)
        ydl_opts["outtmpl"] = outtmpl
        ydl_opts["progress_hooks"] = [_make_progress_hook(job_id)]

        if fmt == "mp3":
            ydl_opts.update({
                "format": audio_format(url),
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

        def fetch(opts):
            with cookies.ydl_options() as cookie_opts, yt_dlp.YoutubeDL({**opts, **cookie_opts}) as ydl:
                return ydl.extract_info(extract_url, download=True)

        if captions:
            with_captions = {**ydl_opts, **CAPTION_OPTS,
                             "postprocessors": [CAPTION_CONVERTER, *ydl_opts.get("postprocessors", [])]}
            try:
                info = fetch(with_captions)
            except yt_dlp.utils.DownloadError as exc:
                # A caption that fails to download shouldn't cost the audio.
                if "subtitle" not in str(exc).lower():
                    raise
                JOBS[job_id]["caption_error"] = "Captions couldn't be downloaded for this video."
                info = fetch(ydl_opts)
        else:
            info = fetch(ydl_opts)
        title = clean_title(info) or "download"
        JOBS[job_id]["card"] = video_card(info)

        # Where the audio came from, kept so a Hugging Face push can credit
        # the original video/page (see huggingface.hf_row).
        JOBS[job_id]["source"] = {
            "id": info.get("id"),
            "url": url if extract_url != url else info.get("webpage_url") or url,
            "platform": info.get("extractor_key") or info.get("extractor"),
            "channel": info.get("channel") or info.get("uploader"),
            "channel_url": info.get("channel_url") or info.get("uploader_url"),
            "upload_date": info.get("upload_date"),
            "duration": info.get("duration"),
            "clip_minutes": limit_minutes,
        }
        # The post's own text (on TikTok, the caption the creator wrote).
        JOBS[job_id]["description"] = info.get("description") or info.get("title")

        # Find the actual produced file (extension is set by postprocessor/merge)
        expected_ext = "mp3" if fmt == "mp3" else "mp4"
        produced_file = f"{unique_name}.{expected_ext}"
        produced_path = os.path.join(download_dir, produced_file)

        if not os.path.exists(produced_path):
            # fallback: search for any file starting with unique_name
            candidates = [f for f in os.listdir(download_dir)
                          if f.startswith(unique_name) and not f.endswith(CAPTION_EXTS)]
            if candidates:
                produced_file = candidates[0]
            else:
                raise FileNotFoundError("Converted file was not found after download.")

        # yt-dlp saved it under the job id (unique while downloading); name
        # it after the video now that it's done.
        produced_file = rename_to_title(download_dir, produced_file, title)
        JOBS[job_id]["captions"] = _rename_captions(download_dir, unique_name, produced_file)
        if captions and not JOBS[job_id]["captions"] and not JOBS[job_id].get("caption_error"):
            JOBS[job_id]["caption_error"] = "This video has no captions."

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
        elif "audio codec" in low:
            # yt-dlp's way of saying the video has no audio track at all.
            msg = "This video has no sound, so there's no audio to save as MP3. Choose MP4 to get the video."
        elif "ffmpeg" in low:
            msg += " (Try running 'pip install -U imageio-ffmpeg' and restarting the app.)"
        JOBS[job_id]["status"] = "error"
        JOBS[job_id]["error"] = msg


def _rename_captions(download_dir: str, unique_name: str, produced_file: str) -> list[dict]:
    """Give "<job id>.<lang>.vtt" files the media file's name, e.g.
    "Title.khm-KH.vtt" next to "Title.mp3". Returns [{"lang", "filename"}]."""
    base = os.path.splitext(produced_file)[0]
    found = []
    for name in sorted(os.listdir(download_dir)):
        if not (name.startswith(unique_name + ".") and name.endswith(CAPTION_EXTS)):
            continue
        new_name = base + name[len(unique_name):]
        with RENAME_LOCK:
            os.replace(os.path.join(download_dir, name), os.path.join(download_dir, new_name))
        found.append({"lang": name[len(unique_name) + 1:].rsplit(".", 1)[0], "filename": new_name})
    return found


def job_files(job: dict) -> list[str]:
    """Every file a finished job produced: the media file and its captions."""
    return [job["filename"], *(c["filename"] for c in job.get("captions") or [])]


METADATA_FIELDS = ["file", "caption_files", "title", "description", "url", "channel",
                   "upload_date", "duration_seconds"]


def metadata_csv(jobs: list[dict]) -> str:
    """A metadata.csv for a zip: one row per file with the post's caption text
    and the names of its timed caption (.vtt) files."""
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=METADATA_FIELDS)
    writer.writeheader()
    for job in jobs:
        src = job.get("source") or {}
        row = {"file": job["filename"],
               "caption_files": " | ".join(c["filename"] for c in job.get("captions") or []),
               "title": job.get("title"), "description": job.get("description"),
               "url": src.get("url") or job.get("url"), "channel": src.get("channel"),
               "upload_date": src.get("upload_date"), "duration_seconds": src.get("duration")}
        # Captions may start with spreadsheet formulas. Export them as text.
        writer.writerow({k: "'" + v if isinstance(v, str) and v.startswith(("=", "+", "-", "@", "\t", "\r"))
                         else v for k, v in row.items()})
    return "\ufeff" + output.getvalue()


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
                for name in job_files(job):
                    try:
                        os.remove(os.path.join(download_dir, name))
                    except OSError:
                        pass
                job["status"] = "expired"
                job["error"] = "This file was deleted from the server. Download it again."
            else:
                keep.update(job_files(job))
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


# youtube.com/@name, /channel/ID, /c/name, /user/name, optionally with a tab.
YOUTUBE_CHANNEL_PATH = re.compile(r"/(@[^/]+|(?:channel|c|user)/[^/]+)(?:/(videos|shorts|streams|featured))?/?")


def channel_tab_url(url: str) -> str:
    """A bare YouTube channel link (or its Home tab) lists the channel's tabs,
    not its videos, so read its Videos tab instead. Other links are unchanged."""
    parts = urlsplit(url)
    host = (parts.hostname or "").removeprefix("www.").removeprefix("m.")
    match = YOUTUBE_CHANNEL_PATH.fullmatch(parts.path) if host == "youtube.com" else None
    if not match:
        return url
    tab = match[2] if match[2] and match[2] != "featured" else "videos"
    return f"https://www.youtube.com/{match[1]}/{tab}"


def expand_playlist(url: str, limit: int | None = None, start: int = 1) -> dict:
    """Turn a playlist link into {"title", "channel", "total", "videos": [{"url",
    "title", "index", "thumbnail", "duration", "channel"}],
    "next_start", "has_more"}. start is the 1-based playlist position to begin
    at and limit, if set, keeps only N videos from there, so a long playlist can
    be fetched in batches by passing the previous "next_start". "total" is how
    many the whole playlist has (None if unknown). A plain video link comes back
    as a one-item list. Raises on failure."""
    check_facebook_page(url)
    url = channel_tab_url(url)
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

    # The YouTube channel that owns the playlist (or posted the video).
    channel = info.get("channel") or info.get("uploader")
    entries = info.get("entries")
    if entries is None:
        return {"title": info.get("title"), "channel": channel, "total": 1,
                "videos": [{"url": url, "title": clean_title(info), "index": 1, **video_card(info)}],
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
            # index: position in the playlist, shown next to the video like YouTube does.
            videos.append({"url": video_url, "title": title, "index": start + seen - 1,
                           **video_card(entry)})

    total = info.get("playlist_count")
    next_start = start + seen
    if total:
        has_more = next_start <= total
    else:
        # Unknown size: a full batch means there may be more.
        has_more = bool(limit) and seen >= limit
    return {"title": info.get("title"), "channel": channel, "total": total, "videos": videos,
            "next_start": next_start, "has_more": has_more}
