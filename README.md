**English** | [中文文档](README.zh-CN.md)

# cyxj-groksearch

> cyxj-groksearch is an MCP server that gives Claude Code real-time web access — Grok AI search, Tavily fetch/map, and Firecrawl screenshots, with multi-key failover.

[![Python](https://img.shields.io/badge/python-3.10%2B-blue)](https://www.python.org/) [![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE) [![MCP](https://img.shields.io/badge/protocol-MCP-purple)](https://modelcontextprotocol.io/)

## Why this instead of Claude Code's built-in WebSearch?

Claude Code ships with its own `WebSearch` and `WebFetch` tools. This MCP server is not a replacement for those — it sits alongside them and offers capabilities the built-ins lack:

- **Multi-source cross-verification** — `web_search` can fan out to Tavily/Firecrawl in parallel (`extra_sources` param) and merge all results before returning, so Claude can compare independently indexed sources in a single call.
- **Grok relay endpoint** — route AI search traffic to any OpenAI-compatible endpoint (official Grok API, self-hosted mirrors, rate-limit bypass relays). The built-ins cannot be redirected.
- **Full-text extraction** — `web_fetch` retrieves raw page content via Tavily Extract (with Firecrawl as fallback), returning 16 KB+ of structured Markdown rather than a short snippet. Useful for fact-pinning against primary sources.
- **Force-routing control** — `toggle_builtin_tools` writes `WebSearch`/`WebFetch` into the project's `.claude/settings.json` deny list, so all web traffic goes through this MCP exclusively. You can flip it back at any time.
- **Multi-key failover pool** — each API (Grok / Tavily / Firecrawl) supports comma-separated key lists with automatic 30-minute cooldown on errors, enabling uninterrupted operation across rate-limit events.

## Features

- 8 MCP tools covering search, fetch, screenshot, site mapping, and runtime control
- Grok AI search with session-scoped source caching
- Tavily Extract as primary fetcher; Firecrawl as automatic fallback
- Firecrawl JS-rendered screenshot (returns signed GCS URL)
- Single key pool shared across `web_fetch` fallback, `extra_sources`, and `web_screenshot`
- Built-in connection diagnostics: config check + 1-token model probe
- Persistent model switching via `~/.config/grok-search/config.json`
- Launcher script that auto-aggregates all `TAVILY_API_KEY*` env vars into a multi-key pool

## Tools

| Tool | Description | Key Parameters |
|------|-------------|----------------|
| `web_search` | AI-powered search via Grok; caches sources, returns `session_id` + `content` + `sources_count` | `query`, `platform` (optional focus), `model` (per-call override), `extra_sources` (0–N extra results from Tavily/Firecrawl) |
| `get_sources` | Retrieve the full source list from a previous `web_search` call | `session_id` |
| `web_fetch` | Extract full-text content from a URL as Markdown; Tavily primary → Firecrawl fallback | `url` |
| `web_screenshot` | Capture a JS-rendered screenshot via Firecrawl; returns a signed PNG URL | `url`, `full_page` (bool, default false) |
| `web_map` | Traverse a site's link graph and return a URL structure map (Tavily) | `url`, `instructions`, `max_depth`, `max_breadth`, `limit`, `timeout` |
| `get_config_info` | Show current configuration, run connection test, list available models, show key cooldown status | — |
| `switch_model` | Change the default Grok model; persisted to `~/.config/grok-search/config.json` | `model` |
| `toggle_builtin_tools` | Add/remove `WebSearch` and `WebFetch` from the project's deny list | `action` (`"on"` / `"off"` / `"status"`) |

## Install & Setup

### Prerequisites

- [uv](https://docs.astral.sh/uv/) — Python package manager
- [Claude Code](https://claude.ai/code)

### Option A — Install from GitHub (recommended)

Install directly with `uvx` and register it as an MCP server. Only `GROK_API_URL` and `GROK_API_KEY` are required; Tavily and Firecrawl are optional.

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

Verify: `claude mcp list` should show `grok-search ✓`.

### Option B — Local source with launcher script (for development)

Clone the repo and point Claude Code at the launcher script. The launcher loads a `.env` file and auto-aggregates all `TAVILY_API_KEY*` variables into the multi-key pool, then starts the server from local source using `uv run`.

```json
{
  "grok-search": {
    "type": "stdio",
    "command": "/absolute/path/to/GrokSearch/grok-search-launcher.sh",
    "args": [],
    "env": { "GROK_SEARCH_ENV_FILE": "/absolute/path/to/your/.env" }
  }
}
```

The `.env` file defaults to the script's own directory. Override with `GROK_SEARCH_ENV_FILE`. See [`.env.example`](.env.example) for all available variables.

## Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `GROK_API_URL` | Yes | — | OpenAI-compatible Grok endpoint (must include `/v1`) |
| `GROK_API_KEY` | Yes | — | Grok API key |
| `GROK_MODEL` | No | `grok-4.3-console` | Default model (also overridable via `~/.config/grok-search/config.json`) |
| `TAVILY_API_KEYS` | No | — | Comma-separated Tavily keys; also accepts single `TAVILY_API_KEY` |
| `TAVILY_API_URL` | No | `https://api.tavily.com` | Tavily endpoint |
| `FIRECRAWL_API_KEYS` | No | — | Comma-separated Firecrawl keys; fallback: `FIRECRAWL_SCREENSHOT_API_KEYS` |
| `FIRECRAWL_API_URL` | No | `https://api.firecrawl.dev/v2` | Firecrawl endpoint |
| `GROK_DEBUG` | No | `false` | Enable debug logging |
| `GROK_LOG_LEVEL` / `GROK_LOG_DIR` | No | `INFO` / `logs` | Log level / log directory |
| `GROK_RETRY_MAX_ATTEMPTS` / `GROK_RETRY_MULTIPLIER` / `GROK_RETRY_MAX_WAIT` | No | `3` / `1` / `10` | Retry policy |

> Firecrawl key resolution order: `FIRECRAWL_API_KEYS` → `FIRECRAWL_API_KEY` → `FIRECRAWL_SCREENSHOT_API_KEYS` → `FIRECRAWL_SCREENSHOT_API_KEY`.

## Force-routing all web traffic here

After installation, tell Claude: *"Call toggle_builtin_tools with action=on"*. This writes `WebSearch` and `WebFetch` into the current project's `.claude/settings.json` deny list, so all web requests go through this MCP exclusively. Run with `action=off` to restore Claude Code's built-in tools.

## Compared to alternatives

| | cyxj-groksearch | Tavily Official MCP | Firecrawl Official MCP | Brave Search MCP | Exa MCP | Claude Code built-in WebSearch |
|---|---|---|---|---|---|---|
| AI search (Grok) | Yes | No | No | No | No | Yes (via Claude) |
| Full-text fetch | Yes (Tavily + Firecrawl fallback) | Partial | Yes | No | Yes | Snippet only |
| Site map | Yes | No | Yes | No | No | No |
| JS screenshot | Yes (Firecrawl) | No | Yes | No | No | No |
| Multi-key failover | Yes (all APIs) | No | No | No | No | N/A |
| Force-routing control | Yes (`toggle_builtin_tools`) | No | No | No | No | N/A |
| Session source cache | Yes (`get_sources`) | No | No | No | No | No |
| Relay endpoint support | Yes (any OpenAI-compatible URL) | No | No | No | No | No |

## FAQ

**Can I use this without Tavily or Firecrawl?**
Yes. Only `GROK_API_URL` and `GROK_API_KEY` are required. Without Tavily, `web_fetch` and `web_map` return a configuration notice. Without Firecrawl, `web_screenshot` returns a configuration notice, and Firecrawl fallback in `web_fetch` is skipped.

**How is this different from Claude Code's built-in WebSearch, and how do I force traffic here?**
The built-in `WebSearch` is Claude-managed and cannot be redirected to a custom endpoint. This MCP routes to your own Grok endpoint and adds multi-source aggregation, full-text fetch, and key failover. To force all web traffic here, call `toggle_builtin_tools` with `action="on"`. To restore built-ins, use `action="off"`.

**Which Grok endpoints are supported?**
Any OpenAI-compatible endpoint that exposes `/v1/chat/completions` and `/v1/models`. This includes the official `api.x.ai`, self-hosted mirrors, and commercial relay services. Set `GROK_API_URL` to the base URL including `/v1`.

**How does multi-key failover work?**
Each API (Grok, Tavily, Firecrawl) accepts a comma-separated list of keys. The server rotates through them round-robin. When a key fails (rate limit or network error), it enters a 30-minute cooldown and is skipped. Check current cooldown state with `get_config_info`.

**How do I debug connection issues?**
Call `get_config_info`. It runs a connectivity check to `/models`, sends a 1-token probe to verify the default model is available, and reports cooldown status for all key pools.

## Requirements

- Python 3.10+
- `fastmcp >= 2.3.0`, `mcp[cli] >= 1.21.2`, `httpx[socks] >= 0.28.0`, `tenacity >= 8.0.0`
- `uv` for installation and local development

## Development

```bash
cd GrokSearch
uv sync
uv run --extra dev pytest -v        # run tests
uv run --directory . grok-search    # start local stdio server
```

## License

[MIT License](LICENSE) — a de-branded rewrite of [GuDaStudio/GrokSearch](https://github.com/GuDaStudio/GrokSearch).
