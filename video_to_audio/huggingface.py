"""Push finished MP3s to a Hugging Face *dataset* repo as Parquet.

Every push adds one new data/train-<time>-<id>.parquet (audio bytes + a
reference back to the YouTube video / web page each clip came from) and
never touches the Parquet files already there. The repo README gets a
References table that grows by one row per clip.
`datasets.load_dataset("<user>/<repo>")` then loads all pushes together as
one "train" split.
"""
import json
import os
import re
import threading
import uuid
from datetime import datetime, timezone

import pyarrow as pa
import pyarrow.parquet as pq
from huggingface_hub import HfApi, CommitOperationAdd, hf_hub_download
from werkzeug.utils import secure_filename

HF_REPO_RE = re.compile(r"^[A-Za-z0-9][\w.-]*/[\w.-]+$")
HF_DATA_DIR = "data"
# Serialises pushes so two at once can't overwrite each other's README rows.
HF_LOCK = threading.Lock()

# Column order of every Parquet file. The "huggingface" schema metadata
# tells `datasets` and the Hub viewer that "audio" is playable audio.
HF_COLUMNS = ["title", "source_url", "platform", "channel", "channel_url", "upload_date",
              "duration_seconds", "clip_minutes", "reference", "downloaded_at"]
HF_SCHEMA = pa.schema(
    [pa.field("audio", pa.struct([pa.field("bytes", pa.binary()), pa.field("path", pa.string())]))]
    + [pa.field(c, pa.float64() if c in ("duration_seconds", "clip_minutes") else pa.string())
       for c in HF_COLUMNS],
    metadata={"huggingface": json.dumps({"info": {"features": {
        "audio": {"_type": "Audio"},
        **{c: {"dtype": "float64" if c in ("duration_seconds", "clip_minutes") else "string",
               "_type": "Value"} for c in HF_COLUMNS},
    }}})},
)

# The References table lives between these markers so later pushes can add
# rows without disturbing anything else written in the README.
REFS_START = "<!-- references:start -->"
REFS_END = "<!-- references:end -->"
REFS_HEADER = "| Title | Source | Platform | Channel | Parquet file |\n| --- | --- | --- | --- | --- |\n"

DATASET_CARD = """---
configs:
- config_name: default
  data_files:
  - split: train
    path: data/train-*.parquet
---

# Audio collection

Audio clips converted to MP3 with the Video to MP3 / MP4 Converter, stored
as Parquet in `data/`. Each push adds a new Parquet file; earlier ones are
kept.

| Column | Meaning |
| --- | --- |
| `audio` | The MP3 (plays in the dataset viewer) |
| `title` | Title of the original video or page |
| `source_url` | Link to the original video or page |
| `platform` | Site it came from (e.g. Youtube, Facebook, Generic) |
| `channel` / `channel_url` | Uploader or channel, when the site reports one |
| `upload_date` | Original upload date (YYYYMMDD), when known |
| `duration_seconds` | Length of the original video |
| `clip_minutes` | Set when only the first N minutes were kept |
| `reference` | Ready-to-paste citation |
| `downloaded_at` | When the audio was downloaded (UTC) |

Load it:

```python
from datasets import load_dataset

ds = load_dataset("<user>/<repo>", split="train")
print(ds[0]["title"], ds[0]["source_url"])
```

Check that you have the rights to the original content before you share this dataset.
"""


class HfPushError(Exception):
    """A push failed; the message is ready to show to the user."""


def is_valid_repo_id(repo_id: str) -> bool:
    return bool(HF_REPO_RE.match(repo_id))


def hf_file_name(job: dict) -> str:
    """Named by platform + video id rather than title: titles may be
    non-Latin (secure_filename strips those to nothing)."""
    src = job.get("source") or {}
    # Ids stay case-sensitive: YouTube's abc and ABC are different videos.
    base = f"{(src.get('platform') or 'audio').lower()}-{src.get('id') or os.path.splitext(job['filename'])[0]}"
    if src.get("clip_minutes"):
        base += f"-first{src['clip_minutes']:g}min"
    return (secure_filename(base) or "audio") + ".mp3"


