# Video to MP3 / MP4 Converter (runs on your computer)

A tiny local web app: paste a YouTube video link, choose **MP3 audio** or
**MP4 video**, then click the matching download button. The completed video
download is delivered with the video's title as its filename.

Works with YouTube and hundreds of other sites supported by `yt-dlp`,
including Facebook, Instagram, TikTok, X/Twitter, Vimeo, Reddit, and
SoundCloud. Full supported-site list:
https://github.com/yt-dlp/yt-dlp/blob/master/supportedsites.md

⚠️ Only download videos you have the right to (your own content, Creative
Commons, or content the owner allows). Downloading copyrighted or private
content without permission can violate that platform's Terms of Service.

⚠️ Private, friends-only, or login-required posts (common on Facebook and
Instagram) generally **cannot** be downloaded this way — yt-dlp only works
on content that's publicly viewable without logging in.

## 1. Requirements

- Python 3.9+ installed
- **ffmpeg** installed and available on your PATH (needed to convert to MP3
  and to merge video+audio for MP4)

### Install ffmpeg
- **Windows**: Download from https://www.gyan.dev/ffmpeg/builds/ (get the
  "essentials" build), unzip it, and add the `bin` folder to your PATH.
  Or, if you have it: `winget install ffmpeg`
- **Mac**: `brew install ffmpeg`
- **Linux (Debian/Ubuntu)**: `sudo apt install ffmpeg`

Check it worked by running `ffmpeg -version` in a terminal.

## 2. Install the app

Open a terminal in this folder and run:

```bash
python -m venv venv
# Windows:
venv\Scripts\activate
# Mac/Linux:
source venv/bin/activate

pip install -r requirements.txt
```

## 3. Run it

```bash
python app.py
```

Then open your browser to: **http://127.0.0.1:5000**

Paste a YouTube link, pick MP3 or MP4, click Generate, wait for it to
finish, then click the download link that appears.

## Notes

- Downloaded files are temporarily stored in the `downloads/` folder inside
  this project. You can delete them any time.
- Long videos take longer to convert — the page will show "Converting..."
  until it's ready.
- **"Only download first N minutes"** field: leave it blank to get the full
  video, or enter a number (e.g. `5`) to only pull the first 5 minutes of a
  long video — useful for a 3-hour video when you only need the intro.
- If you get **"HTTP Error 403: Forbidden"**, it almost always means your
  local `yt-dlp` is out of date (YouTube changes things often and yt-dlp
  ships fixes frequently). Fix it with:
  ```
  pip install -U yt-dlp
  ```
  then restart `python app.py` and try again. The app already applies a
  fallback client mode that resolves most 403 errors as long as yt-dlp
  itself is current.
- If yt-dlp stops working over time in general, the same update command
  above usually fixes it.
