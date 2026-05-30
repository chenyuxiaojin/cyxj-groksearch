# grok-search MCP

为 Claude Code 提供实时联网能力的 MCP 服务器：**Grok** 负责 AI 搜索，**Tavily** 负责高保真抓取/站点映射，**Firecrawl** 托底抓取与网页截图。

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

## 安装（推荐）

前置：[uv](https://docs.astral.sh/uv/)、Claude Code。用 `uvx` 直接从本仓库安装，密钥写在注册的 `env` 里：

```bash
claude mcp add-json grok-search --scope user '{
  "type": "stdio",
  "command": "uvx",
  "args": ["--from", "git+https://github.com/chenhuajinchj/cyxj-groksearch@main", "grok-search"],
  "env": {
    "GROK_API_URL": "https://your-grok-endpoint/v1",
    "GROK_API_KEY": "your-grok-api-key",
    "TAVILY_API_KEYS": "tvly-...",
    "FIRECRAWL_API_KEYS": "fc-..."
  }
}'
```

只有 `GROK_API_URL` / `GROK_API_KEY` 必填；Tavily / Firecrawl 可选（不配则对应工具返回配置提示）。

验证：`claude mcp list` 显示 `grok-search ✓`。装好后可在对话里说「调用 grok-search 的 toggle_builtin_tools 关闭官方 WebSearch/WebFetch」，把联网强制路由到本工具。

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

> 完整示例见 [`.env.example`](./.env.example)。
>
> Firecrawl key 读取优先级：`FIRECRAWL_API_KEYS` → `FIRECRAWL_API_KEY` → `FIRECRAWL_SCREENSHOT_API_KEYS` → `FIRECRAWL_SCREENSHOT_API_KEY`。同一把 key 同时供 `web_fetch` 降级、`extra_sources` 补信源、`web_screenshot`，统一多 key failover + 30 分钟 cooldown。

## 用 launcher 启动（可选）

仓库自带 `grok-search-launcher.sh`：加载一个 `.env`、把所有 `TAVILY_API_KEY*` 变量自动聚合成 `TAVILY_API_KEYS` 多 key 轮询，再用本地源码 `uv run` 启动（改源码即时生效，适合本地开发）。注册时把 `command` 指向脚本即可。`.env` 默认取脚本同目录，可用 `GROK_SEARCH_ENV_FILE` 指向别处：

```json
{
  "grok-search": {
    "type": "stdio",
    "command": "/abs/path/to/cyxj-groksearch/grok-search-launcher.sh",
    "args": [],
    "env": { "GROK_SEARCH_ENV_FILE": "/abs/path/to/your/.env" }
  }
}
```

## 开发

```bash
uv run --extra dev pytest -v       # 跑测试
uv run --directory . grok-search   # 本地起 stdio（一般由 launcher 调用）
```

## 许可证

[MIT License](LICENSE) · fork 自 [GuDaStudio/GrokSearch](https://github.com/GuDaStudio/GrokSearch)，已去除原作者商业引流并重写。
