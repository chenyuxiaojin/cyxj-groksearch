[**English**](README.md) | 中文

# cyxj-groksearch

> 为 Claude Code 提供实时联网能力的 MCP 服务器：Grok AI 搜索、Tavily 高保真抓取/站点映射、Firecrawl 网页截图，内置多 key 轮询与自动 failover。

[![Python](https://img.shields.io/badge/python-3.10%2B-blue)](https://www.python.org/) [![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE) [![MCP](https://img.shields.io/badge/protocol-MCP-purple)](https://modelcontextprotocol.io/)

## 为什么用它，而不是 Claude Code 内置 WebSearch？

Claude Code 自带 `WebSearch` 和 `WebFetch`。这个 MCP 不是替代，而是补充——它提供内置工具做不到的能力：

- **多源交叉验证** — `web_search` 可通过 `extra_sources` 参数同步向 Tavily/Firecrawl 并行取独立信源，合并后一次返回，Claude 可在单次调用内对比多方索引结果。
- **Grok 中转路由** — 可将 AI 搜索流量指向任意 OpenAI 兼容端点（官方 Grok API、自建镜像、中转站）。内置工具无法重定向。
- **原文全文抓取** — `web_fetch` 通过 Tavily Extract（Firecrawl 降级）获取 16 KB+ 结构化 Markdown 原文，而非摘要片段，适合钉死一手事实。
- **强制路由控制** — `toggle_builtin_tools` 把 `WebSearch`/`WebFetch` 写入项目 `.claude/settings.json` 黑名单，所有联网请求强制走本 MCP。可随时撤销。
- **多 key 轮询池** — Grok / Tavily / Firecrawl 三个 API 均支持逗号分隔多 key，遇到限速/报错自动 30 分钟 cooldown，无缝切下一个 key。

## 功能特性

- 8 个 MCP 工具，覆盖搜索、抓取、截图、站点映射、运行时控制
- Grok AI 搜索 + session 级信源缓存
- Tavily Extract 为主抓取，Firecrawl 自动降级
- Firecrawl JS 渲染截图（返回签名 GCS URL）
- 同一 Firecrawl key 池同时供 `web_fetch` 降级、`extra_sources` 补信源、`web_screenshot` 三用
- 内置连接诊断：配置检查 + 1-token 模型探针
- 通过 `~/.config/grok-search/config.json` 持久化默认模型
- launcher 脚本自动聚合所有 `TAVILY_API_KEY*` 变量成多 key 轮询池

## 工具列表

| 工具 | 作用 | 关键参数 |
|------|------|---------|
| `web_search` | Grok AI 搜索；缓存信源，返回 `session_id` + `content` + `sources_count` | `query`、`platform`（可选，限定平台）、`model`（单次覆盖）、`extra_sources`（附加 Tavily/Firecrawl 信源数） |
| `get_sources` | 按 `session_id` 取上次 `web_search` 缓存的完整信源列表 | `session_id` |
| `web_fetch` | 抓取 URL 全文，以 Markdown 返回；Tavily 主抓取 → Firecrawl 降级 | `url` |
| `web_screenshot` | Firecrawl JS 渲染截图，返回签名 PNG URL | `url`、`full_page`（bool，默认 false） |
| `web_map` | 遍历站点链接图，返回 URL 结构清单（Tavily） | `url`、`instructions`、`max_depth`、`max_breadth`、`limit`、`timeout` |
| `get_config_info` | 显示配置、执行连接测试、列出可用模型、查看 key cooldown 状态 | — |
| `switch_model` | 切换默认 Grok 模型，持久化到 `~/.config/grok-search/config.json` | `model` |
| `toggle_builtin_tools` | 把 `WebSearch`/`WebFetch` 加入/移出项目 deny 名单 | `action`（`"on"` / `"off"` / `"status"`） |

## 安装与配置

### 前置条件

- [uv](https://docs.astral.sh/uv/) — Python 包管理器
- [Claude Code](https://claude.ai/code)

### 方式 A — 从 GitHub 安装（推荐）

用 `uvx` 从 GitHub 直接安装并注册 MCP。只有 `GROK_API_URL` 和 `GROK_API_KEY` 必填，Tavily/Firecrawl 可选。

```bash
claude mcp add-json grok-search --scope user '{
  "type": "stdio",
  "command": "uvx",
  "args": ["--from", "git+https://github.com/chenyuxiaojin/cyxj-groksearch@main", "grok-search"],
  "env": {
    "GROK_API_URL": "https://your-grok-endpoint/v1",
    "GROK_API_KEY": "your-grok-api-key",
    "TAVILY_API_KEYS": "tvly-...",
    "FIRECRAWL_API_KEYS": "fc-..."
  }
}'
```

验证：`claude mcp list` 显示 `grok-search ✓` 即成功。

### 方式 B — 本地源码 + launcher 脚本（开发调试）

克隆仓库后，在 Claude Code MCP 注册里把 `command` 指向 launcher 脚本。launcher 会加载 `.env`、自动聚合所有 `TAVILY_API_KEY*` 变量到轮询池，再用 `uv run` 从本地源码启动（改代码即时生效）。

```json
{
  "grok-search": {
    "type": "stdio",
    "command": "/绝对路径/GrokSearch/grok-search-launcher.sh",
    "args": [],
    "env": { "GROK_SEARCH_ENV_FILE": "/绝对路径/你的/.env" }
  }
}
```

`.env` 默认取脚本同目录，可用 `GROK_SEARCH_ENV_FILE` 覆盖。参考 [`.env.example`](.env.example) 查看所有变量。

## 环境变量

| 变量 | 必填 | 默认值 | 说明 |
|------|------|--------|------|
| `GROK_API_URL` | ✅ | — | OpenAI 兼容 Grok 端点（含 `/v1`） |
| `GROK_API_KEY` | ✅ | — | Grok API key |
| `GROK_MODEL` | — | `grok-4.3-console` | 默认模型（也可被 `~/.config/grok-search/config.json` 覆盖） |
| `TAVILY_API_KEYS` | — | — | Tavily key，逗号分隔多 key；也支持单数 `TAVILY_API_KEY` |
| `TAVILY_API_URL` | — | `https://api.tavily.com` | Tavily 端点 |
| `FIRECRAWL_API_KEYS` | — | — | Firecrawl key，逗号分隔；兼容回落 `FIRECRAWL_SCREENSHOT_API_KEYS` |
| `FIRECRAWL_API_URL` | — | `https://api.firecrawl.dev/v2` | Firecrawl 端点 |
| `GROK_FETCH_HEDGE_DELAY` | — | `8` | `web_fetch` 对冲延迟（秒）：Tavily 超时未返回则并行追加 Firecrawl；设 `0` 为始终并发 |
| `GROK_DEBUG` | — | `false` | 调试日志开关 |
| `GROK_LOG_LEVEL` / `GROK_LOG_DIR` | — | `INFO` / `logs` | 日志级别/目录 |
| `GROK_RETRY_MAX_ATTEMPTS` / `GROK_RETRY_MULTIPLIER` / `GROK_RETRY_MAX_WAIT` | — | `3` / `1` / `10` | 重试策略 |

> Firecrawl key 读取优先级：`FIRECRAWL_API_KEYS` → `FIRECRAWL_API_KEY` → `FIRECRAWL_SCREENSHOT_API_KEYS` → `FIRECRAWL_SCREENSHOT_API_KEY`

## 强制联网走本工具

安装后在对话里说「调用 toggle_builtin_tools，action=on」。这会把 `WebSearch`、`WebFetch` 写入当前项目 `.claude/settings.json` 的 deny 列表，所有联网请求强制路由到本 MCP。用 `action=off` 可恢复内置工具。

## 与同类工具对比

| | cyxj-groksearch | Tavily 官方 MCP | Firecrawl 官方 MCP | Brave Search MCP | Exa MCP | Claude Code 内置 WebSearch |
|---|---|---|---|---|---|---|
| AI 搜索（Grok） | 有 | 无 | 无 | 无 | 无 | 有（Claude 内置） |
| 全文抓取 | 有（Tavily + Firecrawl 降级） | 部分 | 有 | 无 | 有 | 摘要片段 |
| 站点结构映射 | 有 | 无 | 有 | 无 | 无 | 无 |
| JS 截图 | 有（Firecrawl） | 无 | 有 | 无 | 无 | 无 |
| 多 key failover | 有（三个 API 均支持） | 无 | 无 | 无 | 无 | N/A |
| 强制路由控制 | 有（toggle_builtin_tools） | 无 | 无 | 无 | 无 | N/A |
| session 信源缓存 | 有（get_sources） | 无 | 无 | 无 | 无 | 无 |
| 中转端点支持 | 有（任意 OpenAI 兼容 URL） | 无 | 无 | 无 | 无 | 无 |

## FAQ

**不配 Tavily / Firecrawl 能用吗？**
能。只有 `GROK_API_URL` 和 `GROK_API_KEY` 是必填项。不配 Tavily，`web_fetch` 和 `web_map` 会返回配置提示。不配 Firecrawl，`web_screenshot` 返回配置提示，`web_fetch` 的 Firecrawl 降级路径跳过。

**与内置 WebSearch 的区别是什么？怎么强制走本工具？**
内置 `WebSearch` 由 Claude 托管，无法重定向到自定义端点。本 MCP 路由到你自己的 Grok 端点，并增加多源聚合、全文抓取和 key failover。强制路由：调用 `toggle_builtin_tools` 设 `action="on"`；恢复内置：设 `action="off"`。

**支持哪些 Grok 端点？**
任意暴露了 `/v1/chat/completions` 和 `/v1/models` 的 OpenAI 兼容端点，包括官方 `api.x.ai`、自建镜像和商业中转站。`GROK_API_URL` 填含 `/v1` 的基础 URL。

**多 key failover 怎么工作？**
每个 API（Grok、Tavily、Firecrawl）都接受逗号分隔的多 key 列表，服务器按轮询顺序使用。某个 key 报错（限速或网络错误）后进入 30 分钟 cooldown，期间自动跳过。用 `get_config_info` 可查看当前 cooldown 状态。

**连接有问题怎么排查？**
调用 `get_config_info`。它会向 `/models` 发连接测试请求、发送 1-token 探针验证默认模型是否可用，并报告所有 key 池的 cooldown 状态。

## 环境要求

- Python 3.10+
- `fastmcp >= 2.3.0`、`mcp[cli] >= 1.21.2`、`httpx[socks] >= 0.28.0`、`tenacity >= 8.0.0`
- `uv`（用于安装和本地开发）

## 开发

```bash
cd GrokSearch
uv sync
uv run --extra dev pytest -v        # 跑测试
uv run --directory . grok-search    # 本地 stdio 调试
```

## 许可证

[MIT License](LICENSE) — 基于 [GuDaStudio/GrokSearch](https://github.com/GuDaStudio/GrokSearch) 重写。
