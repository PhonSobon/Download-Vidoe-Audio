"""Video & Audio Downloader: a small local web app that turns video links
into MP3/MP4 files and can push the MP3s to a Hugging Face dataset."""
import os

from flask import Flask

from .config import Config

__version__ = "1.0.0"


def create_app(overrides: dict | None = None) -> Flask:
    app = Flask(__name__)
    app.config.from_object(Config)
    if overrides:
        app.config.update(overrides)
    os.makedirs(app.config["DOWNLOAD_DIR"], exist_ok=True)

    from .routes import bp
    app.register_blueprint(bp)
    return app
