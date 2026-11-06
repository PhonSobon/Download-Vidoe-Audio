import threading
from queue import Queue

from video_to_audio import downloader


def test_bulk_queue_bounds_workers_and_continues_after_failure(monkeypatch, tmp_path):
    queue = Queue()
    jobs = {}
    lock = threading.Lock()
    ready, release, finished = threading.Event(), threading.Event(), threading.Event()
    active, peak, completed = 0, 0, 0

    def fake_download(job_id, *args):
        nonlocal active, peak, completed
        with lock:
            active += 1
            peak = max(peak, active)
            if active == 2:
                ready.set()
        try:
            assert release.wait(timeout=3)
            if job_id == "3":
                raise RuntimeError("One failed post")
            jobs[job_id]["status"] = "done"
        finally:
            with lock:
                active -= 1
                completed += 1
                if completed == 20:
                    finished.set()

    monkeypatch.setattr(downloader, "DOWNLOAD_QUEUE", queue)
    monkeypatch.setattr(downloader, "DOWNLOAD_WORKERS_STARTED", False)
    monkeypatch.setattr(downloader, "JOBS", jobs)
    monkeypatch.setattr(downloader.Config, "MAX_PARALLEL_DOWNLOADS", 2)
    monkeypatch.setattr(downloader, "run_download", fake_download)
    try:
        for index in range(20):
            downloader.start_download(str(index), f"https://example.com/video{index}", "mp3", None, str(tmp_path))
        assert ready.wait(timeout=3)
        assert len([job for job in jobs.values() if job["status"] == "queued"]) == 20
        release.set()
        assert finished.wait(timeout=3)
        assert peak == 2
        assert jobs["3"]["status"] == "error"
        assert all(job["status"] == "done" for key, job in jobs.items() if key != "3")
    finally:
        release.set()
        queue.put(None)
        queue.put(None)


def test_same_video_in_another_link_form_is_not_downloaded_twice(monkeypatch, tmp_path):
    queue, jobs = Queue(), {}
    monkeypatch.setattr(downloader, "DOWNLOAD_QUEUE", queue)
    monkeypatch.setattr(downloader, "DOWNLOAD_WORKERS_STARTED", True)  # no workers: jobs stay queued
    monkeypatch.setattr(downloader, "JOBS", jobs)
    start = lambda job_id, url, fmt="mp3", limit=None, captions=False: downloader.start_download(
        job_id, url, fmt, limit, str(tmp_path), captions)

    assert start("a", "https://www.youtube.com/watch?v=abc123") == "a"
    for url in ("https://youtu.be/abc123", "https://m.youtube.com/watch?v=abc123&t=30&list=PLx",
                "https://www.youtube.com/shorts/abc123"):
        assert start("dup", url) == "a"
    # A different format, clip length or captions setting is a different file.
    assert start("b", "https://youtu.be/abc123", fmt="mp4") == "b"
    assert start("c", "https://youtu.be/abc123", limit=5) == "c"
    assert start("d", "https://youtu.be/abc123", captions=True) == "d"
    assert "dup" not in jobs and queue.qsize() == 4

    # A finished job is reused only while its file is still on the server.
    jobs["a"].update(status="done", filename="Title.mp3")
    assert start("e", "https://youtu.be/abc123") == "e"
    (tmp_path / "Title.mp3").write_bytes(b"x")
    jobs["e"]["status"] = "error"
    assert start("f", "https://youtu.be/abc123") == "a"
    # Failed and expired jobs can be downloaded again.
    jobs["a"]["status"] = "expired"
    assert start("g", "https://youtu.be/abc123") == "g"


def test_video_key():
    key = downloader.video_key
    assert key("https://www.tiktok.com/@me/video/123?lang=en") == key("https://m.tiktok.com/@me/photo/123") == "tiktok:123"
    assert key("https://www.facebook.com/reel/987/") == key("https://web.facebook.com/watch/?v=987") == "fb:987"
    assert key("https://example.com/a.mp4#t=5") == "https://example.com/a.mp4"
    assert key("https://example.com/a.mp4") != key("https://example.com/b.mp4")
