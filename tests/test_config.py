import importlib
import pytest


@pytest.fixture
def fresh_config(monkeypatch, tmp_path):
    """每个测试拿到一个干净的 Config 单例（清掉所有相关环境变量）。"""
    for var in [
        "GROK_API_URL", "GROK_API_KEY", "GROK_MODEL",
        "TAVILY_API_URL", "TAVILY_API_KEY", "TAVILY_API_KEYS",
        "FIRECRAWL_API_URL", "FIRECRAWL_API_KEY", "FIRECRAWL_API_KEYS",
        "FIRECRAWL_SCREENSHOT_API_KEY", "FIRECRAWL_SCREENSHOT_API_KEYS",
    ]:
        monkeypatch.delenv(var, raising=False)
    import grok_search.config as config_mod
    importlib.reload(config_mod)
    # 单例可能已缓存，强制重建
    config_mod.Config._instance = None
    cfg = config_mod.Config()
    # 隔离本机 ~/.config/grok-search/config.json（switch_model 会持久化 model，污染默认值断言）
    cfg._config_file = tmp_path / "config.json"
    return cfg


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


def test_guda_vars_are_ignored_no_derivation(fresh_config, monkeypatch):
    """回归守卫：即便误设了 GuDa 旧变量，也不得派生出任何端点/key。"""
    monkeypatch.setenv("GUDA_API_KEY", "should-be-ignored")
    monkeypatch.setenv("GUDA_BASE_URL", "https://code.guda.studio")
    # 没配 GROK_API_URL/KEY 时仍应报错——证明不再从 GUDA 派生
    with pytest.raises(ValueError):
        _ = fresh_config.grok_api_url
    with pytest.raises(ValueError):
        _ = fresh_config.grok_api_key
    # Tavily/Firecrawl 也不得从 GUDA 派生，只回落官方端点
    assert fresh_config.tavily_api_url == "https://api.tavily.com"
    assert fresh_config.firecrawl_api_url == "https://api.firecrawl.dev/v2"
    assert fresh_config.tavily_api_keys == []
    assert fresh_config.firecrawl_api_keys == []


def test_no_guda_in_config_info(fresh_config, monkeypatch):
    monkeypatch.setenv("GROK_API_URL", "https://relay.example.com/v1")
    monkeypatch.setenv("GROK_API_KEY", "sk-abc")
    info = fresh_config.get_config_info()
    assert not any("GUDA" in k or "guda" in str(v).lower() for k, v in info.items())
