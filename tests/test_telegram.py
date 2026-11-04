import csv
import io
import os
import time
import zipfile

import pytest

from video_to_audio import huggingface, telegram
from video_to_audio.downloader import JOBS

CHAT = "ScholarshipforCambodianStudents"


def voice_page(post_id, author="អុីវ នរ៉ា Iv Nora", duration="0:43", text=None):
    """Trimmed copy of a real t.me/<chat>/<id>?embed=1 voice message page."""
    body = (f'<div class="tgme_widget_message_text js-message_text" dir="auto">{text}</div>' if text else "")
    return f"""<html><body class="widget_frame_base">
<div class="tgme_widget_message text_not_supported_wrap js-widget_message" data-post="{CHAT}/{post_id}">
<div class="tgme_widget_message_author accent_color"><span class="tgme_widget_message_author_name"><span dir="auto">{author}</span></span>&nbsp;in&nbsp;<a class="tgme_widget_message_owner_name" href="https://t.me/{CHAT}"><span dir="auto">ដំណឹងអាហារូបករណ៍ &amp; More</span></a></div>
<div class="tgme_widget_message_voice_player js-message_voice_player">
<audio class="tgme_widget_message_voice js-message_voice" src="https://cdn5.telesco.pe/file/abc.ogg?token=x&amp;y=1" data-ogg width="100%" data-waveform="AAA"></audio>
<time class="tgme_widget_message_voice_duration js-message_voice_duration">{duration}</time></div>
{body}
<a class="tgme_widget_message_date" href="https://t.me/{CHAT}/{post_id}"><time datetime="2021-01-18T05:14:09+00:00" class="datetime">Jan 18</time></a>
</div></body></html>"""


def photo_page(post_id):
    return f"""<div class="tgme_widget_message js-widget_message" data-post="{CHAT}/{post_id}">
<a class="tgme_widget_message_owner_name" href="https://t.me/{CHAT}"><span dir="auto">Chat</span></a>
<div class="tgme_widget_message_text js-message_text" dir="auto">Apply now<br/>=deadline</div>
<time datetime="2026-01-01T00:00:00+00:00" class="datetime">Jan 1</time></div>"""


NOT_FOUND = '<div class="tgme_widget_message_error" dir="auto">Post not found</div>'


def fake_chat(monkeypatch, pages: dict):
    """Serve post pages from {post_id: html}; every other number is 'not found'."""
    calls = []

    def get(url, retries=3):
        post_id = int(url.split("?")[0].rsplit("/", 1)[1])
        calls.append(post_id)
        return pages.get(post_id, NOT_FOUND).encode()

    monkeypatch.setattr(telegram, "_get", get)
    return calls


@pytest.mark.parametrize("link,expected", [
    ("https://t.me/ScholarshipforCambodianStudents", (CHAT, None)),
    ("t.me/s/ScholarshipforCambodianStudents", (CHAT, None)),
    ("@ScholarshipforCambodianStudents", (CHAT, None)),
    ("https://t.me/ScholarshipforCambodianStudents/45125?single", (CHAT, 45125)),
    ("https://telegram.me/some_channel/", ("some_channel", None)),
])
def test_parse_link(link, expected):
    assert telegram.parse_link(link) == expected


@pytest.mark.parametrize("link", ["https://youtube.com/@x", "https://t.me/+AbCdEf", "https://t.me/joinchat/abc",
                                  "https://t.me/c/123/45", "https://t.me/", "https://t.me/name/abc", ""])
def test_parse_link_rejects(link):
    with pytest.raises(telegram.TelegramError):
        telegram.parse_link(link)


def test_parse_voice_post():
    post = telegram.parse_post(voice_page(45125, text="សួស្តី<br>hello"), CHAT, 45125)
    assert post["voice"] and post["duration"] == 43
    assert post["audio_url"] == "https://cdn5.telesco.pe/file/abc.ogg?token=x&y=1"
    assert post["author"] == "អុីវ នរ៉ា Iv Nora"
    assert post["chat_title"] == "ដំណឹងអាហារូបករណ៍ & More"
    assert post["date"] == "2021-01-18T05:14:09+00:00"
    assert post["text"] == "សួស្តី\nhello"
    assert post["url"] == f"https://t.me/{CHAT}/45125"


def test_parse_other_posts():
    post = telegram.parse_post(photo_page(7), CHAT, 7)
    assert not post["voice"] and post["author"] == "Chat" and post["text"] == "Apply now\n=deadline"
    assert telegram.parse_post(NOT_FOUND, CHAT, 8) is None


