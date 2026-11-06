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

- Python 3.10+ installed
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
python run.py
```

(`python -m video_to_audio` does the same.) Then open your browser to:
**http://127.0.0.1:5000**

Paste one or more links (one per line), pick MP3 or MP4, and click
**Download**. Each link appears in the queue with its own progress bar.
Click **Save** on any finished item, or **Download all (.zip)** to grab the
whole batch as one file.

**Whole playlist:** paste the playlist link (`youtube.com/playlist?list=...`,
or a video opened from a playlist, `watch?v=...&list=...`). Every video in
it is added to the queue. Deleted/private videos are skipped. YouTube "Mix"
links (`list=RD...`) are endless auto-playlists, so only that one video is
downloaded.

**Whole channel:** paste a channel link, e.g.
`youtube.com/@kimsunofficial02/videos` (also `/shorts`, `/streams`, or just
`youtube.com/@name`, which reads its Videos tab). Its videos are added to the
queue newest first, like a playlist.

**Long playlists and channels:** "From each playlist, channel or TikTok
profile" sets how many videos to take from each link: the first 10, 20
(default), 30, 50, 100, 200, 500, 1000, all, or a custom number. Afterwards
the queue offers buttons to add +10, +50, +100, +500 more, or all the rest.

Up to 3 links download at once; the rest wait their turn.

**TikTok photo posts:** paste the original `/photo/...` link and select
**MP3 audio** to download its soundtrack. The app automatically uses the
matching video URL for audio extraction and keeps the original photo link
in the source metadata. Downloading the photos or generating an MP4
slideshow from them is not supported.

### Collect TikTok links from creators

Use the **TikTok link collector** at the top of the page:

1. Enter up to 10 creator profile links, one per line, such as
   `https://www.tiktok.com/@creator`.
2. Choose how many posts to inspect per creator (1–500, default 50).
   Optionally add a topic label, date range, and comma-separated caption
   keywords. A post matches if its available caption contains **any** keyword.
3. Click **Collect links**. Collection runs in the background; the page shows
   progress, a preview, and any creator-specific errors. **Cancel collection**
   stops after the current request; already collected links stay available.
4. Click **Export CSV** to save `tiktok_links.csv`, or **Add links to MP3
   downloads**, then **Download as MP3** in the downloader below.

The inspection limit applies **before filtering**, so the number of collected
links may be smaller. Date filters include both boundary dates and omit posts
whose date is unavailable. Topic labels are notes, not an automatic topic
classifier. Country and spoken language are not inferred; listen to the audio
to check that it contains Khmer speech. This feature collects from selected
creators; it does not search TikTok by category or keyword.

Collections are saved in `data/collections.sqlite3` (ignored by git) and can be
reopened from **Saved collections** after restarting. Interrupted runs retain
their collected links; select the saved run and collect again to retry its
profiles. One collection runs at a time. TikTok can block or restrict profile
listing even when individual posts work; errors are shown without discarding
results from other creators. Updating `yt-dlp` may help when TikTok changes.
The requirements include yt-dlp's `curl-cffi` extra for sites that need its
browser-compatible networking support.

The UTF-8 CSV contains `url`, `post_id`, `creator`, `title`, `upload_date`,
`duration_seconds`, and `topic`. Duplicate post IDs are removed within each
collection. Durations are source metadata, not measured Khmer speech hours.

### Import a saved link list

Click **Import links from CSV / TXT** in the downloader. CSV needs a `url`
column; TXT needs one individual post URL per line. Up to 10,000 rows / 2 MB
can be imported at a time. Blank rows are ignored and invalid or duplicate
links are skipped. TikTok photo/video links with the same post ID count as
duplicates. Imported links are added to the pending list with MP3 selected;
click **Download as MP3** to start.

Only collected links are stored permanently. Audio downloads and the audio
queue still follow the existing temporary/session behavior: save completed
MP3s or ZIPs before they expire or you restart the server. A fixed worker
pool handles imported batches without creating one thread per link.

### Settings

Each setting in [video_to_audio/config.py](video_to_audio/config.py) can be
changed with an environment variable of the same name before starting the
app:

| Variable | Default | Meaning |
| --- | --- | --- |
| `MAX_PARALLEL_DOWNLOADS` | `3` | Links that download at the same time |
| `DOWNLOAD_DIR` | `video_to_audio` in the system temp folder | Working folder for files waiting to be saved; its contents are deleted automatically |
| `COLLECTION_DB` | `data/collections.sqlite3` in this project | Persistent creator collection history and collected post links |
| `FILE_LIFETIME_MINUTES` | `60` | How long a finished file stays available on the page before it's deleted |
| `HOST` / `PORT` | `127.0.0.1` / `5000` | Address the server listens on |
| `HF_TOKEN` | not set | Hugging Face token used when the HF_TOKEN box is left blank |
| `COOKIES_FILE` | `cookies.txt` in this folder | Where the YouTube cookies uploaded on the page are kept |
| `COOKIES_FROM_BROWSER` | not set | Browser to read YouTube cookies from while there's no `cookies.txt` (Firefox only on Windows), e.g. `firefox` |

For example, on Windows PowerShell: `$env:PORT = "8080"; python run.py`

**"Sign in to confirm you're not a bot":** YouTube sometimes blocks
downloads until it sees a logged-in session. Fix it in the **YouTube
sign-in** panel on the page, no restart needed:

1. Install the **Get cookies.txt LOCALLY** extension in Chrome or Edge.
2. Open a **private/Incognito** window (allow the extension there) and log in
   at youtube.com.
3. Click the extension, then **Export**, and close that window right away.
   If you keep browsing in it, YouTube swaps the cookies and the exported
   ones stop working.
