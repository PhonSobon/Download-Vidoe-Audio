"""Timed captions (.vtt) saved next to the audio, and caption text in metadata.csv."""
from pathlib import Path

from video_to_audio import downloader


VIDEO_URL = "https://www.youtube.com/watch?v=abc123"
VTT = "WEBVTT\n\n00:00:00.000 --> 00:00:02.500\nសួស្តី\n"


def fake_ydl(seen_opts, langs=("khm-KH",), fail_captions=False):
    class FakeYDL:
        def __init__(self, opts):
            self.opts = opts
            seen_opts.append(opts)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def extract_info(self, extract_url, download):
            base = self.opts["outtmpl"].replace(".%(ext)s", "")
            if self.opts.get("writesubtitles"):
                if fail_captions:
                    raise downloader.yt_dlp.utils.DownloadError("Unable to download video subtitles")
                for lang in langs:
                    Path(f"{base}.{lang}.vtt").write_bytes(VTT.encode("utf-8"))
            Path(base + ".mp3").write_bytes(b"ID3test")
            return {"id": "abc123", "title": "Hello", "description": "=Hello #news",
                    "webpage_url": extract_url, "extractor_key": "Youtube", "uploader": "creator",
                    "upload_date": "20261101", "duration": 30}
    return FakeYDL


def test_captions_are_saved_as_vtt_next_to_the_audio(monkeypatch, tmp_path):
    seen_opts = []
    monkeypatch.setattr(downloader.yt_dlp, "YoutubeDL", fake_ydl(seen_opts, ("eng-US", "khm-KH")))
    monkeypatch.setattr(downloader, "JOBS", {"cap": {"status": "queued", "url": VIDEO_URL}})
    downloader.run_download("cap", VIDEO_URL, "mp3", None, str(tmp_path), captions=True)

    job = downloader.JOBS["cap"]
    assert job["status"] == "done"
    assert seen_opts[0]["writesubtitles"] and seen_opts[0]["subtitleslangs"] == ["all", "-live_chat"]
    assert seen_opts[0]["postprocessors"][0]["key"] == "FFmpegSubtitlesConvertor"
    assert job["filename"] == "Hello.mp3"
    assert job["captions"] == [{"lang": "eng-US", "filename": "Hello.eng-US.vtt"},
                               {"lang": "khm-KH", "filename": "Hello.khm-KH.vtt"}]
    assert (tmp_path / "Hello.khm-KH.vtt").read_bytes().decode("utf-8") == VTT
    assert job["description"] == "=Hello #news"


def test_without_captions_no_subtitles_are_requested(monkeypatch, tmp_path):
    seen_opts = []
    monkeypatch.setattr(downloader.yt_dlp, "YoutubeDL", fake_ydl(seen_opts))
    monkeypatch.setattr(downloader, "JOBS", {"plain": {"status": "queued", "url": VIDEO_URL}})
    downloader.run_download("plain", VIDEO_URL, "mp3", None, str(tmp_path))
    assert "writesubtitles" not in seen_opts[0]
    assert downloader.JOBS["plain"]["captions"] == []


def test_failed_captions_still_save_the_audio(monkeypatch, tmp_path):
    seen_opts = []
    monkeypatch.setattr(downloader.yt_dlp, "YoutubeDL", fake_ydl(seen_opts, fail_captions=True))
    monkeypatch.setattr(downloader, "JOBS", {"cap": {"status": "queued", "url": VIDEO_URL}})
    downloader.run_download("cap", VIDEO_URL, "mp3", None, str(tmp_path), captions=True)
    job = downloader.JOBS["cap"]
    assert job["status"] == "done" and job["filename"] == "Hello.mp3"
    assert job["captions"] == [] and "couldn't be downloaded" in job["caption_error"]


def test_caption_route_and_zip_with_metadata(client, monkeypatch, download_dir):
    import csv
    import io
    import os
    import zipfile

    os.makedirs(download_dir, exist_ok=True)
    seen_opts = []
    monkeypatch.setattr(downloader.yt_dlp, "YoutubeDL", fake_ydl(seen_opts))
    downloader.JOBS["cap"] = {"status": "queued", "url": VIDEO_URL, "want_captions": True}
    downloader.run_download("cap", VIDEO_URL, "mp3", None, download_dir, captions=True)

    res = client.get("/download/cap/captions/0")
    assert res.status_code == 200 and res.data.decode("utf-8") == VTT
    assert "Hello.khm-KH.vtt" in res.headers["Content-Disposition"]
    res.close()
    assert client.get("/download/cap/captions/5").status_code == 404

    res = client.post("/statuses", json={"ids": ["cap"]})
    assert res.get_json()["jobs"]["cap"]["captions"][0]["lang"] == "khm-KH"

    res = client.get("/download_zip?ids=cap")
    zf = zipfile.ZipFile(io.BytesIO(res.data))
    res.close()
    assert sorted(zf.namelist()) == ["Hello.khm-KH.vtt", "Hello.mp3", "metadata.csv"]
    rows = list(csv.DictReader(io.StringIO(zf.read("metadata.csv").decode("utf-8-sig"))))
    assert rows[0]["caption_files"] == "Hello.khm-KH.vtt"
    assert rows[0]["description"] == "'=Hello #news"  # formula-safe in spreadsheets
    assert rows[0]["upload_date"] == "20261101"


def test_generate_passes_captions_flag(client, monkeypatch):
    calls = []
    monkeypatch.setattr("video_to_audio.routes.start_download", lambda *a, **k: calls.append(k))
    assert client.post("/generate", json={"url": VIDEO_URL, "format": "mp3", "captions": True}).status_code == 200
    assert client.post("/generate", json={"url": VIDEO_URL, "format": "mp3"}).status_code == 200
    assert [c["captions"] for c in calls] == [True, False]
