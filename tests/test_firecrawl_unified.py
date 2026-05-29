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
