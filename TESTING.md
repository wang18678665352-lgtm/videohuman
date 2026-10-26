# 怎么给 videohuman 做测试

这份说明写给第一次写测试的人。看完以后,你应该能自己跑完仓库里的测试,看懂结果,并照着加一条新的。

videohuman 是一条本地数字人视频流水线:下载文案、改写、配音、对口型、加字幕,最后发布。真正生成视频需要显卡和大约 16GB 的模型。**下面这套测试故意不碰那些东西。** 它只检查「字怎么切、任务怎么续跑、门有没有锁好」。

## 测试是什么

测试就是一小段程序,专门用来检查另一段程序。

你平时会打开软件、点几下、看结果对不对。测试把这件事写成代码:给一个输入,声明正确的输出,然后让程序自动对答案。对了就显示 `PASSED`,错了就显示 `FAILED`,并告诉你期望是什么、实际是什么。

举这个仓库里的例子。字幕要把口播文案切成短句:

```text
你好，世界。   →   ["你好", "世界"]
```

人眼看一次很容易,但三个月后如果有人改了标点列表,整篇文案可能又粘成一句。测试会在几秒内把这件事喊出来,不用你重新做一条视频。

## 为什么要做

- 改一个小地方时,不用把整条流水线手动跑一遍。
- 失败信息会指出哪一个函数、哪一行断言不对,比「生成出来的视频看起来有点怪」好查。
- 这套测试不需要显卡、不需要 API 密钥、不下载模型,笔记本上就能跑。

## 这个仓库里的三种测试

名字不重要,重要的是它们各查多大一块。

### 单元测试:只查一个函数

输入输出都在内存里,不写视频,不联网。

| 查什么 | 在哪 |
|---|---|
| 字幕按中文逗号断开 | `tests/test_text.py` 里的 `test_chinese_punctuation_splits_and_is_stripped` |
| API 密钥外面套着 JSON 列表时,只取第一把 | 同文件的 `test_clean_api_key_accepts_plain_text_and_json_list` |
| 抖音标题最长 55 个字 | 同文件的 `test_platform_title_and_tag_limits` |
| 静音帧收成时间段 | `tests/test_media.py` 里的 `test_silence_frames_become_segments` |

### 集成测试:几个零件装在一起

仍然不用显卡,但会真的写文件,或者真的调用本机的 ffmpeg。

| 查什么 | 在哪 |
|---|---|
| 流水线失败时,前面已经成功的环节不会被抹掉 | `tests/test_pipeline.py` 里的 `test_failed_stage_does_not_wipe_earlier_success` |
| 没配大模型密钥时,改写环节原样保存文案 | 同文件的 `test_rewrite_passthrough_without_api_key` |
| 发布环节用假的上传器,确认「一个平台失败不拖垮另一个」 | `tests/test_text.py` 里的 `test_publish_stage_records_each_platform_and_fails_only_when_all_fail` |
| ffmpeg 能读懂一段 0.4 秒的小视频 | `tests/test_media.py` 里的 `test_settings_ffmpeg_and_tiny_video_roundtrip` |

「假的上传器」就是 mock:测试临时换上一个假对象,让代码以为自己在发视频,其实什么网络请求都没发。显卡模型也是这样处理的:测试根本不调用它们。

### 冒烟测试:先点着,看会不会立刻冒烟

不证明功能全对,只证明「能打开」。

| 查什么 | 在哪 |
|---|---|
| `vh_core`、命令行、Web 服务能被 import | `tests/test_smoke.py` 里的 `test_core_packages_import` |
| `vh --help` 和 `vh capability` 能跑 | 同文件的 `test_cli_help_and_capability_json` |
| 工作台的 `/api/settings` 能打开,乱写的文件路径读不出去 | 同文件的 `test_settings_api_and_optional_token` 和 `test_job_and_asset_paths_cannot_escape` |

`tests/conftest.py` 会把运行日志关掉,这样屏幕上主要是测试结果。想看日志时,把那个文件里的 `logger.remove()` 暂时注释掉。

## 准备环境

项目要求 **Python 3.11**(见 `pyproject.toml` 的 `requires-python`)。系统里的 `python3` 如果是 3.12,装不了这个项目,请用下面的 3.11。

