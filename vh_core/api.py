"""内核脚本化 API — "内核即可编程库",CLI 只是它的薄壳。

- spec 文件(TOML/JSON)驱动单 job:vh run --spec job.toml
- batch 文件驱动批量:vh batch --file batch.toml(模型跨 job 驻留、失败隔离)
- 工作流:spec 的 params 里给 workflow(模板 id / JSON 路径 / 内联 dict)与
  node_params({节点 id: {参数: 值}}),即按工作流执行(见 vh_core.workflow)
- Python 直接调用:
      from vh_core.api import run_job, run_batch, load_spec
      manifest = run_job(load_spec("job.toml"))
"""

from __future__ import annotations

import json
import tomllib
from collections.abc import Callable
from pathlib import Path
from typing import Any

from loguru import logger

from .config import Settings, load_settings
from .models import JobManifest, JobSpec
from .pipeline import Pipeline, PipelineStageError


def build_pipeline(settings: Settings) -> Pipeline:
    from .stages import (
        AvatarStage,
        DownloadStage,
        PostprocessStage,
        PublishStage,
        RewriteStage,
        TranscribeStage,
        TTSStage,
    )

    return Pipeline(
        stages=[
            DownloadStage(),
            TranscribeStage(),
            RewriteStage(),
            TTSStage(),
            AvatarStage(),
            PostprocessStage(),
            PublishStage(),
        ],
        settings=settings,
    )


def _load_dict(path: Path) -> dict[str, Any]:
    path = Path(path)
    raw = path.read_bytes()
    if path.suffix.lower() == ".json":
        return json.loads(raw)
    return tomllib.loads(raw.decode("utf-8"))


def load_spec(path: str | Path) -> JobSpec:
    """加载单 job spec:job_id + [params](可含每 stage 子表,如 [params.tts])。"""
    d = _load_dict(Path(path))
    return JobSpec(job_id=d["job_id"], params=dict(d.get("params", {})))


def load_batch(path: str | Path) -> list[JobSpec]:
    """加载批量文件:[defaults.params] + [[jobs]](job 顶层键并入 params)。"""
    d = _load_dict(Path(path))
    defaults = dict(d.get("defaults", {}).get("params", {}))
    specs = []
    for j in d.get("jobs", []):
        j = dict(j)
        job_id = j.pop("job_id")
        params = {**defaults, **j.pop("params", {}), **j}
        specs.append(JobSpec(job_id=job_id, params=params))
    if not specs:
        raise ValueError(f"批量文件没有 jobs: {path}")
    return specs


def run_job(
    spec: JobSpec,
    settings: Settings | None = None,
    pipeline: Pipeline | None = None,
    only: list[str] | None = None,
    resume: bool = True,
    should_cancel: Callable[[], bool] | None = None,
) -> JobManifest:
    settings = settings or load_settings()
    if spec.params.get("workflow"):
        from . import workflow as wf

        wf_spec = wf.resolve_job_workflow(settings, settings.workspace / spec.job_id, spec.params["workflow"])
        pipeline = wf.build_pipeline(wf_spec, settings, spec.params.get("node_params"))
        return pipeline.run(spec.job_id, only=only, resume=resume, should_cancel=should_cancel)
    pipeline = pipeline or build_pipeline(settings)
    return pipeline.run(spec.job_id, params=spec.params, only=only, resume=resume, should_cancel=should_cancel)


def run_batch(
    specs: list[JobSpec],
    settings: Settings | None = None,
    resume: bool = True,
) -> dict[str, Any]:
    """串行批量执行:provider 跨 job 驻留(模型只加载一次),单 job 失败不中断。

    返回批次报告 dict(可 JSON 序列化):每 job 的状态/失败 stage/错误/成品路径。
    """
    settings = settings or load_settings()
    pipeline = build_pipeline(settings)
    report: dict[str, Any] = {"total": len(specs), "succeeded": 0, "failed": 0, "jobs": []}
    for spec in specs:
        entry: dict[str, Any] = {"job_id": spec.job_id, "ok": False, "failed_stage": None, "error": None, "final": None}
        try:
            manifest = run_job(spec, settings, pipeline, resume=resume)
            entry["ok"] = True
            r = manifest.stages.get("postprocess")
            if r and "final" in r.outputs:
                entry["final"] = str(settings.workspace / spec.job_id / r.outputs["final"])
            report["succeeded"] += 1
        except PipelineStageError as e:
            entry["failed_stage"] = e.stage_name
            entry["error"] = str(e)
            report["failed"] += 1
            logger.error(f"[{spec.job_id}] 失败于 stage '{e.stage_name}',继续下一 job")
        except Exception as e:
            entry["error"] = str(e)
            report["failed"] += 1
            logger.error(f"[{spec.job_id}] 失败: {e},继续下一 job")
        report["jobs"].append(entry)
    report["ok"] = report["failed"] == 0
    return report
