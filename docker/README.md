# Docker 部署(备用方案)

> **结论:低配本机(16GB 内存)跑 10GB+ GPU 镜像过于勉强,构建导出阶段引擎反复卡死;
> 日常推荐原生部署(见下文"当前推荐:原生部署"),本目录文件保留备用
> (机器扩容或换服务器后可直接用)。**

## 当前推荐:原生部署(无 Docker)

```bat
start_server.bat    :: 起服务(0.0.0.0:8100,自动开浏览器)
```

1. 首次:复制 `configs/server.local.bat.example` 为 `configs/server.local.bat`,
   填入 `VH_WEB_TOKEN`(自己生成一个长随机串);
2. 双击 `start_server.bat`;局域网访问 `http://<本机IP>:8100`,浏览器里输入 token 进入;
3. 需要公网访问时,自行搭配任意内网穿透工具(cpolar、frp 等)把 8100 端口暴露出去。

---

## 原 Docker 方案(备用)

`vh serve` 跑在 Linux 容器里。

## 前置条件

- Docker Desktop(已装),**先启动它**(任务栏图标变绿、`docker info` 能跑通);
  GPU 直通走 WSL2 后端,Docker Desktop 默认支持,无需额外装 NVIDIA Container Toolkit。
- 模型、素材、引擎不进镜像,全部运行时卷挂载,所以这些目录必须在仓库原位置:
  `models/`、`assets/`、`workspace/`、`configs/`、`third_party/`。
- 可选:仅当使用 lstmsync 引擎时,在 `docker-compose.yml` 里取消注释
  `VH_LSTMSYNC_DIR` 和对应的卷挂载(引擎需自备,见 `../third_party/README.md`)。

## 启动

```bash
cd docker
cp .env.example .env     # 填入 VH_WEB_TOKEN(必填,未设置时服务拒绝启动)
docker compose up -d --build
```

- 首次构建约 10~20 分钟(要下载 CUDA 基础镜像、torch cu128、Chromium 等约 8~10GB);之后改代码重建只需一两分钟(依赖层有缓存)。
- 前端在镜像构建阶段自动编译(多阶段构建),宿主机无需安装 Node;改动后 `docker compose up -d --build` 即可。

## 验证

```bash
docker compose exec vh-server nvidia-smi -L                       # GPU 直通
docker compose exec vh-server uv run --no-sync python -c \
  "import torch; print(torch.cuda.is_available())"                # torch 能用 CUDA
curl http://127.0.0.1:8100/api/settings                           # 未设 token 时
curl -H "X-VH-Token: <你的token>" http://127.0.0.1:8100/api/settings  # 设了 token 时
```

浏览器访问 `http://<本机IP>:8100`(局域网)或穿透域名(公网),输入 token 进入。

## 限制(与宿主机直跑一致,未劣化)

- 抖音扫码登录(`vh login douyin`)需要可见浏览器,**只能在宿主机跑一次**;
  登录态在 `configs/cookies/`,已通过卷共享给容器。
- 发布遇滑块验证码仍需人工在宿主机浏览器介入。
