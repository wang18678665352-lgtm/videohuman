# 萝卜根 v2 服务端镜像(CUDA 12.8 + Python 3.11 + uv)
# build context 为项目根:docker compose 里 context: .. + dockerfile: docker/Dockerfile
#
# 注意:Windows 本机的 torch wheel 是 win_amd64,无法用于 Linux 容器;
# 这里经 PyPI(镜像)解析 Linux cu128 wheel(5060Ti Blackwell 需要)。

# ---- 阶段 1:构建前端(产物 web/dist 打进运行镜像,宿主机无需装 Node)
FROM node:22-slim AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY web/ ./
RUN npm run build

# ---- 阶段 2:运行镜像
FROM nvidia/cuda:12.8.1-cudnn-runtime-ubuntu22.04

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUTF8=1 \
    UV_PYTHON=3.11 \
    UV_LINK_MODE=copy

# 系统依赖:编译工具(insightface 无 manylinux wheel,需源码构建)、
# opencv 运行时库、ffmpeg(兜底,项目首选 imageio-ffmpeg 内置二进制)
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl ca-certificates git build-essential \
        libgl1 libglib2.0-0 ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# uv(静态二进制)+ uv 管理的 CPython 3.11
RUN curl -LsSf https://astral.sh/uv/install.sh | sh
ENV PATH="/root/.local/bin:$PATH"
RUN uv python install 3.11

WORKDIR /app

# 先只拷锁文件,依赖层最大化缓存。
# --no-sources:忽略 pyproject 里 Windows 本地 wheel 路径,从 PyPI 解 Linux 版;
# insightface 的 sdist 构建期要 import Cython/numpy,先手工铺进 venv,
# 再以 --no-build-isolation-package 编译(--inexact 保留预装的 setuptools);
# 阿里云镜像加速国内拉取(失败可去掉 UV_DEFAULT_INDEX 重试)。
COPY pyproject.toml uv.lock ./
RUN uv venv --python 3.11 \
    && uv pip install cython "numpy<2" setuptools
RUN UV_DEFAULT_INDEX=https://mirrors.aliyun.com/pypi/simple/ \
    uv sync --no-sources --no-install-project --inexact \
            --no-build-isolation-package insightface

# Playwright Chromium(download/publish 环节用)
RUN /app/.venv/bin/playwright install --with-deps chromium

# 代码与静态资源(数据目录 models/assets/workspace/third_party/configs 全部卷挂载)
COPY README.md LICENSE NOTICE ./
COPY vh_core vh_core
COPY vh_cli vh_cli
COPY vh_server vh_server
COPY --from=web /web/dist web/dist
COPY configs/default.toml configs/default.toml
RUN uv sync --no-sources --no-editable --inexact

EXPOSE 8100
# 探活接口免鉴权;GPU 冷启动加载模型较慢,给足 start-period
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8100/api/health || exit 1
CMD ["uv", "run", "--no-sync", "vh", "serve", "--host", "0.0.0.0", "--port", "8100"]
