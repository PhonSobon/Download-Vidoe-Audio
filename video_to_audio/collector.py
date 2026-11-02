"""Collect TikTok post links without downloading media; keep results in SQLite."""
import csv
import io
import itertools
import json
import math
import re
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

import yt_dlp

from . import cookies

PROFILE_PATH = re.compile(r"/@([\w.-]+)/?")
POST_PATH = re.compile(r"/@([\w.-]+)/(video|photo)/(\d+)/?")
TIKTOK_HOSTS = {"tiktok.com", "www.tiktok.com", "m.tiktok.com"}
COLLECTION_SLOT = threading.BoundedSemaphore(1)
CSV_FIELDS = ["url", "post_id", "creator", "title", "upload_date", "duration_seconds", "topic"]
MAX_IMPORT_BYTES = 2 * 1024 * 1024
MAX_IMPORT_LINKS = 10000


class CollectionError(ValueError):
    pass


def profile_url(value):
    try:
        parts = urlsplit(value.strip())
        match = PROFILE_PATH.fullmatch(parts.path)
        if parts.scheme in {"http", "https"} and parts.hostname in TIKTOK_HOSTS and match:
            return f"https://www.tiktok.com/@{match[1]}"
    except (ValueError, AttributeError):
        pass
    raise CollectionError("Enter TikTok creator profile links, such as https://www.tiktok.com/@creator.")


def collection_options(data):
    if not isinstance(data, dict):
        raise CollectionError("Collection settings must be a JSON object.")
    profiles = data.get("profiles", "")
    if isinstance(profiles, str):
        profiles = profiles.splitlines()
    if not isinstance(profiles, list) or not all(isinstance(p, str) for p in profiles):
        raise CollectionError("Enter one creator profile link per line.")
    profiles = list(dict.fromkeys(profile_url(p) for p in profiles if p.strip()))
    if not 1 <= len(profiles) <= 10:
        raise CollectionError("Enter between 1 and 10 creator profiles.")
    try:
        raw_limit = data.get("max_posts", 50)
        if isinstance(raw_limit, bool) or str(raw_limit) != str(int(raw_limit)):
            raise ValueError
        limit = int(raw_limit)
        if not 1 <= limit <= 500:
            raise ValueError
    except (ValueError, TypeError, OverflowError):
        raise CollectionError("Posts to inspect must be a whole number between 1 and 500 per creator.")
    options = {"profiles": profiles, "max_posts": limit}
    for field in ("date_from", "date_to"):
        value = data.get(field) or ""
        try:
            options[field] = datetime.strptime(value, "%Y-%m-%d").strftime("%Y%m%d") if value else ""
        except (TypeError, ValueError):
            raise CollectionError("Dates must use YYYY-MM-DD.")
    if options["date_from"] and options["date_to"] and options["date_from"] > options["date_to"]:
        raise CollectionError("The start date must be before or equal to the end date.")
    keywords, topic = data.get("keywords") or "", data.get("topic") or ""
    if not isinstance(keywords, str) or not isinstance(topic, str) or len(keywords) > 500 or len(topic) > 100:
        raise CollectionError("Keep keywords under 500 characters and the topic under 100 characters.")
    options.update(keywords=[k.strip().casefold() for k in keywords.split(",") if k.strip()], topic=topic.strip())
    return options


