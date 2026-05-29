import pytest


async def _tool_names() -> set[str]:
    """枚举已注册工具名，兼容 FastMCP 3.x 的异步 list_tools() -> list[Tool]。"""
    from grok_search.server import mcp
    tools = await mcp.list_tools()
    return {t.name for t in tools}


@pytest.mark.asyncio
async def test_exactly_eight_tools_registered():
    names = await _tool_names()
    expected = {
        "web_search", "get_sources", "web_fetch", "web_screenshot",
        "web_map", "get_config_info", "switch_model", "toggle_builtin_tools",
    }
    assert names == expected, f"工具集不符: 多={names - expected} 少={expected - names}"


@pytest.mark.asyncio
async def test_no_planning_tools():
    names = await _tool_names()
    assert not any(n.startswith("plan_") for n in names)
