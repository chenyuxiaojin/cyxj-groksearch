import os
import json
from pathlib import Path


class Config:
    _instance = None
    _DEFAULT_MODEL = "grok-4.3-console"

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._config_file = None
            cls._instance._cached_model = None
        return cls._instance

    @property
    def config_file(self) -> Path:
        if self._config_file is None:
            config_dir = Path.home() / ".config" / "grok-search"
            try:
                config_dir.mkdir(parents=True, exist_ok=True)
            except OSError:
                config_dir = Path.cwd() / ".grok-search"
                config_dir.mkdir(parents=True, exist_ok=True)
            self._config_file = config_dir / "config.json"
        return self._config_file

    def _load_config_file(self) -> dict:
        if not self.config_file.exists():
            return {}
        try:
            with open(self.config_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return {}

    def _save_config_file(self, config_data: dict) -> None:
        try:
            with open(self.config_file, "w", encoding="utf-8") as f:
                json.dump(config_data, f, ensure_ascii=False, indent=2)
        except IOError as e:
            raise ValueError(f"无法保存配置文件: {str(e)}")

    @property
    def debug_enabled(self) -> bool:
        return os.getenv("GROK_DEBUG", "false").lower() in ("true", "1", "yes")

    @property
    def retry_max_attempts(self) -> int:
        return int(os.getenv("GROK_RETRY_MAX_ATTEMPTS", "3"))

    @property
    def retry_multiplier(self) -> float:
        return float(os.getenv("GROK_RETRY_MULTIPLIER", "1"))

    @property
    def retry_max_wait(self) -> int:
        return int(os.getenv("GROK_RETRY_MAX_WAIT", "10"))

    @property
    def fetch_hedge_delay(self) -> float:
        """web_fetch 对冲延迟（秒）：Tavily 超过该时长未返回时并行追加 Firecrawl。
        设为 0 表示两者始终并发（更快但多耗 Firecrawl 额度）。"""
        try:
            return max(0.0, float(os.getenv("GROK_FETCH_HEDGE_DELAY", "8")))
        except ValueError:
            return 8.0

    @property
    def grok_api_url(self) -> str:
        url = os.getenv("GROK_API_URL")
        if not url:
            raise ValueError(
                "Grok API URL 未配置！请在环境变量（或 密钥存储/.env）中设置 "
                "GROK_API_URL（OpenAI 兼容端点，含 /v1）与 GROK_API_KEY。"
            )
        return url

    @property
    def grok_api_key(self) -> str:
        key = os.getenv("GROK_API_KEY")
        if not key:
            raise ValueError("Grok API Key 未配置！请设置环境变量 GROK_API_KEY。")
        return key

    @property
    def tavily_enabled(self) -> bool:
        return os.getenv("TAVILY_ENABLED", "true").lower() in ("true", "1", "yes")

    @property
    def tavily_api_url(self) -> str:
        return os.getenv("TAVILY_API_URL") or "https://api.tavily.com"

    @property
    def tavily_api_key(self) -> str | None:
        keys = self.tavily_api_keys
        return keys[0] if keys else None

    @property
    def tavily_api_keys(self) -> list[str]:
        """Tavily key 列表（多 key 轮询）。优先 TAVILY_API_KEYS（逗号分隔），回落 TAVILY_API_KEY。"""
        for raw in (os.getenv("TAVILY_API_KEYS"), os.getenv("TAVILY_API_KEY")):
            if raw:
                keys = [k.strip() for k in raw.split(",") if k.strip()]
                if keys:
                    return keys
        return []

    @property
    def firecrawl_api_url(self) -> str:
        return os.getenv("FIRECRAWL_API_URL") or "https://api.firecrawl.dev/v2"

    @property
    def firecrawl_api_keys(self) -> list[str]:
        """统一 Firecrawl key 列表，供抓取降级 / 补信源 / 截图三用。
        优先级：FIRECRAWL_API_KEYS → FIRECRAWL_API_KEY
                → FIRECRAWL_SCREENSHOT_API_KEYS → FIRECRAWL_SCREENSHOT_API_KEY（向后兼容旧 .env）。"""
        candidates = (
            os.getenv("FIRECRAWL_API_KEYS"),
            os.getenv("FIRECRAWL_API_KEY"),
            os.getenv("FIRECRAWL_SCREENSHOT_API_KEYS"),
            os.getenv("FIRECRAWL_SCREENSHOT_API_KEY"),
        )
        for raw in candidates:
            if raw:
                keys = [k.strip() for k in raw.split(",") if k.strip()]
                if keys:
                    return keys
        return []

    @property
    def firecrawl_api_key(self) -> str | None:
        keys = self.firecrawl_api_keys
        return keys[0] if keys else None

    @property
    def log_level(self) -> str:
        return os.getenv("GROK_LOG_LEVEL", "INFO").upper()

    @property
    def log_dir(self) -> Path:
        log_dir_str = os.getenv("GROK_LOG_DIR", "logs")
        log_dir = Path(log_dir_str)
        if log_dir.is_absolute():
            return log_dir
        home_log_dir = Path.home() / ".config" / "grok-search" / log_dir_str
        try:
            home_log_dir.mkdir(parents=True, exist_ok=True)
            return home_log_dir
        except OSError:
            pass
        cwd_log_dir = Path.cwd() / log_dir_str
        try:
            cwd_log_dir.mkdir(parents=True, exist_ok=True)
            return cwd_log_dir
        except OSError:
            pass
        tmp_log_dir = Path("/tmp") / "grok-search" / log_dir_str
        tmp_log_dir.mkdir(parents=True, exist_ok=True)
        return tmp_log_dir

    def _apply_model_suffix(self, model: str) -> str:
        try:
            url = self.grok_api_url
        except ValueError:
            return model
        if "openrouter" in url and ":online" not in model:
            return f"{model}:online"
        return model

    @property
    def grok_model(self) -> str:
        if self._cached_model is not None:
            return self._cached_model
        model = (
            os.getenv("GROK_MODEL")
            or self._load_config_file().get("model")
            or self._DEFAULT_MODEL
        )
        self._cached_model = self._apply_model_suffix(model)
        return self._cached_model

    def set_model(self, model: str) -> None:
        config_data = self._load_config_file()
        config_data["model"] = model
        self._save_config_file(config_data)
        self._cached_model = self._apply_model_suffix(model)

    @staticmethod
    def _mask_api_key(key: str) -> str:
        if not key or len(key) <= 8:
            return "***"
        return f"{key[:4]}{'*' * (len(key) - 8)}{key[-4:]}"

    def get_config_info(self) -> dict:
        try:
            api_url = self.grok_api_url
            api_key_masked = self._mask_api_key(self.grok_api_key)
            config_status = "✅ 配置完整"
        except ValueError as e:
            api_url = "未配置"
            api_key_masked = "未配置"
            config_status = f"❌ 配置错误: {str(e)}"

        return {
            "GROK_API_URL": api_url,
            "GROK_API_KEY": api_key_masked,
            "GROK_MODEL": self.grok_model,
            "GROK_DEBUG": self.debug_enabled,
            "GROK_LOG_LEVEL": self.log_level,
            "GROK_LOG_DIR": str(self.log_dir),
            "TAVILY_API_URL": self.tavily_api_url,
            "TAVILY_ENABLED": self.tavily_enabled,
            "TAVILY_API_KEYS": [self._mask_api_key(k) for k in self.tavily_api_keys] if self.tavily_api_keys else "未配置",
            "TAVILY_API_KEYS_COUNT": len(self.tavily_api_keys),
            "FIRECRAWL_API_URL": self.firecrawl_api_url,
            "FIRECRAWL_API_KEYS": [self._mask_api_key(k) for k in self.firecrawl_api_keys] if self.firecrawl_api_keys else "未配置",
            "FIRECRAWL_API_KEYS_COUNT": len(self.firecrawl_api_keys),
            "config_status": config_status,
        }


config = Config()
