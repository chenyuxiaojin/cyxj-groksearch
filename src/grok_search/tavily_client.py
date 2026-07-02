import json

import httpx

from .config import config
from .http_client import get_client
from .key_pool import pick_tavily_key, mark_key_failed

# key 级错误（额度耗尽 / 被吊销 / 限速）：冷却当前 key 并立刻换下一个重试，
# 其余错误（超时 / 网络 / 目标站问题）换 key 也没用，直接返回。
_KEY_ERROR_STATUS = (401, 403, 429)


def _headers(api_key: str) -> dict:
    return {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}


async def tavily_extract(url: str) -> str | None:
    keys = config.tavily_api_keys
    if not keys:
        return None
    endpoint = f"{config.tavily_api_url.rstrip('/')}/extract"
    body = {"urls": [url], "format": "markdown"}
    client = get_client()
    for _ in range(len(keys)):
        api_key = pick_tavily_key(keys)
        if not api_key:
            return None
        try:
            response = await client.post(endpoint, headers=_headers(api_key), json=body, timeout=60.0)
            if response.status_code in _KEY_ERROR_STATUS:
                mark_key_failed(api_key)
                continue
            response.raise_for_status()
            data = response.json()
            results = data.get("results") or []
            if results:
                content = results[0].get("raw_content", "")
                return content if content and content.strip() else None
            return None
        except Exception:
            return None
    return None


async def tavily_search(query: str, max_results: int = 6) -> list[dict] | None:
    keys = config.tavily_api_keys
    if not keys:
        return None
    endpoint = f"{config.tavily_api_url.rstrip('/')}/search"
    body = {
        "query": query,
        "max_results": max_results,
        "search_depth": "advanced",
        "include_raw_content": False,
        "include_answer": False,
    }
    client = get_client()
    for _ in range(len(keys)):
        api_key = pick_tavily_key(keys)
        if not api_key:
            return None
        try:
            response = await client.post(endpoint, headers=_headers(api_key), json=body, timeout=90.0)
            if response.status_code in _KEY_ERROR_STATUS:
                mark_key_failed(api_key)
                continue
            response.raise_for_status()
            data = response.json()
            results = data.get("results", [])
            return [
                {"title": r.get("title", ""), "url": r.get("url", ""), "content": r.get("content", ""), "score": r.get("score", 0)}
                for r in results
            ] if results else None
        except Exception:
            return None
    return None


async def tavily_map(url: str, instructions: str = None, max_depth: int = 1,
                     max_breadth: int = 20, limit: int = 50, timeout: int = 150) -> str:
    keys = config.tavily_api_keys
    if not keys:
        return "配置错误: TAVILY_API_KEY 未配置，请设置环境变量 TAVILY_API_KEYS"
    endpoint = f"{config.tavily_api_url.rstrip('/')}/map"
    body = {"url": url, "max_depth": max_depth, "max_breadth": max_breadth, "limit": limit, "timeout": timeout}
    if instructions:
        body["instructions"] = instructions
    client = get_client()
    for _ in range(len(keys)):
        api_key = pick_tavily_key(keys)
        if not api_key:
            return f"映射失败: 所有 {len(keys)} 个 Tavily key 都在 cooldown 中"
        try:
            response = await client.post(
                endpoint, headers=_headers(api_key), json=body, timeout=float(timeout + 10)
            )
            if response.status_code in _KEY_ERROR_STATUS:
                mark_key_failed(api_key)
                continue
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
    return f"映射失败: 所有 {len(keys)} 个 Tavily key 均被拒（已进入 cooldown）"
