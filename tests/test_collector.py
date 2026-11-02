import csv
import io

import pytest

from video_to_audio import collector, create_app


@pytest.fixture
def store(tmp_path):
    return collector.CollectionStore(tmp_path / "collections.sqlite3")


@pytest.fixture
def fake_tiktok(monkeypatch):
    calls = []

    class FakeYDL:
        def __init__(self, opts):
            self.opts = opts

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def extract_info(self, url, download=False):
            assert download is False
            assert self.opts["skip_download"] is True
            assert self.opts["extract_flat"] == "in_playlist"
            calls.append(url)
            if url.endswith("@blocked"):
                raise RuntimeError("TikTok did not allow this profile request")
            entries = [
                {"id": "1001", "title": "រីវីវ food review", "uploader": "creator", "timestamp": 1767225600,
                 "duration": 60, "url": "https://www.tiktok.com/@creator/photo/1001?tracking=1"},
                {"id": "1001", "title": "Same post", "upload_date": "20260101"},
                {"id": "1002", "title": "Music", "upload_date": "20260102", "duration": 90},
                {"id": "1003", "title": "Another food review", "upload_date": "20250101"},
                {"id": "1004", "title": "food review, unknown date"},
            ]
            return {"entries": iter(entries)}

    monkeypatch.setattr(collector.yt_dlp, "YoutubeDL", FakeYDL)
    return calls


def test_collects_filters_deduplicates_and_persists(store, fake_tiktok):
    options = collector.collection_options({"profiles": "https://tiktok.com/@creator?lang=en",
        "max_posts": 5, "keywords": "FOOD, រីវីវ", "date_from": "2026-01-01", "topic": "Food reviews"})
    collection_id = store.create(options)
    collector.run_collection(store, collection_id)
    result = store.get(collection_id)
    assert result["status"] == "done"
    assert result["scanned"] == 5 and result["count"] == 1
    assert result["profiles_done"] == 1
    posts = store.posts(collection_id)
    assert posts[0]["url"] == "https://www.tiktok.com/@creator/photo/1001"
    assert posts[0]["upload_date"] == "20260101"
    assert posts[0]["topic"] == "Food reviews"
    reopened = collector.CollectionStore(store.path)
    assert reopened.posts(collection_id) == posts
    assert reopened.recent()[0]["id"] == collection_id


def test_profile_limit_is_inspection_limit(store, fake_tiktok):
    options = collector.collection_options({"profiles": ["https://www.tiktok.com/@creator"], "max_posts": 3})
    collection_id = store.create(options)
    collector.run_collection(store, collection_id)
    assert store.get(collection_id)["scanned"] == 3
    assert [post["post_id"] for post in store.posts(collection_id)] == ["1001", "1002"]


def test_profile_failure_keeps_other_results(store, fake_tiktok):
    options = collector.collection_options({"profiles": "https://www.tiktok.com/@blocked\nhttps://www.tiktok.com/@creator"})
    collection_id = store.create(options)
    collector.run_collection(store, collection_id)
    result = store.get(collection_id)
    assert result["status"] == "partial"
    assert result["count"] == 4 and result["profiles_done"] == 2
    assert result["errors"][0]["profile"].endswith("@blocked")


def test_cancel_and_restart_preserve_links(store, fake_tiktok):
    options = collector.collection_options({"profiles": "https://www.tiktok.com/@creator"})
    cancelled = store.create(options)
    store.update(cancelled, status="cancelled")
    collector.run_collection(store, cancelled)
    assert fake_tiktok == []
    interrupted = store.create(options)
    store.add(interrupted, {"url": "https://www.tiktok.com/@creator/video/1", "post_id": "1"})
    store.update(interrupted, status="running")
    store.recover_interrupted()
    assert store.get(interrupted)["status"] == "interrupted"
    assert store.get(interrupted)["count"] == 1
    assert store.get(cancelled)["status"] == "cancelled"


