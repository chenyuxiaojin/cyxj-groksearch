# GrokSearch 去广告重写 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 fork 自 `GuDaStudio/GrokSearch` 的 MCP 彻底重写为干净、去品牌、适配使用者真实环境的版本，对外保持 `grok-search` 名字与 8 个工具的参数/返回 100% 兼容。

**Architecture:** 把 1033 行、工具定义与上游 HTTP 调用揉一起的 `server.py` 按职责拆成「薄工具层 + 三个上游 client + config/key_pool/sources/prompts 基础层」。删除 GuDa 派生逻辑、6 个 `plan_*` 工具、providers 抽象层及全部死代码。Firecrawl 合并为单 key 池供抓取降级/补信源/截图三用。

**Tech Stack:** Python 3.10+，FastMCP 2.x，httpx，tenacity，pytest + pytest-asyncio，uv。

设计文档：`docs/superpowers/specs/2026-05-29-groksearch-debrand-rewrite-design.md`

---

## 目标文件结构

```
src/grok_search/
  __init__.py          导出 mcp（不变）
  server.py            仅 8 个 @mcp.tool 薄封装 + get_config_info 组装 + main()  ← 重写
  config.py            Config 单例：去 GuDa、默认模型 grok-4.3-console、Firecrawl 单通道  ← 重写
  logger.py            日志（不变）
  key_pool.py          round-robin + failover + cooldown（不变，仅复核）
  sources.py           答案/信源拆分 + SourcesCache + extract_unique_urls（并入）  ← 改
  prompts.py           仅 search_prompt  ← 新建（从 utils.py 抽）
  grok_client.py       Grok 流式 chat 客户端 + 重试 + 时间注入  ← 由 providers/grok.py 改造
  tavily_client.py     tavily_search / tavily_extract / tavily_map  ← 从 server.py 抽
  firecrawl_client.py  firecrawl_search / firecrawl_scrape / firecrawl_screenshot（统一 key 池）  ← 从 server.py 抽

tests/
  test_config.py
  test_key_pool.py
  test_sources.py
  test_firecrawl_unified.py
  test_server_tools.py

删除：src/grok_search/planning.py、src/grok_search/providers/（整包）、src/grok_search/utils.py
删除：docs/README_EN.md、images/（title.png/wgrok.png/wogrok.png）
```

**保留逻辑、仅搬运或微调的模块**（不要从零重写，照搬现有实现）：`logger.py`（原样）、`key_pool.py`（原样）、`sources.py` 拆分逻辑（原样，仅把 `from .utils import extract_unique_urls` 改成本文件内置）、`grok_client.py` 的流式解析与重试（照搬 `providers/grok.py`，仅删 3 个死方法）。

---

## Task 1: 测试脚手架 + config.py 重写（去 GuDa + Firecrawl 单通道）

**Files:**
- Create: `tests/test_config.py`
- Modify（整体重写）: `src/grok_search/config.py`

`config.py` 是改动最大、风险最高的文件（去派生 + Firecrawl 四级回落）。先写测试钉死新行为。

- [ ] **Step 1: 写失败测试 `tests/test_config.py`**

```python
import importlib
import pytest


@pytest.fixture
def fresh_config(monkeypatch):
    """每个测试拿到一个干净的 Config 单例（清掉所有相关环境变量）。"""
    for var in [
        "GROK_API_URL", "GROK_API_KEY", "GROK_MODEL",
        "TAVILY_API_URL", "TAVILY_API_KEY", "TAVILY_API_KEYS",
        "FIRECRAWL_API_URL", "FIRECRAWL_API_KEY", "FIRECRAWL_API_KEYS",
        "FIRECRAWL_SCREENSHOT_API_KEY", "FIRECRAWL_SCREENSHOT_API_KEYS",
        "GUDA_API_KEY", "GUDA_BASE_URL",
    ]:
        monkeypatch.delenv(var, raising=False)
    import grok_search.config as config_mod
    importlib.reload(config_mod)
    # 单例可能已缓存，强制重建
    config_mod.Config._instance = None
    return config_mod.Config()


def test_grok_url_and_key_required(fresh_config):
    with pytest.raises(ValueError):
        _ = fresh_config.grok_api_url
    with pytest.raises(ValueError):
        _ = fresh_config.grok_api_key


def test_grok_reads_env(fresh_config, monkeypatch):
    monkeypatch.setenv("GROK_API_URL", "https://relay.example.com/v1")
    monkeypatch.setenv("GROK_API_KEY", "sk-abc")
    assert fresh_config.grok_api_url == "https://relay.example.com/v1"
    assert fresh_config.grok_api_key == "sk-abc"


def test_tavily_url_falls_back_to_official(fresh_config):
    assert fresh_config.tavily_api_url == "https://api.tavily.com"


def test_firecrawl_url_falls_back_to_official(fresh_config):
    assert fresh_config.firecrawl_api_url == "https://api.firecrawl.dev/v2"


def test_tavily_keys_multi(fresh_config, monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEYS", "k1, k2 ,k3")
    assert fresh_config.tavily_api_keys == ["k1", "k2", "k3"]


def test_firecrawl_priority_keys_first(fresh_config, monkeypatch):
    monkeypatch.setenv("FIRECRAWL_API_KEYS", "a,b")
    monkeypatch.setenv("FIRECRAWL_API_KEY", "single")
    monkeypatch.setenv("FIRECRAWL_SCREENSHOT_API_KEYS", "s1,s2")
    assert fresh_config.firecrawl_api_keys == ["a", "b"]


def test_firecrawl_falls_back_to_screenshot_keys(fresh_config, monkeypatch):
    # 使用者现状：只有 FIRECRAWL_SCREENSHOT_API_KEYS
    monkeypatch.setenv("FIRECRAWL_SCREENSHOT_API_KEYS", "fc1,fc2")
    assert fresh_config.firecrawl_api_keys == ["fc1", "fc2"]
    assert fresh_config.firecrawl_api_key == "fc1"


def test_firecrawl_empty_when_unset(fresh_config):
    assert fresh_config.firecrawl_api_keys == []
    assert fresh_config.firecrawl_api_key is None


def test_default_model_is_console(fresh_config):
    fresh_config._cached_model = None
    assert fresh_config.grok_model == "grok-4.3-console"


def test_no_guda_anywhere(fresh_config, monkeypatch):
    monkeypatch.setenv("GROK_API_URL", "https://relay.example.com/v1")
    monkeypatch.setenv("GROK_API_KEY", "sk-abc")
    info = fresh_config.get_config_info()
    assert not any("GUDA" in k or "guda" in str(v).lower() for k, v in info.items())
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd ~/项目/自己的应用/GrokSearch && uv run pytest tests/test_config.py -v`
Expected: FAIL（旧 config 仍有 GUDA、默认模型为 grok-4.20-beta、无 firecrawl_api_keys 属性）

- [ ] **Step 3: 整体重写 `src/grok_search/config.py`**

```python
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
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd ~/项目/自己的应用/GrokSearch && uv run pytest tests/test_config.py -v`
Expected: PASS（10 个测试全过）

- [ ] **Step 5: 提交**

```bash
git add tests/test_config.py src/grok_search/config.py
git commit -m "refactor(config): 去 GuDa 派生逻辑，Firecrawl 单通道四级回落，默认模型 grok-4.3-console"
```

---

## Task 2: prompts.py + sources.py（抽 prompt、内置 url 提取）

**Files:**
- Create: `src/grok_search/prompts.py`
- Modify: `src/grok_search/sources.py`（首行 import）
- Create: `tests/test_sources.py`

- [ ] **Step 1: 新建 `src/grok_search/prompts.py`**

把 `src/grok_search/utils.py` 中 `search_prompt = """..."""` 整段（约 209-241 行）原样复制到新文件 `prompts.py`。**只复制 `search_prompt` 一个，不要复制 `fetch_prompt`/`url_describe_prompt`/`rank_sources_prompt`**（它们随死方法删除）。

- [ ] **Step 2: 把 `extract_unique_urls` 内置进 sources.py**

在 `src/grok_search/sources.py` 顶部，把 `from .utils import extract_unique_urls` 删除，改为在本文件直接定义（从 `utils.py` 第 5-17 行原样搬入）：

