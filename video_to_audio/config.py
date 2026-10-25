import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class Config:
    """Settings, each overridable with an environment variable of the same name."""

    # Where finished MP3/MP4 files (and temporary zips) are kept.
    DOWNLOAD_DIR = os.environ.get("DOWNLOAD_DIR", str(PROJECT_ROOT / "downloads"))

    # When many links are submitted at once, only this many download at the
    # same time; the rest wait in "queued" until a slot frees up. Keeps the
    # machine and the network (and YouTube's rate limits) from being overwhelmed.
    MAX_PARALLEL_DOWNLOADS = int(os.environ.get("MAX_PARALLEL_DOWNLOADS", "3"))

    HOST = os.environ.get("HOST", "127.0.0.1")
    PORT = int(os.environ.get("PORT", "5000"))
