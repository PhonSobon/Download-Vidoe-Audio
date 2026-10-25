"""Start the local server: `python -m video_to_audio` (or `python run.py`)."""
from . import create_app


def main():
    app = create_app()
    host, port = app.config["HOST"], app.config["PORT"]
    print(f"Starting server at http://{host}:{port}")
    app.run(host=host, port=port, debug=False)


if __name__ == "__main__":
    main()