```python
_URL_PATTERN = re.compile(r'https?://[^\s<>"\'`，。、；：！？》）】\)]+')


def extract_unique_urls(text: str) -> list[str]:
    """从文本中提取所有唯一 URL，按首次出现顺序排列。"""
    seen: set[str] = set()
    urls: list[str] = []
    for m in _URL_PATTERN.finditer(text):
        url = m.group().rstrip('.,;:!?')
        if url not in seen:
            seen.add(url)
            urls.append(url)
    return urls
```

`sources.py` 其余拆分逻辑（`split_answer_and_sources` 及四个 `_split_*`、`merge_sources`、`SourcesCache`、`new_session_id`）**原样保留不动**。

- [ ] **Step 3: 写 `tests/test_sources.py`**

```python
from grok_search.sources import (
    split_answer_and_sources, merge_sources, extract_unique_urls,
)


def test_heading_sources():
    text = "答案正文。\n\n## Sources\n- [标题A](https://a.com)\n- [标题B](https://b.com)"
    answer, sources = split_answer_and_sources(text)
    assert answer == "答案正文。"
    assert {s["url"] for s in sources} == {"https://a.com", "https://b.com"}


def test_function_call_sources():
    text = '正文内容。\ncitation_card([{"url": "https://x.com", "title": "X"}])'
    answer, sources = split_answer_and_sources(text)
    assert answer == "正文内容。"
    assert sources[0]["url"] == "https://x.com"


def test_no_sources_returns_full_text():
    text = "纯答案，没有任何信源。"
    answer, sources = split_answer_and_sources(text)
    assert answer == text
    assert sources == []


def test_merge_dedup():
    merged = merge_sources(
        [{"url": "https://a.com", "title": "A"}],
        [{"url": "https://a.com"}, {"url": "https://b.com"}],
    )
    assert [s["url"] for s in merged] == ["https://a.com", "https://b.com"]


def test_extract_unique_urls_strips_trailing_punct():
    urls = extract_unique_urls("见 https://a.com。 和 https://b.com, 还有 https://a.com")
    assert urls == ["https://a.com", "https://b.com"]
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd ~/项目/自己的应用/GrokSearch && uv run pytest tests/test_sources.py -v`
Expected: PASS（5 个测试全过）

- [ ] **Step 5: 提交**

```bash
git add src/grok_search/prompts.py src/grok_search/sources.py tests/test_sources.py
git commit -m "refactor: 抽出 prompts.py（仅 search_prompt），url 提取内置进 sources.py"
```

---

## Task 3: key_pool.py 复核 + 测试

**Files:**
- Keep（不改）: `src/grok_search/key_pool.py`
- Create: `tests/test_key_pool.py`

`key_pool.py` 逻辑正确，原样保留。补测试钉死轮询/failover/cooldown 行为（重写后 firecrawl 也依赖它）。

- [ ] **Step 1: 写 `tests/test_key_pool.py`**

```python
import time
import grok_search.key_pool as kp


def setup_function():
    # 清理模块级状态，避免测试间串扰
    kp._state["keys"] = None
    kp._state["cycle"] = None
    kp._cooldown.clear()


def test_round_robin_cycles():
    keys = ["k1", "k2", "k3"]
    picks = [kp.pick_tavily_key(keys) for _ in range(4)]
    assert picks[0] != picks[1]
    assert set(picks[:3]) == {"k1", "k2", "k3"}
    assert picks[3] == picks[0]


def test_failover_sticks_to_first():
    keys = ["k1", "k2"]
    assert kp.pick_failover_key(keys) == "k1"
    assert kp.pick_failover_key(keys) == "k1"


def test_mark_failed_removes_from_pool():
    keys = ["k1", "k2"]
    kp.mark_key_failed("k1", cooldown_seconds=60)
    assert kp.pick_failover_key(keys) == "k2"
    for _ in range(4):
        assert kp.pick_tavily_key(keys) == "k2"


def test_all_cooldown_returns_none():
    keys = ["k1"]
    kp.mark_key_failed("k1", cooldown_seconds=60)
    assert kp.pick_tavily_key(keys) is None
    assert kp.pick_failover_key(keys) is None


def test_cooldown_status_reports():
    keys = ["k1", "k2"]
    kp.mark_key_failed("k1", cooldown_seconds=60)
    status = kp.cooldown_status(keys)
    assert status["total"] == 2
    assert status["active"] == 1
    assert len(status["cooling_down"]) == 1
```

- [ ] **Step 2: 跑测试确认通过**

Run: `cd ~/项目/自己的应用/GrokSearch && uv run pytest tests/test_key_pool.py -v`
Expected: PASS（5 个测试全过）

- [ ] **Step 3: 提交**

```bash
git add tests/test_key_pool.py
git commit -m "test(key_pool): 钉死 round-robin / failover / cooldown 行为"
```

---

## Task 4: grok_client.py（由 providers/grok.py 改造，删死方法）

**Files:**
- Create: `src/grok_search/grok_client.py`
- 稍后删除: `src/grok_search/providers/`（在 Task 7 统一删）

- [ ] **Step 1: 创建 `src/grok_search/grok_client.py`**

照搬 `src/grok_search/providers/grok.py` 的内容，做以下改造：
1. 顶部 import：`from .base import BaseSearchProvider, SearchResult` → 删除；`from ..utils import search_prompt, fetch_prompt, url_describe_prompt, rank_sources_prompt` → 改为 `from .prompts import search_prompt`；`from ..logger import log_info` → `from .logger import log_info`；`from ..config import config` → `from .config import config`。
2. 保留：`get_local_time_info`、`_needs_time_context`、`RETRYABLE_STATUS_CODES`、`_is_retryable_exception`、`_WaitWithRetryAfter`。
3. `class GrokSearchProvider(BaseSearchProvider)` → `class GrokClient`（不再继承）；删除 `super().__init__(...)` 改为直接 `self.api_url = api_url; self.api_key = api_key; self.model = model`。
4. **删除方法**：`get_provider_name`、`fetch`、`describe_url`、`rank_sources`。
5. **保留方法**：`__init__`、`search`（返回 str）、`_parse_streaming_response`、`_execute_stream_with_retry`。
6. `search` 的签名简化为 `async def search(self, query: str, platform: str = "", ctx=None) -> str:`（删掉没用的 `min_results`/`max_results`）。返回类型从 `List[SearchResult]` 改为 `str`（与实际实现一致）。

完整结果（核对用）：

```python
import httpx
import json
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Optional
from tenacity import AsyncRetrying, retry_if_exception, stop_after_attempt, wait_random_exponential
from tenacity.wait import wait_base
from .prompts import search_prompt
from .logger import log_info
from .config import config


def get_local_time_info() -> str:
    try:
        local_tz = datetime.now().astimezone().tzinfo
        local_now = datetime.now(local_tz)
    except Exception:
        local_now = datetime.now(timezone.utc)
    weekdays_cn = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]
    weekday = weekdays_cn[local_now.weekday()]
    return (
        f"[Current Time Context]\n"
        f"- Date: {local_now.strftime('%Y-%m-%d')} ({weekday})\n"
        f"- Time: {local_now.strftime('%H:%M:%S')}\n"
        f"- Timezone: {local_now.tzname() or 'Local'}\n"
    )


def _needs_time_context(query: str) -> bool:
    cn_keywords = [
        "当前", "现在", "今天", "明天", "昨天", "本周", "上周", "下周", "这周",
        "本月", "上月", "下月", "这个月", "今年", "去年", "明年",
        "最新", "最近", "近期", "刚刚", "刚才", "实时", "即时", "目前",
    ]
    en_keywords = [
        "current", "now", "today", "tomorrow", "yesterday",
        "this week", "last week", "next week", "this month", "last month", "next month",
        "this year", "last year", "next year", "latest", "recent", "recently",
        "just now", "real-time", "realtime", "up-to-date",
    ]
    query_lower = query.lower()
    for keyword in cn_keywords:
        if keyword in query:
            return True
    for keyword in en_keywords:
        if keyword in query_lower:
            return True
    return False


RETRYABLE_STATUS_CODES = {408, 429, 500, 502, 503, 504}


def _is_retryable_exception(exc) -> bool:
    if isinstance(exc, (httpx.TimeoutException, httpx.NetworkError, httpx.ConnectError, httpx.RemoteProtocolError)):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in RETRYABLE_STATUS_CODES
    return False


