import io
import os

import pytest

from video_to_audio import cookies
from video_to_audio.config import Config

LOGGED_IN = (
    "# Netscape HTTP Cookie File\n"
    ".youtube.com\tTRUE\t/\tTRUE\t0\tVISITOR_INFO1_LIVE\tabc\n"
    "#HttpOnly_.youtube.com\tTRUE\t/\tTRUE\t4102444800\tSAPISID\txyz\n"
)
LOGGED_OUT = ".youtube.com\tTRUE\t/\tTRUE\t0\tVISITOR_INFO1_LIVE\tabc\n"
EXPIRED = ".youtube.com\tTRUE\t/\tTRUE\t1000\tLOGIN_INFO\told\n"


def test_parse():
    assert cookies.parse(LOGGED_IN) == {"youtube_cookies": 2, "logged_in": True, "expired": False}
    assert cookies.parse(LOGGED_OUT)["logged_in"] is False
    assert cookies.parse(EXPIRED)["expired"] is True
    with pytest.raises(cookies.CookieError, match="isn't a cookies.txt"):
        cookies.parse('[{"name": "SID"}]')
    with pytest.raises(cookies.CookieError, match="no youtube.com cookies"):
        cookies.parse(".google.com\tTRUE\t/\tTRUE\t0\tSID\tx\n")


def test_ydl_options_uses_a_private_copy(cookies_file):
    with cookies.ydl_options() as opts:
        assert opts == {}

    cookies.save(LOGGED_OUT.encode())
    with open(cookies_file) as f:
        assert f.readline().startswith("# Netscape HTTP Cookie File")
    with cookies.ydl_options() as opts:
        copy = opts["cookiefile"]
        assert copy != cookies_file and os.path.isfile(copy)
    assert not os.path.exists(copy)


def test_ydl_options_from_browser(monkeypatch):
    monkeypatch.setattr(Config, "COOKIES_FROM_BROWSER", "Chrome:Profile 1")
    with cookies.ydl_options() as opts:
        assert opts == {"cookiesfrombrowser": ("chrome", "Profile 1")}


def test_explain_error(cookies_file):
    assert "Upload a cookies.txt" in cookies.explain_error("Sign in to confirm you’re not a bot.")
    assert "Chrome and Edge" in cookies.explain_error("Could not copy Chrome cookie database.")
    cookies.save(LOGGED_IN.encode())
    assert "expired" in cookies.explain_error("Sign in to confirm you're not a bot.")
    assert cookies.explain_error("HTTP Error 404") is None


def test_cookie_routes(client, cookies_file):
    assert client.get("/cookies").get_json() == {"source": None}

    res = client.post("/cookies", data={"file": (io.BytesIO(b"hello"), "cookies.txt")})
    assert res.status_code == 400
    assert not os.path.exists(cookies_file)

    res = client.post("/cookies", data={"file": (io.BytesIO(LOGGED_IN.encode()), "cookies.txt")})
    assert res.status_code == 200
    assert res.get_json()["logged_in"] is True
    assert client.get("/cookies").get_json()["source"] == "file"

    assert client.delete("/cookies").get_json() == {"source": None}
    assert not os.path.exists(cookies_file)