[uv](https://docs.astral.sh/uv/) 是本仓库使用的安装工具。没有的话:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

装好后新开一个终端,或执行 `source $HOME/.local/bin/env`。

### 方式 A:轻量安装(学习测试用这个)

在仓库根目录执行。它不安装 torch,也不下载模型。要装哪些包,只写在 `requirements-test.txt` 这一处;本地和后面的 GitHub Actions 用的是同一份清单。

```bash
uv venv --python 3.11 .venv
uv pip install --python .venv/bin/python -r requirements-test.txt
```

`.venv/` 已经在 `.gitignore` 里,不会被提交。

如果根目录已经有一个用 `uv sync` 装过的 `.venv`,不要删掉它。给那个环境补上测试包即可:

```bash
uv pip install --python .venv/bin/python pytest httpx
```

`httpx` 是访问 Web 接口的测试客户端要用的。其余库完整安装时已经在。

### 方式 B:完整安装

README 里的 `uv sync` 会安装显卡推理栈(torch、CUDA 相关库、模型相关库)。学习测试不需要走这条路。这套依赖怎么在 Windows 和 Linux 显卡机器上解得开,见文末「完整安装和 uv.lock」。

## 运行测试

始终在仓库根目录执行(那里有 `pyproject.toml`)。

跑全部,并列出每一条的名字:

```bash
.venv/bin/pytest -v
```

只跑一个文件:

```bash
.venv/bin/pytest -v tests/test_text.py
```

只跑一条。`::` 后面是函数名:

```bash
.venv/bin/pytest -v tests/test_text.py::test_chinese_punctuation_splits_and_is_stripped
```

按名字里的关键词筛选:

```bash
.venv/bin/pytest -v -k subtitle
```

## 怎么读输出

通过时,每一行以 `PASSED` 结尾,最后是一句总结。这次在一台没有显卡的 Linux 上,实际结果是:

```text
============================= test session starts ==============================
platform linux -- Python 3.11.17, pytest-9.1.1, pluggy-1.7.0
rootdir: /workspace
configfile: pyproject.toml
testpaths: tests
collected 47 items

tests/test_pipeline.py::test_failed_stage_does_not_wipe_earlier_success PASSED
tests/test_text.py::test_chinese_punctuation_splits_and_is_stripped[...] PASSED
...
============================== 47 passed in 1.06s ==============================
```

这几处值得看:

- `collected 47 items`:找到了 47 条测试。
- 一行一个测试。文件名 `::` 函数名,最后是 `PASSED`(通过)或 `FAILED`(失败)。
- 最后一行是总数和耗时。全部通过时退出码是 0。

失败时 pytest 用 `>` 标出断言那一行,用 `E` 标出原因。假设有人把中文逗号从断句规则里拿掉,你会看到类似:

```text
tests/test_text.py::test_chinese_punctuation_splits_and_is_stripped FAILED

    def test_chinese_punctuation_splits_and_is_stripped(split) -> None:
>       assert split("你好，世界。") == ["你好", "世界"]
E       AssertionError: assert ['你好，世界'] == ['你好', '世界']

tests/test_text.py:36: AssertionError
========================= 1 failed, 46 passed in 1.40s =========================
```

读法:

1. 先看哪条测试红了(函数名通常就说明它在保护什么)。
2. 再看 `E` 下面:左边是程序实际给出的,右边是测试认为正确的。
3. 最后一行 `tests/test_text.py:36` 是断言的位置,不是一定是 bug 的位置。从那个测试调用的函数往回看。

`SKIPPED` 表示这条测试主动没跑,比如机器上没有 ffmpeg。它不是失败。

## GitHub 上的自动测试(CI)

### CI/CD 是什么

CI 是持续集成(Continuous Integration)。意思是:代码一推上去,就有一台干净的机器自动把测试跑一遍,通过了才算这次改动能和其他人的代码合在一起。你不用记得「提交之前先在自己电脑上跑 pytest」——忘了也有人(其实是机器人)帮你跑。

CD 是持续交付(Continuous Delivery)。意思是测试通过之后,再自动把软件装到服务器或打成安装包。**这个仓库现在只做了 CI,没有做 CD。** 工作流跑完测试就停,不会发布视频,也不会把程序部署到任何地方。

### 这个工作流在做什么

文件在 `.github/workflows/test.yml`。GitHub 看见 `.github/workflows/` 下面的 YAML,就会按里面的说明执行。下面按文件从上到下说每一段是干什么的。

| 文件里的内容 | 它在说什么 |
|---|---|
| `name: tests` | 这次检查在网页上显示的名字,叫 tests |
| `on: push` | 有人用 git 把提交推到 GitHub 时就跑 |
| `on: pull_request` | 有人打开 Pull Request,或往这个 PR 里再推新提交时也跑 |
| `jobs: pytest` | 一组要按顺序做完的步骤,名字叫 pytest |
| `runs-on: ubuntu-latest` | 用 GitHub 提供的一台最新 Ubuntu 虚拟机。它没有显卡 |
| `actions/checkout@v4` | 把你这次提交的代码下载到那台虚拟机里 |
| `actions/setup-python@v5`,并且 `python-version: "3.11"` | 安装 Python 3.11。项目要求的就是这个版本 |
| `sudo apt-get install -y ffmpeg` | 安装 ffmpeg。有一条测试会拿它生成一段不到一秒的视频 |
| `pip install -r requirements-test.txt` | 只安装测试清单里的包。不跑 `uv sync`,因为那个会下载数 GB 的 CUDA 版 torch,而这些测试用不到 |
| `python -m pytest -v` | 跑 `tests/` 里的全部测试,并把每一条的名字打出来 |

工作流里没有 `secrets`,也不会访问需要登录的网站,也不会下载 `models/` 里的权重。

### 在 GitHub 上哪里看结果

两条路,看的是同一份日志。

1. **仓库的 Actions 标签页。** 打开仓库首页,点顶部的 **Actions**。左边选 **tests**,右边是每一次运行。绿色勾是通过,红叉是失败。点进某一次,再点 **pytest**,就能看到和你在本地终端里一样的 pytest 输出。
2. **Pull Request 的检查。** 打开一个 PR,往下滚到对话底部的检查列表,或者点 **Checks** 标签。里面会出现 **tests**。它变绿,这次提交的测试就是过的;变红,就点进去看哪一条 `FAILED`。

PR 页面上的检查,就是 Actions 里同一次运行的快捷入口。

### 红了怎么办

1. 点进红色的那一次运行,打开 **pytest** 那一步的日志,滚到带 `FAILED` 的地方。
2. 读法和上一节「怎么读输出」一样:函数名告诉你在保护什么,`E` 下面左边是实际值、右边是期望值。
3. 把仓库拉到你自己的电脑,用同样的 Python 3.11 和 `requirements-test.txt` 跑那一条,例如 `.venv/bin/pytest -v tests/test_text.py::函数名`。
4. 改完代码,在本地把全部测试跑绿,再 push。GitHub 会自动再跑一遍。新的一次变绿,PR 上的检查也会跟着变绿。

常见的红,和显卡无关:断言写错、少提交了一个文件、`requirements-test.txt` 里漏了一个 import 需要的包、或者测试调用了本机没有的 ffmpeg。日志最后几十行通常就够定位。

## 自己加一条测试

用「标题去掉首尾空格」做例子。函数在 `vh_core/publishers/base.py`,叫 `truncate_title`。

**第一步。** 打开 `tests/test_text.py`,滚到 `test_platform_title_and_tag_limits` 附近。文件顶部已经导入了 `truncate_title`,不用再导入。

**第二步。** 在那个测试函数后面加一个新函数。名字以 `test_` 开头,pytest 才会运行它:

```python
def test_title_strips_spaces_before_counting() -> None:
    """标题先去掉首尾空白,再决定要不要截断。"""
    assert truncate_title("bilibili", "  你好  ") == "你好"
```

函数上面的三引号字符串是给人看的说明。pytest 失败时也会把它打出来,所以写「这条测试在保护什么」,而不是把代码再翻译一遍。

**第三步。** 只跑这一条:

```bash
.venv/bin/pytest -v tests/test_text.py::test_title_strips_spaces_before_counting
```

看到 `PASSED` 就对了。

**第四步。** 故意把期望改错,例如改成 `== "你好  "`,再跑同一条命令。你应该看到 `FAILED`,以及实际值 `"你好"` 和你写的期望不一样。看懂之后改回来,再跑一次,确认恢复成 `PASSED`。最后跑一遍全部:

```bash
.venv/bin/pytest -v
```

新测试就完成了。可以把它和代码一起提交。

写测试时可以借用这几个习惯:

- 一个函数只查一件事。名字读起来像一句话。
- 需要文件时用参数 `tmp_path`。pytest 会给一个空的临时目录,测完删掉,不会弄脏 `workspace/`。
- 需要换掉环境变量或某个函数时用 `monkeypatch`。测试结束它会自动改回去。
- 期望程序报错时写 `with pytest.raises(ValueError, match="关键词"):`。
- 不要在测试里调用真正的配音、对口型或发布。那些要显卡或账号。用假对象,或者只测「没配密钥时应该跳过」。

## 测试文件各看哪一块

| 文件 | 内容 |
|---|---|
| `tests/test_pipeline.py` | 任务清单、断点续跑、读取 `examples/` 里的说明书 |
| `tests/test_text.py` | 字幕、封面文案、改写、参考音文本、发布标题和 B 站登录文件检查 |
| `tests/test_media.py` | 颜色、高亮词、静音、字体、封面图、ffmpeg 小视频 |
| `tests/test_smoke.py` | import、命令行、抖音链接解析、Web 接口和路径限制 |
| `tests/conftest.py` | 所有测试开始前的共用准备 |

## 这次顺便修好的问题

测试写到这些行为时,发现原来的代码和说明不一致。改动都很小:

1. **断点续跑。** 后面的环节失败时,会把前面已经成功的环节一起写成 `skipped`。下次再跑,那些成功环节会被重做。现在只在整次运行成功之后,才在返回值里标 `skipped`;磁盘上的 `success` 保持不动。
2. **中文字幕。** 断句原来不认识 `，！？；`,「你好，世界。」会留在同一行,句尾的逗号也剥不掉。`vh_core/media.py` 和后期环节各有一份,两份一起补上了。
3. **字幕时间轴。** 识别出的词比时间戳多时会 `IndexError`,整段字幕退回平均分配。现在按较短的一侧对齐。
4. **参考音文本。** `<|NEUTRAL|>` 这种情感标记只删了括号,`NEUTRAL` 几个字母会留在文本里。现在整段标记都去掉。
5. **工作台读文件。** 判断「文件在不在允许的目录里」用的是字符串前缀。任务 `demo` 因此能读到 `demo-secret`,素材目录 `assets` 也能读到旁边的 `assets2`。现在改成真正的路径包含关系,并且拒绝 `..` 这种任务名。

## 这次没测到的部分

| 没跑的东西 | 原因 |
|---|---|
| 配音、数字人、语音识别的真实推理 | 需要 NVIDIA 显卡和 `download_weights.py` 下载的模型。这台机器没有显卡,测试也不该依赖它们 |
| 把 torch 真正装进环境并 import | 求解已经能通过(见下一节),但 CUDA 版 torch 有数 GB,这台检查机器也没有显卡。没有下载这些轮子,也没有跑配音或对口型 |
| 真的下载抖音、调用大模型、上传 B 站或抖音 | 需要登录态和网络,而且会动到真实账号。测试只检查链接怎么解析、密钥为空时怎么跳过、登录文件缺了怎么报错 |
| `web/` 前端 | Vue 页面没有现成的测试脚本,业务逻辑也不在前端。这次没有为它另装一套 JavaScript 测试工具 |
| `third_party/` 里上游项目自带的测试 | 那是 MuseTalk、CosyVoice、IndexTTS 自己的套件,很多要模型和显卡,不属于 videohuman 这层 |

## 完整安装和 uv.lock

`uv sync` 会按照 `uv.lock` 把 `pyproject.toml` 里的全部依赖装进 `.venv`,包括 CUDA 12.8 的 torch。项目要照顾的机器是 **带 NVIDIA 显卡的 Linux(x86_64)和 Windows**。

以前在 Linux 上重新生成这把锁时,会选中 `torch==2.0.1`。cu128 索引上的这个版本只有一块 aarch64 轮子,Linux x86_64 和 Windows 都装不上,CUDA 相关的包也会一起丢掉。原因有两层:

1. `openai-whisper==20231117` 的 `setup.py` 会看「正在跑 `uv lock` 的那台机器」。如果它是 Linux x86_64,就把 `triton>=2,<3` 写进依赖,而且不附带「只在 Linux 上需要」这种条件。cu128 的 torch 2.7 及以上在 Linux 上要的是 triton 3.x。两个条件碰在一起,新的 torch 全部被排除。
2. 求解器退回到索引里那个 `torch==2.0.1`。它不依赖 triton 3,所以看起来「能解开」,但它的轮子其实是 aarch64 的。uv 还把 Linux x86_64 和 Windows 算成同一个分支,于是这两块平台都继承了这个装不上的版本。

`pyproject.toml` 里现在做了两处对应的修改:

- `exclude-dependencies` 只从 whisper 身上去掉 triton。torch 自己在 Linux 上仍然依赖 `triton==3.6.0`,CUDA 包(nvidia-cublas、cudnn 等)还在锁里。whisper 的 triton 只用于可选的 CUDA 时间戳加速,内核启动失败时会退回普通 PyTorch。
- `required-environments` 写明 Linux x86_64 和 Windows 64 位必须有轮子。再退回只有 aarch64 轮子的 torch 2.0.1 时,`uv lock` 会直接失败,而不是写出一把装不上的锁。

在 Linux x86_64 上用 `uv lock` 重新求解、再用 `uv sync --dry-run` 检查,结果是 `torch==2.11.0+cu128`,并且带有 Linux x86_64 和 Windows 的轮子。真正把数 GB 的轮子下载下来,要在有显卡的机器上执行 `uv sync`。
