"""A Telegram bot that downloads a channel's voice messages for you.

Put the token from @BotFather in .env (TELEGRAM_BOT_TOKEN=...), start the
app, open the bot in Telegram and send it a public channel or group link,
like https://t.me/ScholarshipforCambodianStudents. It starts the same job
as the website's "Download voice messages" button (telegram.py: every voice
message in the chat, saved into TELEGRAM_SAVE_DIR/<channel>/), the website
shows its progress, and the bot messages you when it's done.

Only the bot's owner (the first person to message it) can use it, so
strangers who find the bot can't make this computer download chats.
"""
from __future__ import annotations

import json
import re
import sqlite3
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from contextlib import contextmanager
from pathlib import Path

from . import telegram as channels

API = "https://api.telegram.org"
# A chat link in a message: t.me/name, t.me/name/123, or @name.
LINK_IN_TEXT = re.compile(r"(?:https?://)?(?:www\.)?(?:t\.me|telegram\.me|telegram\.dog)/\S+"
                          r"|(?<![\w@])@[A-Za-z][A-Za-z0-9_]{3,31}\b", re.I)


class BotError(ValueError):
    pass


def call(token: str, method: str, wait: float = 30, **params):
    """One Bot API call. Raises BotError with Telegram's own explanation."""
    data = urllib.parse.urlencode({k: json.dumps(v) if isinstance(v, (list, dict, bool)) else v
                                   for k, v in params.items() if v is not None}).encode()
    req = urllib.request.Request(f"{API}/bot{token}/{method}", data=data)
    try:
        with urllib.request.urlopen(req, timeout=wait) as res:
            body = json.load(res)
    except urllib.error.HTTPError as exc:
        try:
            body = json.load(exc)
        except Exception:
            raise BotError(f"Telegram answered {exc.code}.") from None
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise BotError(f"Couldn't reach Telegram: {getattr(exc, 'reason', exc)}") from None
    if not body.get("ok"):
        code = body.get("error_code")
        if code == 401:
            raise BotError("Telegram doesn't recognise the bot token in .env. Copy it again from @BotFather.")
        err = BotError(body.get("description") or "unknown error")
        err.code = code
        err.retry_after = (body.get("parameters") or {}).get("retry_after")
        raise err
    return body["result"]


class BotStore:
    """Small key/value settings in SQLite: the bot's name, its owner, and
    where in the update stream it got to."""

    def __init__(self, path: str):
        self.path = path
        self.lock = threading.Lock()
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self._db() as db:
            db.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)")

    @contextmanager
    def _db(self):
        with self.lock:
            db = sqlite3.connect(self.path)
            try:
                yield db
                db.commit()
            finally:
                db.close()

    def get(self, key: str, default=None):
        with self._db() as db:
            row = db.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def put(self, key: str, value):
        with self._db() as db:
            if value is None:
                db.execute("DELETE FROM settings WHERE key = ?", (key,))
            else:
                db.execute("INSERT OR REPLACE INTO settings VALUES (?, ?)", (key, json.dumps(value)))


