"""AvatarProvider 的 LstmSync(ONNX)本地实现。

引擎代码与权重不随本仓库分发,需用户自行获取(见 README,目录由 config lstmsync_dir 指向),要点:
- 唇形网络 checkpoints/256.onnx(onnxruntime CUDA,LSTM 时序逐步推理);
  音频特征 checkpoints/chinese-hubert-large(HuBERT,fp16);
  人脸检测 insightface buffalo_l(checkpoints/auxiliary);
- 引擎每次 run() 重建 ort session 与 HuBERT(秒级),FaceAnalysis 随实例驻留;
- 引擎内部 subprocess.call('ffmpeg ...') 依赖 PATH:导入后把其模块命名空间里的
  subprocess 换成 shim,ffmpeg 改走项目的解析链(imageio-ffmpeg 内置);
- 音频长于素材时引擎自带 ping-pong 循环;帧里检不出人脸会直接抛 "no face"。
"""

from __future__ import annotations

import os
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any

from loguru import logger

from ...config import Settings
from ...ffmpeg_utils import get_ffmpeg, run_ffmpeg

_STATE: dict[str, Any] = {}


def _patch_subprocess(mod: Any, ffmpeg_exe: str) -> None:
    """把引擎模块里的 subprocess.call('ffmpeg ...') 重定向到指定 ffmpeg 二进制。"""

    class _Shim:
        @staticmethod
        def call(cmd: str, shell: bool = False, **kwargs: Any) -> int:
            args = shlex.split(cmd, posix=False)
            if args and args[0].lower() == "ffmpeg":
                args[0] = ffmpeg_exe
            rc = subprocess.call(args, shell=shell, **kwargs)
            if rc != 0:
                raise RuntimeError(f"引擎内 ffmpeg 调用失败(code={rc}): {cmd[:200]}")
            return rc

    mod.subprocess = _Shim()


class LstmSyncAvatar:
    """以 video 为形象、audio 为驱动音频生成数字人视频(ONNX wav2lip 系)。"""

    def __init__(self, settings: Settings):
        self.settings = settings

    @property
    def name(self) -> str:
        return "lstmsync-onnx"

    def _engine(self, model: str, batch_size: int, sync_offset: int, scale_h: float, scale_w: float):
        key = (model, batch_size, sync_offset, scale_h, scale_w)
        # 注意:"key" 里存的是参数元组本身,不能写成 _STATE.get("key")(dict 键是字符串)
        if _STATE.get("key") is not None and _STATE["key"] == key:
            return _STATE["engine"]
        root = Path(self.settings.lstmsync_dir)
        if not root.is_absolute():
            root = self.settings.abs_path(root)
        if not (root / "lstmsync_func.py").exists():
            raise FileNotFoundError(f"LstmSync 引擎目录无效(缺 lstmsync_func.py): {root}")
        if str(root) not in sys.path:
            sys.path.insert(0, str(root))
        import lstmsync_func

        _patch_subprocess(lstmsync_func, get_ffmpeg(self.settings))
        logger.info(f"加载 LstmSync 引擎: {root / 'checkpoints' / model}")
        engine = lstmsync_func.LstmSync(
            human_path=str(root / "checkpoints" / model),
            hubert_path=str(root / "checkpoints" / "chinese-hubert-large"),
            batch_size=batch_size,
            sync_offset=sync_offset,
            scale_h=scale_h,
            scale_w=scale_w,
        )
        _STATE.clear()
        _STATE.update(key=key, engine=engine)
        return _STATE["engine"]

    def _sanitize_material(self, video: Path, work_dir: Path, engine) -> Path:
        """逐帧人脸预检,丢弃无脸/过渡帧,合成"帧帧有脸"的干净素材。

        LstmSync 遇无脸帧直接抛异常,而 vlog 素材常见亚秒级甩镜过渡帧,
        肉眼抽帧根本看不见——所以在送引擎前先洗一遍。检测器直接复用引擎的
        FaceAnalysis 实例,判据与引擎完全一致。
        """
        import cv2

        detector = engine.detect_face.face_detector
        cap = cv2.VideoCapture(str(video))
        fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
        frames_dir = work_dir / "clean_frames"
        frames_dir.mkdir(parents=True, exist_ok=True)
        total = kept = 0
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            total += 1
            bbox, _ = detector(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
            if bbox is None:
                continue
            cv2.imwrite(str(frames_dir / f"{kept:08d}.png"), frame)
            kept += 1
        cap.release()
        dropped = total - kept
        if dropped:
            logger.info(f"素材清洗: {total} 帧丢 {dropped} 帧(无脸/过渡),保留 {kept} 帧")
        if kept < int(fps):
            raise RuntimeError(f"素材可用帧过少({total} 帧中仅 {kept} 帧有正脸),请换正脸口播素材")
        clean = work_dir / "clean.mp4"
        run_ffmpeg(
            self.settings,
            [
                "-framerate",
                str(round(fps)),
                "-i",
                str(frames_dir / "%08d.png"),
                "-c:v",
                "libx264",
                "-crf",
                "16",
                "-pix_fmt",
                "yuv420p",
                str(clean),
            ],
            desc="清洗素材合成",
        )
        return clean

    def generate(
        self,
        video: Path,
        audio: Path,
        out_path: Path,
        work_dir: Path | None = None,
        batch_size: int = 4,
        sync_offset: int = 0,
        scale_h: float = 1.6,
        scale_w: float = 3.6,
        model: str = "256.onnx",
        **kwargs: Any,
    ) -> Path:
        video, audio = Path(video), Path(audio)
        if not video.exists():
            raise FileNotFoundError(f"数字人素材视频不存在: {video}")
        if not audio.exists():
            raise FileNotFoundError(f"驱动音频不存在: {audio}")

        engine = self._engine(model, batch_size, sync_offset, scale_h, scale_w)

        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        work_dir = Path(work_dir) if work_dir else out_path.parent / "_lstmsync_tmp"
        # 引擎检不出人脸时写相对路径 temp/noface.jpg,chdir 到 work_dir 接住;
        # 因此传给引擎的路径必须全部先转绝对,否则 chdir 后相对路径全错
        video = video.resolve()
        audio = audio.resolve()
        out_path = out_path.resolve()
        work_dir = work_dir.resolve()
        (work_dir / "temp").mkdir(parents=True, exist_ok=True)
        clean_video = self._sanitize_material(video, work_dir, engine)
        old_cwd = os.getcwd()
        os.chdir(work_dir)
        try:
            engine.run(
                video_path=str(clean_video),
                video_fps25_path=str(work_dir / "fps25.mp4"),
                video_temp_path=str(work_dir / "lip"),  # 引擎自动追加 .avi
                audio_path=str(audio),
                audio_temp_path=str(work_dir / "audio16k.wav"),
                video_out_path=str(out_path),
            )
        finally:
            os.chdir(old_cwd)
        if not out_path.exists():
            raise RuntimeError("LstmSync 未产出视频(常见原因:素材帧里检不出正脸,见 work_dir/temp/noface.jpg)")
        logger.info(f"数字人视频生成完成: {out_path}")
        return out_path

    def unload(self) -> None:
        _STATE.clear()
