"""v2 模型权重下载脚本：ModelScope + HuggingFace(hf-mirror 镜像)。

直接用解释器 API 下载,绕开 Git Bash 下 .exe CLI 包装器静默失效的问题。
用法: uv run python download_weights.py [ms|hf|all]
"""

import os
import sys

SRC_V2 = os.path.dirname(os.path.abspath(__file__))
MODELS_DIR = os.path.join(SRC_V2, "models")
MUSETALK_MODELS = os.path.join(SRC_V2, "third_party", "MuseTalk", "models")
S3FD_PATH = os.path.join(
    SRC_V2,
    "third_party",
    "MuseTalk",
    "musetalk",
    "utils",
    "face_detection",
    "detection",
    "sfd",
    "s3fd.pth",
)
# S3FD 人脸检测器官方权重(face-alignment 项目同源,见 sfd_detector.py 的 models_urls)
S3FD_URL = "https://www.adrianbulat.com/downloads/python-fan/s3fd-619a316812.pth"


def download_ms():
    """ModelScope:CosyVoice3 + SenseVoice-Small。"""
    from modelscope import snapshot_download as ms_download

    for repo, name in [
        ("FunAudioLLM/Fun-CosyVoice3-0.5B-2512", "Fun-CosyVoice3-0.5B-2512"),
        ("iic/SenseVoiceSmall", "SenseVoiceSmall"),
    ]:
        dest = os.path.join(MODELS_DIR, name)
        print(f"== [MS] {repo} -> {dest}", flush=True)
        ms_download(repo, local_dir=dest)
        print(f"== [MS] {name} 完成", flush=True)


def download_hf():
    """HuggingFace(hf-mirror):MuseTalk 配套件 + IndexTTS-2。

    注意:hf_hub 0.28 的 hf_hub_download 对 hf-mirror 的 HEAD 请求有兼容问题,
    这里用 requests 直连 resolve URL 下载(已验证可用)。
    """
    import requests

    base = "https://hf-mirror.com/{repo}/resolve/main/{path}"
    jobs = [
        (
            "TMElyralab/MuseTalk",
            MUSETALK_MODELS,
            [
                "musetalk/musetalk.json",
                "musetalk/pytorch_model.bin",
                "musetalkV15/musetalk.json",
                "musetalkV15/unet.pth",
            ],
        ),
        (
            "stabilityai/sd-vae-ft-mse",
            os.path.join(MUSETALK_MODELS, "sd-vae"),
            [
                "config.json",
                "diffusion_pytorch_model.bin",
            ],
        ),
        (
            "openai/whisper-tiny",
            os.path.join(MUSETALK_MODELS, "whisper"),
            [
                "config.json",
                "pytorch_model.bin",
                "preprocessor_config.json",
            ],
        ),
        (
            "ByteDance/LatentSync",
            os.path.join(MUSETALK_MODELS, "syncnet"),
            [
                "latentsync_syncnet.pt",
            ],
        ),
        # MuseTalk 人脸解析权重(官方 download_weights.bat 指定的 HF 来源,
        # 文件清单与放置路径同 third_party/MuseTalk/README.md)
        (
            "ManyOtherFunctions/face-parse-bisent",
            os.path.join(MUSETALK_MODELS, "face-parse-bisent"),
            [
                "79999_iter.pth",
                "resnet18-5c106cde.pth",
            ],
        ),
        (
            "IndexTeam/IndexTTS-2",
            os.path.join(MODELS_DIR, "IndexTTS-2"),
            [
                "bpe.model",
                "config.yaml",
                "feat1.pt",
                "feat2.pt",
                "gpt.pth",
                "s2mel.pth",
                "wav2vec2bert_stats.pt",
                "qwen0.6bemo4-merge/Modelfile",
                "qwen0.6bemo4-merge/added_tokens.json",
                "qwen0.6bemo4-merge/chat_template.jinja",
                "qwen0.6bemo4-merge/config.json",
                "qwen0.6bemo4-merge/generation_config.json",
                "qwen0.6bemo4-merge/merges.txt",
                "qwen0.6bemo4-merge/model.safetensors",
                "qwen0.6bemo4-merge/special_tokens_map.json",
                "qwen0.6bemo4-merge/tokenizer.json",
                "qwen0.6bemo4-merge/tokenizer_config.json",
                "qwen0.6bemo4-merge/vocab.json",
            ],
        ),
    ]
    for repo, dest_dir, files in jobs:
        for rel in files:
            url = base.format(repo=repo, path=rel)
            out = os.path.join(dest_dir, rel.replace("/", os.sep))
            os.makedirs(os.path.dirname(out), exist_ok=True)
            if os.path.exists(out) and os.path.getsize(out) > 0:
                print(f"== [HF] 跳过已存在 {rel}", flush=True)
                continue
            print(f"== [HF] 下载 {repo}/{rel}", flush=True)
            part = out + ".part"
            for attempt in range(1, 11):  # 断点续传 + 最多 10 次重试
                headers = {}
                mode = "wb"
                pos = os.path.getsize(part) if os.path.exists(part) else 0
                if pos:
                    headers["Range"] = f"bytes={pos}-"
                    mode = "ab"
                try:
                    with requests.get(url, stream=True, timeout=60, headers=headers) as r:
                        if r.status_code == 416:  # 已完整
                            break
                        r.raise_for_status()
                        total = int(r.headers.get("Content-Length", 0)) + pos
                        done = pos
                        with open(part, mode) as f:
                            for chunk in r.iter_content(chunk_size=1 << 20):
                                f.write(chunk)
                                done += len(chunk)
                                if total and done % (500 << 20) < (1 << 20):
                                    print(f"    {done / 1e9:.2f}/{total / 1e9:.2f} GB", flush=True)
                    break
                except Exception as e:
                    print(f"    中断(第{attempt}次,已下 {pos / 1e9:.2f} GB): {type(e).__name__},5 秒后续传", flush=True)
                    import time

                    time.sleep(5)
            else:
                raise RuntimeError(f"{rel} 重试 10 次仍失败")
            os.replace(part, out)
            print(f"== [HF] 完成 {rel} ({os.path.getsize(out) / 1e6:.1f} MB)", flush=True)

    # bilibili 模型协议要求每份拷贝附协议全文:下载完成后把 LICENSE 复制进权重目录
    import shutil

    lic_src = os.path.join(SRC_V2, "third_party", "index-tts", "LICENSE")
    lic_dst = os.path.join(MODELS_DIR, "IndexTTS-2", "LICENSE")
    if os.path.isdir(os.path.join(MODELS_DIR, "IndexTTS-2")) and os.path.exists(lic_src):
        shutil.copyfile(lic_src, lic_dst)
        print(f"== [HF] 已复制 bilibili 协议 -> {lic_dst}", flush=True)