class Bot:
    """Long-polls Telegram for messages to the bot. One per app."""

    def __init__(self, store: BotStore, token: str = "", site_url: str = "http://127.0.0.1:5000",
                 save_root: str | None = None):
        self.store = store
        self.token = token.strip()
        self.site_url = site_url.rstrip("/")
        self.save_root = save_root
        self.thread: threading.Thread | None = None
        self.stop_event = threading.Event()
        self.error: str | None = None

    def status(self) -> dict:
        """What the page shows about the bot. Never includes the token."""
        info = self.store.get("bot") if self.token else None
        return {"enabled": bool(self.token), "username": (info or {}).get("username"),
                "running": bool(self.thread and self.thread.is_alive()), "error": self.error,
                "owner": self.store.get("owner")}

    # Polling ---------------------------------------------------------------

    def start(self):
        if not self.token or (self.thread and self.thread.is_alive()):
            return
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self._poll_loop, args=(self.stop_event,), daemon=True,
                                       name="telegram-bot-poll")
        self.thread.start()

    def stop(self):
        self.stop_event.set()

    def _poll_loop(self, stop: threading.Event):
        try:
            me = call(self.token, "getMe", wait=15)
        except BotError as exc:
            self.error = str(exc)
            return
        if (self.store.get("bot") or {}).get("id") != me["id"]:  # a different bot: start afresh
            self.store.put("offset", None)
            self.store.put("owner", None)
        self.store.put("bot", {"id": me["id"], "username": me.get("username")})
        try:
            call(self.token, "deleteWebhook", wait=15)  # getUpdates doesn't work while a webhook is set
        except BotError:
            pass
        delay = 1
        while not stop.is_set():
            try:
                updates = call(self.token, "getUpdates", wait=40, offset=self.store.get("offset"),
                               allowed_updates=["message"], timeout=25)
                self.error, delay = None, 1
            except BotError as exc:
                self.error = ("Another program is reading this bot's messages (or the app is running twice)."
                              if getattr(exc, "code", None) == 409 else str(exc))
                stop.wait(getattr(exc, "retry_after", None) or delay)
                delay = min(delay * 2, 60)
                continue
            for update in updates:
                try:
                    self.handle_update(update)
                except Exception as exc:  # one odd message must not stop the bot
                    self.error = f"Skipped a message: {exc}"
                self.store.put("offset", update["update_id"] + 1)

    # Messages -----------------------------------------------------------------

    def reply(self, message: dict, text: str):
        try:
            call(self.token, "sendMessage", wait=15, chat_id=message["chat"]["id"], text=text,
                 reply_parameters={"message_id": message["message_id"], "allow_sending_without_reply": True},
                 link_preview_options={"is_disabled": True})
        except BotError:
            pass

    def _is_owner(self, message: dict) -> bool:
        """The first person to message the bot becomes its owner; it ignores
        everyone else (the owner can be reset on the website)."""
        user = message.get("from") or {}
        owner = self.store.get("owner")
        if not owner and user.get("id") and not user.get("is_bot"):
            owner = {"id": user["id"], "username": user.get("username"),
                     "name": " ".join(filter(None, [user.get("first_name"), user.get("last_name")]))}
            self.store.put("owner", owner)
        return bool(owner) and user.get("id") == owner["id"]

    def help_text(self) -> str:
        return ("Send me a public Telegram channel or group link, like\n"
                "https://t.me/ScholarshipforCambodianStudents\n\n"
                "I'll download all of its voice messages. Watch the progress and get them at\n"
                f"{self.site_url}/#telegram")

    def handle_update(self, update: dict):
        message = update.get("message")
        if not message or (message.get("chat") or {}).get("type") != "private":
            return
        if not self._is_owner(message):
            self.reply(message, "Sorry, this bot is private.")
            return
        match = LINK_IN_TEXT.search(message.get("text") or "")
        if not match:
            self.reply(message, self.help_text())
            return
        running = channels.scan_status(channels.LATEST["id"]) if channels.LATEST["id"] else None
        if channels.is_active(running):
            self.reply(message, f"⏳ Still downloading t.me/{running['chat']}: "
                                f"{running['saved'] + running['already']:,} voice messages so far. "
                                f"Stop it on the website first: {self.site_url}/#telegram")
            return
        try:
            options = channels.save_all_options({"link": match.group(0)})
        except channels.TelegramError as exc:
            self.reply(message, f"⚠️ {exc}")
            return
        scan_id = channels.start_scan(options, self.save_root)
        self.reply(message, f"⬇️ Downloading every voice message in t.me/{options['chat']}.\n"
                            f"Watch it at {self.site_url}/#telegram\n"
                            "I'll message you when it's done.")
        threading.Thread(target=self._report_when_done, args=(message, scan_id), daemon=True,
                         name=f"telegram-bot-report-{scan_id}").start()

    def _report_when_done(self, message: dict, scan_id: str, every: float = 3):
        while channels.is_active(scan := channels.scan_status(scan_id)):
            time.sleep(every)
        if not scan:
            return
        name = scan["chat_title"] or f"t.me/{scan['chat']}"
        have = scan["saved"] + scan["already"]
        if scan["status"] == "error" and not have:
            self.reply(message, f"⚠️ {name}: {scan['error']}")
            return
        word = "Stopped" if scan["status"] in ("cancelled", "error") else "Done"
        text = (f"✅ {word}: {have:,} voice message{'s' if have != 1 else ''} from {name} "
                f"(looked through {scan['scanned']:,} messages).")
        if scan["failed"]:
            text += f"\n{scan['failed']:,} couldn't be downloaded: send the link again to retry them."
        text += f"\nSaved in {scan['folder']}\nDownload them as a .zip at {self.site_url}/#telegram"
        self.reply(message, text)
