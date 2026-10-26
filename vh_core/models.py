"""内核数据模型：JobSpec / StageResult / JobManifest。

所有跨 stage、跨壳(CLI/服务端)传递的数据都用这里的 pydantic 模型，
保证 `--json` 输出与 HTTP 响应结构一致。
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator

# 全链路环节顺序(CLI/服务端/前端共用的唯一来源)
STAGE_NAMES: tuple[str, ...] = ("download", "transcribe", "rewrite", "tts", "avatar", "postprocess", "publish")


# job_id 直接用作 workspace 子目录名:只允许安全字符,杜绝 "../" 路径穿越
_JOB_ID_RE = re.compile(r"\w[\w\-]{0,127}")


def validate_job_id(job_id: str) -> str:
    if not isinstance(job_id, str) or not _JOB_ID_RE.fullmatch(job_id):
        raise ValueError(f"非法 job_id: {job_id!r}(仅允许字母数字下划线连字符,不能以连字符开头,最长 128)")
    return job_id


def _utcnow() -> datetime:
    return datetime.now(UTC)


class StageStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"  # 本 job 不适用的 stage(如无 share_url 时的 download)


class JobSpec(BaseModel):
    """一次任务的规格：job_id + 传给各 stage 的参数。"""

    job_id: str
    params: dict[str, Any] = Field(default_factory=dict)

    @field_validator("job_id")
    @classmethod
    def _check_job_id(cls, v: str) -> str:
        return validate_job_id(v)


class StageResult(BaseModel):
    """单个 stage 的执行结果，产物以相对 job 目录的路径登记。"""

    name: str
    status: StageStatus
    outputs: dict[str, str] = Field(default_factory=dict)
    error: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None

    @property
    def duration_s(self) -> float | None:
        if self.started_at and self.finished_at:
            return (self.finished_at - self.started_at).total_seconds()
        return None


class JobManifest(BaseModel):
    """任务清单：落在 workspace/{job_id}/manifest.json，是断点续跑的判断依据。"""

    job_id: str
    created_at: datetime = Field(default_factory=_utcnow)
    stages: dict[str, StageResult] = Field(default_factory=dict)

    def is_done(self, stage_name: str) -> bool:
        r = self.stages.get(stage_name)
        if r is None:
            return False
        if r.status == StageStatus.SUCCESS:
            return True
        # 兼容旧版 manifest:曾把续跑跳过的已完成 stage 改写成 SKIPPED,
        # 这类记录有 started_at(真正执行过);"本 job 不适用"的 SKIPPED 没有
        return r.status == StageStatus.SKIPPED and r.started_at is not None and bool(r.outputs)
