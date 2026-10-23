"""AvatarProvider 的 MuseTalk 1.5 本地实现(进程内推理,lazy 单例、跨 job 驻留)。

移植自 third_party/MuseTalk/scripts/inference.py 的主流程(v15 路径),
差异:
- 模型只加载一次,驻留显存供批量连续生成;
- ffmpeg 调用改 subprocess(原脚本 os.system 拼接命令,路径含空格会炸);
- MuseTalk 代码大量硬编码相对路径(models/...),通过 chdir 到仓库根满足;
- 人脸 landmark 走打补丁后的 preprocessing(mediapipe,无 mmpose)。
"""

from __future__ import annotations

import copy
import glob
import os
import shutil
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import numpy as np
from loguru import logger

from ...config import Settings
from ...ffmpeg_utils import run_ffmpeg

_STATE: dict[str, Any] = {}


@contextmanager
def _musetalk_env(settings: Settings):
    """chdir 到 MuseTalk 仓库根 + sys.path 注入(其代码硬编码相对路径)。"""
    repo = settings.musetalk_repo
    old_cwd = os.getcwd()
    for p in (repo, repo / "musetalk" / "utils"):
        sp = str(p)
        if sp not in sys.path:
            sys.path.insert(0, sp)
    os.chdir(repo)
    try:
        yield
    finally:
        os.chdir(old_cwd)


def _load_models(settings: Settings, use_float16: bool) -> dict[str, Any]:
    if _STATE.get("loaded") and _STATE.get("fp16") == use_float16:
        return _STATE
    with _musetalk_env(settings):
        import torch
        from musetalk.utils.audio_processor import AudioProcessor
        from musetalk.utils.face_parsing import FaceParsing
        from musetalk.utils.utils import load_all_model
        from transformers import WhisperModel

        device = torch.device("cuda:0" if torch.cuda.is_available() else "cpu")
        models_dir = settings.musetalk_repo / "models"
        logger.info("加载 MuseTalk V15 模型(vae/unet/whisper/face-parsing)...")
        vae, unet, pe = load_all_model(
            unet_model_path=str(models_dir / "musetalkV15" / "unet.pth"),
            vae_type="sd-vae",
            unet_config=str(models_dir / "musetalkV15" / "musetalk.json"),
            device=device,
        )
        if use_float16:
            pe = pe.half()
            vae.vae = vae.vae.half()
            unet.model = unet.model.half()
        pe = pe.to(device)
        vae.vae = vae.vae.to(device)
        unet.model = unet.model.to(device)

        whisper_dir = str(models_dir / "whisper")
        audio_processor = AudioProcessor(feature_extractor_path=whisper_dir)
        whisper = WhisperModel.from_pretrained(whisper_dir)
        whisper = whisper.to(device=device, dtype=unet.model.dtype).eval()
        whisper.requires_grad_(False)

        fp = FaceParsing(left_cheek_width=90, right_cheek_width=90)

        _STATE.clear()
        _STATE.update(
            loaded=True,
            fp16=use_float16,
            device=device,
            vae=vae,
            unet=unet,
            pe=pe,
            whisper=whisper,
            audio_processor=audio_processor,
            fp=fp,
            timesteps=torch.tensor([0], device=device),
        )
        logger.info("MuseTalk 模型加载完成")
        return _STATE


