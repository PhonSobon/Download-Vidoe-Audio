"""TikTok photo posts use the audio extractor and keep their original source."""
from pathlib import Path

import pytest

from video_to_audio import downloader


PHOTO_URL = "https://www.tiktok.com/@arom2966/photo/7558788092166032647?is_from_webapp=1"


@pytest.mark.parametrize("url", [
    PHOTO_URL,
    "https://tiktok.com/@arom2966/photo/7558788092166032647/",
    "https://m.tiktok.com/@arom2966/photo/7558788092166032647",
])
def test_photo_post_downloads_audio_and_keeps_source(monkeypatch, tmp_path, url):
    seen_urls = []

    class FakeYDL:
        def __init__(self, opts):
            self.opts = opts

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def extract_info(self, extract_url, download):
            assert download
            assert extract_url.startswith("https://www.tiktok.com/@arom2966/video/7558788092166032647")
            seen_urls.append(extract_url)
            Path(self.opts["outtmpl"].replace("%(ext)s", "mp3")).write_bytes(b"ID3test")
            return {"id": "7558788092166032647", "title": "Photo soundtrack",
                    "webpage_url": extract_url, "extractor_key": "TikTok", "duration": 60}

    monkeypatch.setattr(downloader.yt_dlp, "YoutubeDL", FakeYDL)
    monkeypatch.setattr(downloader, "JOBS", {"photo": {"status": "queued", "url": url}})
    downloader.run_download("photo", url, "mp3", None, str(tmp_path))

    job = downloader.JOBS["photo"]
    assert len(seen_urls) == 1
    assert job["status"] == "done"
    assert job["format"] == "mp3"
    assert job["source"]["url"] == url
    assert job["source"]["platform"] == "TikTok"
    assert (tmp_path / job["filename"]).read_bytes() == b"ID3test"


def test_photo_post_mp4_explains_audio_option_without_fetching(monkeypatch, tmp_path):
    def unexpected_download(*args, **kwargs):
        pytest.fail("A photo post selected as MP4 should be rejected before downloading")

    monkeypatch.setattr(downloader.yt_dlp, "YoutubeDL", unexpected_download)
    monkeypatch.setattr(downloader, "JOBS", {"photo": {"status": "queued"}})
    downloader.run_download("photo", PHOTO_URL, "mp4", None, str(tmp_path))
    job = downloader.JOBS["photo"]
    assert job["status"] == "error"
    assert "photo/slideshow post" in job["error"]
    assert "Choose MP3 audio" in job["error"]
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("url", [
    "https://www.tiktok.com/@arom2966/video/7558788092166032647",
    "https://www.youtube.com/watch?v=abc",
    "https://example.com/@arom2966/photo/7558788092166032647",
    "https://tiktok.com.example.com/@arom2966/photo/7558788092166032647",
])
def test_other_links_are_unchanged(url):
    assert downloader.prepare_download_url(url, "mp3") == url
    assert downloader.prepare_download_url(url, "mp4") == url


@pytest.mark.parametrize("url, skips_music", [
    ("https://www.tiktok.com/@arom2966/video/7558788092166032647", True),
    ("https://vt.tiktok.com/ZSabc123/", True),
    (PHOTO_URL, False),
    ("https://www.youtube.com/watch?v=abc", False),
])
def test_tiktok_video_mp3_uses_video_audio_not_music_track(url, skips_music):
    assert ("format_id!=audio" in downloader.audio_format(url)) is skips_music
