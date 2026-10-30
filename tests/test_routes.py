import zipfile
import io


def test_index_page(client):
    res = client.get("/")
    assert res.status_code == 200
    assert b"Push to Hugging Face" in res.data
    assert client.get("/static/js/app.js").status_code == 200
    assert client.get("/static/css/style.css").status_code == 200


def test_generate_rejects_bad_input(client):
    assert client.post("/generate", json={"url": "not a link"}).status_code == 400
    assert client.post("/generate", json={"url": "https://youtu.be/x", "format": "wav"}).status_code == 400
    res = client.post("/generate", json={"url": "https://youtu.be/x", "limit_minutes": "abc"})
    assert res.status_code == 400


def test_unknown_routes_return_json(client):
    res = client.get("/status/nope")
    assert res.status_code == 404
    assert "error" in res.get_json()
    assert client.get("/no-such-page").is_json


def test_download_and_zip(client, make_job):
    make_job("j1", title="Song")
    make_job("j2", title="Song")
    res = client.get("/download/j1")
    assert res.status_code == 200
    assert "Song.mp3" in res.headers["Content-Disposition"]
    res.close()

    res = client.get("/download_zip?ids=j1,j2,missing")
    assert res.status_code == 200
    names = zipfile.ZipFile(io.BytesIO(res.data)).namelist()
    res.close()
    assert sorted(names) == ["Song (2).mp3", "Song.mp3"]
    assert "downloads.zip" in res.headers["Content-Disposition"]


def test_old_files_are_deleted(client, make_job, download_dir):
    import os
    import time
    from video_to_audio.downloader import JOBS, delete_old_files
    old = make_job("old1", title="Old")
    old["done_at"] = time.time() - 3700
    make_job("new1", title="New")
    JOBS["run1"] = {"status": "downloading"}
    for name in ("run1.webm.part", "leftover.mp3", "batch-x.zip"):
        open(os.path.join(download_dir, name), "wb").close()
    stale = time.time() - 3700
    os.utime(os.path.join(download_dir, "leftover.mp3"), (stale, stale))

    delete_old_files(download_dir, 3600)
    assert sorted(os.listdir(download_dir)) == ["New.mp3", "batch-x.zip", "run1.webm.part"]
    assert old["status"] == "expired"
    res = client.get("/download/old1")
    assert res.status_code == 410 and "Download it again" in res.get_json()["error"]

    # On startup everything not in use goes, however new.
    delete_old_files(download_dir, 0)
    assert os.listdir(download_dir) == ["run1.webm.part"]


def test_zip_is_named_after_the_playlist(client, make_job):
    from urllib.parse import quote
    make_job("p1", title="A")
    make_job("p2", title="B")
    playlist = "សមិទ្ធផលរបស់គណៈកម្មការជំនាញរបស់ព្រឹទ្ធសភា - ព្រឹទ្ធសភា"
    res = client.get(f"/download_zip?ids=p1,p2&name={quote(playlist)}")
    assert res.status_code == 200
    assert quote(f"{playlist}.zip") in res.headers["Content-Disposition"]
    res.close()


def test_push_hf_validates_input(client, make_job):
    make_job("v1", fmt="mp4")
    res = client.post("/push_hf", json={"repo_id": "bad", "ids": ["v1"]})
    assert "your-username/dataset-name" in res.get_json()["error"]
    res = client.post("/push_hf", json={"repo_id": "me/ds", "ids": ["v1"]})
    assert res.get_json()["error"] == "No finished MP3s to push."


def test_expand_passes_playlist_limit(client, monkeypatch):
    from video_to_audio import routes
    calls = []
    monkeypatch.setattr(routes, "expand_playlist",
                        lambda url, limit, start: calls.append((limit, start)) or {"title": "P", "total": 200, "videos": []})
    url = "https://www.youtube.com/playlist?list=PLx"
    assert client.post("/expand", json={"url": url, "limit": "10"}).status_code == 200
    assert client.post("/expand", json={"url": url, "limit": ""}).status_code == 200
    assert client.post("/expand", json={"url": url, "limit": "10", "start": 11}).status_code == 200
    assert client.post("/expand", json={"url": url, "limit": "lots"}).status_code == 400
    assert client.post("/expand", json={"url": url, "start": "x"}).status_code == 400
    assert calls == [(10, 1), (None, 1), (10, 11)]


def test_expand_playlist_batches(monkeypatch):
    from video_to_audio import downloader
    seen_opts = []

    class FakeYDL:
        def __init__(self, opts): seen_opts.append(opts)
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def extract_info(self, url, download=False):
            o = seen_opts[-1]
            first, last = o.get("playliststart", 1), min(o.get("playlistend", 25), 25)
            return {"title": "P", "channel": "Senate TV", "playlist_count": 25,
                    "entries": [{"id": f"v{i}", "title": f"T{i}", "ie_key": "Youtube", "duration": 227,
                                 "channel": "Senate TV"} for i in range(first, last + 1)]}

    monkeypatch.setattr(downloader.yt_dlp, "YoutubeDL", FakeYDL)
    url = "https://www.youtube.com/playlist?list=PLx"
    first = downloader.expand_playlist(url, 10)
    assert [v["title"] for v in first["videos"]][0] == "T1" and len(first["videos"]) == 10
    assert first["next_start"] == 11 and first["has_more"]
    assert first["channel"] == "Senate TV"
    second = downloader.expand_playlist(url, 10, first["next_start"])
    assert second["videos"][0]["title"] == "T11" and seen_opts[-1]["playlistend"] == 20
    # Each video carries what the queue shows next to it, like a YouTube playlist.
    v = second["videos"][0]
    assert v["index"] == 11 and v["duration"] == 227 and v["channel"] == "Senate TV"
    assert v["thumbnail"] == "https://i.ytimg.com/vi/v11/mqdefault.jpg"
    rest = downloader.expand_playlist(url, None, second["next_start"])
    assert len(rest["videos"]) == 5 and not rest["has_more"]


def test_files_are_named_after_the_video_title(client, make_job):
    from video_to_audio.downloader import title_to_filename
    assert title_to_filename("ចម្រៀងខ្មែរ | Official MV") == "ចម្រៀងខ្មែរ Official MV"
    assert title_to_filename(r'a/b\c:d*e?"f<g>h.') == "a b c d e f g h"
    assert title_to_filename("CON") == "audio"
    assert title_to_filename(None) == "audio"

    job = make_job("k1", title="ចម្រៀងខ្មែរ | Official MV")
    assert job["filename"] == "ចម្រៀងខ្មែរ Official MV.mp3"
    res = client.get("/download/k1")
    assert "filename*=UTF-8''" in res.headers["Content-Disposition"]
    res.close()
