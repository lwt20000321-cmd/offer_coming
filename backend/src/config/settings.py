"""应用配置：只从 backend/.env 经 ConfigManager 读取，不读进程环境。"""

from pathlib import Path

from pycore.core import BaseSettings, ConfigManager, ConfigurationError
from pydantic import field_validator, model_validator

_BACKEND_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ENV_PATH = _BACKEND_ROOT / ".env"

_DEFAULT_CORS = [
    "http://localhost:5199",
    "http://127.0.0.1:5199",
    "http://localhost:5175",
    "http://127.0.0.1:5175",
]

_MODEL_FIELDS = (
    "llm_orchestrator_model",
    "llm_nudge_model",
    "llm_interview_model",
    "llm_knowledge_model",
    "llm_counseling_model",
)


class AppSettings(BaseSettings):
    debug: bool = True
    secret_key: str
    host: str = "127.0.0.1"
    port: int = 8099
    cors_origins: list[str] = _DEFAULT_CORS
    database_path: str = "data/offer_coming.db"
    upload_dir: str = "data/uploads"
    resume_max_bytes: int = 10485760
    knowledge_max_bytes: int = 10485760
    resume_allowed_extensions: list[str] = [".pdf", ".docx", ".txt"]
    knowledge_allowed_extensions: list[str] = [".pdf", ".docx", ".txt", ".md"]
    session_ttl_days: int = 30
    timezone: str = "Asia/Shanghai"
    llm_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    llm_api_key: str = ""
    llm_allow_mock: bool = False
    # 废弃：不得作为五套模型的唯一来源
    llm_model: str = ""
    llm_orchestrator_model: str = "qwen-plus"
    llm_nudge_model: str = "qwen-flash"
    llm_interview_model: str = "qwen-max"
    llm_knowledge_model: str = "qwen-plus-latest"
    llm_counseling_model: str = "qwen-turbo"
    llm_orchestrator_temperature: float = 0.2
    llm_nudge_temperature: float = 0.7
    llm_interview_temperature: float = 0.3
    llm_knowledge_temperature: float = 0.2
    llm_counseling_temperature: float = 0.6
    llm_timeout_seconds: float = 90
    llm_connect_timeout_seconds: float = 10
    llm_max_retries: int = 2
    llm_enable_thinking: bool = False
    llm_reply_max_tokens: int = 2048
    llm_reply_max_chars: int = 500
    llm_history_max_messages: int = 16
    llm_history_max_chars: int = 400
    agent_max_steps: int = 8
    knowledge_search_max_items: int = 3
    jd_fetch_timeout_seconds: float = 15
    jd_fetch_user_agent: str = "offer_coming/1.0"
    smtp_host: str = ""
    smtp_port: int | None = None
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = ""
    smtp_use_tls: bool = True
    message_list_default: int = 200
    message_list_max: int = 200

    @field_validator("smtp_port", mode="before")
    @classmethod
    def empty_smtp_port(cls, value: object) -> object:
        if value == "":
            return None
        return value

    @model_validator(mode="after")
    def five_models_must_be_distinct(self) -> "AppSettings":
        names: list[str] = []
        missing: list[str] = []
        for field in _MODEL_FIELDS:
            raw = getattr(self, field)
            value = raw.strip() if isinstance(raw, str) else ""
            if not value:
                missing.append(field)
            names.append(value)
        if missing:
            raise ValueError(f"五套模型未配齐：{', '.join(missing)}")
        if len(set(names)) != 5:
            raise ValueError("五套模型名称必须互不相同，不能静默退回单模型")
        return self


def load_app_config(config_path: str | Path | None = None) -> ConfigManager[AppSettings]:
    path = Path(config_path) if config_path is not None else DEFAULT_ENV_PATH
    config = ConfigManager[AppSettings]()
    config.load(AppSettings, path, use_env=False)
    return config


def load_settings_from_dict(data: dict) -> AppSettings:
    config: ConfigManager[AppSettings] = ConfigManager[AppSettings]()
    config.load_from_dict(AppSettings, data)
    return config.settings


def get_settings() -> AppSettings:
    config: ConfigManager[AppSettings] = ConfigManager.instance()
    settings: AppSettings = config.settings
    if not isinstance(settings, AppSettings):
        raise ConfigurationError("Configuration not loaded as AppSettings")
    return settings


def config_is_loaded() -> bool:
    try:
        loaded: object = ConfigManager.instance().settings
    except ConfigurationError:
        return False
    return isinstance(loaded, AppSettings)
