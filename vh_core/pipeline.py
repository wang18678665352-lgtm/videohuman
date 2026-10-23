"""Stage 抽象基类 + 流水线编排 + 断点续跑。

每个 stage：输入 pydantic 模型 → 执行 → 产物落盘 workspace/{job_id}/{stage}/
并登记到 manifest.json。流水线按 manifest 判断已完成的 stage，支持续跑。
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar

from loguru import logger

from .config import Settings
from .models import JobManifest, StageResult, StageStatus


@dataclass
class StageContext:
    """传给每个 stage 的上下文：配置、job 信息、本 stage 的产物目录。"""

    settings: Settings
    job_id: str
    params: dict[str, Any]
    stage_dir: Path
    job_dir: Path

    def rel(self, p: Path) -> str:
        """产物路径统一登记为相对 job 目录的 POSIX 形式。"""
        return p.resolve().relative_to(self.job_dir.resolve()).as_posix()


class Stage(ABC):
    """环节抽象：子类只需声明 name 并实现 run()，返回 {产物名: 路径}。"""

    name: ClassVar[str]

    def enabled(self, ctx: StageContext) -> bool:
        """本 job 是否需要执行该环节(如 download 无 share_url 时跳过)。"""
        return True

    @abstractmethod
    def run(self, ctx: StageContext) -> dict[str, Path]:
        """执行本环节，产物必须写在 ctx.stage_dir 下并返回。失败抛异常。"""


class Pipeline:
    def __init__(self, stages: list[Stage], settings: Settings):
        self.stages = stages
        self.settings = settings

    def _job_dir(self, job_id: str) -> Path:
        return self.settings.workspace / job_id

    def _manifest_path(self, job_id: str) -> Path:
        return self._job_dir(job_id) / "manifest.json"

    def load_manifest(self, job_id: str) -> JobManifest:
        p = self._manifest_path(job_id)
        if p.exists():
            return JobManifest.model_validate(json.loads(p.read_text(encoding="utf-8")))
        return JobManifest(job_id=job_id)

    def save_manifest(self, manifest: JobManifest) -> None:
        p = self._manifest_path(manifest.job_id)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(manifest.model_dump_json(indent=2), encoding="utf-8")

    def run(
        self,
        job_id: str,
        params: dict[str, Any] | None = None,
        only: list[str] | None = None,
        resume: bool = True,
    ) -> JobManifest:
        """按序执行 stages。resume=True 时跳过 manifest 中已成功的 stage。"""
        params = params or {}
        job_dir = self._job_dir(job_id)
        job_dir.mkdir(parents=True, exist_ok=True)
        manifest = self.load_manifest(job_id) if resume else JobManifest(job_id=job_id)

        for stage in self.stages:
            if only is not None and stage.name not in only:
                continue
            if manifest.is_done(stage.name):
                logger.info(f"[{job_id}] stage '{stage.name}' 已完成，跳过(断点续跑)")
                manifest.stages[stage.name].status = StageStatus.SKIPPED
                continue

            stage_dir = job_dir / stage.name
            stage_dir.mkdir(parents=True, exist_ok=True)
            ctx = StageContext(
                settings=self.settings,
                job_id=job_id,
                params=params,
                stage_dir=stage_dir,
                job_dir=job_dir,
            )
            if not stage.enabled(ctx):
                logger.info(f"[{job_id}] stage '{stage.name}' 本 job 不适用,跳过")
                manifest.stages[stage.name] = StageResult(
                    name=stage.name,
                    status=StageStatus.SKIPPED,
                )
                self.save_manifest(manifest)
                continue
            result = StageResult(
                name=stage.name,
                status=StageStatus.RUNNING,
                started_at=datetime.now(UTC),
            )
            manifest.stages[stage.name] = result
            self.save_manifest(manifest)  # RUNNING 也落盘，崩溃后可见现场

            try:
                outputs = stage.run(ctx)
            except Exception as e:
                logger.exception(f"[{job_id}] stage '{stage.name}' 失败")
                result.status = StageStatus.FAILED
                result.error = str(e)
                result.finished_at = datetime.now(UTC)
                self.save_manifest(manifest)
                raise PipelineStageError(stage.name, e) from e

            result.status = StageStatus.SUCCESS
            result.outputs = {k: ctx.rel(v) for k, v in outputs.items()}
            result.finished_at = datetime.now(UTC)
            self.save_manifest(manifest)
            logger.info(f"[{job_id}] stage '{stage.name}' 完成: {result.outputs}")

        return manifest


class PipelineStageError(RuntimeError):
    def __init__(self, stage_name: str, cause: Exception):
        super().__init__(f"stage '{stage_name}' 执行失败: {cause}")
        self.stage_name = stage_name
        self.cause = cause
