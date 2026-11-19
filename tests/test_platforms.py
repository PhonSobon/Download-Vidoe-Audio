"""Links from sites other than YouTube: Pinterest, TikTok, Instagram, X..."""
import os

from video_to_audio import downloader


def test_video_key_for_other_platforms():
    key = downloader.video_key
    assert key("https://www.pinterest.com/pin/some-title--123456/") == key("https://pinterest.com/pin/123456") == "pin:123456"
    assert key("https://www.tiktok.com/@user/video/7234567890?is_from_webapp=1") == "tt:7234567890"
    assert key("https://www.instagram.com/reel/Cabc_12/") == key("https://instagram.com/p/Cabc_12/?igsh=x") == "ig:Cabc_12"
    assert key("https://x.com/user/status/111") == key("https://twitter.com/other/status/111/video/1") == "x:111"
    assert key("https://vimeo.com/123456789") == "vimeo:123456789"


class FakeYDL:
    """Records the options of each run and plays back a canned result."""
    runs = []
    result = None
    error = None

    def __init__(self, opts): self.opts = opts; FakeYDL.runs.append(opts)
    def __enter__(self): return self
    def __exit__(self, *a): pass

    def extract_info(self, url, download=False):
        if FakeYDL.error:
            raise downloader.yt_dlp.utils.DownloadError(FakeYDL.error)
        if download:
            path = self.opts["outtmpl"].replace("%(ext)s", "mp4")
            open(path, "wb").close()
        return FakeYDL.result


def run(monkeypatch, tmp_path, url, fmt="mp4", result=None, error=None):
    FakeYDL.runs, FakeYDL.result, FakeYDL.error = [], result, error
    monkeypatch.setattr(downloader.yt_dlp, "YoutubeDL", FakeYDL)
    downloader.JOBS["j"] = {"status": "queued"}
    downloader._run_download("j", url, fmt, None, str(tmp_path))
    return downloader.JOBS.pop("j")


def test_mp4_falls_back_to_separate_video_and_audio_streams(monkeypatch, tmp_path):
    job = run(monkeypatch, tmp_path, "https://www.pinterest.com/pin/123/",
              result={"title": "A pin", "extractor_key": "Pinterest", "id": "123"})
    assert job["status"] == "done" and job["filename"] == "A pin.mp4"
    opts = FakeYDL.runs[0]
    # Sites that only have separate non-MP4 streams still get a merged MP4.
    assert "bv*+ba" in opts["format"] and opts["merge_output_format"] == "mp4"
    # A board/profile link never writes several videos into one job's file.
    assert opts["playlist_items"] == "1"
    assert os.path.exists(tmp_path / "A pin.mp4")


def test_clear_errors_for_unsupported_links_and_image_posts(monkeypatch, tmp_path):
    job = run(monkeypatch, tmp_path, "https://example.com/", error="ERROR: Unsupported URL: https://example.com/")
    assert job["status"] == "error" and "isn't supported" in job["error"]
    job = run(monkeypatch, tmp_path, "https://www.pinterest.com/pin/1/", error="ERROR: [Pinterest] 1: No video formats found!")
    assert "image-only" in job["error"]


def test_expand_board_keeps_entry_links_and_skips_entries_without_one(monkeypatch):
    class BoardYDL(FakeYDL):
        def extract_info(self, url, download=False):
            return {"title": "Board", "playlist_count": 2, "entries": [
                {"url": "https://www.pinterest.com/pin/1/", "id": "1", "ie_key": "Pinterest", "title": "One"},
                {"id": "2", "ie_key": "Pinterest", "title": "No link"},
            ]}
    monkeypatch.setattr(downloader.yt_dlp, "YoutubeDL", BoardYDL)
    result = downloader.expand_playlist("https://www.pinterest.com/user/board/")
    # The entry without a link isn't turned into a YouTube watch link.
    assert [v["url"] for v in result["videos"]] == ["https://www.pinterest.com/pin/1/"]
