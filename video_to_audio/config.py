import os
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
# Names in .env that mean the same as a setting below.
ENV_ALIASES = {"TELEGRAM_BOT": "TELEGRAM_BOT_TOKEN", "BOT_TOKEN": "TELEGRAM_BOT_TOKEN"}


def load_env_file(path: Path = PROJECT_ROOT / ".env"):
    """Read KEY=value lines from .env into the environment (names are
    case-insensitive). Variables already set in the environment win."""
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except OSError:
        return
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.removeprefix("export ").split("=", 1)
        key = key.strip().upper()
        key = ENV_ALIASES.get(key, key)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        if key and value:
            os.environ.setdefault(key, value)


load_env_file()


class Config:
    """Settings, each overridable with an environment variable of the same name."""

    # Working folder for MP3/MP4 files (and zips) while they wait to be saved
    # from the page. It's in the system temp folder, not the project, and
    # everything in it is deleted: finished files FILE_LIFETIME_MINUTES after
    # they're ready, and anything left over each time the app starts.
    DOWNLOAD_DIR = os.environ.get("DOWNLOAD_DIR", os.path.join(tempfile.gettempdir(), "video_to_audio"))
    FILE_LIFETIME_MINUTES = float(os.environ.get("FILE_LIFETIME_MINUTES", "60"))

    # When many links are submitted at once, only this many download at the
    # same time; the rest wait in "queued" until a slot frees up. Keeps the
    # machine and the network (and YouTube's rate limits) from being overwhelmed.
    MAX_PARALLEL_DOWNLOADS = int(os.environ.get("MAX_PARALLEL_DOWNLOADS", "3"))

    # Collected links survive restarts and are separate from temporary audio.
    COLLECTION_DB = os.environ.get("COLLECTION_DB", str(PROJECT_ROOT / "data" / "collections.sqlite3"))

    # YouTube sometimes answers "Sign in to confirm you're not a bot". Passing
    # the cookies of a browser that is logged in to YouTube gets past that.
    # COOKIES_FILE is a Netscape-format cookies.txt; uploading one on the page
    # saves it here. COOKIES_FROM_BROWSER is a browser name, optionally with a
    # profile ("firefox", "chrome:Profile 1"), used only while COOKIES_FILE
    # doesn't exist. On Windows only Firefox works; Chrome/Edge can't be read.
    COOKIES_FILE = os.environ.get("COOKIES_FILE", str(PROJECT_ROOT / "cookies.txt"))
    COOKIES_FROM_BROWSER = os.environ.get("COOKIES_FROM_BROWSER", "")

    # Voice messages downloaded from a Telegram channel are saved here, one
    # folder per channel (<channel>/<channel>-<message>.ogg + metadata.csv).
    # Unlike DOWNLOAD_DIR, nothing here is deleted automatically.
    TELEGRAM_SAVE_DIR = os.environ.get("TELEGRAM_SAVE_DIR", str(PROJECT_ROOT / "downloads" / "telegram"))

    # Optional Telegram bot (token from @BotFather, in .env): send it a
    # channel link and it downloads that channel's voice messages, like the
    # page does. TELEGRAM_BOT_DB keeps who owns the bot and its scans.
    TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    TELEGRAM_BOT_DB = os.environ.get("TELEGRAM_BOT_DB", str(PROJECT_ROOT / "data" / "telegram_bot.sqlite3"))
    # The address the bot sends you in its replies. Defaults to http://HOST:PORT.
    SITE_URL = os.environ.get("SITE_URL", "")

    HOST = os.environ.get("HOST", "127.0.0.1")
    PORT = int(os.environ.get("PORT", "5000"))