4. Click **Upload cookies.txt** on the page, then **Retry failed**.

The panel tells you if the file isn't logged in or has expired. When
downloads start failing again later, export and upload a fresh file. The
file stays on your computer (`cookies.txt` here, ignored by git). Downloading a
lot with your account can get it rate-limited, so consider a spare account.

Chrome and Edge can't be read directly on Windows: they lock and encrypt
their cookies ("Could not copy Chrome cookie database" / "Failed to decrypt
with DPAPI"). That's why the page uses an uploaded file. Firefox users can
use `$env:COOKIES_FROM_BROWSER = "firefox"; python run.py` instead.

## 4. Push audio to Hugging Face (optional)

After your MP3s finish, the **Push to Hugging Face** panel uploads them to a
Hugging Face **dataset** as **Parquet**. Each push adds a **new** Parquet
file, and files from earlier pushes are never replaced. Each clip also gets
a row in the **References** table in the dataset README, linking back to the
YouTube video or web page it came from.

1. Create an access token with **Write** permission at
   https://huggingface.co/settings/tokens
2. In the panel, enter the dataset name (e.g. `your-username/my-audio`, a
   new or an existing one) and paste the token into the **HF_TOKEN** box.
   A token is required for private datasets. You can leave the box blank
   only if you've already run `hf auth login` or set the `HF_TOKEN`
   environment variable before starting the app.
3. Tick **Private dataset** to create a new dataset as private (an existing
   dataset keeps its current visibility).
4. Click **Push N MP3s to Hugging Face**.

What the dataset looks like after three pushes:

```
README.md                                  ← References table grows each push
data/
  train-20261025-093000-a1b2c3.parquet     ← push 1
  train-20261026-141500-d4e5f6.parquet     ← push 2
  train-20261101-080000-0789ab.parquet     ← push 3
```

Columns in every Parquet file:

| Column | Example |
| --- | --- |
| `audio` | the MP3 (plays in the Hugging Face dataset viewer) |
| `title` | `Rick Astley - Never Gonna Give You Up (Official Video)` |
| `source_url` | `https://www.youtube.com/watch?v=dQw4w9WgXcQ` |
| `platform` | `Youtube` (for an ordinary website: its extractor name, or `Generic`) |
| `channel` / `channel_url` | `Rick Astley` / `https://www.youtube.com/channel/UCuAXFkgsw1L7xaCfnd5JJOw` |
| `upload_date` | `20091025` |
| `duration_seconds` | `213.0` |
| `clip_minutes` | empty, or `5.0` if you used "Only first N minutes" |
| `reference` | `Rick Astley - Never Gonna Give You Up (Official Video) by Rick Astley (Youtube). https://www.youtube.com/watch?v=dQw4w9WgXcQ` |
| `downloaded_at` | `2026-10-25T09:30:00+00:00` |

Example References section added to the dataset README:

```markdown
## References

Original source of every clip in this dataset.

<!-- references:start -->
| Title | Source | Platform | Channel | Parquet file |
| --- | --- | --- | --- | --- |
| Rick Astley - Never Gonna Give You Up (Official Video) | [https://www.youtube.com/watch?v=dQw4w9WgXcQ](https://www.youtube.com/watch?v=dQw4w9WgXcQ) | Youtube | Rick Astley | `data/train-20261025-093000-a1b2c3.parquet` |
| My page audio | [https://example.com/talk](https://example.com/talk) | Generic |  | `data/train-20261026-141500-d4e5f6.parquet` |
<!-- references:end -->
```

If the dataset already has a README, your text is kept: the References
section is added at the end the first time, and later pushes only add rows
between the two `references` markers. For a new dataset the app also writes
a README that tells Hugging Face to load `data/train-*.parquet`.

Load every push together in Python (`pip install datasets torchcodec`):

```python
from datasets import load_dataset

ds = load_dataset("your-username/my-audio", split="train")
row = ds[0]
print(row["title"], row["source_url"])
print(row["audio"]["sampling_rate"])
```

> If you push into a dataset that already has its own Parquet files in
> `data/`, its columns should match the ones above, otherwise Hugging Face
> can't load the old and new files as one table.

## Project structure

```
Convert-video-to-audio/
├── run.py                    # start the app: python run.py
├── pyproject.toml            # package metadata and dependencies
├── requirements.txt          # runtime dependencies
├── requirements-dev.txt      # + test dependencies
├── video_to_audio/           # the application package
│   ├── __init__.py           # create_app() application factory
│   ├── __main__.py           # python -m video_to_audio
│   ├── config.py             # settings (overridable by environment variables)
│   ├── downloader.py         # yt-dlp download jobs and playlist expansion
│   ├── collector.py          # creator link collection, SQLite history, CSV import/export
│   ├── huggingface.py        # Parquet build + push to a Hugging Face dataset
│   ├── routes.py             # HTTP endpoints
│   ├── templates/index.html  # the page
│   └── static/
│       ├── css/style.css
│       └── js/app.js
└── tests/                    # pytest tests (no network needed)
```

## Development

```bash
pip install -r requirements-dev.txt
python -m pytest
```

The tests use a fake Hugging Face repo, so they never upload anything.

## Notes

- Files are not kept in this project. While they wait for you to press
  **Save**, they sit in a temporary folder (see `DOWNLOAD_DIR`) and are
  deleted 60 minutes after they're ready (`FILE_LIFETIME_MINUTES`), and
  whenever the app restarts. Save what you need before then; if a file is
  gone, press **Retry** on its row to download it again.
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
  then restart `python run.py` and try again. The app already applies a
  fallback client mode that resolves most 403 errors as long as yt-dlp
  itself is current.
- If yt-dlp stops working over time in general, the same update command
  above usually fixes it.
