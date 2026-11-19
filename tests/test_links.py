"""Importing video links from CSV / TXT files."""
import io

import pytest

from video_to_audio import links


def test_txt_import_skips_bad_lines_and_duplicate_videos():
    text = ("https://www.youtube.com/watch?v=abc&t=5\n"
            "https://youtu.be/abc\n"
            "not a link\n\n"
            "https://www.facebook.com/reel/987\n")
    result = links.import_links(text.encode(), "links.txt")
    assert result == {"urls": ["https://www.youtube.com/watch?v=abc&t=5", "https://www.facebook.com/reel/987"],
                      "skipped": 2}


def test_csv_import_reads_the_url_column():
    data = "﻿title,url\nOne,https://youtu.be/one\nTwo,https://youtu.be/two\n".encode("utf-8")
    assert links.import_links(data, "list.csv")["urls"] == ["https://youtu.be/one", "https://youtu.be/two"]


@pytest.mark.parametrize("data, name", [
    (b"hello", "links.json"),
    (b"not a link", "links.txt"),
    (b"link\nhttps://youtu.be/a\n", "links.csv"),
    (b"\xff\xfe", "links.txt"),
    (b"a" * (links.MAX_IMPORT_BYTES + 1), "links.txt"),
], ids=["json", "no-links", "no-url-column", "not-utf8", "too-big"])
def test_bad_files_are_rejected(data, name):
    with pytest.raises(links.LinkImportError):
        links.import_links(data, name)


def test_import_endpoint(client):
    res = client.post("/links/import", data={"file": (io.BytesIO(b"https://youtu.be/a\n"), "links.txt")})
    assert res.get_json() == {"urls": ["https://youtu.be/a"], "skipped": 0}
    assert client.post("/links/import", data={}).status_code == 400
    bad = client.post("/links/import", data={"file": (io.BytesIO(b"nope"), "links.txt")})
    assert bad.status_code == 400 and "error" in bad.get_json()