def hf_row(job: dict) -> dict:
    src = job.get("source") or {}
    title = job.get("title")
    source_url = src.get("url") or job.get("url")
    by = f" by {src['channel']}" if src.get("channel") else ""
    platform = f" ({src['platform']})" if src.get("platform") else ""
    return {
        "title": title,
        "source_url": source_url,
        "platform": src.get("platform"),
        "channel": src.get("channel"),
        "channel_url": src.get("channel_url"),
        "upload_date": src.get("upload_date"),
        "duration_seconds": src.get("duration"),
        "clip_minutes": src.get("clip_minutes"),
        "reference": f"{title}{by}{platform}. {source_url}",
        "downloaded_at": job.get("finished_at"),
    }


def build_parquet(jobs: list, download_dir: str) -> bytes:
    rows = []
    for job in jobs:
        with open(os.path.join(download_dir, job["filename"]), "rb") as f:
            audio = {"bytes": f.read(), "path": hf_file_name(job)}
        rows.append({"audio": audio, **hf_row(job)})
    table = pa.Table.from_pylist(rows, schema=HF_SCHEMA)
    buf = pa.BufferOutputStream()
    # Small row groups keep the Hub viewer responsive with multi-MB audio rows.
    pq.write_table(table, buf, row_group_size=20)
    return buf.getvalue().to_pybytes()


def _md_cell(value) -> str:
    return str(value or "").replace("|", "\\|").replace("\n", " ")


def readme_with_refs(readme: str, rows: list, parquet_path: str) -> str:
    """Append one References row per clip, creating the table if needed."""
    lines = "".join(
        f"| {_md_cell(r['title'])} | [{_md_cell(r['source_url'])}]({r['source_url']}) "
        f"| {_md_cell(r['platform'])} | {_md_cell(r['channel'])} | `{parquet_path}` |\n"
        for r in rows
    )
    if REFS_START in readme and REFS_END in readme:
        head, rest = readme.split(REFS_START, 1)
        table, tail = rest.split(REFS_END, 1)
        table = table.strip("\n") + "\n" if table.strip() else REFS_HEADER
        return f"{head}{REFS_START}\n{table}{lines}{REFS_END}{tail}"
    return (readme.rstrip("\n") + "\n\n## References\n\n"
            f"Original source of every clip in this dataset.\n\n{REFS_START}\n{REFS_HEADER}{lines}{REFS_END}\n")


def push_to_hub(repo_id: str, jobs: list, download_dir: str,
                token: str | None = None, private: bool = False) -> str:
    """Upload finished MP3 jobs as one new Parquet file and return its path
    in the repo. With no token, uses HF_TOKEN or the login saved by
    `hf auth login`. Raises HfPushError with a user-facing message."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    parquet_path = f"{HF_DATA_DIR}/train-{stamp}-{uuid.uuid4().hex[:6]}.parquet"
    api = HfApi(token=token)
    try:
        parquet = build_parquet(jobs, download_dir)
        with HF_LOCK:
            api.create_repo(repo_id, repo_type="dataset", private=private, exist_ok=True)

            readme = DATASET_CARD
            if api.file_exists(repo_id, "README.md", repo_type="dataset"):
                path = hf_hub_download(repo_id, "README.md", repo_type="dataset",
                                       token=token, force_download=True)
                with open(path, encoding="utf-8") as f:
                    readme = f.read()
            readme = readme_with_refs(readme, [hf_row(j) for j in jobs], parquet_path)

            api.create_commit(
                repo_id,
                [CommitOperationAdd(parquet_path, parquet),
                 CommitOperationAdd("README.md", readme.encode("utf-8"))],
                repo_type="dataset",
                commit_message=f"Add {len(jobs)} audio file(s) as {parquet_path}",
            )
    except Exception as e:
        msg = str(e)
        low = msg.lower()
        if "401" in msg or "token" in low or "unauthorized" in low:
            msg += " (Paste your HF_TOKEN -- a Hugging Face access token with write permission: https://huggingface.co/settings/tokens)"
        elif "403" in msg:
            msg += " (This token can't write to that repo. Use a write token, or a repo under your own username.)"
        raise HfPushError(f"Hugging Face upload failed: {msg}") from e
    return parquet_path