class _WaitWithRetryAfter(wait_base):
    def __init__(self, multiplier: float, max_wait: int):
        self._base_wait = wait_random_exponential(multiplier=multiplier, max=max_wait)
        self._protocol_error_base = 3.0

    def __call__(self, retry_state):
        if retry_state.outcome and retry_state.outcome.failed:
            exc = retry_state.outcome.exception()
            if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code == 429:
                retry_after = self._parse_retry_after(exc.response)
                if retry_after is not None:
                    return retry_after
            if isinstance(exc, httpx.RemoteProtocolError):
                return self._base_wait(retry_state) + self._protocol_error_base
        return self._base_wait(retry_state)

    def _parse_retry_after(self, response: httpx.Response) -> Optional[float]:
        header = response.headers.get("Retry-After")
        if not header:
            return None
        header = header.strip()
        if header.isdigit():
            return float(header)
        try:
            retry_dt = parsedate_to_datetime(header)
            if retry_dt.tzinfo is None:
                retry_dt = retry_dt.replace(tzinfo=timezone.utc)
            delay = (retry_dt - datetime.now(timezone.utc)).total_seconds()
            return max(0.0, delay)
        except (TypeError, ValueError):
            return None


class GrokClient:
    def __init__(self, api_url: str, api_key: str, model: str = "grok-4.3-console"):
        self.api_url = api_url
        self.api_key = api_key
        self.model = model

    async def search(self, query: str, platform: str = "", ctx=None) -> str:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        platform_prompt = ""
        if platform:
            platform_prompt = "\n\nYou should search the web for the information you need, and focus on these platform: " + platform + "\n"
        time_context = get_local_time_info() + "\n"
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": search_prompt},
                {"role": "user", "content": time_context + query + platform_prompt},
            ],
            "stream": True,
        }
        await log_info(ctx, f"platform_prompt: {query + platform_prompt}", config.debug_enabled)
        return await self._execute_stream_with_retry(headers, payload, ctx)

    async def _parse_streaming_response(self, response, ctx=None) -> str:
        content = ""
        full_body_buffer = []
        async for line in response.aiter_lines():
            line = line.strip()
            if not line:
                continue
            full_body_buffer.append(line)
            if line.startswith("data:"):
                if line in ("data: [DONE]", "data:[DONE]"):
                    continue
                try:
                    json_str = line[5:].lstrip()
                    data = json.loads(json_str)
                    choices = data.get("choices", [])
                    if choices and len(choices) > 0:
                        delta = choices[0].get("delta", {})
                        if "content" in delta:
                            content += delta["content"]
                except (json.JSONDecodeError, IndexError):
                    continue
        if not content and full_body_buffer:
            try:
                full_text = "".join(full_body_buffer)
                data = json.loads(full_text)
                if "choices" in data and len(data["choices"]) > 0:
                    message = data["choices"][0].get("message", {})
                    content = message.get("content", "")
            except json.JSONDecodeError:
                pass
        await log_info(ctx, f"content: {content}", config.debug_enabled)
        return content

    async def _execute_stream_with_retry(self, headers: dict, payload: dict, ctx=None) -> str:
        timeout = httpx.Timeout(connect=6.0, read=120.0, write=10.0, pool=None)
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            async for attempt in AsyncRetrying(
                stop=stop_after_attempt(config.retry_max_attempts + 1),
                wait=_WaitWithRetryAfter(config.retry_multiplier, config.retry_max_wait),
                retry=retry_if_exception(_is_retryable_exception),
                reraise=True,
            ):
                with attempt:
                    async with client.stream(
                        "POST",
                        f"{self.api_url}/chat/completions",
                        headers=headers,
                        json=payload,
                    ) as response:
                        response.raise_for_status()
                        return await self._parse_streaming_response(response, ctx)
```

- [ ] **Step 2: 验证可导入**

Run: `cd ~/项目/自己的应用/GrokSearch && uv run python -c "from grok_search.grok_client import GrokClient, get_local_time_info, _needs_time_context; print('ok')"`
Expected: 输出 `ok`（无 import 错误）

- [ ] **Step 3: 提交**

```bash
git add src/grok_search/grok_client.py
git commit -m "refactor: providers/grok.py → grok_client.py（GrokClient，删 fetch/describe_url/rank_sources 死方法）"
```

---

## Task 5: tavily_client.py（从 server.py 抽 Tavily 调用）

**Files:**
- Create: `src/grok_search/tavily_client.py`

把 `server.py` 中三个 Tavily 相关 helper 抽到独立模块。函数签名与 body 照搬现有实现（`_call_tavily_extract` 138-260 段、`_call_tavily_search` 263-291 段、`_call_tavily_map` 478-509 段），仅去掉前导下划线并补 import。

- [ ] **Step 1: 创建 `src/grok_search/tavily_client.py`**

```python
import json
import httpx
from .config import config
from .key_pool import pick_tavily_key, mark_key_failed


async def tavily_extract(url: str) -> str | None:
    api_url = config.tavily_api_url
    api_key = pick_tavily_key(config.tavily_api_keys)
    if not api_key:
        return None
    endpoint = f"{api_url.rstrip('/')}/extract"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    body = {"urls": [url], "format": "markdown"}
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(endpoint, headers=headers, json=body)
            if response.status_code in (401, 403, 429):
                mark_key_failed(api_key)
                return None
            response.raise_for_status()
            data = response.json()
            if data.get("results") and len(data["results"]) > 0:
                content = data["results"][0].get("raw_content", "")
                return content if content and content.strip() else None
            return None
    except Exception:
        return None


async def tavily_search(query: str, max_results: int = 6) -> list[dict] | None:
    api_key = pick_tavily_key(config.tavily_api_keys)
    if not api_key:
        return None
    endpoint = f"{config.tavily_api_url.rstrip('/')}/search"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    body = {
        "query": query,
        "max_results": max_results,
        "search_depth": "advanced",
        "include_raw_content": False,
        "include_answer": False,
    }
    try:
        async with httpx.AsyncClient(timeout=90.0) as client:
            response = await client.post(endpoint, headers=headers, json=body)
            if response.status_code in (401, 403, 429):
                mark_key_failed(api_key)
                return None
            response.raise_for_status()
            data = response.json()
            results = data.get("results", [])
            return [
                {"title": r.get("title", ""), "url": r.get("url", ""), "content": r.get("content", ""), "score": r.get("score", 0)}
                for r in results
            ] if results else None
    except Exception:
        return None


async def tavily_map(url: str, instructions: str = None, max_depth: int = 1,
                     max_breadth: int = 20, limit: int = 50, timeout: int = 150) -> str:
    api_url = config.tavily_api_url
    api_key = pick_tavily_key(config.tavily_api_keys)
    if not api_key:
        return "配置错误: TAVILY_API_KEY 未配置，请设置环境变量 TAVILY_API_KEYS"
    endpoint = f"{api_url.rstrip('/')}/map"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    body = {"url": url, "max_depth": max_depth, "max_breadth": max_breadth, "limit": limit, "timeout": timeout}
    if instructions:
        body["instructions"] = instructions
    try:
        async with httpx.AsyncClient(timeout=float(timeout + 10)) as client:
            response = await client.post(endpoint, headers=headers, json=body)
            if response.status_code in (401, 403, 429):
                mark_key_failed(api_key)
                return f"HTTP错误: {response.status_code}（key 已暂时移出轮询池）"
            response.raise_for_status()
            data = response.json()
            return json.dumps({
                "base_url": data.get("base_url", ""),
                "results": data.get("results", []),
                "response_time": data.get("response_time", 0),
            }, ensure_ascii=False, indent=2)
    except httpx.TimeoutException:
        return f"映射超时: 请求超过{timeout}秒"
    except httpx.HTTPStatusError as e:
        return f"HTTP错误: {e.response.status_code} - {e.response.text[:200]}"
    except Exception as e:
        return f"映射错误: {str(e)}"