@pytest.mark.parametrize("data", [
    {}, {"profiles": "https://example.com/@creator"},
    {"profiles": "https://www.tiktok.com/@creator/video/1"},
    {"profiles": "https://www.tiktok.com.evil.com/@creator"},
    {"profiles": [123]}, {"profiles": "https://www.tiktok.com/@creator", "max_posts": 0},
    {"profiles": "https://www.tiktok.com/@creator", "max_posts": 501},
    {"profiles": "https://www.tiktok.com/@creator", "max_posts": 1.5},
    {"profiles": "https://www.tiktok.com/@creator", "max_posts": True},
    {"profiles": "https://www.tiktok.com/@creator", "date_from": "2026-02-30"},
    {"profiles": "https://www.tiktok.com/@creator", "date_from": "2026-02-01", "date_to": "2026-01-01"},
])
def test_invalid_inputs_are_rejected(data):
    with pytest.raises(collector.CollectionError):
        collector.collection_options(data)


def test_csv_export_import_round_trip_and_formula_cells():
    posts = [{"url": "https://www.tiktok.com/@creator/photo/1001", "post_id": "1001", "creator": "creator",
              "title": "=formula, សួស្តី\nreview", "topic": "Khmer"}]
    data = collector.export_csv(posts).encode("utf-8")
    rows = list(csv.DictReader(io.StringIO(data.decode("utf-8-sig"))))
    assert rows[0]["title"] == "'=formula, សួស្តី\nreview"
    assert collector.import_links(data, "tiktok_links.csv")["urls"] == [posts[0]["url"]]


def test_import_removes_duplicate_post_ids_and_bad_rows():
    text = ("https://www.tiktok.com/@creator/photo/1001?tracking=1\n"
            "https://www.tiktok.com/@creator/video/1001\n"
            "not a link\nhttps://www.tiktok.com/@creator\nhttps://example.com/talk\n")
    result = collector.import_links(text.encode(), "links.txt")
    assert len(result["urls"]) == 2
    assert result["skipped"] == 3


@pytest.mark.parametrize("data,name", [(b"title\nhello", "links.csv"), (b"\xff", "links.txt"),
    (b"hello", "links.json"), (b"https://www.tiktok.com/@creator", "links.txt"),
    (b"a" * (collector.MAX_IMPORT_BYTES + 1), "links.txt")])
def test_invalid_imports(data, name):
    with pytest.raises(collector.CollectionError):
        collector.import_links(data, name)


def test_collection_endpoints_and_saved_history(client, fake_tiktok, monkeypatch):
    # Run synchronously so route assertions are independent of thread timing.
    def start(store, options):
        collection_id = store.create(options)
        collector.run_collection(store, collection_id)
        return collection_id

    monkeypatch.setattr(collector, "start_collection", start)
    response = client.post("/collections", json={"profiles": "https://www.tiktok.com/@creator"})
    assert response.status_code == 202
    collection_id = response.get_json()["id"]
    status = client.get(f"/collections/{collection_id}").get_json()
    assert status["count"] == 4 and len(status["preview"]) == 4
    assert client.get("/collections").get_json()[0]["id"] == collection_id
    links = client.get(f"/collections/{collection_id}/links").get_json()["urls"]
    exported = client.get(f"/collections/{collection_id}/export.csv")
    assert "tiktok_links.csv" in exported.headers["Content-Disposition"]
    imported = client.post("/links/import", data={"file": (io.BytesIO(exported.data), "tiktok_links.csv")})
    assert imported.status_code == 200 and imported.get_json()["urls"] == links
    app = create_app({"TESTING": True, "DOWNLOAD_DIR": client.application.config["DOWNLOAD_DIR"],
                      "COLLECTION_DB": client.application.config["COLLECTION_DB"]})
    assert app.test_client().get(f"/collections/{collection_id}").get_json()["count"] == 4


def test_collection_routes_reject_invalid_requests(client):
    assert client.post("/collections", json={"profiles": "bad"}).status_code == 400
    assert client.post("/collections", json=["bad"]).status_code == 400
    assert client.get("/collections/missing").status_code == 404
    assert client.get("/collections/missing/export.csv").status_code == 404
    assert client.get("/collections/missing/links").status_code == 404
    assert client.post("/collections/missing/cancel").status_code == 404
    assert client.post("/links/import").status_code == 400


def test_background_collection_releases_slot(store, fake_tiktok):
    import time
    options = collector.collection_options({"profiles": "https://www.tiktok.com/@creator", "max_posts": 1})
    collection_id = collector.start_collection(store, options)
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        result = store.get(collection_id)
        if result["status"] == "done":
            break
        time.sleep(0.01)
    assert result["status"] == "done" and result["count"] == 1