class CollectionStore:
    def __init__(self, path):
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS collections (
                    id TEXT PRIMARY KEY, created_at TEXT NOT NULL, status TEXT NOT NULL,
                    options TEXT NOT NULL, scanned INTEGER NOT NULL DEFAULT 0,
                    profiles_done INTEGER NOT NULL DEFAULT 0, errors TEXT NOT NULL DEFAULT '[]'
                );
                CREATE TABLE IF NOT EXISTS collected_posts (
                    collection_id TEXT NOT NULL, post_id TEXT NOT NULL, url TEXT NOT NULL,
                    creator TEXT, title TEXT, upload_date TEXT, duration_seconds REAL, topic TEXT,
                    PRIMARY KEY (collection_id, post_id)
                );
            """)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def recover_interrupted(self):
        with self.connect() as db:
            db.execute("UPDATE collections SET status='interrupted' WHERE status IN ('queued', 'running')")

    def create(self, options):
        collection_id = uuid.uuid4().hex
        with self.connect() as db:
            db.execute("INSERT INTO collections (id, created_at, status, options) VALUES (?, ?, 'queued', ?)",
                       (collection_id, datetime.now(timezone.utc).isoformat(timespec="seconds"), json.dumps(options)))
        return collection_id

    def update(self, collection_id, **values):
        if not values or not set(values) <= {"status", "scanned", "profiles_done", "errors"}:
            raise ValueError("Invalid collection update")
        if "errors" in values:
            values["errors"] = json.dumps(values["errors"], ensure_ascii=False)
        with self.connect() as db:
            db.execute(f"UPDATE collections SET {', '.join(key + '=?' for key in values)} WHERE id=?",
                       (*values.values(), collection_id))

    def get(self, collection_id):
        with self.connect() as db:
            row = db.execute("SELECT * FROM collections WHERE id=?", (collection_id,)).fetchone()
            if not row:
                return None
            result = dict(row)
            result["options"] = json.loads(result["options"])
            result["errors"] = json.loads(result["errors"])
            result["count"] = db.execute("SELECT COUNT(*) FROM collected_posts WHERE collection_id=?",
                                         (collection_id,)).fetchone()[0]
            return result

    def recent(self):
        with self.connect() as db:
            ids = [row[0] for row in db.execute("SELECT id FROM collections ORDER BY created_at DESC, rowid DESC LIMIT 20")]
        return [self.get(collection_id) for collection_id in ids]

    def posts(self, collection_id, limit=None):
        sql = "SELECT " + ", ".join(CSV_FIELDS) + " FROM collected_posts WHERE collection_id=? ORDER BY rowid"
        params = (collection_id,)
        if limit is not None:
            sql += " LIMIT ?"
            params += (limit,)
        with self.connect() as db:
            return [dict(row) for row in db.execute(sql, params)]

    def add(self, collection_id, post):
        with self.connect() as db:
            db.execute("INSERT OR IGNORE INTO collected_posts (collection_id, " + ", ".join(CSV_FIELDS) +
                       ") VALUES (" + ", ".join("?" for _ in range(len(CSV_FIELDS) + 1)) + ")",
                       (collection_id, *(post.get(field) for field in CSV_FIELDS)))


def post_metadata(entry, profile, topic):
    if not isinstance(entry, dict):
        return None
    post_id = str(entry.get("id") or "")
    if not post_id.isdigit():
        return None
    creator = entry.get("uploader") or profile.rsplit("@", 1)[-1]
    if not isinstance(creator, str) or not re.fullmatch(r"[\w.-]+", creator):
        creator = profile.rsplit("@", 1)[-1]
    kind = "video"
    raw_url = entry.get("webpage_url") or entry.get("url") or ""
    if isinstance(raw_url, str) and "/photo/" in raw_url:
        kind = "photo"
    date = entry.get("upload_date") or ""
    if not isinstance(date, str) or not re.fullmatch(r"\d{8}", date):
        try:
            date = datetime.fromtimestamp(float(entry["timestamp"]), timezone.utc).strftime("%Y%m%d")
        except (KeyError, ValueError, TypeError, OverflowError, OSError):
            date = ""
    try:
        duration = float(entry.get("duration"))
        if not math.isfinite(duration) or duration <= 0:
            duration = None
    except (ValueError, TypeError):
        duration = None
    return {"url": f"https://www.tiktok.com/@{creator}/{kind}/{post_id}", "post_id": post_id,
            "creator": creator, "title": str(entry.get("title") or entry.get("description") or ""),
            "upload_date": date, "duration_seconds": duration, "topic": topic}


def matches_filters(post, entry, options):
    if options["date_from"] or options["date_to"]:
        date = post["upload_date"]
        if not date or options["date_from"] and date < options["date_from"] or options["date_to"] and date > options["date_to"]:
            return False
    text = (post["title"] + " " + str(entry.get("description") or "")).casefold()
    return not options["keywords"] or any(word in text for word in options["keywords"])


def run_collection(store, collection_id):
    options = store.get(collection_id)["options"]
    scanned, errors = 0, []
    if store.get(collection_id)["status"] == "cancelled":
        return
    store.update(collection_id, status="running")
    for index, profile in enumerate(options["profiles"]):
        if store.get(collection_id)["status"] == "cancelled":
            return
        try:
            ydl_opts = {"quiet": True, "skip_download": True, "extract_flat": "in_playlist",
                        "lazy_playlist": True, "noplaylist": False, "playlistend": options["max_posts"],
                        "socket_timeout": 20, "retries": 1, "extractor_retries": 1,
                        "sleep_interval_requests": 1}
            with cookies.ydl_options() as cookie_opts, yt_dlp.YoutubeDL({**ydl_opts, **cookie_opts}) as ydl:
                info = ydl.extract_info(profile, download=False)
                if not info or info.get("entries") is None:
                    raise CollectionError("TikTok did not return a list of posts for this profile.")
                for entry in itertools.islice(info["entries"], options["max_posts"]):
                    if store.get(collection_id)["status"] == "cancelled":
                        return
                    scanned += 1
                    post = post_metadata(entry, profile, options["topic"])
                    if post and matches_filters(post, entry, options):
                        store.add(collection_id, post)
                    store.update(collection_id, scanned=scanned)
        except Exception as exc:
            errors.append({"profile": profile, "error": str(exc)})
        store.update(collection_id, profiles_done=index + 1, errors=errors)
    if store.get(collection_id)["status"] != "cancelled":
        store.update(collection_id, status="partial" if errors and store.get(collection_id)["count"] else
                     "error" if errors else "done")


def start_collection(store, options):
    if not COLLECTION_SLOT.acquire(blocking=False):
        raise CollectionError("A collection is already running. Wait for it to finish or cancel it first.")
    collection_id = None
    try:
        collection_id = store.create(options)

        def worker():
            try:
                run_collection(store, collection_id)
            except Exception as exc:
                store.update(collection_id, status="error", errors=[{"profile": "", "error": str(exc)}])
            finally:
                COLLECTION_SLOT.release()

        threading.Thread(target=worker, daemon=True, name="tiktok-collector").start()
    except Exception:
        COLLECTION_SLOT.release()
        raise
    return collection_id


def export_csv(posts):
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=CSV_FIELDS)
    writer.writeheader()
    for post in posts:
        # Captions may start with spreadsheet formulas. Export them as text.
        row = {key: ("'" + value if isinstance(value, str) and value.startswith(("=", "+", "-", "@", "\t", "\r"))
                     else value) for key, value in post.items() if key in CSV_FIELDS}
        writer.writerow(row)
    return "\ufeff" + output.getvalue()


def import_links(data, filename):
    if len(data) > MAX_IMPORT_BYTES:
        raise CollectionError("Choose a CSV or TXT file smaller than 2 MB.")
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise CollectionError("Save the file as UTF-8 CSV or TXT.")
    suffix = Path(filename).suffix.lower()
    try:
        if suffix == ".csv":
            reader = csv.DictReader(io.StringIO(text))
            if not reader.fieldnames or "url" not in reader.fieldnames:
                raise CollectionError("The CSV needs a column named url.")
            candidates = (row.get("url", "") for row in reader)
        elif suffix == ".txt":
            candidates = text.splitlines()
        else:
            raise CollectionError("Choose a .csv or .txt file.")
        urls, seen, skipped = [], set(), 0
        for index, value in enumerate(candidates):
            if index >= MAX_IMPORT_LINKS:
                raise CollectionError("Import at most 10,000 links at a time.")
            url = (value or "").strip()
            if not url:
                continue
            try:
                parts = urlsplit(url)
                match = POST_PATH.fullmatch(parts.path) if parts.hostname in TIKTOK_HOSTS else None
                if parts.scheme not in {"http", "https"} or not parts.hostname or any(c.isspace() for c in url):
                    raise ValueError
                if parts.hostname in TIKTOK_HOSTS and not match:
                    raise ValueError
                key = "tiktok:" + match[3] if match else url
            except ValueError:
                skipped += 1
                continue
            if key in seen:
                skipped += 1
                continue
            seen.add(key)
            urls.append(url)
    except csv.Error:
        raise CollectionError("The CSV could not be read. Export it again with a url column.")
    if not urls:
        raise CollectionError("The file has no valid post links. Use individual post URLs, one per row.")
    return {"urls": urls, "skipped": skipped}
