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
| rewrite | 文案口播化润色/纠错(LLM,OpenAI 兼容接口) | DeepSeek 等,自配 |
| tts | 语音合成(音色克隆) | CosyVoice3 |
| avatar | 数字人口型同步 | MuseTalk 1.5(可选 LstmSync,需自备) |
| postprocess | 静音切除、字幕、BGM、背景图、水印、封面、AI 生成标识 | ffmpeg |
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

LLM 改写需要配置 API Key(默认 DeepSeek 兼容接口):设置环境变量
`VH_LLM_API_KEY`,或在 Web 工作台"素材与设置"页填写。不配置时 rewrite 环节直通原文。

发布功能需要各平台登录态:抖音用 `uv run vh login` 扫码;B 站用 biliup 登录 JSON,
均放在 `configs/cookies/`(已在 .gitignore 中)。

## 工作流:自由编排生产流程

除经典数字人流水线外,所有步骤都可以在工作台「工作流」页**拖拽连线**自由组合:节点有类型化的
输入/输出端口(文本/音频/视频/图片/字幕),类型不符的连线会被拒绝;保存前服务端再做完整校验
(必需输入、环路、单输入单连线)。

**内置模板**(`vh_core/workflows/`,只读,可另存后修改):

| 模板 | 流程 | GPU |
|---|---|---|
| `digital_human` 数字人口播 | 文案 → 润色 → 配音 → 数字人对口型 → 后期 → 发布 | 需要 |
| `broll_voiceover` 配音混剪(不出人) | 文案 → 润色 → 配音 → 按句轮换素材库图片/视频 → 后期 → 发布 | 仅 TTS |
| `video_translate` 视频翻译(对口型) | 识别原文 → 翻译 → 截取原声作参考音色 → 克隆配音 → 对齐时长 → 口型同步 → 目标语言字幕 | 需要 |
| `video_dub` 视频翻译(仅配音) | 同上,但直接替换音轨,不改画面 | 仅 ASR/TTS |

**可用步骤**:文案/视频/音频输入、抖音下载、语音转写、文案润色、文案翻译、语音合成、提取音频、
配音对齐时长、数字人口型同步、素材混剪、替换音轨、后期包装、多平台发布(`vh steps` 查看端口与参数)。

- 混剪素材放 `assets/broll/`(工作台「素材与设置」可上传),图片会缓慢推近,视频裁成画布比例;
- 翻译需配置 LLM(`VH_LLM_API_KEY`);翻译失败不会回退原文;
- 自定义工作流保存在 `configs/workflows/*.json`;每个任务运行时写入 `workspace/{job_id}/workflow.json`
  快照,之后修改工作流不影响已有任务续跑。

```bash
uv run vh workflows list                       # 列出模板与自定义工作流
uv run vh workflows show video_dub             # 查看节点、连线与必填参数
uv run vh run --job-id t1 -w broll_voiceover -s script.text="今天聊聊……"
uv run vh run --job-id t2 -w video_dub -s source.path=assets/talk.mp4 -s trans.target_lang=日语
```

工作流任务的单节点重跑用工作台详情页或 `POST /api/jobs/{id}/run {"only": "节点id"}`;
`vh tts` 等经典单步命令只适用于经典流水线任务。

## 合规内置项

- **AI 生成标识**:成片默认在画面左上角加"本视频由AI生成"角标(显式标识),并在 MP4 元数据写入
  `AIGC` 字段(隐式标识,字段参照 GB 45438-2025,不可关闭);封面同样带 AI 标识。
  角标可经 `params.postprocess.ai_label=false` 关闭(会告警),文字/位置可用 `ai_label_text`、
  `ai_label_position` 调整。角标添加失败时 postprocess 直接报错,不会产出无标识成片。
  发布到平台时,仍请在平台侧勾选"内容由 AI 生成"等声明。
- **素材授权确认**:工作台上传声音/形象/BGM 素材前须确认授权声明,确认记录(文件 sha256、
  声明原文、IP、时间)追加到 `assets/.consent.jsonl` 存档。直接拷入 `assets/` 的文件不经此流程,
  使用者需自行留存授权凭证。
- **文案润色**:内置提示词只做口播化润色与纠错、忠于原意;自定义提示词用 `llm_prompt_file` 配置,
  内容由使用者负责。

## 生产部署与运维

### 环境自检

```bash
uv run vh doctor        # GPU / ffmpeg / 模型权重 / 字体 / 前端构建 / 鉴权,输出 JSON,必需项缺失时退出码 1
uv run vh --version
```

### Web 工作台的安全基线

