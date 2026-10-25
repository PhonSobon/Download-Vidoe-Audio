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


def test_push_hf_validates_input(client, make_job):
    make_job("v1", fmt="mp4")
    res = client.post("/push_hf", json={"repo_id": "bad", "ids": ["v1"]})
    assert "your-username/dataset-name" in res.get_json()["error"]
    res = client.post("/push_hf", json={"repo_id": "me/ds", "ids": ["v1"]})
    assert res.get_json()["error"] == "No finished MP3s to push."
