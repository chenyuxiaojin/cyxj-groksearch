import sys
import asyncio
import json
import time
from pathlib import Path

import httpx

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
    from grok_search.http_client import get_client
    from grok_search.sources import SourcesCache, merge_sources, new_session_id, split_answer_and_sources
    from grok_search.key_pool import cooldown_status
    from grok_search.tavily_client import tavily_extract, tavily_search, tavily_map
    from grok_search.firecrawl_client import firecrawl_search, firecrawl_scrape, firecrawl_screenshot
except ImportError:
    from .grok_client import GrokClient
    from .logger import log_info
    from .config import config
    from .http_client import get_client
    from .sources import SourcesCache, merge_sources, new_session_id, split_answer_and_sources
    from .key_pool import cooldown_status
    from .tavily_client import tavily_extract, tavily_search, tavily_map
    from .firecrawl_client import firecrawl_search, firecrawl_scrape, firecrawl_screenshot

mcp = FastMCP("grok-search")

_SOURCES_CACHE = SourcesCache(max_size=256)
# (api_url, api_key) -> (缓存时间戳, 模型列表)。带 TTL，且拉取失败不写缓存，
# 避免一次网络抖动导致模型校验永久失效（旧实现把空列表缓存到进程结束）。
_AVAILABLE_MODELS_CACHE: dict[tuple[str, str], tuple[float, list[str]]] = {}
_AVAILABLE_MODELS_LOCK = asyncio.Lock()
_AVAILABLE_MODELS_TTL = 600.0


