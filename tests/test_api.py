from __future__ import annotations

from pathlib import Path

import pytest

from vh_core.api import load_batch, load_spec


def test_load_spec_toml(tmp_path: Path) -> None:
    f = tmp_path / "job.toml"
    f.write_text('job_id = "demo"\n[params]\ntext = "hi"\n[params.tts]\nspeed = 1.1\n', encoding="utf-8")
    spec = load_spec(f)
    assert spec.job_id == "demo"
    assert spec.params == {"text": "hi", "tts": {"speed": 1.1}}


def test_load_spec_json(tmp_path: Path) -> None:
    f = tmp_path / "job.json"
    f.write_text('{"job_id": "demo", "params": {"text": "hi"}}', encoding="utf-8")
    assert load_spec(f).params == {"text": "hi"}


def test_load_batch_merges_defaults(tmp_path: Path) -> None:
    f = tmp_path / "batch.toml"
    f.write_text(
        '[defaults.params]\navatar_video = "a.mp4"\ntext = "default"\n'
        '[[jobs]]\njob_id = "j1"\ntext = "one"\n'
        '[[jobs]]\njob_id = "j2"\n[jobs.params]\navatar_video = "b.mp4"\n',
        encoding="utf-8",
    )
    specs = load_batch(f)
    assert [s.job_id for s in specs] == ["j1", "j2"]
    assert specs[0].params == {"avatar_video": "a.mp4", "text": "one"}
    assert specs[1].params == {"avatar_video": "b.mp4", "text": "default"}


def test_load_batch_requires_jobs(tmp_path: Path) -> None:
    f = tmp_path / "batch.toml"
    f.write_text("[defaults.params]\n", encoding="utf-8")
    with pytest.raises(ValueError):
        load_batch(f)
