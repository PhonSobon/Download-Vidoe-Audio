import os

import pytest

from video_to_audio import create_app
from video_to_audio.config import Config
from video_to_audio.downloader import JOBS, rename_to_title


@pytest.fixture(autouse=True)
def cookies_file(tmp_path, monkeypatch):
    """Keep tests away from the real cookies.txt in the project folder."""
    path = str(tmp_path / "cookies.txt")
    monkeypatch.setattr(Config, "COOKIES_FILE", path)
    monkeypatch.setattr(Config, "COOKIES_FROM_BROWSER", "")
    return path


@pytest.fixture
def download_dir(tmp_path):
    return str(tmp_path / "downloads")


@pytest.fixture
def client(download_dir):
    app = create_app({"TESTING": True, "DOWNLOAD_DIR": download_dir})
    yield app.test_client()
    JOBS.clear()


@pytest.fixture
def make_job(download_dir):
    """Register a finished MP3 job (with a small fake file) in JOBS."""
    def make(job_id, title="Title", url="https://www.youtube.com/watch?v=abc",
             platform="Youtube", channel="Chan", video_id="abc", fmt="mp3", clip_minutes=None):
        os.makedirs(download_dir, exist_ok=True)
        filename = f"{job_id}.{fmt}"
        with open(os.path.join(download_dir, filename), "wb") as f:
            f.write(b"ID3fake-" + job_id.encode())
        filename = rename_to_title(download_dir, filename, title)
        JOBS[job_id] = {
            "status": "done", "format": fmt, "filename": filename, "title": title, "url": url,
            "finished_at": "2026-10-25T00:00:00+00:00",
            "source": {"id": video_id, "url": url, "platform": platform, "channel": channel,
                       "channel_url": None, "upload_date": "20240101", "duration": 60,
                       "clip_minutes": clip_minutes},
        }
        return JOBS[job_id]
    return make
