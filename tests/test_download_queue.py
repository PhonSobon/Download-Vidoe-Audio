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
            downloader.start_download(str(index), "https://example.com/video", "mp3", None, str(tmp_path))
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
