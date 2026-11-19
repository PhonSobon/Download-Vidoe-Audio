"""Read video links from an uploaded CSV (with a url column) or TXT file."""
import csv
import io
from pathlib import Path
from urllib.parse import urlsplit

from .downloader import video_key

MAX_IMPORT_BYTES = 2 * 1024 * 1024
MAX_IMPORT_LINKS = 10000


class LinkImportError(ValueError):
    pass


def import_links(data: bytes, filename: str) -> dict:
    """Return {"urls": [...], "skipped": n}. Links to the same video count once."""
    if len(data) > MAX_IMPORT_BYTES:
        raise LinkImportError("Choose a CSV or TXT file smaller than 2 MB.")
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise LinkImportError("Save the file as UTF-8 CSV or TXT.")
    suffix = Path(filename).suffix.lower()
    try:
        if suffix == ".csv":
            reader = csv.DictReader(io.StringIO(text))
            if not reader.fieldnames or "url" not in reader.fieldnames:
                raise LinkImportError("The CSV needs a column named url.")
            candidates = (row.get("url", "") for row in reader)
        elif suffix == ".txt":
            candidates = text.splitlines()
        else:
            raise LinkImportError("Choose a .csv or .txt file.")
        urls, seen, skipped = [], set(), 0
        for index, value in enumerate(candidates):
            if index >= MAX_IMPORT_LINKS:
                raise LinkImportError("Import at most 10,000 links at a time.")
            url = (value or "").strip()
            if not url:
                continue
            try:
                parts = urlsplit(url)
                if parts.scheme not in {"http", "https"} or not parts.hostname or any(c.isspace() for c in url):
                    raise ValueError
            except ValueError:
                skipped += 1
                continue
            key = video_key(url)
            if key in seen:
                skipped += 1
                continue
            seen.add(key)
            urls.append(url)
    except csv.Error:
        raise LinkImportError("The CSV could not be read. Save it again with a url column.")
    if not urls:
        raise LinkImportError("The file has no valid video links. Put one link per line or row.")
    return {"urls": urls, "skipped": skipped}