```

- [ ] **Step 2: 验证可导入**

Run: `cd ~/项目/自己的应用/GrokSearch && uv run python -c "from grok_search.tavily_client import tavily_extract, tavily_search, tavily_map; print('ok')"`
Expected: 输出 `ok`

- [ ] **Step 3: 提交**

```bash
git add src/grok_search/tavily_client.py
git commit -m "refactor: 抽出 tavily_client.py（extract/search/map）"
```

---

## Task 6: firecrawl_client.py（统一单通道 key 池）

**Files:**
- Create: `src/grok_search/firecrawl_client.py`
- Create: `tests/test_firecrawl_unified.py`

把 `server.py` 中 Firecrawl 三个 helper（`_call_firecrawl_search` 294-313、`_call_firecrawl_scrape` 316-344、`_call_firecrawl_screenshot` 388-443）抽出并**统一到 `config.firecrawl_api_keys` 单一 key 池**，三者都走 `pick_failover_key` + `mark_key_failed`（截图原本就是这样；scrape/search 原来用裸 `firecrawl_api_key` 无轮询，现统一）。`firecrawl_api_url` 也统一一个。

- [ ] **Step 1: 先写失败测试 `tests/test_firecrawl_unified.py`**

```python
import importlib
import pytest


@pytest.fixture
def fc_config(monkeypatch):
    for var in [
        "FIRECRAWL_API_URL", "FIRECRAWL_API_KEY", "FIRECRAWL_API_KEYS",
        "FIRECRAWL_SCREENSHOT_API_KEY", "FIRECRAWL_SCREENSHOT_API_KEYS",
    ]:
        monkeypatch.delenv(var, raising=False)
    import grok_search.config as config_mod
    importlib.reload(config_mod)
    config_mod.Config._instance = None
    return config_mod.Config()


def test_screenshot_keys_serve_all_firecrawl(fc_config, monkeypatch):
    """使用者现状：只配 FIRECRAWL_SCREENSHOT_API_KEYS，三功能都应读到它。"""
    monkeypatch.setenv("FIRECRAWL_SCREENSHOT_API_KEYS", "fc1,fc2")
    # firecrawl_client 的三个函数都从 config.firecrawl_api_keys 取池
    assert fc_config.firecrawl_api_keys == ["fc1", "fc2"]
    # 模块内不再读取任何 *SCREENSHOT* 专属变量
    import grok_search.firecrawl_client as fcmod
    import inspect
    src = inspect.getsource(fcmod)
    assert "SCREENSHOT" not in src
    assert "firecrawl_screenshot_api" not in src
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd ~/项目/自己的应用/GrokSearch && uv run pytest tests/test_firecrawl_unified.py -v`
Expected: FAIL（firecrawl_client 还不存在）

- [ ] **Step 3: 创建 `src/grok_search/firecrawl_client.py`**

```python
import httpx
from .config import config
from .key_pool import pick_failover_key, mark_key_failed, mask_tail
from .logger import log_info


async def firecrawl_search(query: str, limit: int = 14) -> list[dict] | None:
    api_key = pick_failover_key(config.firecrawl_api_keys, label="firecrawl-search")
    if not api_key:
        return None
    endpoint = f"{config.firecrawl_api_url.rstrip('/')}/search"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    body = {"query": query, "limit": limit}
    try:
        async with httpx.AsyncClient(timeout=90.0) as client:
            response = await client.post(endpoint, headers=headers, json=body)
            if response.status_code in (401, 403, 429):
                mark_key_failed(api_key)
                return None
            response.raise_for_status()
            data = response.json()
            results = data.get("data", {}).get("web", [])
            return [
                {"title": r.get("title", ""), "url": r.get("url", ""), "description": r.get("description", "")}
                for r in results
            ] if results else None
    except Exception:
        return None


async def firecrawl_scrape(url: str, ctx=None) -> str | None:
    api_url = config.firecrawl_api_url
    keys = config.firecrawl_api_keys
    if not keys:
        return None
    endpoint = f"{api_url.rstrip('/')}/scrape"
    max_retries = config.retry_max_attempts
    for attempt in range(max_retries):
        api_key = pick_failover_key(keys, label="firecrawl-scrape")
        if not api_key:
            return None
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        body = {
            "url": url,
            "formats": ["markdown"],
            "timeout": 60000,
            "waitFor": (attempt + 1) * 1500,
        }
        try:
            async with httpx.AsyncClient(timeout=90.0) as client:
                response = await client.post(endpoint, headers=headers, json=body)
                if response.status_code in (401, 403, 429):
                    mark_key_failed(api_key)
                    await log_info(ctx, f"Firecrawl scrape: key ...{mask_tail(api_key)} cooldown, 重试", config.debug_enabled)
                    continue
                response.raise_for_status()
                data = response.json()
                markdown = data.get("data", {}).get("markdown", "")
                if markdown and markdown.strip():
                    return markdown
                await log_info(ctx, f"Firecrawl: markdown为空, 重试 {attempt + 1}/{max_retries}", config.debug_enabled)
        except Exception as e:
            await log_info(ctx, f"Firecrawl error: {e}", config.debug_enabled)
            return None
    return None


async def firecrawl_screenshot(url: str, full_page: bool, ctx=None) -> dict | str:
    """Firecrawl /scrape 全页/首屏截图，多 key failover。
    成功返回 dict（含 screenshot_url 等元数据），失败返回中文错误描述字符串。"""
    api_url = config.firecrawl_api_url
    keys = config.firecrawl_api_keys
    if not keys:
        return "配置错误: Firecrawl key 未配置（设 FIRECRAWL_API_KEYS / FIRECRAWL_API_KEY）"
    endpoint = f"{api_url.rstrip('/')}/scrape"
    screenshot_format = {"type": "screenshot", "fullPage": True} if full_page else {"type": "screenshot"}
    body = {"url": url, "formats": [screenshot_format], "timeout": 60000}
    last_error = None
    for _ in range(len(keys)):
        api_key = pick_failover_key(keys, label="firecrawl-screenshot")
        if not api_key:
            return last_error or f"截图失败: 所有 {len(keys)} 个 Firecrawl key 都在 cooldown 中（默认 30 分钟）"
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        try:
            async with httpx.AsyncClient(timeout=90.0) as client:
                response = await client.post(endpoint, headers=headers, json=body)
                if response.status_code in (401, 403, 429):
                    mark_key_failed(api_key)
                    last_error = f"HTTP错误: {response.status_code}（key ...{mask_tail(api_key)} 已 cooldown 30 分钟）"
                    await log_info(ctx, f"Firecrawl screenshot: {last_error}, 尝试下一个 key", config.debug_enabled)
                    continue
                response.raise_for_status()
                data = response.json()
                inner = data.get("data") or {}
                screenshot_url = inner.get("screenshot")
                if not screenshot_url:
                    await log_info(ctx, f"Firecrawl screenshot: 响应缺少 screenshot 字段, payload={data}", config.debug_enabled)
                    return "截图失败: Firecrawl 未返回截图 URL"
                meta = inner.get("metadata") or {}
                return {
                    "url": url,
                    "screenshot_url": screenshot_url,
                    "format": "screenshot@fullPage" if full_page else "screenshot",
                    "title": meta.get("title"),
                    "status_code": meta.get("statusCode"),
                    "credits_used": meta.get("creditsUsed"),
                    "cache_state": meta.get("cacheState"),
                    "key_tail": mask_tail(api_key),
                    "note": "screenshot_url 是 GCS 签名链接，会在数小时内过期，请尽快下载",
                }
        except httpx.HTTPStatusError as e:
            await log_info(ctx, f"Firecrawl screenshot HTTP错误: {e.response.status_code} - {e.response.text[:200]}", config.debug_enabled)
            return f"HTTP错误: {e.response.status_code} - {e.response.text[:200]}"
        except httpx.TimeoutException:
            return "截图超时: Firecrawl 90 秒内未返回"
        except Exception as e:
            await log_info(ctx, f"Firecrawl screenshot error: {e}", config.debug_enabled)
            return f"截图错误: {str(e)}"
    return last_error or "截图失败: 所有 key 均被拒"
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd ~/项目/自己的应用/GrokSearch && uv run pytest tests/test_firecrawl_unified.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add src/grok_search/firecrawl_client.py tests/test_firecrawl_unified.py
git commit -m "refactor: 抽出 firecrawl_client.py，三功能统一单 key 池 + failover"
```

---

## Task 7: server.py 重写（8 个薄工具 + main）+ 删死文件

**Files:**
- Modify（整体重写）: `src/grok_search/server.py`
- Delete: `src/grok_search/planning.py`、`src/grok_search/providers/`（整目录）、`src/grok_search/utils.py`

- [ ] **Step 1: 整体重写 `src/grok_search/server.py`**

```python
import sys
import asyncio
from pathlib import Path