async def _fetch_available_models(api_url: str, api_key: str) -> list[str]:
    models_url = f"{api_url.rstrip('/')}/models"
    response = await get_client().get(
        models_url,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        timeout=10.0,
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
    now = time.monotonic()
    async with _AVAILABLE_MODELS_LOCK:
        cached = _AVAILABLE_MODELS_CACHE.get(key)
        if cached and now - cached[0] < _AVAILABLE_MODELS_TTL:
            return cached[1]
    try:
        models = await _fetch_available_models(api_url, api_key)
    except Exception:
        return []
    async with _AVAILABLE_MODELS_LOCK:
        _AVAILABLE_MODELS_CACHE[key] = (time.monotonic(), models)
    return models


def _split_extra_counts(extra_sources: int, has_tavily: bool, has_firecrawl: bool) -> tuple[int, int]:
    """把 extra_sources 分配给 (tavily, firecrawl) 两个引擎。

    两者都可用时对半分（Firecrawl 拿多的那半），让补充信源来自两个独立
    搜索引擎，覆盖面更广；旧实现的 round(extra_sources * 1) 会把全部名额
    给 Firecrawl，Tavily 永远分到 0。"""
    if extra_sources <= 0:
        return 0, 0
    if has_tavily and has_firecrawl:
        firecrawl_count = (extra_sources + 1) // 2
        return extra_sources - firecrawl_count, firecrawl_count
    if has_firecrawl:
        return 0, extra_sources
    if has_tavily:
        return extra_sources, 0
    return 0, 0


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

    tavily_count, firecrawl_count = _split_extra_counts(
        extra_sources, bool(config.tavily_api_keys), bool(config.firecrawl_api_keys)
    )

    async def _safe_grok() -> str:
        try:
            return await grok.search(query, platform)
        except Exception:
            return ""

    # tavily_search / firecrawl_search 内部已吞掉所有异常并返回 None，
    # 这里直接建 task 并发跑，按名字取结果，不再依赖 gather 的位置索引。
    grok_task = asyncio.create_task(_safe_grok())
    tavily_task = asyncio.create_task(tavily_search(query, tavily_count)) if tavily_count > 0 else None
    firecrawl_task = asyncio.create_task(firecrawl_search(query, firecrawl_count)) if firecrawl_count > 0 else None

    grok_result: str = (await grok_task) or ""
    tavily_results = await tavily_task if tavily_task else None
    firecrawl_results = await firecrawl_task if firecrawl_task else None

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
    has_tavily = bool(config.tavily_api_keys)
    has_firecrawl = bool(config.firecrawl_api_keys)
    if not has_tavily and not has_firecrawl:
        return "配置错误: TAVILY_API_KEYS 和 FIRECRAWL_API_KEYS 均未配置"

    await log_info(ctx, f"Begin Fetch: {url}", config.debug_enabled)
    if not has_firecrawl:
        result = await tavily_extract(url)
    elif not has_tavily:
        result = await firecrawl_scrape(url, ctx)
    else:
        result = await _hedged_fetch(url, ctx)

    if result:
        await log_info(ctx, "Fetch Finished!", config.debug_enabled)
        return result
    await log_info(ctx, "Fetch Failed!", config.debug_enabled)
    return "提取失败: 所有提取服务均未能获取内容"


async def _hedged_fetch(url: str, ctx=None) -> str | None:
    """对冲式抓取：先发 Tavily；超过 GROK_FETCH_HEDGE_DELAY 秒仍未返回，
    并行追加 Firecrawl，谁先出有效内容用谁，另一方立即取消。

    相比旧的串行降级（Tavily 最长等 60 秒失败后才轮到 Firecrawl），慢站点
    最坏等待从「Tavily 超时 + Firecrawl 全程」缩短为约「hedge 延迟 + 较快
    一方的耗时」；Tavily 在延迟窗口内正常返回时行为不变，不多耗额度。"""
    tavily_task = asyncio.create_task(tavily_extract(url))
    done, _ = await asyncio.wait({tavily_task}, timeout=config.fetch_hedge_delay)

    if tavily_task in done:
        result = tavily_task.result()
        if result:
            return result
        # Tavily 快速失败 → 直接走 Firecrawl，无需对冲
        return await firecrawl_scrape(url, ctx)

    await log_info(ctx, "Tavily slow, hedging with Firecrawl...", config.debug_enabled)
    firecrawl_task = asyncio.create_task(firecrawl_scrape(url, ctx))
    pending = {tavily_task, firecrawl_task}
    result = None
    while pending and not result:
        done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            value = task.result()
            if value:
                result = value
                break
    for task in pending:
        task.cancel()
    return result


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
    config_info = config.get_config_info()

    # 两个探针互相独立，并发执行：总耗时从「连接测试 + 模型探针」之和
    # 缩短为两者中较慢的一个。
    connection_test, default_model_health = await asyncio.gather(
        _probe_models_endpoint(), _probe_default_model()
    )
    config_info["connection_test"] = connection_test
    config_info["default_model_health"] = default_model_health

    config_info["tavily_key_cooldown"] = cooldown_status(config.tavily_api_keys)
    config_info["firecrawl_key_cooldown"] = cooldown_status(config.firecrawl_api_keys)

    return json.dumps(config_info, ensure_ascii=False, indent=2)


async def _probe_models_endpoint() -> dict:
    test_result = {"status": "未测试", "message": "", "response_time_ms": 0}
    try:
        api_url = config.grok_api_url
        api_key = config.grok_api_key
        models_url = f"{api_url.rstrip('/')}/models"
        start_time = time.monotonic()
        response = await get_client().get(
            models_url,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            timeout=10.0,
        )
        response_time = (time.monotonic() - start_time) * 1000
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
    return test_result


async def _probe_default_model() -> dict:
    default_model_health = {"model": "未配置", "status": "未测试", "response_time_ms": 0, "message": ""}
    try:
        api_url = config.grok_api_url
        api_key = config.grok_api_key
        model = config.grok_model
        default_model_health["model"] = model
        probe_payload = {"model": model, "stream": False, "max_tokens": 1, "messages": [{"role": "user", "content": "ping"}]}
        start_time = time.monotonic()
        resp = await get_client().post(
            f"{api_url.rstrip('/')}/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json=probe_payload,
            timeout=10.0,
        )
        default_model_health["response_time_ms"] = round((time.monotonic() - start_time) * 1000, 2)
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
    return default_model_health


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
