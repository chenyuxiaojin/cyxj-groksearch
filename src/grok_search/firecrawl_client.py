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
