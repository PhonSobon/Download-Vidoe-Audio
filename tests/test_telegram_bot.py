import os

import pytest

from video_to_audio import telegram as channels, telegram_bot

TOKEN = "123456789:AAEabcdefghijklmnopqrstuvwxyz0123456"
ME = {"id": 42, "first_name": "Sok", "last_name": "Dara", "username": "sokdara"}
STRANGER = {"id": 77, "first_name": "Stranger"}


class FakeTelegram:
    def __init__(self):
        self.calls = []

    def __call__(self, token, method, wait=30, **params):
        self.calls.append((method, params))
        return True

    def replies(self):
        return [p["text"] for m, p in self.calls if m == "sendMessage"]


class ImmediateThread:
    def __init__(self, target, args=(), **kwargs):
        self.target, self.args = target, args

    def start(self):
        self.target(*self.args)


@pytest.fixture
def fake(monkeypatch):
    fake = FakeTelegram()
    monkeypatch.setattr(telegram_bot, "call", fake)
    return fake


@pytest.fixture
def bot(tmp_path, fake):
    return telegram_bot.Bot(telegram_bot.BotStore(str(tmp_path / "bot.sqlite3")), TOKEN,
                            save_root=str(tmp_path / "saved"))


@pytest.fixture
def scans(monkeypatch):
    """Record downloads instead of reading Telegram; each one finishes at once."""
    started = []

    def start_scan(options, save_root=None):
        scan_id = f"s{len(started)}"
        started.append((options, save_root))
        channels.SCANS[scan_id] = {
            "id": scan_id, "status": "done", "chat": options["chat"], "chat_title": "ដំណឹងអាហារូបករណ៍",
            "start": 500, "end": 1, "next": None, "scanned": 500, "posts": 400, "error": None,
            "voices": [{"post_id": 7}, {"post_id": 3}], "max_voices": None, "max_posts": 500,
            "cancel": False, "folder": os.path.join(save_root, options["chat"]),
            "saved": 2, "already": 1, "failed": 0}
        channels.LATEST["id"] = scan_id
        return scan_id
    monkeypatch.setattr(channels, "start_scan", start_scan)
    monkeypatch.setattr(telegram_bot.threading, "Thread", ImmediateThread)
    yield started
    channels.SCANS.clear()
    channels.LATEST["id"] = None


def text(update_id, user, body, chat_type="private"):
    return {"update_id": update_id, "message": {
        "message_id": update_id, "date": 1, "text": body, "from": {**user, "is_bot": False},
        "chat": {"id": user["id"] if chat_type == "private" else -100, "type": chat_type}}}


def test_link_downloads_every_voice_message(bot, fake, scans, tmp_path):
    bot.handle_update(text(1, ME, "/start"))
    assert "Send me a public Telegram channel" in fake.replies()[-1]
    assert bot.status()["owner"]["username"] == "sokdara"

    bot.handle_update(text(2, ME, "please get https://t.me/ScholarshipforCambodianStudents"))
    [(options, save_root)] = scans
    assert options == {"chat": "ScholarshipforCambodianStudents", "start": None,
                       "max_voices": None, "max_posts": None, "save": True}
    assert save_root == str(tmp_path / "saved")
    started, done = fake.replies()[-2:]
    assert "Downloading every voice message in t.me/ScholarshipforCambodianStudents" in started
    assert "http://127.0.0.1:5000/#telegram" in started
    assert done.startswith("✅ Done: 3 voice messages from ដំណឹងអាហារូបករណ៍")
    assert os.path.join("saved", "ScholarshipforCambodianStudents") in done


def test_one_download_at_a_time(bot, fake, scans):
    bot.handle_update(text(1, ME, "@ScholarshipforCambodianStudents"))
    channels.SCANS["s0"]["status"] = "scanning"
    bot.handle_update(text(2, ME, "https://t.me/other_channel"))
    assert len(scans) == 1 and fake.replies()[-1].startswith("⏳ Still downloading")


def test_bad_links_strangers_and_groups(bot, fake, scans):
    bot.handle_update(text(1, ME, "https://t.me/+private"))
    assert fake.replies()[-1].startswith("⚠️")
    bot.handle_update(text(2, STRANGER, "https://t.me/ScholarshipforCambodianStudents"))
    assert fake.replies()[-1] == "Sorry, this bot is private."
    count = len(fake.calls)
    bot.handle_update(text(3, ME, "https://t.me/ScholarshipforCambodianStudents", chat_type="group"))
    assert not scans and len(fake.calls) == count  # messages in groups are ignored


def test_status_never_has_the_token(bot):
    bot.store.put("bot", {"id": 1, "username": "dataaudiotestbot"})
    status = bot.status()
    assert status["enabled"] and status["username"] == "dataaudiotestbot" and TOKEN not in str(status)


def test_reset_owner_route(client):
    bot = client.application.extensions["telegram_bot"]
    bot.store.put("owner", {"id": 1, "name": "A", "username": None})
    assert client.delete("/telegram/bot/owner").get_json()["owner"] is None


def test_env_file(tmp_path, monkeypatch):
    from video_to_audio import config
    env = tmp_path / ".env"
    env.write_text('# comment\nTelegram_bot = "123:abc"\nexport PORT=8080\nEMPTY=\n', encoding="utf-8")
    for key in ("TELEGRAM_BOT_TOKEN", "EMPTY"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("PORT", "9000")  # the real environment wins
    config.load_env_file(env)
    assert os.environ["TELEGRAM_BOT_TOKEN"] == "123:abc"
    assert os.environ["PORT"] == "9000" and "EMPTY" not in os.environ
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN")