src_dir = Path(__file__).parent.parent
if str(src_dir) not in sys.path:
    sys.path.insert(0, str(src_dir))

from fastmcp import FastMCP, Context
from typing import Annotated
from pydantic import Field

try:
    from grok_search.grok_client import GrokClient
    from grok_search.logger import log_info
    from grok_search.config import config
    from grok_search.sources import SourcesCache, merge_sources, new_session_id, split_answer_and_sources
    from grok_search.key_pool import cooldown_status
    from grok_search.tavily_client import tavily_extract, tavily_search, tavily_map
    from grok_search.firecrawl_client import firecrawl_search, firecrawl_scrape, firecrawl_screenshot
except ImportError:
    from .grok_client import GrokClient
    from .logger import log_info
    from .config import config
    from .sources import SourcesCache, merge_sources, new_session_id, split_answer_and_sources
    from .key_pool import cooldown_status
    from .tavily_client import tavily_extract, tavily_search, tavily_map
    from .firecrawl_client import firecrawl_search, firecrawl_scrape, firecrawl_screenshot

mcp = FastMCP("grok-search")

_SOURCES_CACHE = SourcesCache(max_size=256)
_AVAILABLE_MODELS_CACHE: dict[tuple[str, str], list[str]] = {}
_AVAILABLE_MODELS_LOCK = asyncio.Lock()


async def _fetch_available_models(api_url: str, api_key: str) -> list[str]:
    import httpx
    models_url = f"{api_url.rstrip('/')}/models"
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.get(
            models_url,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        )
        response.raise_for_status()
        data = response.json()
    models: list[str] = []
    for item in (data or {}).get("data", []) or []:
        if isinstance(item, dict) and isinstance(item.get("id"), str):
            models.append(item["id"])
    return models


async def _get_available_models_cached(api_url: str, api_key: str) -> list[str]:
    key = (api_url, api_key)
    async with _AVAILABLE_MODELS_LOCK:
        if key in _AVAILABLE_MODELS_CACHE:
            return _AVAILABLE_MODELS_CACHE[key]
    try:
        models = await _fetch_available_models(api_url, api_key)
    except Exception:
        models = []
    async with _AVAILABLE_MODELS_LOCK:
        _AVAILABLE_MODELS_CACHE[key] = models
    return models


def _extra_results_to_sources(tavily_results, firecrawl_results) -> list[dict]:
    sources: list[dict] = []
    seen: set[str] = set()
    if firecrawl_results:
        for r in firecrawl_results:
            url = (r.get("url") or "").strip()
            if not url or url in seen:
                continue
            seen.add(url)
            item: dict = {"url": url, "provider": "firecrawl"}
            title = (r.get("title") or "").strip()
            if title:
                item["title"] = title
            desc = (r.get("description") or "").strip()
            if desc:
                item["description"] = desc
            sources.append(item)
    if tavily_results:
        for r in tavily_results:
            url = (r.get("url") or "").strip()
            if not url or url in seen:
                continue
            seen.add(url)
            item: dict = {"url": url, "provider": "tavily"}
            title = (r.get("title") or "").strip()
            if title:
                item["title"] = title
            content = (r.get("content") or "").strip()
            if content:
                item["description"] = content
            sources.append(item)
    return sources


@mcp.tool(
    name="web_search",
    output_schema=None,
    description="""
    Performs a deep web search based on the given query and returns Grok's answer directly.

    This tool extracts sources if provided by upstream, caches them, and returns:
    - session_id: string (When you feel confused or curious about the main content, use this field to invoke the get_sources tool to obtain the corresponding list of information sources)
    - content: string (answer only)
    - sources_count: int
    """,
    meta={"version": "2.0.0"},
)
async def web_search(
    query: Annotated[str, "Clear, self-contained natural-language search query."],
    platform: Annotated[str, "Target platform to focus on (e.g., 'Twitter', 'GitHub', 'Reddit'). Leave empty for general web search."] = "",
    model: Annotated[str, "Optional model ID for this request only. This value is used ONLY when user explicitly provided."] = "",
    extra_sources: Annotated[int, "Number of additional reference results from Tavily/Firecrawl. Set 0 to disable. Default 0."] = 0,
) -> dict:
    session_id = new_session_id()
    try:
        api_url = config.grok_api_url
        api_key = config.grok_api_key
    except ValueError as e:
        await _SOURCES_CACHE.set(session_id, [])
        return {"session_id": session_id, "content": f"配置错误: {str(e)}", "sources_count": 0}

    effective_model = config.grok_model
    if model:
        available = await _get_available_models_cached(api_url, api_key)
        if available and model not in available:
            await _SOURCES_CACHE.set(session_id, [])
            return {"session_id": session_id, "content": f"无效模型: {model}", "sources_count": 0}
        effective_model = model

    grok = GrokClient(api_url, api_key, effective_model)

    has_tavily = bool(config.tavily_api_keys)
    has_firecrawl = bool(config.firecrawl_api_keys)
    firecrawl_count = 0
    tavily_count = 0
    if extra_sources > 0:
        if has_firecrawl and has_tavily:
            firecrawl_count = round(extra_sources * 1)
            tavily_count = extra_sources - firecrawl_count
        elif has_firecrawl:
            firecrawl_count = extra_sources
        elif has_tavily:
            tavily_count = extra_sources

    async def _safe_grok() -> str:
        try:
            return await grok.search(query, platform)
        except Exception:
            return ""

    async def _safe_tavily():
        try:
            if tavily_count:
                return await tavily_search(query, tavily_count)
        except Exception:
            return None

    async def _safe_firecrawl():
        try:
            if firecrawl_count:
                return await firecrawl_search(query, firecrawl_count)
        except Exception:
            return None

    coros = [_safe_grok()]
    if tavily_count > 0:
        coros.append(_safe_tavily())
    if firecrawl_count > 0:
        coros.append(_safe_firecrawl())

    gathered = await asyncio.gather(*coros)

    grok_result: str = gathered[0] or ""
    tavily_results = None
    firecrawl_results = None
    idx = 1
    if tavily_count > 0:
        tavily_results = gathered[idx]
        idx += 1
    if firecrawl_count > 0:
        firecrawl_results = gathered[idx]

    answer, grok_sources = split_answer_and_sources(grok_result)
    extra = _extra_results_to_sources(tavily_results, firecrawl_results)
    all_sources = merge_sources(grok_sources, extra)

    await _SOURCES_CACHE.set(session_id, all_sources)
    return {"session_id": session_id, "content": answer, "sources_count": len(all_sources)}


@mcp.tool(
    name="get_sources",
    description="""
    When you feel confused or curious about the search response content, use the session_id returned by web_search to invoke this tool to obtain the corresponding list of information sources.
    Retrieve all cached sources for a previous web_search call.
    Provide the session_id returned by web_search to get the full source list.
    """,
    meta={"version": "1.0.0"},
)
async def get_sources(
    session_id: Annotated[str, "Session ID from previous web_search call."]
) -> dict:
    sources = await _SOURCES_CACHE.get(session_id)
    if sources is None:
        return {
            "session_id": session_id,
            "sources": [],
            "sources_count": 0,
            "error": "session_id_not_found_or_expired",
        }
    return {"session_id": session_id, "sources": sources, "sources_count": len(sources)}