class MuseTalkAvatar:
    """以 video(或单张图片)为形象、audio 为驱动音频生成数字人视频。"""

    def __init__(self, settings: Settings):
        self.settings = settings

    @property
    def name(self) -> str:
        return "musetalk-v15"

    def generate(
        self,
        video: Path,
        audio: Path,
        out_path: Path,
        work_dir: Path | None = None,
        batch_size: int = 8,
        fps: int = 25,
        extra_margin: int = 10,
        parsing_mode: str = "jaw",
        use_float16: bool = True,
        audio_padding_length_left: int = 2,
        audio_padding_length_right: int = 2,
        **kwargs: Any,
    ) -> Path:
        if not Path(video).exists():
            raise FileNotFoundError(f"数字人素材视频不存在: {video}")
        if not Path(audio).exists():
            raise FileNotFoundError(f"驱动音频不存在: {audio}")

        s = _load_models(self.settings, use_float16)
        vae, unet, pe = s["vae"], s["unet"], s["pe"]
        device, timesteps = s["device"], s["timesteps"]

        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        work_dir = Path(work_dir) if work_dir else out_path.parent / "_musetalk_tmp"
        frames_out = work_dir / "res_frames"
        frames_out.mkdir(parents=True, exist_ok=True)

        with _musetalk_env(self.settings):
            import cv2
            from musetalk.utils.blending import get_image
            from musetalk.utils.preprocessing import (
                coord_placeholder,
                get_landmark_and_bbox,
            )
            from musetalk.utils.utils import (
                datagen,
                get_file_type,
                get_video_fps,
            )

            video = str(video)
            audio = str(audio)

            # 1. 拆帧(单张图片则直接作为唯一帧,输出帧率取 fps 参数)
            ftype = get_file_type(video)
            if ftype == "video":
                src_frames_dir = work_dir / "src_frames"
                src_frames_dir.mkdir(exist_ok=True)
                run_ffmpeg(
                    self.settings,
                    [
                        "-v",
                        "fatal",
                        "-i",
                        video,
                        "-start_number",
                        "0",
                        str(src_frames_dir / "%08d.png"),
                    ],
                    desc="拆帧",
                )
                input_img_list = sorted(glob.glob(str(src_frames_dir / "*.png")))
                fps = get_video_fps(video)
            elif ftype == "image":
                input_img_list = [video]
            else:
                raise ValueError(f"不支持的素材类型: {video}")

            # 2. 音频特征(whisper-tiny encoder)
            whisper_input, librosa_length = s["audio_processor"].get_audio_feature(audio)
            whisper_chunks = s["audio_processor"].get_whisper_chunk(
                whisper_input,
                device,
                unet.model.dtype,
                s["whisper"],
                librosa_length,
                fps=fps,
                audio_padding_length_left=audio_padding_length_left,
                audio_padding_length_right=audio_padding_length_right,
            )

            # 3. landmark + 裁剪框(mediapipe 后端,见 preprocessing 补丁)
            logger.info("提取人脸 landmark 与裁剪框...")
            coord_list, frame_list = get_landmark_and_bbox(input_img_list, 0)
            logger.info(f"帧数: {len(frame_list)}")

            # 4. 逐帧裁剪 256x256 → VAE latent
            input_latent_list = []
            for bbox, frame in zip(coord_list, frame_list):
                if bbox == coord_placeholder:
                    continue
                x1, y1, x2, y2 = bbox
                y2 = min(y2 + extra_margin, frame.shape[0])
                crop = frame[y1:y2, x1:x2]
                crop = cv2.resize(crop, (256, 256), interpolation=cv2.INTER_LANCZOS4)
                input_latent_list.append(vae.get_latents_for_unet(crop))

            # 5. UNet 推理(首尾循环平滑)
            frame_cycle = frame_list + frame_list[::-1]
            coord_cycle = coord_list + coord_list[::-1]
            latent_cycle = input_latent_list + input_latent_list[::-1]
            gen = datagen(
                whisper_chunks=whisper_chunks,
                vae_encode_latents=latent_cycle,
                batch_size=batch_size,
                delay_frame=0,
                device=device,
            )
            logger.info("开始 UNet 推理...")
            res_frame_list = []
            total = int(np.ceil(len(whisper_chunks) / batch_size))
            from tqdm import tqdm

            for whisper_batch, latent_batch in tqdm(gen, total=total):
                audio_feature_batch = pe(whisper_batch)
                latent_batch = latent_batch.to(dtype=unet.model.dtype)
                pred = unet.model(
                    latent_batch,
                    timesteps,
                    encoder_hidden_states=audio_feature_batch,
                ).sample
                for res_frame in vae.decode_latents(pred):
                    res_frame_list.append(res_frame)

            # 6. 融合回原图
            logger.info("融合生成帧...")
            for i, res_frame in enumerate(tqdm(res_frame_list)):
                bbox = coord_cycle[i % len(coord_cycle)]
                ori_frame = copy.deepcopy(frame_cycle[i % len(frame_cycle)])
                x1, y1, x2, y2 = bbox
                y2 = min(y2 + extra_margin, ori_frame.shape[0])
                try:
                    res_frame = cv2.resize(res_frame.astype(np.uint8), (x2 - x1, y2 - y1))
                except cv2.error:
                    continue
                combine = get_image(ori_frame, res_frame, [x1, y1, x2, y2], mode=parsing_mode, fp=s["fp"])
                cv2.imwrite(str(frames_out / f"{i:08d}.png"), combine)

        # 7. 合成视频 + 合音轨
        tmp_video = work_dir / "tmp_noaudio.mp4"
        run_ffmpeg(
            self.settings,
            [
                "-y",
                "-v",
                "warning",
                "-r",
                str(fps),
                "-f",
                "image2",
                "-i",
                str(frames_out / "%08d.png"),
                "-vcodec",
                "libx264",
                "-vf",
                "format=yuv420p",
                "-crf",
                "18",
                str(tmp_video),
            ],
            desc="帧合成视频",
        )
        run_ffmpeg(
            self.settings,
            [
                "-y",
                "-v",
                "warning",
                "-i",
                audio,
                "-i",
                str(tmp_video),
                "-c:v",
                "copy",
                "-c:a",
                "aac",
                "-shortest",
                str(out_path),
            ],
            desc="合音轨",
        )

        shutil.rmtree(work_dir, ignore_errors=True)
        logger.info(f"数字人视频生成完成: {out_path}")
        return out_path

    def unload(self) -> None:
        _STATE.clear()
        try:
            import torch

            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except ImportError:
            pass
