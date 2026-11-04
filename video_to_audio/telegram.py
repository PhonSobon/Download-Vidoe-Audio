"""Collect voice messages from a public Telegram channel or group, no login.

Telegram's post widget page (t.me/<name>/<id>?embed=1) works without an
account for public channels *and* public groups, and a voice message on it
has a direct link to its .ogg (Opus) file. A scan reads posts one by one,
newest first, and keeps the voice messages it finds; each one can then be
downloaded as a normal job in downloader.JOBS, so the page's queue, zip and
Hugging Face push work for them like for any other download.

Private groups and channels, and anything that needs you to be a member,
can't be read this way.
"""
from __future__ import annotations

import csv
import html
import os
import re
import threading
import time
import urllib.error
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from urllib.parse import urlsplit

from .config import Config
from .downloader import JOBS, rename_to_title

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130 Safari/537.36"
# Posts fetched at the same time while scanning (~25 posts a second). Each
# request mostly waits on the network; _get backs off if Telegram rate limits.
SCAN_WORKERS = 24
MAX_SCAN_POSTS = 20000
MAX_VOICES = 2000
# Telegram user names: 5-32 letters, digits and underscores.
NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{3,31}$")
TELEGRAM_HOSTS = {"t.me", "telegram.me", "telegram.dog", "www.t.me", "www.telegram.me"}
# t.me paths that aren't a channel or group.
RESERVED_NAMES = {"joinchat", "addstickers", "addemoji", "share", "proxy", "socks", "login", "s", "c"}


class TelegramError(ValueError):
    pass


def parse_link(text: str) -> tuple[str, int | None]:
    """"https://t.me/name", "t.me/s/name", "@name" or a post link
    "t.me/name/123" -> ("name", 123 or None)."""
    text = (text or "").strip()
    if text.startswith("@"):
        text = "https://t.me/" + text[1:]
    if not re.match(r"^https?://", text, re.I):
        text = "https://" + text
    parts = urlsplit(text)
    if (parts.hostname or "").lower() not in TELEGRAM_HOSTS:
        raise TelegramError("Paste a Telegram link like https://t.me/channel_name.")
    segments = [s for s in parts.path.split("/") if s]
    if segments[:1] == ["s"]:
        segments = segments[1:]
    if segments[:1] in (["c"], ["joinchat"]) or (segments and segments[0].startswith("+")):
        raise TelegramError("This is a private group or channel link. Only public ones (t.me/<name>) "
                            "can be read without logging in.")
    if not segments or segments[0].lower() in RESERVED_NAMES or not NAME_RE.match(segments[0]):
        raise TelegramError("Paste a public channel or group link like https://t.me/channel_name.")
    post_id = None
    if len(segments) > 1:
        if not segments[1].isdigit():
            raise TelegramError("A message link should end in its number, like t.me/channel_name/123.")
        post_id = int(segments[1])
    return segments[0], post_id


def _get(url: str, retries: int = 3) -> bytes:
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=20) as res:
                return res.read()
        except urllib.error.HTTPError as exc:
            if exc.code not in (429, 500, 502, 503, 504) or attempt == retries - 1:
                raise
        except (urllib.error.URLError, TimeoutError):
            if attempt == retries - 1:
                raise
        time.sleep(1.5 * (attempt + 1))  # rate limited or a network blip
    raise AssertionError("unreachable")


def post_url(name: str, post_id: int) -> str:
    return f"https://t.me/{name}/{post_id}"


def fetch_post_html(name: str, post_id: int) -> str:
    return _get(f"{post_url(name, post_id)}?embed=1&mode=tme").decode("utf-8", "replace")


def _text(fragment: str) -> str:
    fragment = re.sub(r"<br\s*/?>", "\n", fragment)
    return html.unescape(re.sub(r"<[^>]+>", "", fragment)).strip()


def _seconds(duration: str) -> int | None:
    try:
        total = 0
        for part in duration.strip().split(":"):
            total = total * 60 + int(part)
        return total
    except ValueError:
        return None


