"""内核数据模型：JobSpec / StageResult / JobManifest。

所有跨 stage、跨壳(CLI/服务端)传递的数据都用这里的 pydantic 模型，
保证 `--json` 输出与 HTTP 响应结构一致。
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


def _utcnow() -> datetime:
    return datetime.now(UTC)


class StageStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"  # 断点续跑时跳过已完成的 stage


class JobSpec(BaseModel):
    """一次任务的规格：job_id + 传给各 stage 的参数。"""

    job_id: str
    params: dict[str, Any] = Field(default_factory=dict)


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
        return r is not None and r.status == StageStatus.SUCCESS
