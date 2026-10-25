"""Hugging Face pushes against an in-memory fake repo (no network)."""
import os

import pyarrow.parquet as pq
import pytest

from video_to_audio import huggingface


@pytest.fixture
def fake_repo(monkeypatch, tmp_path):
    """Path -> bytes. Starts with a hand-written README, like an existing dataset."""
    repo = {"README.md": "# My existing dataset\n\nHand-written intro.\n".encode()}

    class FakeApi:
        def __init__(self, token=None):
            pass

        def create_repo(self, repo_id, **kw):
            pass

        def file_exists(self, repo_id, name, **kw):
            return name in repo

        def create_commit(self, repo_id, ops, **kw):
            for op in ops:
                assert op.path_in_repo == "README.md" or op.path_in_repo not in repo, \
                    f"push would replace {op.path_in_repo}"
                repo[op.path_in_repo] = op.path_or_fileobj

    def fake_download(repo_id, name, **kw):
        path = tmp_path / name
        path.write_bytes(repo[name])
        return str(path)

    monkeypatch.setattr(huggingface, "HfApi", FakeApi)
    monkeypatch.setattr(huggingface, "hf_hub_download", fake_download)
    return repo


def test_pushes_add_parquet_and_references(client, make_job, fake_repo, tmp_path):
    make_job("j1", title="សួស្តី | Khmer", video_id="abc")
    make_job("j2", title="Web talk", url="https://example.com/talk", platform="Generic",
             channel=None, video_id="talk")

    first = client.post("/push_hf", json={"repo_id": "me/ds", "ids": ["j1"]}).get_json()
    second = client.post("/push_hf", json={"repo_id": "me/ds", "ids": ["j2"]}).get_json()

    parquet_files = sorted(k for k in fake_repo if k.endswith(".parquet"))
    assert parquet_files == sorted([first["parquet"], second["parquet"]])

    readme = fake_repo["README.md"].decode("utf-8")
    assert readme.startswith("# My existing dataset\n\nHand-written intro.")
    assert readme.count(huggingface.REFS_START) == 1
    assert "| សួស្តី \\| Khmer | [https://www.youtube.com/watch?v=abc]" in readme
    assert f"| Web talk | [https://example.com/talk](https://example.com/talk) | Generic |  | `{second['parquet']}` |" in readme

    path = tmp_path / "first.parquet"
    path.write_bytes(fake_repo[first["parquet"]])
    table = pq.read_table(path)
    assert table.column_names == ["audio"] + huggingface.HF_COLUMNS
    row = table.to_pylist()[0]
    assert row["audio"] == {"bytes": b"ID3fake-j1", "path": "youtube-abc.mp3"}
    assert row["reference"] == "សួស្តី | Khmer by Chan (Youtube). https://www.youtube.com/watch?v=abc"


def test_new_repo_gets_dataset_card(make_job, fake_repo, download_dir):
    del fake_repo["README.md"]
    job = make_job("j1")
    huggingface.push_to_hub("me/new", [job], download_dir)
    readme = fake_repo["README.md"].decode("utf-8")
    assert "path: data/train-*.parquet" in readme
    assert huggingface.REFS_START in readme


def test_parquet_loads_as_audio_dataset(make_job, download_dir, tmp_path):
    datasets = pytest.importorskip("datasets")
    path = tmp_path / "train.parquet"
    path.write_bytes(huggingface.build_parquet([make_job("j1")], download_dir))
    ds = datasets.load_dataset("parquet", data_files=str(path), split="train")
    assert isinstance(ds.features["audio"], datasets.Audio)


def test_file_name_keeps_id_case_and_clip(make_job):
    job = make_job("j1", video_id="aBc_D", clip_minutes=2.5)
    assert huggingface.hf_file_name(job) == "youtube-aBc_D-first2.5min.mp3"


def test_auth_errors_get_a_hint(make_job, download_dir, monkeypatch):
    class DeniedApi:
        def __init__(self, token=None):
            pass

        def create_repo(self, *a, **kw):
            raise RuntimeError("401 Client Error: Unauthorized")

    monkeypatch.setattr(huggingface, "HfApi", DeniedApi)
    with pytest.raises(huggingface.HfPushError, match="HF_TOKEN"):
        huggingface.push_to_hub("me/ds", [make_job("j1")], download_dir)