def parse_post(page: str, name: str, post_id: int) -> dict | None:
    """What a post's widget page says about it. None if the post doesn't
    exist (deleted, or a number past the newest post)."""
    if 'class="tgme_widget_message_error"' in page or "js-widget_message" not in page:
        return None
    post = {"post_id": post_id, "url": post_url(name, post_id), "voice": False}
    audio = re.search(r'<audio[^>]*class="tgme_widget_message_voice[^"]*"[^>]*\ssrc="([^"]+)"', page) \
        or re.search(r'<audio[^>]*\ssrc="([^"]+)"[^>]*class="tgme_widget_message_voice', page)
    if audio:
        post["voice"] = True
        post["audio_url"] = html.unescape(audio.group(1))
        duration = re.search(r'tgme_widget_message_voice_duration[^>]*>([^<]+)<', page)
        post["duration"] = _seconds(duration.group(1)) if duration else None
    date = re.search(r'<time[^>]*datetime="([^"]+)"', page)
    post["date"] = date.group(1) if date else None
    # Who sent it: a member's name in a group, the channel's name in a channel.
    author = re.search(r'class="tgme_widget_message_author_name"[^>]*>(?:\s*<[^>]+>)*([^<]+)', page) \
        or re.search(r'class="tgme_widget_message_owner_name"[^>]*>(?:\s*<[^>]+>)*([^<]+)', page)
    post["author"] = html.unescape(author.group(1)).strip() if author else None
    owner = re.search(r'class="tgme_widget_message_owner_name"[^>]*>(?:\s*<[^>]+>)*([^<]+)', page)
    post["chat_title"] = html.unescape(owner.group(1)).strip() if owner else None
    text = re.search(r'<div class="tgme_widget_message_text js-message_text"[^>]*>(.*?)</div>', page, re.S)
    post["text"] = _text(text.group(1)) if text else ""
    return post


def fetch_post(name: str, post_id: int) -> dict | None:
    return parse_post(fetch_post_html(name, post_id), name, post_id)


def _exists_near(name: str, start: int, pool: ThreadPoolExecutor, width: int = 16) -> bool:
    """Any post among start..start+width-1? Deleted posts leave gaps, so one
    missing number doesn't mean the chat ends there."""
    return any(pool.map(lambda n: fetch_post(name, n) is not None, range(start, start + width)))


def find_latest_post(name: str) -> int:
    """The newest post number (to within a few posts), found by doubling then
    bisecting. Raises TelegramError if the chat can't be read at all."""
    with ThreadPoolExecutor(16) as pool:
        if not any(_exists_near(name, n, pool, 32) for n in (1, 100, 1000)):
            raise TelegramError(f"Couldn't read t.me/{name}. Check that it's a public channel or group "
                                "(open the link in a private browser window: it must show messages "
                                "without logging in).")
        low, high = 1, 1024
        while _exists_near(name, high, pool):
            low, high = high, high * 2
            if high > 1 << 31:
                break
        while high - low > 16:
            mid = (low + high) // 2
            if _exists_near(name, mid, pool):
                low = mid
            else:
                high = mid
    return high + 16  # scanning starts just past it; missing numbers are skipped


# --- Scans ------------------------------------------------------------------

# scan_id -> {"id", "status", "chat", "chat_title", "start", "end", "next", "scanned",
#             "posts", "voices": [...], "error", "max_voices", "max_posts", "cancel"}
SCANS: dict[str, dict] = {}
SCANS_LOCK = threading.Lock()


def scan_options(data: dict) -> dict:
    name, post_id = parse_link(data.get("link") or "")

    def number(key, default, maximum, label):
        value = data.get(key)
        if value in (None, ""):
            return default
        try:
            value = int(value)
        except (TypeError, ValueError):
            raise TelegramError(f"{label} must be a whole number.")
        if not 1 <= value <= maximum:
            raise TelegramError(f"{label} must be between 1 and {maximum:,}.")
        return value

    return {"chat": name, "start": post_id,
            "max_voices": number("max_voices", 50, MAX_VOICES, "Voice messages to find"),
            "max_posts": number("max_posts", 2000, MAX_SCAN_POSTS, "Posts to look through")}


def save_all_options(data: dict) -> dict:
    """Every voice message in the chat, from the newest message (or the one
    the link points to) back to the first, saved into a folder."""
    name, post_id = parse_link(data.get("link") or "")
    return {"chat": name, "start": post_id, "max_voices": None, "max_posts": None, "save": True}


# The most recent save-everything scan, whoever started it (the page or the
# bot), so the page can show a scan the bot started.
LATEST = {"id": None}
SAVE_WORKERS = 4
RECENT = 30  # saved voice messages listed on the page


def start_scan(options: dict, save_root: str | None = None) -> str:
    scan_id = uuid.uuid4().hex[:12]
    SCANS[scan_id] = {"id": scan_id, "status": "starting", "chat": options["chat"], "chat_title": None,
                      "start": options["start"], "end": None, "next": None, "scanned": 0, "posts": 0,
                      "voices": [], "error": None, "max_voices": options["max_voices"],
                      "max_posts": options["max_posts"], "cancel": False,
                      "folder": None, "saved": 0, "already": 0, "failed": 0}
    if options.get("save"):
        SCANS[scan_id]["folder"] = os.path.join(save_root or Config.TELEGRAM_SAVE_DIR, options["chat"])
        LATEST["id"] = scan_id
    threading.Thread(target=_run_scan, args=(scan_id,), daemon=True, name=f"telegram-scan-{scan_id}").start()
    return scan_id