@mcp.tool(
    name="web_fetch",
    output_schema=None,
    description="""
    Fetches and extracts complete content from a URL, returning it as a structured Markdown document.

    **Key Features:**
        - **Full Content Extraction:** Retrieves and parses all meaningful content (text, images, links, tables, code blocks).
        - **Markdown Conversion:** Converts HTML structure to well-formatted Markdown with preserved hierarchy.
        - **Content Fidelity:** Maintains 100% content fidelity without summarization or modification.

    **Edge Cases & Best Practices:**
        - Ensure URL is complete and accessible (not behind authentication or paywalls).
        - May not capture dynamically loaded content requiring JavaScript execution.
        - Large pages may take longer to process; consider timeout implications.
    """,
    meta={"version": "1.3.0"},
)
async def web_fetch(
    url: Annotated[str, "Valid HTTP/HTTPS web address pointing to the target page. Must be complete and accessible."],
    ctx: Context = None,
) -> str:
    await log_info(ctx, f"Begin Fetch: {url}", config.debug_enabled)
    result = await tavily_extract(url)
    if result:
        await log_info(ctx, "Fetch Finished (Tavily)!", config.debug_enabled)
        return result
    await log_info(ctx, "Tavily unavailable or failed, trying Firecrawl...", config.debug_enabled)
    result = await firecrawl_scrape(url, ctx)
    if result:
        await log_info(ctx, "Fetch Finished (Firecrawl)!", config.debug_enabled)
        return result
    await log_info(ctx, "Fetch Failed!", config.debug_enabled)
    if not config.tavily_api_keys and not config.firecrawl_api_keys:
        return "配置错误: TAVILY_API_KEYS 和 FIRECRAWL_API_KEYS 均未配置"
    return "提取失败: 所有提取服务均未能获取内容"


@mcp.tool(
    name="web_screenshot",
    output_schema=None,
    description="""
    Captures a screenshot of a webpage via Firecrawl and returns a temporary signed PNG URL.

    **Key Features:**
        - **Viewport or Full Page:** Toggle between first-fold (default) and full-page capture.
        - **JS-Rendered Pages:** Firecrawl waits for the page to load before snapping, so single-page apps and JS-heavy sites work.
        - **Direct URL Return:** No local download; caller receives a GCS signed URL ready to fetch.

    **Edge Cases & Best Practices:**
        - The returned screenshot_url is short-lived (expires within hours). Download the PNG promptly if you need to keep it.
        - Each call costs ~1 Firecrawl credit (full-page on long pages may cost more).
        - Pages behind auth/paywalls or geo-blocked content may screenshot a login or error page.
    """,
    meta={"version": "1.0.0"},
)
async def web_screenshot(
    url: Annotated[str, "Valid HTTP/HTTPS URL of the page to screenshot."],
    full_page: Annotated[bool, "If True, capture the entire scrollable page; otherwise just the viewport (first fold). Default False."] = False,
    ctx: Context = None,
) -> str:
    import json
    await log_info(ctx, f"Begin Screenshot: {url} (full_page={full_page})", config.debug_enabled)
    result = await firecrawl_screenshot(url, full_page, ctx)
    if isinstance(result, dict):
        await log_info(ctx, "Screenshot Finished!", config.debug_enabled)
        return json.dumps(result, ensure_ascii=False, indent=2)
    return result


@mcp.tool(
    name="web_map",
    description="""
    Maps a website's structure by traversing it like a graph, discovering URLs and generating a comprehensive site map.

    **Key Features:**
        - **Graph Traversal:** Explores website structure starting from root URL.
        - **Depth & Breadth Control:** Configure traversal limits to balance coverage and performance.
        - **Instruction Filtering:** Use natural language to focus crawler on specific content types.

    **Edge Cases & Best Practices:**
        - Start with low max_depth (1-2) for initial exploration, increase if needed.
        - Use instructions to filter for specific content (e.g., "only documentation pages").
        - Large sites may hit timeout limits; adjust timeout and limit parameters accordingly.
    """,
    meta={"version": "1.3.0"},
)
async def web_map(
    url: Annotated[str, "Root URL to begin the mapping (e.g., 'https://docs.example.com')."],
    instructions: Annotated[str, "Natural language instructions for the crawler to filter or focus on specific content."] = "",
    max_depth: Annotated[int, Field(description="Maximum depth of mapping from the base URL.", ge=1, le=5)] = 1,
    max_breadth: Annotated[int, Field(description="Maximum number of links to follow per page.", ge=1, le=500)] = 20,
    limit: Annotated[int, Field(description="Total number of links to process before stopping.", ge=1, le=500)] = 50,
    timeout: Annotated[int, Field(description="Maximum time in seconds for the operation.", ge=10, le=150)] = 150,
) -> str:
    return await tavily_map(url, instructions, max_depth, max_breadth, limit, timeout)


@mcp.tool(
    name="get_config_info",
    output_schema=None,
    description="""
    Returns current Grok Search MCP server configuration and tests API connectivity.

    **Key Features:**
        - **Configuration Check:** Verifies environment variables and current settings.
        - **Connection Test:** Sends request to /models endpoint to validate API access.
        - **Model Discovery:** Lists all available models from the API.

    **Edge Cases & Best Practices:**
        - Use this tool first when debugging connection or configuration issues.
        - API keys are automatically masked for security in the response.
        - Connection test timeout is 10 seconds; network issues may cause delays.
    """,
    meta={"version": "1.3.0"},
)
async def get_config_info() -> str:
    import json
    import httpx
    import time

    config_info = config.get_config_info()

    test_result = {"status": "未测试", "message": "", "response_time_ms": 0}
    try:
        api_url = config.grok_api_url
        api_key = config.grok_api_key
        models_url = f"{api_url.rstrip('/')}/models"
        start_time = time.time()
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(
                models_url,
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            )
            response_time = (time.time() - start_time) * 1000
            if response.status_code == 200:
                test_result["status"] = "✅ 连接成功"
                test_result["message"] = f"成功获取模型列表 (HTTP {response.status_code})"
                test_result["response_time_ms"] = round(response_time, 2)
                try:
                    models_data = response.json()
                    if "data" in models_data and isinstance(models_data["data"], list):
                        model_count = len(models_data["data"])
                        test_result["message"] += f"，共 {model_count} 个模型"
                        model_names = [m["id"] for m in models_data["data"] if isinstance(m, dict) and "id" in m]
                        if model_names:
                            test_result["available_models"] = model_names
                except Exception:
                    pass
            else:
                test_result["status"] = "⚠️ 连接异常"
                test_result["message"] = f"HTTP {response.status_code}: {response.text[:100]}"
                test_result["response_time_ms"] = round(response_time, 2)
    except httpx.TimeoutException:
        test_result["status"] = "❌ 连接超时"
        test_result["message"] = "请求超时（10秒），请检查网络连接或 API URL"
    except httpx.RequestError as e:
        test_result["status"] = "❌ 连接失败"
        test_result["message"] = f"网络错误: {str(e)}"
    except ValueError as e:
        test_result["status"] = "❌ 配置错误"
        test_result["message"] = str(e)
    except Exception as e:
        test_result["status"] = "❌ 测试失败"
        test_result["message"] = f"未知错误: {str(e)}"
    config_info["connection_test"] = test_result

    default_model_health = {"model": "未配置", "status": "未测试", "response_time_ms": 0, "message": ""}
    try:
        api_url = config.grok_api_url
        api_key = config.grok_api_key
        model = config.grok_model
        default_model_health["model"] = model
        probe_payload = {"model": model, "stream": False, "max_tokens": 1, "messages": [{"role": "user", "content": "ping"}]}
        _start = time.time()
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                f"{api_url.rstrip('/')}/chat/completions",
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json=probe_payload,
            )
            default_model_health["response_time_ms"] = round((time.time() - _start) * 1000, 2)
            text = resp.text or ""
            if "No available accounts" in text or "rate_limit_exceeded" in text:
                default_model_health["status"] = "❌ 中转站无账号"
                default_model_health["message"] = "该模型在中转站没有可用账号，建议切换其他模型"
            elif resp.status_code == 200:
                default_model_health["status"] = "✅ 可用"
                default_model_health["message"] = "1-token 探针通过"
            else:
                default_model_health["status"] = f"⚠️ HTTP {resp.status_code}"
                default_model_health["message"] = text[:200]
    except httpx.TimeoutException:
        default_model_health["status"] = "❌ 探针超时"
    except Exception as e:
        default_model_health["status"] = "❌ 探针失败"
        default_model_health["message"] = str(e)[:200]
    config_info["default_model_health"] = default_model_health

    config_info["tavily_key_cooldown"] = cooldown_status(config.tavily_api_keys)
    config_info["firecrawl_key_cooldown"] = cooldown_status(config.firecrawl_api_keys)

    return json.dumps(config_info, ensure_ascii=False, indent=2)


