"""Finished downloads are copied into SAVE_DIR/<playlist or channel>/."""
import csv
import os
from pathlib import Path

from video_to_audio import downloader
from test_captions import VIDEO_URL, fake_ydl


def read_rows(folder):
    with open(os.path.join(folder, "metadata.csv"), encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def test_download_is_saved_in_its_playlist_folder(monkeypatch, tmp_path, save_dir):
    monkeypatch.setattr(downloader.yt_dlp, "YoutubeDL", fake_ydl([]))
    monkeypatch.setattr(downloader, "JOBS", {"j": {"status": "queued", "url": VIDEO_URL,
                                                    "folder": "My list: part 1 - MPTC"}})
    downloads = tmp_path / "dl"
    downloads.mkdir()
    downloader.run_download("j", VIDEO_URL, "mp3", None, str(downloads), captions=True)

    job = downloader.JOBS["j"]
    folder = Path(save_dir) / "My list part 1 - MPTC"  # ":" isn't allowed in Windows names
    assert job["status"] == "done" and job["saved_to"] == str(folder)
    assert (folder / "Hello.mp3").read_bytes() == b"ID3test"
    assert (folder / "Hello.khm-KH.vtt").exists()
    rows = read_rows(folder)
    assert [r["file"] for r in rows] == ["Hello.mp3"] and rows[0]["caption_files"] == "Hello.khm-KH.vtt"
    # The temporary copy stays for the page's download/zip buttons.
    assert (downloads / "Hello.mp3").exists()


def test_single_video_uses_channel_and_skips_same_file(tmp_path, save_dir):
    downloads = tmp_path / "dl"
    downloads.mkdir()
    (downloads / "Hello.mp3").write_bytes(b"abc")
    job = {"filename": "Hello.mp3", "title": "Hello", "source": {"channel": "MPTC Official"}}

    folder = downloader.save_to_folder(job, str(downloads), save_dir)
    assert folder == os.path.join(save_dir, "MPTC Official")
    downloader.save_to_folder(job, str(downloads), save_dir)  # same video again
    assert os.listdir(folder) == ["Hello.mp3", "metadata.csv"] and len(read_rows(folder)) == 1

    # A different video with the same title gets its own name.
    (downloads / "Hello.mp3").write_bytes(b"different")
    downloader.save_to_folder(job, str(downloads), save_dir)
    assert [r["file"] for r in read_rows(folder)] == ["Hello.mp3", "Hello (2).mp3"]


def test_unreachable_save_folder_does_not_fail_the_download(monkeypatch, tmp_path):
    blocker = tmp_path / "not-a-folder"
    blocker.write_text("x")  # a file where the folder should be
    monkeypatch.setattr(downloader.Config, "SAVE_DIR", str(blocker))
    monkeypatch.setattr(downloader.yt_dlp, "YoutubeDL", fake_ydl([]))
    monkeypatch.setattr(downloader, "JOBS", {"j": {"status": "queued", "url": VIDEO_URL}})
    downloader.run_download("j", VIDEO_URL, "mp3", None, str(tmp_path))
    job = downloader.JOBS["j"]
    assert job["status"] == "done" and "Couldn't save a copy" in job["save_error"]


def test_generate_passes_folder(client, monkeypatch):
    calls = []
    monkeypatch.setattr("video_to_audio.routes.start_download", lambda *a, **k: calls.append(k) or "x")
    client.post("/generate", json={"url": VIDEO_URL, "folder": " Playlist - Chan "})
    client.post("/generate", json={"url": VIDEO_URL})
    assert [c["folder"] for c in calls] == ["Playlist - Chan", None]
