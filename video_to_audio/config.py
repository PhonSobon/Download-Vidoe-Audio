import os
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


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

    # YouTube sometimes answers "Sign in to confirm you're not a bot". Passing
    # the cookies of a browser that is logged in to YouTube gets past that.
    # COOKIES_FILE is a Netscape-format cookies.txt; uploading one on the page
    # saves it here. COOKIES_FROM_BROWSER is a browser name, optionally with a
    # profile ("firefox", "chrome:Profile 1"), used only while COOKIES_FILE
    # doesn't exist. On Windows only Firefox works; Chrome/Edge can't be read.
    COOKIES_FILE = os.environ.get("COOKIES_FILE", str(PROJECT_ROOT / "cookies.txt"))
    COOKIES_FROM_BROWSER = os.environ.get("COOKIES_FROM_BROWSER", "")

    HOST = os.environ.get("HOST", "127.0.0.1")
    PORT = int(os.environ.get("PORT", "5000"))
