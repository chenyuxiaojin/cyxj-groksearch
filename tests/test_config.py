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
        "GROK_BACKEND", "GROK_CLI_PATH", "GROK_CLI_MODEL", "GROK_CLI_EFFORT",
        "GROK_CLI_TIMEOUT", "GROK_SEARCH_NESTED",
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


def test_default_model_is_fast(fresh_config):
    fresh_config._cached_model = None
    assert fresh_config.grok_model == "grok-4.3-fast"


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


# ---------- Grok 后端选择（CLI / API） ----------

def _fake_cli(tmp_path):
    cli = tmp_path / "grok"
    cli.write_text("#!/bin/sh\necho grok 0.0.0\n")
    cli.chmod(0o755)
    return cli


def test_backend_none_when_nothing_available(fresh_config, monkeypatch):
    monkeypatch.setenv("GROK_CLI_PATH", "/nonexistent/grok")
    assert fresh_config.grok_cli_path is None
    assert fresh_config.resolve_grok_backend() == "none"


def test_backend_auto_prefers_cli(fresh_config, monkeypatch, tmp_path):
    monkeypatch.setenv("GROK_CLI_PATH", str(_fake_cli(tmp_path)))
    monkeypatch.setenv("GROK_API_URL", "https://relay.example.com/v1")
    monkeypatch.setenv("GROK_API_KEY", "sk-abc")
    assert fresh_config.grok_backend == "auto"
    assert fresh_config.resolve_grok_backend() == "cli"


def test_backend_auto_falls_back_to_api_without_cli(fresh_config, monkeypatch):
    monkeypatch.setenv("GROK_CLI_PATH", "/nonexistent/grok")
    monkeypatch.setenv("GROK_API_URL", "https://relay.example.com/v1")
    monkeypatch.setenv("GROK_API_KEY", "sk-abc")
    assert fresh_config.resolve_grok_backend() == "api"


def test_backend_nested_disables_cli(fresh_config, monkeypatch, tmp_path):
    monkeypatch.setenv("GROK_CLI_PATH", str(_fake_cli(tmp_path)))
    monkeypatch.setenv("GROK_SEARCH_NESTED", "1")
    assert fresh_config.grok_cli_nested is True
    assert fresh_config.resolve_grok_backend() == "none"
    monkeypatch.setenv("GROK_API_URL", "https://relay.example.com/v1")
    monkeypatch.setenv("GROK_API_KEY", "sk-abc")
    assert fresh_config.resolve_grok_backend() == "api"


def test_backend_explicit_api_ignores_cli(fresh_config, monkeypatch, tmp_path):
    monkeypatch.setenv("GROK_CLI_PATH", str(_fake_cli(tmp_path)))
    monkeypatch.setenv("GROK_BACKEND", "api")
    assert fresh_config.resolve_grok_backend() == "none"
    monkeypatch.setenv("GROK_API_URL", "https://relay.example.com/v1")
    monkeypatch.setenv("GROK_API_KEY", "sk-abc")
    assert fresh_config.resolve_grok_backend() == "api"


def test_backend_explicit_cli_does_not_fall_back(fresh_config, monkeypatch):
    monkeypatch.setenv("GROK_BACKEND", "cli")
    monkeypatch.setenv("GROK_CLI_PATH", "/nonexistent/grok")
    monkeypatch.setenv("GROK_API_URL", "https://relay.example.com/v1")
    monkeypatch.setenv("GROK_API_KEY", "sk-abc")
    assert fresh_config.resolve_grok_backend() == "none"


def test_invalid_backend_value_falls_back_to_auto(fresh_config, monkeypatch):
    monkeypatch.setenv("GROK_BACKEND", "banana")
    assert fresh_config.grok_backend == "auto"


def test_cli_timeout_floor_and_default(fresh_config, monkeypatch):
    assert fresh_config.grok_cli_timeout == 180.0
    monkeypatch.setenv("GROK_CLI_TIMEOUT", "3")
    assert fresh_config.grok_cli_timeout == 10.0
    monkeypatch.setenv("GROK_CLI_TIMEOUT", "abc")
    assert fresh_config.grok_cli_timeout == 180.0


def test_config_info_reports_cli_backend_without_api(fresh_config, monkeypatch, tmp_path):
    monkeypatch.setenv("GROK_CLI_PATH", str(_fake_cli(tmp_path)))
    info = fresh_config.get_config_info()
    assert info["active_grok_backend"] == "cli"
    assert info["GROK_API_URL"] == "未配置"
    assert info["config_status"].startswith("✅")


def test_cli_effort_defaults_to_medium_and_can_defer_to_cli(fresh_config, monkeypatch):
    assert fresh_config.grok_cli_effort == "medium"
    monkeypatch.setenv("GROK_CLI_EFFORT", "xhigh")
    assert fresh_config.grok_cli_effort == "xhigh"
    monkeypatch.setenv("GROK_CLI_EFFORT", "default")
    assert fresh_config.grok_cli_effort == ""