def scan_status(scan_id: str) -> dict | None:
    scan = SCANS.get(scan_id)
    if not scan:
        return None
    with SCANS_LOCK:
        status = {k: (list(v) if k == "voices" else v) for k, v in scan.items()
                  if not k.startswith(("cancel", "_"))}
    status["found"] = len(status["voices"])
    if scan["folder"]:  # can be thousands: the page only lists the latest
        status["voices"] = status["voices"][-RECENT:]
    return status


def is_active(scan: dict | None) -> bool:
    return bool(scan) and scan["status"] in ("starting", "finding", "scanning", "saving")


def cancel_scan(scan_id: str) -> bool:
    scan = SCANS.get(scan_id)
    if not scan:
        return False
    scan["cancel"] = True
    return True


def _run_scan(scan_id: str):
    scan = SCANS[scan_id]
    try:
        if scan["start"] is None:
            scan["status"] = "finding"
            scan["start"] = find_latest_post(scan["chat"])
        scan["status"] = "scanning"
        if scan["max_posts"] is None:  # the whole chat
            scan["end"] = 1
            scan["max_posts"] = scan["start"]
        else:
            scan["end"] = max(1, scan["start"] - scan["max_posts"] + 1)
        if scan["folder"]:
            _prepare_folder(scan)
        try:
            _scan_range(scan, scan["start"], scan["end"])
        finally:
            if scan.get("_saver"):
                scan["status"] = "saving"  # the last few downloads
                scan["_saver"].shutdown(wait=True)
        scan["status"] = "cancelled" if scan["cancel"] else "done"
    except TelegramError as exc:
        scan["status"], scan["error"] = "error", str(exc)
    except Exception as exc:  # network errors etc.: keep what was found
        scan["status"], scan["error"] = "error", f"Stopped: {exc}"


def _scan_range(scan: dict, start: int, end: int):
    """Read posts start, start-1, ... end (newest first) in small batches until
    enough voice messages are found or the scan is cancelled."""
    batch = SCAN_WORKERS * 2
    with ThreadPoolExecutor(SCAN_WORKERS) as pool:
        for top in range(start, end - 1, -batch):
            if scan["cancel"] or (scan["max_voices"] is not None and len(scan["voices"]) >= scan["max_voices"]):
                return
            ids = list(range(top, max(end, top - batch + 1) - 1, -1))
            for post in pool.map(lambda n: fetch_post(scan["chat"], n), ids):
                with SCANS_LOCK:
                    scan["scanned"] += 1
                    if not post:
                        continue
                    scan["posts"] += 1
                    scan["chat_title"] = scan["chat_title"] or post.get("chat_title")
                    if post["voice"] and (scan["max_voices"] is None or len(scan["voices"]) < scan["max_voices"]):
                        scan["voices"].append({k: post.get(k) for k in
                                               ("post_id", "url", "duration", "date", "author", "text")})
                        if scan.get("_saver"):
                            scan["_saver"].submit(_save_voice, scan, post)
            scan["next"] = ids[-1] - 1


# --- Saving every voice message into a folder ----------------------------------

METADATA_FIELDS = ["file", "post_id", "url", "date", "author", "duration_seconds", "text"]


def voice_filename(chat: str, post_id: int) -> str:
    return f"{chat}-{post_id}.ogg"


def _prepare_folder(scan: dict):
    os.makedirs(scan["folder"], exist_ok=True)
    scan["_metadata"] = os.path.join(scan["folder"], "metadata.csv")
    scan["_listed"] = set()  # files metadata.csv already has a row for (from an earlier run)
    try:
        with open(scan["_metadata"], encoding="utf-8-sig", newline="") as f:
            scan["_listed"] = {row.get("file") for row in csv.DictReader(f)}
    except OSError:
        pass
    scan["_saver"] = ThreadPoolExecutor(SAVE_WORKERS, thread_name_prefix="telegram-save")


def _download_to(url: str, path: str):
    part = path + ".part"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=60) as res, open(part, "wb") as f:
            while chunk := res.read(64 * 1024):
                f.write(chunk)
        with open(part, "rb") as f:
            if f.read(4) != b"OggS":
                raise TelegramError("Telegram sent something that isn't an audio file.")
        os.replace(part, path)
    finally:
        if os.path.exists(part):
            os.remove(part)