@mcp.tool(
    name="switch_model",
    output_schema=None,
    description="""
    Switches the default Grok model used for search and fetch operations, persisting the setting.

    **Key Features:**
        - **Model Selection:** Change the AI model for web search and content fetching.
        - **Persistent Storage:** Model preference saved to ~/.config/grok-search/config.json.
        - **Immediate Effect:** New model used for all subsequent operations.

    **Edge Cases & Best Practices:**
        - Use get_config_info to verify available models before switching.
        - Invalid model IDs may cause API errors in subsequent requests.
        - Model changes persist across sessions until explicitly changed again.
    """,
    meta={"version": "1.3.0"},
)
async def switch_model(
    model: Annotated[str, "Model ID to switch to (e.g., 'grok-4.3-console', 'grok-4.20-fast')."]
) -> str:
    import json
    try:
        previous_model = config.grok_model
        config.set_model(model)
        current_model = config.grok_model
        return json.dumps({
            "status": "✅ 成功",
            "previous_model": previous_model,
            "current_model": current_model,
            "message": f"模型已从 {previous_model} 切换到 {current_model}",
            "config_file": str(config.config_file),
        }, ensure_ascii=False, indent=2)
    except ValueError as e:
        return json.dumps({"status": "❌ 失败", "message": f"切换模型失败: {str(e)}"}, ensure_ascii=False, indent=2)
    except Exception as e:
        return json.dumps({"status": "❌ 失败", "message": f"未知错误: {str(e)}"}, ensure_ascii=False, indent=2)


@mcp.tool(
    name="toggle_builtin_tools",
    output_schema=None,
    description="""
    Toggle Claude Code's built-in WebSearch and WebFetch tools on/off.

    **Key Features:**
        - **Tool Control:** Enable or disable Claude Code's native web tools.
        - **Project Scope:** Changes apply to current project's .claude/settings.json.
        - **Status Check:** Query current state without making changes.

    **Edge Cases & Best Practices:**
        - Use "on" to block built-in tools when preferring this MCP server's implementation.
        - Use "off" to restore Claude Code's native tools.
        - Use "status" to check current configuration without modification.
    """,
    meta={"version": "1.3.0"},
)
async def toggle_builtin_tools(
    action: Annotated[str, "Action to perform: 'on' (block built-in), 'off' (allow built-in), or 'status' (check current state)."] = "status"
) -> str:
    import json
    root = Path.cwd()
    while root != root.parent and not (root / ".git").exists():
        root = root.parent
    settings_path = root / ".claude" / "settings.json"
    tools = ["WebFetch", "WebSearch"]
    if settings_path.exists():
        with open(settings_path, "r", encoding="utf-8") as f:
            settings = json.load(f)
    else:
        settings = {"permissions": {"deny": []}}
    deny = settings.setdefault("permissions", {}).setdefault("deny", [])
    blocked = all(t in deny for t in tools)
    if action in ["on", "enable"]:
        for t in tools:
            if t not in deny:
                deny.append(t)
        settings_path.parent.mkdir(parents=True, exist_ok=True)
        with open(settings_path, "w", encoding="utf-8") as f:
            json.dump(settings, f, ensure_ascii=False, indent=2)
        msg = "官方工具已禁用"
        blocked = True
    elif action in ["off", "disable"]:
        deny[:] = [t for t in deny if t not in tools]
        settings_path.parent.mkdir(parents=True, exist_ok=True)
        with open(settings_path, "w", encoding="utf-8") as f:
            json.dump(settings, f, ensure_ascii=False, indent=2)
        msg = "官方工具已启用"
        blocked = False
    else:
        msg = f"官方工具当前{'已禁用' if blocked else '已启用'}"
    return json.dumps({"blocked": blocked, "deny_list": deny, "file": str(settings_path), "message": msg}, ensure_ascii=False, indent=2)


def main():
    import signal
    import os
    import threading

    if threading.current_thread() is threading.main_thread():
        def handle_shutdown(signum, frame):
            os._exit(0)
        signal.signal(signal.SIGINT, handle_shutdown)
        if sys.platform != "win32":
            signal.signal(signal.SIGTERM, handle_shutdown)

    if sys.platform == "win32":
        import time
        import ctypes
        parent_pid = os.getppid()

        def is_parent_alive(pid):
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            STILL_ACTIVE = 259
            kernel32 = ctypes.windll.kernel32
            handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            if not handle:
                return True
            exit_code = ctypes.c_ulong()
            result = kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))
            kernel32.CloseHandle(handle)
            return result and exit_code.value == STILL_ACTIVE

        def monitor_parent():
            while True:
                if not is_parent_alive(parent_pid):
                    os._exit(0)
                time.sleep(2)

        threading.Thread(target=monitor_parent, daemon=True).start()

    try:
        mcp.run(transport="stdio", show_banner=False)
    except KeyboardInterrupt:
        pass
    finally:
        os._exit(0)


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: 删除死文件**

```bash
cd ~/项目/自己的应用/GrokSearch
git rm -r src/grok_search/planning.py src/grok_search/providers src/grok_search/utils.py
```

- [ ] **Step 3: 验证导入无残留**

Run: `cd ~/项目/自己的应用/GrokSearch && uv run python -c "from grok_search.server import mcp; print('ok')"`
Expected: 输出 `ok`（确认没有遗留对 planning/providers/utils 的 import）

- [ ] **Step 4: 提交**

```bash
git add -A
git commit -m "refactor(server): 拆成 8 个薄工具，删 6 个 plan_* + planning/providers/utils 死代码"
```

---

## Task 8: 8 工具注册冒烟测试

**Files:**
- Create: `tests/test_server_tools.py`

- [ ] **Step 1: 写测试 `tests/test_server_tools.py`**

```python
import pytest


@pytest.mark.asyncio
async def test_exactly_eight_tools_registered():
    from grok_search.server import mcp
    tools = await mcp.get_tools()  # FastMCP 2.x: dict[name -> Tool]
    names = set(tools.keys())
    expected = {
        "web_search", "get_sources", "web_fetch", "web_screenshot",
        "web_map", "get_config_info", "switch_model", "toggle_builtin_tools",
    }
    assert names == expected, f"工具集不符: 多={names - expected} 少={expected - names}"


@pytest.mark.asyncio
async def test_no_planning_tools():
    from grok_search.server import mcp
    tools = await mcp.get_tools()
    assert not any(n.startswith("plan_") for n in tools.keys())
```

> 注：若该 FastMCP 版本 `get_tools()` 接口不同，改用同步 `mcp._tool_manager.list_tools()` 或对应 API；目标是断言「恰好这 8 个名字、无 plan_*」。执行时以实际 FastMCP 版本的 API 为准。

- [ ] **Step 2: 跑测试确认通过**

Run: `cd ~/项目/自己的应用/GrokSearch && uv run pytest tests/test_server_tools.py -v`
Expected: PASS（恰好 8 个工具，无 plan_*）

- [ ] **Step 3: 跑全量测试**

Run: `cd ~/项目/自己的应用/GrokSearch && uv run pytest -v`
Expected: 全部 PASS（config 10 + sources 5 + key_pool 5 + firecrawl 1 + server 2）

- [ ] **Step 4: 提交**

```bash
git add tests/test_server_tools.py
git commit -m "test(server): 钉死恰好 8 个工具注册、无 plan_*"
```

---

## Task 9: README 重写 + 删营销资产 + pyproject 清理

**Files:**
- Modify（整体重写）: `README.md`
- Delete: `docs/README_EN.md`、`images/title.png`、`images/wgrok.png`、`images/wogrok.png`
- Modify: `pyproject.toml`（描述）、`LICENSE`（版权署名，见下）

- [ ] **Step 1: 删除营销资产**

```bash
cd ~/项目/自己的应用/GrokSearch
git rm docs/README_EN.md images/title.png images/wgrok.png images/wogrok.png
```

- [ ] **Step 2: 整体重写 `README.md`**

