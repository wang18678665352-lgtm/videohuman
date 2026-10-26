"""测试公共夹具:所有路径指向 tmp_path,不触碰真实 workspace/assets,不加载任何模型。"""

from __future__ import annotations

from pathlib import Path

import pytest

from vh_core.config import Settings


@pytest.fixture(autouse=True)
def _clean_vh_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """隔离开发机上的 VH_* 环境变量,保证测试可复现。"""
    import os

    for k in list(os.environ):
        if k.startswith("VH_"):
            monkeypatch.delenv(k, raising=False)


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    (tmp_path / "workspace").mkdir()
    (tmp_path / "assets" / "bgm").mkdir(parents=True)
    return Settings(
        workspace_dir=tmp_path / "workspace",
        assets_dir=tmp_path / "assets",
        publish_cookies_dir=tmp_path / "cookies",
        log_dir=tmp_path / "logs",
        llm_api_key="",
    )