def run_scan(options):
    scan_id = telegram.start_scan(options)
    deadline = time.time() + 10
    while telegram.SCANS[scan_id]["status"] in ("starting", "finding", "scanning"):
        assert time.time() < deadline
        time.sleep(0.01)
    return telegram.scan_status(scan_id)


def test_scan_finds_voice_messages_newest_first_and_stops_at_limit(monkeypatch):
    fake_chat(monkeypatch, {100: voice_page(100), 99: photo_page(99), 97: voice_page(97, author="B"),
                            60: voice_page(60), 10: voice_page(10)})
    scan = run_scan({"chat": CHAT, "start": 100, "max_voices": 3, "max_posts": 1000})
    assert scan["status"] == "done"
    assert [v["post_id"] for v in scan["voices"]] == [100, 97, 60]
    assert scan["voices"][1]["author"] == "B"
    assert scan["next"] and scan["next"] < 60  # "look further back" carries on from here
    assert scan["chat_title"] == "ដំណឹងអាហារូបករណ៍ & More"


def test_scan_respects_the_message_limit(monkeypatch):
    calls = fake_chat(monkeypatch, {50: voice_page(50), 20: voice_page(20)})
    scan = run_scan({"chat": CHAT, "start": 50, "max_voices": 10, "max_posts": 20})
    assert [v["post_id"] for v in scan["voices"]] == [50]
    assert scan["end"] == 31 and scan["scanned"] == 20 and min(calls) == 31


def test_find_latest_post_skips_gaps_of_deleted_messages(monkeypatch):
    latest = 67_670
    # Every 9th message still exists, like a busy group with many deletions.
    fake_chat(monkeypatch, {n: photo_page(n) for n in range(5, latest + 1, 9)} | {latest: photo_page(latest)})
    found = telegram.find_latest_post(CHAT)
    assert latest <= found <= latest + 40


def test_find_latest_post_unreadable_chat(monkeypatch):
    fake_chat(monkeypatch, {})
    with pytest.raises(telegram.TelegramError, match="public"):
        telegram.find_latest_post(CHAT)


def test_scan_route_validation(client):
    assert client.post("/telegram/scans", json={"link": "https://t.me/+private"}).status_code == 400
    res = client.post("/telegram/scans", json={"link": f"https://t.me/{CHAT}", "max_voices": 0})
    assert res.status_code == 400 and "between" in res.get_json()["error"]
    assert client.get("/telegram/scans/nope").status_code == 404


class FakeAudio(io.BytesIO):
    headers = {"Content-Length": "11"}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


def test_voice_download_route_saves_ogg_and_zip_has_metadata(client, monkeypatch, download_dir):
    fake_chat(monkeypatch, {45125: voice_page(45125, text="=hi")})
    monkeypatch.setattr(telegram.urllib.request, "urlopen", lambda req, timeout: FakeAudio(b"OggS-audio!"))
    # Run downloads inline instead of in the thread pool.
    monkeypatch.setattr(telegram.DOWNLOADS, "submit", lambda fn, *args: fn(*args))
    os.makedirs(download_dir, exist_ok=True)

    res = client.post("/telegram/download", json={"chat": CHAT, "post_id": 45125})
    job = JOBS[res.get_json()["job_id"]]
    assert job["status"] == "done", job["error"]
    assert job["filename"] == f"{CHAT}-45125.ogg" and job["format"] == "ogg"
    assert job["title"] == "អុីវ នរ៉ា Iv Nora · 2021-01-18"
    assert job["source"]["upload_date"] == "20210118" and job["source"]["platform"] == "Telegram"
    with open(os.path.join(download_dir, job["filename"]), "rb") as f:
        assert f.read() == b"OggS-audio!"

    job_id = res.get_json()["job_id"]
    res = client.get(f"/download_zip?ids={job_id}")
    zf = zipfile.ZipFile(io.BytesIO(res.data))
    res.close()
    assert sorted(zf.namelist()) == [f"{CHAT}-45125.ogg", "metadata.csv"]
    row = next(csv.DictReader(io.StringIO(zf.read("metadata.csv").decode("utf-8-sig"))))
    assert row["speaker"] == "អុីវ នរ៉ា Iv Nora" and row["description"] == "'=hi"
    assert huggingface.hf_file_name(job) == f"telegram-{CHAT}-45125.ogg"


