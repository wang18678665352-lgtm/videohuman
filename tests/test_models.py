from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from vh_core.models import JobManifest, JobSpec, StageResult, StageStatus, validate_job_id


@pytest.mark.parametrize("job_id", ["demo1", "job-20260101-120000", "a_b-c", "任务1"])
def test_valid_job_ids(job_id: str) -> None:
    assert validate_job_id(job_id) == job_id
    assert JobSpec(job_id=job_id).job_id == job_id


@pytest.mark.parametrize("job_id", ["", "../etc", "a/b", "a\\b", ".hidden", "-x", "a b", "x" * 200])
def test_invalid_job_ids(job_id: str) -> None:
    with pytest.raises(ValueError):
        validate_job_id(job_id)
    with pytest.raises(ValidationError):
        JobSpec(job_id=job_id)


def test_is_done_success_and_legacy_skipped() -> None:
    now = datetime.now(UTC)
    m = JobManifest(
        job_id="j",
        stages={
            "tts": StageResult(name="tts", status=StageStatus.SUCCESS, outputs={"speech": "tts/speech.wav"}),
            # 旧版本把续跑跳过的已完成 stage 写成 SKIPPED:有 started_at 与产物,应视为已完成
            "avatar": StageResult(
                name="avatar", status=StageStatus.SKIPPED, outputs={"avatar": "a.mp4"}, started_at=now, finished_at=now
            ),
            # "本 job 不适用"的 SKIPPED:没执行过,不算完成(下次仍需重新判断 enabled)
            "download": StageResult(name="download", status=StageStatus.SKIPPED),
            "rewrite": StageResult(name="rewrite", status=StageStatus.FAILED, error="x"),
        },
    )
    assert m.is_done("tts")
    assert m.is_done("avatar")
    assert not m.is_done("download")
    assert not m.is_done("rewrite")
    assert not m.is_done("publish")
