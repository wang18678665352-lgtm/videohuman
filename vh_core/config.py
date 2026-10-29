"""配置加载：pydantic-settings，configs/default.toml + VH_ 环境变量覆盖。"""

from __future__ import annotations

from pathlib import Path

from pydantic import AliasChoices, Field
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    TomlConfigSettingsSource,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_FILE = PROJECT_ROOT / "configs" / "default.toml"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        toml_file=str(DEFAULT_CONFIG_FILE),
        env_prefix="VH_",
        extra="ignore",
    )

    ffmpeg_bin: str = "ffmpeg"
    workspace_dir: Path = Path("workspace")
    assets_dir: Path = Path("assets")
    models_dir: Path = Path("models")
    third_party_dir: Path = Path("third_party")

    # 发布:各平台登录态(cookie)存放目录,敏感数据勿入库
    publish_cookies_dir: Path = Path("configs/cookies")

    # 抖音下载 cookie(env VH_DOUYIN_COOKIE 注入,勿写进仓库文件)
    douyin_cookie: str = ""

    # LLM 改写(rewrite stage);api_key 为空 = 直通
    llm_base_url: str = "https://api.deepseek.com"
    llm_api_key: str = ""
    llm_model: str = "deepseek-chat"
    llm_timeout: float = 120.0  # 单次请求超时(秒)

    # AI 生成内容标识(合规):画面角标文字 + 隐式元数据里的内容制作者名称
    aigc_label_text: str = "本视频由AI生成"
    aigc_producer: str = "videohuman"
    # 商用模式下禁止关闭画面 AI 生成角标。环境变量 VH_COMMERCIAL。
    commercial_mode: bool = Field(
        default=True,
        validation_alias=AliasChoices("VH_COMMERCIAL", "VH_COMMERCIAL_MODE", "commercial_mode"),
    )

    # LLM 改写提示词文件(空=内置合规版:润色/纠错,忠于原意);详见 stages/rewrite.py
    llm_prompt_file: str = ""

    # Web 工作台(服务端)
    web_token: str = ""  # 访问令牌,env VH_WEB_TOKEN;非本机监听时必须设置
    cors_origins: str = ""  # 逗号分隔的跨域白名单;同源部署留空即可
    max_upload_mb: int = 2048  # 单个素材上传上限
    log_dir: Path = Path("logs")  # 服务端日志目录(按天滚动)
    workflows_dir: Path = Path("configs/workflows")  # 用户自定义工作流(JSON)

    # TTS 参考音频(默认 CosyVoice 仓库自带示例)
    tts_ref_audio: str = ""
    tts_ref_text: str = "希望你以后能够做的比我还好呦。"
    cosyvoice_model_dir: str = "models/Fun-CosyVoice3-0.5B-2512"
    indextts_model_dir: str = "models/IndexTTS-2"
    indextts_repo_dir: str = "third_party/index-tts"

    # 数字人素材视频(默认空,必须由 spec/CLI 提供)
    avatar_video: str = ""

    # 数字人引擎:musetalk(默认,MIT 开源)| lstmsync(可选,引擎与权重需用户自备)
    avatar_engine: str = "musetalk"
    lstmsync_dir: str = "third_party/lstmsync"

    # TTS 引擎:cosyvoice3(本地) | indextts2(本地)
    tts_engine: str = "cosyvoice3"

    # 后期:BGM(空=自动选 assets/bgm 第一首)与字幕样式
    bgm_path: str = ""
    bgm_volume: float = 0.15
    subtitle_font: str = ""  # 空=自动选 assets/font 第一个字体
    subtitle_fontsize: int = 44

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return (
            init_settings,
            env_settings,
            TomlConfigSettingsSource(settings_cls),
            file_secret_settings,
        )

    def abs_path(self, p: Path) -> Path:
        """配置里的目录允许相对项目根书写，这里统一转绝对路径。"""
        return p if p.is_absolute() else (PROJECT_ROOT / p).resolve()

    @property
    def workspace(self) -> Path:
        return self.abs_path(self.workspace_dir)

    @property
    def assets(self) -> Path:
        return self.abs_path(self.assets_dir)

    @property
    def publish_cookies(self) -> Path:
        return self.abs_path(self.publish_cookies_dir)

    @property
    def logs(self) -> Path:
        return self.abs_path(self.log_dir)

    @property
    def models(self) -> Path:
        return self.abs_path(self.models_dir)

    @property
    def third_party(self) -> Path:
        return self.abs_path(self.third_party_dir)

    @property
    def cosyvoice_repo(self) -> Path:
        return self.third_party / "CosyVoice"

    @property
    def musetalk_repo(self) -> Path:
        return self.third_party / "MuseTalk"

    @property
    def indextts_repo(self) -> Path:
        return self.abs_path(Path(self.indextts_repo_dir))

    @property
    def indextts_model(self) -> Path:
        return self.abs_path(Path(self.indextts_model_dir))

    @property
    def cosyvoice_model(self) -> Path:
        return self.abs_path(Path(self.cosyvoice_model_dir))

    @property
    def default_ref_audio(self) -> Path:
        if self.tts_ref_audio:
            return self.abs_path(Path(self.tts_ref_audio))
        return self.cosyvoice_repo / "asset" / "zero_shot_prompt.wav"


def load_settings(**overrides) -> Settings:
    return Settings(**overrides)