- **访问令牌**:`vh serve` 监听非本机地址(如 `--host 0.0.0.0`)时**必须**设置 `VH_WEB_TOKEN`,
  否则拒绝启动(确需在可信内网裸跑可加 `--allow-no-auth`)。令牌用常量时间比较;
  同一 IP 5 分钟内失败 20 次会被临时拒绝(HTTP 429)。
- **路径隔离**:经 API 提交的素材路径(`audio`/`ref_audio`/`avatar_video`/`bgm_path` 等)
  只允许指向 `assets/` 或 `workspace/` 内;文件下载接口禁止路径穿越;`job_id` 仅允许安全字符。
- **上传**:文件名清洗、同名自动改名不覆盖、大小上限 `VH_MAX_UPLOAD_MB`(默认 2048)。
- **CORS**:默认关闭(前端与 API 同源);前后端分离部署时用 `VH_CORS_ORIGINS` 配白名单。
- 公网暴露请放在 HTTPS 反向代理(Nginx/Caddy)之后;token 会出现在媒体 URL 的 query 中,
  勿在不可信的代理上记录完整 URL。

### 任务队列

- 单 GPU 串行执行,状态落盘 `workspace/{job_id}/status.server.json`;**服务重启后**
  排队中的任务自动恢复,运行中的任务标记为"中断",可一键续跑(断点续跑基于 manifest)。
- 支持取消:排队中立即出队;运行中在当前环节结束后停止(GPU 推理无法安全中途打断)。
- 支持删除任务(连同全部产物),排队/运行中的任务需先取消。

### HTTP API 摘要

除 `/api/health` 外均需 `X-VH-Token` 头(或 `?token=`)。完整交互文档:启动后访问 `/docs`。

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/health` | 探活(免鉴权),含队列状态 |
| GET | `/api/settings` | 引擎/平台/登录态等配置 |
| GET/POST | `/api/assets` | 素材列表 / 上传(`?kind=audio\|video\|bgm\|broll&consent=true`) |
| GET | `/api/steps` | 工作流可用步骤(端口/参数描述) |
| GET/PUT/DELETE | `/api/workflows[/{id}]` | 工作流列表 / 读取 / 保存 / 删除;`POST /api/workflows/validate` 校验 |
| GET/POST | `/api/jobs` | 任务列表(`?status=&limit=&offset=`)/ 新建任务(经典参数,或 `workflow` + `node_params`) |
| GET/DELETE | `/api/jobs/{id}` | 任务详情 / 删除任务 |
| POST | `/api/jobs/{id}/run` | 续跑、单环节重跑(`only`)、全部重跑(`resume=false`) |
| POST | `/api/jobs/{id}/cancel` | 取消任务 |
| GET | `/api/jobs/{id}/file?path=` | 下载任务产物 |

### 日志

服务端日志按天滚动写入 `logs/server_YYYY-MM-DD.log`(保留 14 天,目录可用 `VH_LOG_DIR` 修改);
每个请求带 `X-Request-ID` 响应头,出错时可据此在日志中定位。

### 开发与测试

测试不依赖 GPU 与模型权重(使用假 runner),几秒内跑完:

```bash
uv venv .venv-dev && uv pip install -p .venv-dev -r requirements-dev.txt
.venv-dev/bin/python -m pytest
.venv-dev/bin/ruff check . && .venv-dev/bin/ruff format --check vh_core vh_cli vh_server tests
cd web && npm ci && npm run build
```

CI(`.github/workflows/ci.yml`)在每次 push/PR 上执行以上检查。

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
├── vh_server/          # FastAPI Web 工作台 API + 持久化任务队列
├── web/                # Vue3 + Element Plus 工作台前端
├── third_party/        # vendor: MuseTalk / CosyVoice / LatentSync / index-tts
├── models/             # 权重(不入库,download_weights.py 下载)
├── assets/             # 字体 / BGM(自备)/ 背景图(自备)
├── configs/            # default.toml 默认配置 + cookies(敏感,不入库)
├── examples/           # 示例 JobSpec
├── tests/              # 单元/接口测试(无需 GPU)
└── workspace/          # 运行产物(不入库)
```

配置优先级:环境变量(`VH_` 前缀)> `configs/default.toml` > 代码默认值。

## 许可证

本项目代码以 [Apache-2.0](LICENSE) 发布。第三方组件的许可证与署名见 [NOTICE](NOTICE);
字体的来源与许可见 [assets/font/LICENSES.md](assets/font/LICENSES.md)。

**免责声明**:本项目仅提供技术工具。使用者应确保对输入素材(包括抖音克隆模式下载的视频、
参考声音与形象)拥有合法权利,
生成内容需遵守所在地法律法规及各平台规则(包括 AI 生成内容标识要求)。
