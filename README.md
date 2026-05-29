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
