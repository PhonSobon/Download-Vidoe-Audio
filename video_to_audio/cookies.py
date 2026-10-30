"""YouTube cookies. YouTube sometimes answers "Sign in to confirm you're not
a bot"; the cookies of a logged-in browser session get past that. They come
from a cookies.txt the user uploads on the page (saved at Config.COOKIES_FILE)
or, failing that, straight from a browser (Config.COOKIES_FROM_BROWSER)."""
import os
import shutil
import tempfile
import time
from contextlib import contextmanager

from .config import Config

MAX_SIZE = 1024 * 1024

# Cookies YouTube only sets for a signed-in account. A cookies.txt exported
# while logged out has the youtube.com visitor cookies but none of these.
LOGIN_COOKIES = {"LOGIN_INFO", "SAPISID", "__Secure-1PSID", "__Secure-3PSID",
                 "__Secure-1PAPISID", "__Secure-3PAPISID"}


class CookieError(ValueError):
    pass


def parse(text: str) -> dict:
    """Check that text is a Netscape-format cookies.txt and summarize the
    YouTube cookies in it. Raises CookieError if it can't be used at all."""
    youtube, login, expired_login = 0, set(), set()
    now = time.time()
    for line in text.splitlines():
        line = line.strip()
        # Browser extensions mark HttpOnly cookies with this prefix; the rest
        # of the line is a normal cookie.
        if line.startswith("#HttpOnly_"):
            line = line[len("#HttpOnly_"):]
        if not line or line.startswith("#"):
            continue
        fields = line.split("\t")
        if len(fields) != 7:
            continue
        domain, _, _, _, expires, name, _ = fields
        if not domain.lstrip(".").endswith("youtube.com"):
            continue
        youtube += 1
        if name in LOGIN_COOKIES:
            # 0 means a session cookie, which never expires on its own.
            if expires.isdigit() and 0 < int(expires) < now:
                expired_login.add(name)
            else:
                login.add(name)

    if not youtube:
        if "\t" not in text:
            raise CookieError("This isn't a cookies.txt file. Export one with a browser extension "
                              "like \"Get cookies.txt LOCALLY\" (Netscape format).")
        raise CookieError("This cookies.txt has no youtube.com cookies. Open youtube.com, "
                          "log in, then export the cookies while on that tab.")
    return {"youtube_cookies": youtube, "logged_in": bool(login),
            "expired": bool(expired_login) and not login}


def save(data: bytes) -> dict:
    if len(data) > MAX_SIZE:
        raise CookieError("That file is too big to be a cookies.txt.")
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise CookieError("This isn't a cookies.txt file (it isn't plain text).")
    info = parse(text)
    path = Config.COOKIES_FILE
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    # yt-dlp wants the Netscape header line; some extensions leave it out.
    if not text.lstrip().startswith("# Netscape HTTP Cookie File"):
        text = "# Netscape HTTP Cookie File\n" + text
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    return status()


def delete():
    if os.path.isfile(Config.COOKIES_FILE):
        os.remove(Config.COOKIES_FILE)


def status() -> dict:
    """What the page shows in its YouTube sign-in panel."""
    path = Config.COOKIES_FILE
    if os.path.isfile(path):
        result = {"source": "file", "updated": time.strftime(
            "%Y-%m-%d %H:%M", time.localtime(os.path.getmtime(path)))}
        try:
            with open(path, encoding="utf-8-sig") as f:
                result.update(parse(f.read()))
        except (OSError, UnicodeDecodeError, CookieError) as e:
            result.update({"logged_in": False, "error": str(e)})
        return result
    if Config.COOKIES_FROM_BROWSER:
        return {"source": "browser", "browser": Config.COOKIES_FROM_BROWSER}
    return {"source": None}


@contextmanager
def ydl_options():
    """The yt-dlp cookie options for one download, as a dict to merge into
    its options. yt-dlp writes cookies back to its cookiefile when it
    finishes, which would clobber the user's file (and parallel downloads
    would race on it), so each download gets its own temporary copy."""
    path = Config.COOKIES_FILE
    if os.path.isfile(path):
        fd, copy = tempfile.mkstemp(prefix="yt-cookies-", suffix=".txt")
        os.close(fd)
        try:
            shutil.copyfile(path, copy)
            yield {"cookiefile": copy}
        finally:
            try:
                os.remove(copy)
            except OSError:
                pass
    elif Config.COOKIES_FROM_BROWSER:
        browser, _, profile = Config.COOKIES_FROM_BROWSER.partition(":")
        yield {"cookiesfrombrowser": (browser.strip().lower(), profile.strip() or None)}
    else:
        yield {}


def explain_error(msg: str) -> str | None:
    """A hint to append to a yt-dlp error that's about cookies, else None."""
    low = msg.lower()
    if "could not copy chrome cookie database" in low or "dpapi" in low or ("cookie" in low and "decrypt" in low):
        return ("Chrome and Edge lock and encrypt their cookies on Windows, so they can't be read "
                "directly. Upload a cookies.txt in the YouTube sign-in panel instead.")
    if "not a bot" in low or "sign in to confirm" in low:
        state = status()
        if state["source"] == "file":
            return ("YouTube rejected your cookies.txt -- it has expired or isn't logged in. "
                    "Export a fresh one from a private/Incognito window and upload it again "
                    "in the YouTube sign-in panel.")
        return ("YouTube wants a logged-in session. Upload a cookies.txt in the "
                "YouTube sign-in panel, then press Retry.")
    return None