def _save_voice(scan: dict, post: dict):
    """Save one voice message (skipped if an earlier run saved it already)
    and add its row to metadata.csv."""
    if scan["cancel"]:
        return
    name = voice_filename(scan["chat"], post["post_id"])
    path = os.path.join(scan["folder"], name)
    try:
        if os.path.exists(path) and os.path.getsize(path) > 0:
            key = "already"
        else:
            try:
                _download_to(post["audio_url"], path)
            except (urllib.error.URLError, TimeoutError, OSError, TelegramError):
                fresh = fetch_post(scan["chat"], post["post_id"])  # the audio link may have expired
                if not fresh or not fresh["voice"]:
                    raise
                _download_to(fresh["audio_url"], path)
            key = "saved"
        with SCANS_LOCK:
            scan[key] += 1
            if name not in scan["_listed"]:
                scan["_listed"].add(name)
                new = not os.path.exists(scan["_metadata"])
                with open(scan["_metadata"], "a", encoding="utf-8-sig" if new else "utf-8", newline="") as f:
                    writer = csv.DictWriter(f, METADATA_FIELDS)
                    if new:
                        writer.writeheader()
                    row = {"file": name, "post_id": post["post_id"], "url": post["url"],
                           "date": post.get("date"), "author": post.get("author"),
                           "duration_seconds": post.get("duration"), "text": post.get("text")}
                    # Message text may start like a spreadsheet formula: keep it as text.
                    writer.writerow({k: "'" + v if isinstance(v, str) and v.startswith(("=", "+", "-", "@", "\t", "\r"))
                                     else v for k, v in row.items()})
    except Exception:
        with SCANS_LOCK:
            scan["failed"] += 1


def saved_files(folder: str) -> list[str]:
    try:
        return sorted(n for n in os.listdir(folder) if n.endswith(".ogg") or n == "metadata.csv")
    except OSError:
        return []


# --- Downloads ----------------------------------------------------------------

DOWNLOADS = ThreadPoolExecutor(Config.MAX_PARALLEL_DOWNLOADS, thread_name_prefix="telegram-voice")


def start_voice_download(job_id: str, chat: str, post_id: int, download_dir: str):
    url = post_url(chat, post_id)
    JOBS[job_id] = {"status": "queued", "filename": None, "error": None, "progress": 0,
                    "title": None, "url": url, "want_metadata": True}
    DOWNLOADS.submit(run_voice_download, job_id, chat, post_id, download_dir)


def run_voice_download(job_id: str, chat: str, post_id: int, download_dir: str):
    """Save one voice message as "<chat>-<post>.ogg". The post page is read
    again right before downloading: its audio link only works for a while."""
    job = JOBS[job_id]
    job["status"] = "downloading"
    try:
        post = fetch_post(chat, post_id)
        if not post:
            raise TelegramError("This message was deleted, or the chat is no longer public.")
        if not post["voice"]:
            raise TelegramError("This message isn't a voice message.")
        title = " · ".join(filter(None, [post["author"], (post["date"] or "")[:10]])) or f"Voice {post_id}"
        job["title"] = title
        job["card"] = {"thumbnail": None, "duration": post["duration"], "channel": post["chat_title"] or chat}

        path = os.path.join(download_dir, f"{job_id}.ogg")
        req = urllib.request.Request(post["audio_url"], headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=60) as res, open(path, "wb") as f:
            total = int(res.headers.get("Content-Length") or 0)
            done = 0
            while chunk := res.read(64 * 1024):
                f.write(chunk)
                done += len(chunk)
                if total:
                    job["progress"] = min(99.0, done * 100 / total)
        with open(path, "rb") as f:
            if f.read(4) != b"OggS":
                raise TelegramError("Telegram sent something that isn't an audio file. Try again later.")

        filename = rename_to_title(download_dir, f"{job_id}.ogg", f"{chat}-{post_id}")
        date = post["date"] or ""
        job.update({
            "filename": filename, "format": "ogg", "description": post["text"], "speaker": post["author"],
            "source": {"id": f"{chat}-{post_id}", "url": post["url"], "platform": "Telegram",
                       "channel": post["chat_title"] or chat, "channel_url": f"https://t.me/{chat}",
                       "upload_date": date[:10].replace("-", "") or None, "duration": post["duration"],
                       "clip_minutes": None},
            "finished_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "done_at": time.time(), "progress": 100, "status": "done",
        })
    except Exception as exc:
        try:
            os.remove(os.path.join(download_dir, f"{job_id}.ogg"))
        except OSError:
            pass
        job["status"] = "error"
        job["error"] = str(exc) if isinstance(exc, TelegramError) else f"Download failed: {exc}"