def download_s3fd():
    """S3FD 人脸检测权重(MuseTalk 依赖,官方 URL 直连,断点续传)。

    注:即使不跑本函数,sfd_detector.py 在缺文件时也会用 torch load_url
    自动下载到 torch hub 缓存;这里提前落到引擎目录,离线可用。
    """
    import time

    import requests

    out = S3FD_PATH
    if os.path.exists(out) and os.path.getsize(out) > 0:
        print("== [S3FD] 跳过已存在 s3fd.pth", flush=True)
        return
    os.makedirs(os.path.dirname(out), exist_ok=True)
    part = out + ".part"
    for attempt in range(1, 11):
        headers = {}
        mode = "wb"
        pos = os.path.getsize(part) if os.path.exists(part) else 0
        if pos:
            headers["Range"] = f"bytes={pos}-"
            mode = "ab"
        try:
            with requests.get(S3FD_URL, stream=True, timeout=60, headers=headers) as r:
                if r.status_code == 416:  # 已完整
                    break
                r.raise_for_status()
                with open(part, mode) as f:
                    for chunk in r.iter_content(chunk_size=1 << 20):
                        f.write(chunk)
            break
        except Exception as e:
            print(f"    [S3FD] 中断(第{attempt}次): {type(e).__name__},5 秒后续传", flush=True)
            time.sleep(5)
    else:
        raise RuntimeError("s3fd.pth 重试 10 次仍失败(也可由引擎首次运行时自动下载)")
    os.replace(part, out)
    print(f"== [S3FD] 完成 s3fd.pth ({os.path.getsize(out) / 1e6:.1f} MB)", flush=True)


if __name__ == "__main__":
    which = sys.argv[1] if len(sys.argv) > 1 else "all"
    os.makedirs(MODELS_DIR, exist_ok=True)
    if which in ("ms", "all"):
        download_ms()
    if which in ("hf", "all"):
        download_hf()
        download_s3fd()
    print("== 全部下载任务结束 ==", flush=True)