```markdown
# grok-search MCP

为 Claude Code 提供实时联网能力的本地 MCP 服务器：Grok 负责 AI 搜索，Tavily 负责高保真抓取/站点映射，Firecrawl 托底抓取与网页截图。

## 架构

```
Claude ──MCP──► grok-search
                 ├─ web_search        ─► Grok（AI 搜索，可选 extra_sources 补 Tavily/Firecrawl 信源）
                 ├─ get_sources       ─► 按 session_id 取缓存信源
                 ├─ web_fetch         ─► Tavily Extract → 失败降级 Firecrawl Scrape
                 ├─ web_screenshot    ─► Firecrawl 截图（签名链接）
                 ├─ web_map           ─► Tavily Map（站点结构）
                 ├─ get_config_info   ─► 配置诊断 + 连接测试 + 模型探针
                 ├─ switch_model      ─► 切换并持久化默认模型
                 └─ toggle_builtin_tools ─► 开关 Claude Code 官方 WebSearch/WebFetch
```

## 启动方式

本机通过 `grok-search-launcher.sh` 启动：从 `密钥存储/.env` 加载密钥、把所有 `TAVILY_API_KEY*` 聚合成 `TAVILY_API_KEYS` 多 key 轮询，再用 `uv run` 跑本地源码。

`~/.claude.json` 中注册：

```json
{
  "grok-search": {
    "type": "stdio",
    "command": "/Users/chenhuajin/项目/自己的应用/GrokSearch/grok-search-launcher.sh",
    "args": [],
    "env": {}
  }
}
```

验证：`claude mcp list` 显示 `grok-search ✓`。

## 环境变量

| 变量 | 必填 | 默认值 | 说明 |
|------|------|--------|------|
| `GROK_API_URL` | ✅ | - | OpenAI 兼容 Grok 端点（含 `/v1`） |
| `GROK_API_KEY` | ✅ | - | Grok key |
| `GROK_MODEL` | ❌ | `grok-4.3-console` | 默认模型（也可被 `~/.config/grok-search/config.json` 覆盖） |
| `TAVILY_API_KEYS` | ❌ | - | Tavily key（逗号分隔，多 key 轮询）；亦可用单数 `TAVILY_API_KEY` |
| `TAVILY_API_URL` | ❌ | `https://api.tavily.com` | Tavily 端点 |
| `FIRECRAWL_API_KEYS` | ❌ | - | Firecrawl key（逗号分隔，供抓取降级/补信源/截图三用）；兼容回落 `FIRECRAWL_SCREENSHOT_API_KEYS` |
| `FIRECRAWL_API_URL` | ❌ | `https://api.firecrawl.dev/v2` | Firecrawl 端点 |
| `GROK_DEBUG` | ❌ | `false` | 调试日志 |
| `GROK_LOG_LEVEL` / `GROK_LOG_DIR` | ❌ | `INFO` / `logs` | 日志级别/目录 |
| `GROK_RETRY_MAX_ATTEMPTS` / `GROK_RETRY_MULTIPLIER` / `GROK_RETRY_MAX_WAIT` | ❌ | `3` / `1` / `10` | 重试策略 |

> Firecrawl key 读取优先级：`FIRECRAWL_API_KEYS` → `FIRECRAWL_API_KEY` → `FIRECRAWL_SCREENSHOT_API_KEYS` → `FIRECRAWL_SCREENSHOT_API_KEY`。同一把 key 同时供 `web_fetch` 降级、`extra_sources` 补信源、`web_screenshot`，统一多 key failover + 30 分钟 cooldown。

## 开发

```bash
cd ~/项目/自己的应用/GrokSearch
uv run pytest -v                                   # 跑测试
uv run --directory . grok-search                   # 本地起 stdio（一般由 launcher 调用）
```

## 许可证

[MIT License](LICENSE)
```

- [ ] **Step 3: 改 `pyproject.toml` 描述**

把 `description = "MCP server for AI model search capabilities"` 保留即可（无品牌问题）；确认 `[project.scripts]` 仍是 `grok-search = "grok_search.server:main"`（入口不变）。无需其他改动。

- [ ] **Step 4: 处理 LICENSE 署名**

查看 `LICENSE` 顶部版权行。MIT 协议要求保留原作者版权声明——**不要删除原作者行**，按 MIT 惯例追加自己的年份/署名而非替换。执行时把这一行读出来给使用者确认如何署名（见 Task 11 前的确认）。

- [ ] **Step 5: 提交**

```bash
git add -A
git commit -m "docs: 重写 README 去 GuDa 软广，删营销截图与 EN 文档"
```

---

## Task 10: 启动验证 + 修复 MCP 注册（需使用者确认）

**Files:**
- Modify（需确认）: `~/.claude.json`（`grok-search.command` 路径）

- [ ] **Step 1: 本地起 stdio 冒烟**

Run: `cd ~/项目/自己的应用/GrokSearch && timeout 5 uv run --directory . grok-search < /dev/null; echo "exit=$?"`
Expected: 进程正常启动后因无 stdin 输入退出，无 Python traceback / import 错误。

- [ ] **Step 2: 向使用者确认改 `~/.claude.json`**

把 `grok-search.command` 从 `/Users/chenhuajin/项目/自己的应用/grok-search-launcher.sh`（不存在）改成 `/Users/chenhuajin/项目/自己的应用/GrokSearch/grok-search-launcher.sh`。**改前明确告知使用者并取得同意。**

- [ ] **Step 3: 改注册路径**（确认后）

用 Edit 把 `~/.claude.json` 中该行替换为正确路径。

- [ ] **Step 4: 验证连接**

Run: `claude mcp list | grep grok`
Expected: `grok-search: .../GrokSearch/grok-search-launcher.sh - ✓`

> 注：若仍 `✗`，检查 launcher 是否可执行（`chmod +x`）、`uv` 是否在 `~/.local/bin`、`.env` 是否含 `GROK_API_URL`/`GROK_API_KEY`。

---

## Task 11: git 收尾（需使用者确认）

- [ ] **Step 1: 向使用者确认 git 写操作**

展示将执行：`git remote remove origin`（断开 `GuDaStudio/GrokSearch`，保留本地历史）。取得同意后执行。

- [ ] **Step 2: 断开 origin**（确认后）

```bash
cd ~/项目/自己的应用/GrokSearch
git remote remove origin
git remote -v   # 确认无 origin
```

- [ ] **Step 3: 最终核对全量测试 + 工作树干净**

```bash
cd ~/项目/自己的应用/GrokSearch
uv run pytest -v
git status
git log --oneline -12
```

Expected: 测试全 PASS；工作树干净；提交历史含本次重写的若干 commit + 保留的原历史。

---

## Self-Review（写计划者自查）

**Spec coverage（设计文档每节是否都有任务落地）：**
- §4 八工具行为保留 → Task 7（server 重写，逐工具照搬契约）、Task 8（注册冒烟）✓
- §5 去 GuDa（config/meta/默认模型/README/EN/images） → Task 1（config）、Task 7（meta 去 author）、Task 9（README/资产/默认模型已在 Task1）✓
- §6 Firecrawl 单通道四级回落 → Task 1（config 优先级）、Task 6（client 统一池）✓
- §7 删死代码（providers/base/三死方法/两 formatter/三死 prompt/planning） → Task 4（grok_client 删 3 方法）、Task 2（仅留 search_prompt）、Task 7（删 planning/providers/utils）✓
- §8 新模块结构 → Task 2/4/5/6/7 ✓
- §9 修注册 → Task 10 ✓
- §10 验证（sources/key_pool/config/firecrawl 单测 + 8 工具冒烟 + 启动） → Task 1/2/3/6/8/10 ✓
- §11 git 断 origin 保留历史 → Task 11 ✓

**Placeholder scan：** 无 TBD/TODO；Task 8 的 FastMCP API 注记给了具体回退方案；Task 10 失败排查给了具体检查项。✓

**Type consistency：** `GrokClient`（Task 4 定义，Task 7 使用）一致；`tavily_extract/tavily_search/tavily_map`、`firecrawl_search/firecrawl_scrape/firecrawl_screenshot`（Task 5/6 定义，Task 7 import）名字一致；`config.firecrawl_api_keys`/`config.tavily_api_keys`（Task 1 定义，Task 6/7 使用）一致；`cooldown_status`（key_pool 既有，Task 7 用于 tavily + firecrawl 两处）一致。✓

**风险备注：** server.py 工具描述/签名是对外契约，Task 7 中逐字保留（仅去掉 `author` meta 与 web_search 描述里的 plan_intent 句）；执行时如对 FastMCP 注册 API 不确定，先核对已装版本。
