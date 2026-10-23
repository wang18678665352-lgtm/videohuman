"""机器能力检测(GPU/显存)。

优先用 nvidia-smi 探测,避免"为探测能力先装 10GB torch 依赖";
nvidia-smi 不可用时才回退 torch(若已安装)。
纯本地版本:无 CUDA GPU 时 ensure_cuda 直接报错。
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass, field

from loguru import logger


@dataclass
class GpuInfo:
    name: str
    vram_gb: float


@dataclass
class CapabilityInfo:
    has_cuda: bool = False
    gpus: list[GpuInfo] = field(default_factory=list)
    source: str = "none"  # nvidia-smi | torch | none

    @property
    def max_vram_gb(self) -> float:
        return max((g.vram_gb for g in self.gpus), default=0.0)


def _detect_via_nvidia_smi() -> CapabilityInfo | None:
    exe = shutil.which("nvidia-smi")
    if not exe:
        return None
    try:
        out = subprocess.run(
            [exe, "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=15,
            check=True,
        ).stdout
    except (subprocess.SubprocessError, OSError) as e:
        logger.debug(f"nvidia-smi 探测失败: {e}")
        return None
    gpus = []
    for line in out.strip().splitlines():
        name, _, mem = line.partition(",")
        try:
            gpus.append(GpuInfo(name=name.strip(), vram_gb=round(float(mem.strip()) / 1024, 1)))
        except ValueError:
            continue
    return CapabilityInfo(has_cuda=bool(gpus), gpus=gpus, source="nvidia-smi")


def _detect_via_torch() -> CapabilityInfo | None:
    try:
        import torch
    except ImportError:
        return None
    if not torch.cuda.is_available():
        return CapabilityInfo(has_cuda=False, source="torch")
    gpus = [
        GpuInfo(
            name=torch.cuda.get_device_name(i),
            vram_gb=round(torch.cuda.get_device_properties(i).total_memory / 1024**3, 1),
        )
        for i in range(torch.cuda.device_count())
    ]
    return CapabilityInfo(has_cuda=bool(gpus), gpus=gpus, source="torch")


def detect_capability() -> CapabilityInfo:
    info = _detect_via_nvidia_smi() or _detect_via_torch()
    return info if info is not None else CapabilityInfo()


def ensure_cuda(capability: CapabilityInfo | None = None) -> None:
    """纯本地版本的前置检查:无可用 CUDA GPU 时抛出可排障错误。"""
    capability = capability or detect_capability()
    if not capability.has_cuda:
        raise RuntimeError(
            "未检测到可用 CUDA GPU:本项目全部推理在本地 GPU 运行,请确认显卡驱动已安装(nvidia-smi 可用)后重试"
        )