def test_voice_download_of_a_non_voice_message_fails_clearly(client, monkeypatch):
    fake_chat(monkeypatch, {7: photo_page(7)})
    monkeypatch.setattr(telegram.DOWNLOADS, "submit", lambda fn, *args: fn(*args))
    job = JOBS[client.post("/telegram/download", json={"chat": CHAT, "post_id": 7}).get_json()["job_id"]]
    assert job["status"] == "error" and "isn't a voice message" in job["error"]
    job = JOBS[client.post("/telegram/download", json={"chat": CHAT, "post_id": 8}).get_json()["job_id"]]
    assert job["status"] == "error" and "deleted" in job["error"]


@pytest.mark.parametrize("body", [{}, {"chat": CHAT}, {"chat": "bad name", "post_id": 1},
                                  {"chat": CHAT, "post_id": "1"}, {"chat": CHAT, "post_id": True}])
def test_voice_download_route_validation(client, body):
    assert client.post("/telegram/download", json=body).status_code == 400


# --- Download every voice message into a folder -------------------------------------

def wait_until_finished(scan_id):
    deadline = time.time() + 10
    while telegram.is_active(telegram.scan_status(scan_id)):
        assert time.time() < deadline
        time.sleep(0.01)
    return telegram.scan_status(scan_id)


def test_save_all_downloads_every_voice_message(client, monkeypatch, tmp_path):
    pages = {n: voice_page(n, text="=hi" if n == 3 else None) for n in (3, 120, 250)} | {200: photo_page(200)}
    fake_chat(monkeypatch, pages)
    monkeypatch.setattr(telegram, "find_latest_post", lambda name: 260)
    monkeypatch.setattr(telegram.urllib.request, "urlopen", lambda req, timeout: FakeAudio(b"OggS-audio!"))
    client.application.config["TELEGRAM_SAVE_DIR"] = str(tmp_path / "saved")

    res = client.post("/telegram/voice-downloads", json={"link": f"https://t.me/{CHAT}"})
    assert res.status_code == 202
    scan = wait_until_finished(res.get_json()["scan"]["id"])
    assert scan["status"] == "done" and scan["saved"] == 3 and scan["failed"] == 0
    assert scan["scanned"] == 260 and scan["end"] == 1  # the whole chat, back to message 1
    folder = tmp_path / "saved" / CHAT
    assert sorted(os.listdir(folder)) == sorted([f"{CHAT}-3.ogg", f"{CHAT}-120.ogg", f"{CHAT}-250.ogg",
                                                 "metadata.csv"])
    rows = list(csv.DictReader(open(folder / "metadata.csv", encoding="utf-8-sig")))
    assert {r["post_id"] for r in rows} == {"3", "120", "250"}
    assert next(r for r in rows if r["post_id"] == "3")["text"] == "'=hi"

    latest = client.get("/telegram/voice-downloads/latest").get_json()
    assert latest["scan"]["id"] == scan["id"] and latest["bot"]["enabled"] is False

    res = client.get(f"/telegram/voice-downloads/{scan['id']}/zip")
    zf = zipfile.ZipFile(io.BytesIO(res.data))
    res.close()
    assert len(zf.namelist()) == 4 and zf.read(f"{CHAT}-120.ogg") == b"OggS-audio!"

    # Running it again only adds what's new.
    pages[255] = voice_page(255)
    scan = wait_until_finished(client.post("/telegram/voice-downloads", json={"link": f"@{CHAT}"})
                               .get_json()["scan"]["id"])
    assert scan["saved"] == 1 and scan["already"] == 3
    rows = list(csv.DictReader(open(folder / "metadata.csv", encoding="utf-8-sig")))
    assert len(rows) == 4


def test_save_all_counts_failures_and_rejects_bad_links(client, monkeypatch, tmp_path):
    fake_chat(monkeypatch, {5: voice_page(5)})
    monkeypatch.setattr(telegram.urllib.request, "urlopen", lambda req, timeout: FakeAudio(b"<html>nope"))
    client.application.config["TELEGRAM_SAVE_DIR"] = str(tmp_path)
    assert client.post("/telegram/voice-downloads", json={"link": "https://t.me/+secret"}).status_code == 400
    scan_id = client.post("/telegram/voice-downloads", json={"link": f"https://t.me/{CHAT}/6"}).get_json()["scan"]["id"]
    scan = wait_until_finished(scan_id)
    assert scan["failed"] == 1 and scan["saved"] == 0
    assert os.listdir(tmp_path / CHAT) == []  # no half-written files
    assert client.get(f"/telegram/voice-downloads/{scan_id}/zip").status_code == 404
