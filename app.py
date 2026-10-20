import os
import re
import zipfile
import uuid
import threading
import time
from flask import Flask, request, jsonify, send_from_directory, send_file, render_template
from werkzeug.utils import secure_filename

import yt_dlp
from yt_dlp.utils import download_range_func

# imageio-ffmpeg bundles its own portable ffmpeg binary and downloads it on
# first use via pip -- no manual ffmpeg install / PATH editing needed.
try:
    import imageio_ffmpeg
    FFMPEG_PATH = imageio_ffmpeg.get_ffmpeg_exe()
except Exception:
    # Falls back to relying on a system ffmpeg already on PATH, if any.
    FFMPEG_PATH = None

app = Flask(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DOWNLOAD_DIR = os.path.join(BASE_DIR, "downloads")
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# In-memory job tracker: job_id -> {"status": ..., "filename": ..., "error": ...,
# "progress": 0-100, "title": ...}
JOBS = {}

# When many links are submitted at once, only this many download at the same
# time; the rest wait in "queued" until a slot frees up. Keeps the machine and
# the network (and YouTube's rate limits) from being overwhelmed.
MAX_PARALLEL_DOWNLOADS = 3
DOWNLOAD_SLOTS = threading.BoundedSemaphore(MAX_PARALLEL_DOWNLOADS)

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


def run_download(job_id: str, url: str, fmt: str, limit_minutes: float | None):
    """fmt is 'mp3' or 'mp4'. limit_minutes, if set, downloads only the
    first N minutes of the source video instead of the whole thing."""
    with DOWNLOAD_SLOTS:
        _run_download(job_id, url, fmt, limit_minutes)


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


def _run_download(job_id: str, url: str, fmt: str, limit_minutes: float | None):
    JOBS[job_id]["status"] = "downloading"
    unique_name = f"{job_id}"
    outtmpl = os.path.join(DOWNLOAD_DIR, f"{unique_name}.%(ext)s")

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

        # Find the actual produced file (extension is set by postprocessor/merge)
        expected_ext = "mp3" if fmt == "mp3" else "mp4"
        produced_file = f"{unique_name}.{expected_ext}"
        produced_path = os.path.join(DOWNLOAD_DIR, produced_file)

        if not os.path.exists(produced_path):
            # fallback: search for any file starting with unique_name
            candidates = [f for f in os.listdir(DOWNLOAD_DIR) if f.startswith(unique_name)]
            if candidates:
                produced_file = candidates[0]
            else:
                raise FileNotFoundError("Converted file was not found after download.")

        JOBS[job_id]["status"] = "done"
        JOBS[job_id]["filename"] = produced_file
        JOBS[job_id]["title"] = title
        JOBS[job_id]["format"] = expected_ext

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


# Always return JSON on errors instead of Flask/Werkzeug's default HTML
# error pages -- this is what was causing "Unexpected token '<'" in the
# browser console when something went wrong.
@app.errorhandler(404)
def handle_404(e):
    return jsonify({"error": "Not found. Make sure you're visiting http://127.0.0.1:5000"}), 404


@app.errorhandler(500)
def handle_500(e):
    return jsonify({"error": f"Server error: {e}"}), 500


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/generate", methods=["POST"])
def generate():
    try:
        data = request.get_json(silent=True) or {}
        url = (data.get("url") or "").strip()
        fmt = (data.get("format") or "mp3").strip().lower()
        limit_minutes_raw = data.get("limit_minutes")

        if fmt not in ("mp3", "mp4"):
            return jsonify({"error": "Format must be mp3 or mp4"}), 400

        if not is_valid_url(url):
            return jsonify({"error": "Please paste a valid video link (starting with http:// or https://)."}), 400

        limit_minutes = None
        if limit_minutes_raw not in (None, "", "0"):
            try:
                limit_minutes = float(limit_minutes_raw)
                if limit_minutes <= 0:
                    limit_minutes = None
            except (TypeError, ValueError):
                return jsonify({"error": "Time limit must be a number of minutes."}), 400

        job_id = uuid.uuid4().hex[:12]
        JOBS[job_id] = {"status": "queued", "filename": None, "error": None,
                        "progress": 0, "title": None, "url": url}

        thread = threading.Thread(
            target=run_download, args=(job_id, url, fmt, limit_minutes), daemon=True
        )
        thread.start()

        return jsonify({"job_id": job_id})
    except Exception as e:
        # Catch-all so the browser always gets JSON, never an HTML crash page
        return jsonify({"error": f"Unexpected server error: {e}"}), 500


@app.route("/expand", methods=["POST"])
def expand():
    """Turn a playlist link into the list of video links inside it, so the
    page can queue each one as its own download. A plain video link comes
    back as a one-item list."""
    data = request.get_json(silent=True) or {}
    url = (data.get("url") or "").strip()
    if not is_valid_url(url):
        return jsonify({"error": "Please paste a valid link."}), 400

    ydl_opts = dict(COMMON_OPTS)
    ydl_opts.update({
        "noplaylist": False,
        # Only list the entries; don't resolve every video (that's slow).
        "extract_flat": "in_playlist",
        "skip_download": True,
    })
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception as e:
        return jsonify({"error": f"Couldn't read this playlist: {e}"}), 400

    entries = info.get("entries")
    if entries is None:
        return jsonify({"title": info.get("title"), "videos": [{"url": url, "title": info.get("title")}]})

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

    return jsonify({"title": info.get("title"), "videos": videos})


@app.route("/status/<job_id>")
def status(job_id):
    job = JOBS.get(job_id)
    if not job:
        return jsonify({"error": "Unknown job id"}), 404
    return jsonify(job)


@app.route("/download/<job_id>")
def download(job_id):
    job = JOBS.get(job_id)
    if not job or job.get("status") != "done":
        return jsonify({"error": "File not ready"}), 400
    # Deliver a readable filename rather than the internal job id.
    title = secure_filename(job.get("title") or "video") or "video"
    extension = os.path.splitext(job["filename"])[1] or ".mp4"
    return send_from_directory(
        DOWNLOAD_DIR,
        job["filename"],
        as_attachment=True,
        download_name=f"{title}{extension}",
    )


@app.route("/download_zip")
def download_zip():
    """Bundle several finished jobs into one .zip, e.g.
    /download_zip?ids=abc123,def456. Lets the user grab a whole batch in one
    click instead of the browser blocking a dozen separate downloads."""
    ids = [i for i in (request.args.get("ids") or "").split(",") if i]
    jobs = [JOBS[i] for i in ids if JOBS.get(i, {}).get("status") == "done"]
    if not jobs:
        return jsonify({"error": "None of those files are ready"}), 400

    # Zips are temporary bundles of files already on disk. A zip can't be
    # deleted while it's still streaming (Windows locks it), so instead sweep
    # away ones from earlier requests that are old enough to be finished.
    for f in os.listdir(DOWNLOAD_DIR):
        path = os.path.join(DOWNLOAD_DIR, f)
        if f.startswith("batch-") and f.endswith(".zip") and time.time() - os.path.getmtime(path) > 3600:
            try:
                os.remove(path)
            except OSError:
                pass

    zip_path = os.path.join(DOWNLOAD_DIR, f"batch-{uuid.uuid4().hex[:12]}.zip")
    used_names = set()
    # ZIP_STORED: mp3/mp4 are already compressed, so deflating just burns CPU.
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_STORED) as zf:
        for job in jobs:
            title = secure_filename(job.get("title") or "video") or "video"
            extension = os.path.splitext(job["filename"])[1]
            name, n = f"{title}{extension}", 2
            while name in used_names:
                name, n = f"{title} ({n}){extension}", n + 1
            used_names.add(name)
            zf.write(os.path.join(DOWNLOAD_DIR, job["filename"]), arcname=name)

    return send_file(zip_path, as_attachment=True, download_name="downloads.zip")


if __name__ == "__main__":
    print("Starting server at http://127.0.0.1:5000")
    app.run(host="127.0.0.1", port=5000, debug=False)
