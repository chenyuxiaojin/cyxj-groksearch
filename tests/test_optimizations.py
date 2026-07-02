"""覆盖性能/精准度重构引入的新行为：
- web_fetch 对冲抓取（_hedged_fetch）
- extra_sources 在 Tavily / Firecrawl 之间的分配
- 可用模型缓存的 TTL 与失败不缓存
- Tavily 客户端遇 key 级错误自动换 key 重试
"""

import asyncio

import httpx
import pytest

import grok_search.server as server
import grok_search.key_pool as kp


def setup_function():
    kp._state["keys"] = None
    kp._state["cycle"] = None
    kp._cooldown.clear()
    server._AVAILABLE_MODELS_CACHE.clear()


# ---------- extra_sources 分配 ----------

def test_split_extra_counts_both_providers_split():
    tavily, firecrawl = server._split_extra_counts(6, has_tavily=True, has_firecrawl=True)
    assert tavily == 3 and firecrawl == 3

    tavily, firecrawl = server._split_extra_counts(5, has_tavily=True, has_firecrawl=True)
    assert tavily == 2 and firecrawl == 3
    assert tavily + firecrawl == 5


def test_split_extra_counts_single_provider():
    assert server._split_extra_counts(4, True, False) == (4, 0)
    assert server._split_extra_counts(4, False, True) == (0, 4)
    assert server._split_extra_counts(4, False, False) == (0, 0)
    assert server._split_extra_counts(0, True, True) == (0, 0)


# ---------- web_fetch 对冲 ----------

@pytest.mark.asyncio
async def test_hedged_fetch_tavily_fast_wins(monkeypatch):
    """Tavily 在延迟窗口内返回 → 直接用，不动 Firecrawl。"""
    monkeypatch.setenv("GROK_FETCH_HEDGE_DELAY", "1")
    firecrawl_called = False

    async def fake_tavily(url):
        return "tavily-content"

    async def fake_firecrawl(url, ctx=None):
        nonlocal firecrawl_called
        firecrawl_called = True
        return "firecrawl-content"

    monkeypatch.setattr(server, "tavily_extract", fake_tavily)
    monkeypatch.setattr(server, "firecrawl_scrape", fake_firecrawl)

    assert await server._hedged_fetch("https://x.com") == "tavily-content"
    assert not firecrawl_called


@pytest.mark.asyncio
async def test_hedged_fetch_slow_tavily_loses_to_firecrawl(monkeypatch):
    """Tavily 超过对冲延迟仍未返回 → Firecrawl 并行追加并胜出。"""
    monkeypatch.setenv("GROK_FETCH_HEDGE_DELAY", "0.05")

    async def slow_tavily(url):
        await asyncio.sleep(5)
        return "tavily-content"

    async def fake_firecrawl(url, ctx=None):
        return "firecrawl-content"

    monkeypatch.setattr(server, "tavily_extract", slow_tavily)
    monkeypatch.setattr(server, "firecrawl_scrape", fake_firecrawl)

    assert await server._hedged_fetch("https://x.com") == "firecrawl-content"


@pytest.mark.asyncio
async def test_hedged_fetch_tavily_fast_failure_falls_back(monkeypatch):
    """Tavily 快速失败（返回 None）→ 立即降级 Firecrawl，无需等对冲延迟。"""
    monkeypatch.setenv("GROK_FETCH_HEDGE_DELAY", "30")

    async def failing_tavily(url):
        return None

    async def fake_firecrawl(url, ctx=None):
        return "firecrawl-content"

    monkeypatch.setattr(server, "tavily_extract", failing_tavily)
    monkeypatch.setattr(server, "firecrawl_scrape", fake_firecrawl)

    assert await server._hedged_fetch("https://x.com") == "firecrawl-content"


@pytest.mark.asyncio
async def test_hedged_fetch_both_fail_returns_none(monkeypatch):
    monkeypatch.setenv("GROK_FETCH_HEDGE_DELAY", "0.05")

    async def slow_failing_tavily(url):
        await asyncio.sleep(0.2)
        return None

    async def failing_firecrawl(url, ctx=None):
        return None

    monkeypatch.setattr(server, "tavily_extract", slow_failing_tavily)
    monkeypatch.setattr(server, "firecrawl_scrape", failing_firecrawl)

    assert await server._hedged_fetch("https://x.com") is None


# ---------- 模型缓存 ----------

@pytest.mark.asyncio
async def test_models_cache_hits_within_ttl(monkeypatch):
    calls = 0

    async def fake_fetch(api_url, api_key):
        nonlocal calls
        calls += 1
        return ["m1", "m2"]

    monkeypatch.setattr(server, "_fetch_available_models", fake_fetch)
    assert await server._get_available_models_cached("u", "k") == ["m1", "m2"]
    assert await server._get_available_models_cached("u", "k") == ["m1", "m2"]
    assert calls == 1


@pytest.mark.asyncio
async def test_models_cache_failure_not_cached(monkeypatch):
    calls = 0

    async def flaky_fetch(api_url, api_key):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("network down")
        return ["m1"]

    monkeypatch.setattr(server, "_fetch_available_models", flaky_fetch)
    assert await server._get_available_models_cached("u", "k") == []
    # 失败不写缓存，下一次调用应重新拉取并成功
    assert await server._get_available_models_cached("u", "k") == ["m1"]
    assert calls == 2


# ---------- Tavily key failover ----------

@pytest.mark.asyncio
async def test_tavily_extract_fails_over_to_next_key(monkeypatch):
    import grok_search.tavily_client as tc

    monkeypatch.setenv("TAVILY_API_KEYS", "bad-key,good-key")

    def handler(request: httpx.Request) -> httpx.Response:
        auth = request.headers.get("Authorization", "")
        if "bad-key" in auth:
            return httpx.Response(429, json={"error": "rate limited"})
        return httpx.Response(200, json={"results": [{"raw_content": "page content"}]})

    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(tc, "get_client", lambda: mock_client)

    try:
        result = await tc.tavily_extract("https://example.com")
        assert result == "page content"
        # bad-key 应已被冷却
        assert kp._cooldown.get("bad-key", 0) > 0
    finally:
        await mock_client.aclose()
