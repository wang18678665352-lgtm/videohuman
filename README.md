# videohuman

CLI 优先的**纯本地**数字人口播视频流水线:抖音视频下载 → 文案提取 → AI 改写 → 语音合成 → 数字人生成 → 后期(静音切除/字幕/BGM/背景/水印/封面)→ 多平台发布,一条龙跑完。

- **CLI 优先,Agent 可调用**:每个环节是独立子命令,输出结构化 JSON,方便 AI agent 解析与编排
- **纯本地推理**:全部计算在本机 GPU 完成,无任何云端依赖,素材不出本机
- **内核唯一**:业务逻辑只在 `vh_core`;CLI 与 Web 工作台都是内核外的薄壳
- **断点续跑**:按 `workspace/{job_id}/manifest.json` 记录各环节状态,失败后可从断点继续

## 流水线环节

| 环节 | 说明 | 默认实现 |
|---|---|---|
| download | 抖音视频下载(登录态 cookie) | Playwright |
| transcribe | 文案提取(ASR) | SenseVoice-Small |
| rewrite | 文案改写/纠错(LLM,OpenAI 兼容接口) | DeepSeek 等,自配 |
| tts | 语音合成(音色克隆) | CosyVoice3 |
| avatar | 数字人口型同步 | MuseTalk 1.5(可选 LstmSync,需自备) |
| postprocess | 静音切除、字幕、BGM、背景图、水印、封面 | ffmpeg |
| publish | 多平台发布 | 抖音 / B 站 |

## 硬件要求

- NVIDIA GPU(CUDA 可用),建议显存 ≥ 10GB
- Windows 10/11(主要开发平台;Linux 亦可,发布功能依赖本机 Chrome)

## 安装

```bash
# 1. 依赖(需要 uv:https://docs.astral.sh/uv/)
uv sync

# 2. 模型权重(约 16GB,ModelScope + hf-mirror)
uv run python download_weights.py all

# 3. 发布功能需要的浏览器
uv run playwright install chromium
```

torch 默认从 PyTorch 官方 cu128 索引安装(50 系显卡必需);其他显卡可在
`pyproject.toml` 的 `[[tool.uv.index]]` 中改成对应 CUDA 版本。

## 快速上手

```bash
# 检测 GPU
uv run vh capability

# 整跑:抖音链接克隆模式
uv run vh run --job-id demo1 --share-url "https://v.douyin.com/xxxx" \
  --avatar-video assets/my_avatar.mp4

# 整跑:文案直给模式
uv run vh run --job-id demo2 --text "你的文案" \
  --ref-audio assets/my_voice.wav --ref-text "参考音频对应的文本" \
  --avatar-video assets/my_avatar.mp4 --publish bilibili

# 单步重跑某一环节(断点续跑)
uv run vh tts --job-id demo2 --text "改过的文案"

# 批量生成
uv run vh batch --spec examples/batch.toml

# Web 工作台(可选)
uv run vh serve --port 8100   # 或双击 start_web.bat
```

想学怎么测试、以及不装显卡也能跑的检查命令,见 [TESTING.md](TESTING.md)。

LLM 改写需要配置 API Key(默认 DeepSeek 兼容接口):设置环境变量
`VH_LLM_API_KEY`,或在 Web 工作台"素材与设置"页填写。不配置时 rewrite 环节直通原文。

发布功能需要各平台登录态:抖音用 `uv run vh login` 扫码;B 站用 biliup 登录 JSON,
均放在 `configs/cookies/`(已在 .gitignore 中)。

## 可选:LstmSync 引擎

默认数字人引擎是 MuseTalk(MIT)。如果你另有 **LstmSync** 引擎(第三方授权制产品,
见 <https://github.com/oneCodeSuperman/LstmSync>),把引擎代码与 checkpoints 放入
`third_party/lstmsync/`,再把 `configs/default.toml` 的 `avatar_engine` 改为
`"lstmsync"` 即可。详见 `third_party/README.md`。

## 目录结构

```
videohuman/
├── vh_core/            # 内核:pipeline 编排 / stages 环节 / providers 算力 / postproc 后期 / publishers 发布
├── vh_cli/             # typer CLI(vh 命令)
├── vh_server/          # FastAPI Web 工作台 API
├── web/                # Vue3 + Element Plus 工作台前端
├── third_party/        # vendor: MuseTalk / CosyVoice / LatentSync / index-tts
├── models/             # 权重(不入库,download_weights.py 下载)
├── assets/             # 字体 / BGM(自备)/ 背景图(自备)
├── configs/            # default.toml 默认配置 + cookies(敏感,不入库)
├── examples/           # 示例 JobSpec
└── workspace/          # 运行产物(不入库)
```

配置优先级:环境变量(`VH_` 前缀)> `configs/default.toml` > 代码默认值。

## 许可证

本项目代码以 [Apache-2.0](LICENSE) 发布。第三方组件的许可证与署名见 [NOTICE](NOTICE);
字体的来源与许可见 [assets/font/LICENSES.md](assets/font/LICENSES.md)。

**免责声明**:本项目仅提供技术工具。使用者应确保对输入素材拥有合法权利,
生成内容需遵守所在地法律法规及各平台规则(包括 AI 生成内容标识要求)。
