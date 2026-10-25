"""HTTP endpoints used by the page in templates/index.html."""
import os
import time
import uuid
import zipfile

from flask import Blueprint, current_app, jsonify, render_template, request, send_file, send_from_directory
from werkzeug.utils import secure_filename

from . import huggingface
from .downloader import JOBS, expand_playlist, is_valid_url, start_download

bp = Blueprint("main", __name__)


# Always return JSON on errors instead of Flask/Werkzeug's default HTML
# error pages -- this is what was causing "Unexpected token '<'" in the
# browser console when something went wrong.
@bp.app_errorhandler(404)
def handle_404(e):
    return jsonify({"error": "Not found. Make sure you're visiting http://127.0.0.1:5000"}), 404


@bp.app_errorhandler(500)
def handle_500(e):
    return jsonify({"error": f"Server error: {e}"}), 500


@bp.route("/")
def index():
    return render_template("index.html")


@bp.route("/generate", methods=["POST"])
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
        start_download(job_id, url, fmt, limit_minutes, current_app.config["DOWNLOAD_DIR"])
        return jsonify({"job_id": job_id})
    except Exception as e:
        # Catch-all so the browser always gets JSON, never an HTML crash page
        return jsonify({"error": f"Unexpected server error: {e}"}), 500


@bp.route("/expand", methods=["POST"])
def expand():
    """Turn a playlist link into the list of video links inside it, so the
    page can queue each one as its own download."""
    data = request.get_json(silent=True) or {}
    url = (data.get("url") or "").strip()
    if not is_valid_url(url):
        return jsonify({"error": "Please paste a valid link."}), 400
    try:
        return jsonify(expand_playlist(url))
    except Exception as e:
        return jsonify({"error": f"Couldn't read this playlist: {e}"}), 400


@bp.route("/status/<job_id>")
def status(job_id):
    job = JOBS.get(job_id)
    if not job:
        return jsonify({"error": "Unknown job id"}), 404
    return jsonify(job)


@bp.route("/download/<job_id>")
def download(job_id):
    job = JOBS.get(job_id)
    if not job or job.get("status") != "done":
        return jsonify({"error": "File not ready"}), 400
    # Deliver a readable filename rather than the internal job id.
    title = secure_filename(job.get("title") or "video") or "video"
    extension = os.path.splitext(job["filename"])[1] or ".mp4"
    return send_from_directory(
        current_app.config["DOWNLOAD_DIR"],
        job["filename"],
        as_attachment=True,
        download_name=f"{title}{extension}",
    )


@bp.route("/push_hf", methods=["POST"])
def push_hf():
    """Upload finished MP3 jobs to a Hugging Face dataset repo as a new
    Parquet file, e.g.
    {"repo_id": "me/my-audio", "ids": ["abc123"], "token": "hf_...", "private": true}."""
    data = request.get_json(silent=True) or {}
    repo_id = (data.get("repo_id") or "").strip()
    if not huggingface.is_valid_repo_id(repo_id):
        return jsonify({"error": "Dataset name must look like your-username/dataset-name."}), 400
    jobs = [JOBS[i] for i in data.get("ids") or []
            if JOBS.get(i, {}).get("status") == "done" and JOBS[i].get("format") == "mp3"]
    if not jobs:
        return jsonify({"error": "No finished MP3s to push."}), 400

    try:
        parquet_path = huggingface.push_to_hub(
            repo_id, jobs, current_app.config["DOWNLOAD_DIR"],
            token=(data.get("token") or "").strip() or None,
            private=bool(data.get("private")),
        )
    except huggingface.HfPushError as e:
        return jsonify({"error": str(e)}), 400

    return jsonify({"repo_url": f"https://huggingface.co/datasets/{repo_id}",
                    "count": len(jobs), "parquet": parquet_path})


@bp.route("/download_zip")
def download_zip():
    """Bundle several finished jobs into one .zip, e.g.
    /download_zip?ids=abc123,def456. Lets the user grab a whole batch in one
    click instead of the browser blocking a dozen separate downloads."""
    download_dir = current_app.config["DOWNLOAD_DIR"]
    ids = [i for i in (request.args.get("ids") or "").split(",") if i]
    jobs = [JOBS[i] for i in ids if JOBS.get(i, {}).get("status") == "done"]
    if not jobs:
        return jsonify({"error": "None of those files are ready"}), 400

    # Zips are temporary bundles of files already on disk. A zip can't be
    # deleted while it's still streaming (Windows locks it), so instead sweep
    # away ones from earlier requests that are old enough to be finished.
    for f in os.listdir(download_dir):
        path = os.path.join(download_dir, f)
        if f.startswith("batch-") and f.endswith(".zip") and time.time() - os.path.getmtime(path) > 3600:
            try:
                os.remove(path)
            except OSError:
                pass

    zip_path = os.path.join(download_dir, f"batch-{uuid.uuid4().hex[:12]}.zip")
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
            zf.write(os.path.join(download_dir, job["filename"]), arcname=name)

    return send_file(zip_path, as_attachment=True, download_name="downloads.zip")
