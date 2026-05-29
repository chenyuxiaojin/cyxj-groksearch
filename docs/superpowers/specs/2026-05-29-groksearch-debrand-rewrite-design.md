# GrokSearch 去广告重写 — 设计文档

- 日期：2026-05-29
- 状态：已确认，待生成实现计划
- 适用仓库：`~/项目/自己的应用/GrokSearch`

## 1. 背景与目标

本 MCP fork 自 `GuDaStudio/GrokSearch`，原作者把自己的商业中转服务 `code.guda.studio` 深度焊死在代码里（一键派生 URL、工具 `author` 标签、README 软广、Star History 引流）。

目标：**彻底重写内部结构**，去掉所有 GuDa 痕迹，改成完全属于使用者自己、且适配其真实环境的版本。

### 硬约束（行为兼容）

`grok-search` 这个 MCP 名字、保留的 8 个工具的**参数签名与返回结构**必须 100% 不变。使用者的 `CLAUDE.md` 依赖 `web_search` 的 `extra_sources`、`get_config_info` 的 `connection_test` / `default_model_health` / `tavily_key_cooldown`、`switch_model` 等行为，重写后这些对外契约一字不改。重写的是内部实现与文件结构，不是外部接口。

## 2. 决策记录

| # | 决策 | 选择 |
|---|------|------|
| 1 | 重构程度 | 彻底重写（干净结构） |
| 2 | MCP 名称 | 保留 `grok-search` |
| 3 | GuDa 一键派生逻辑 | 完全删除 |
| 4 | git 历史 | 断开 `GuDaStudio` origin，保留本地提交历史 |
| 5 | 6 个 `plan_*` 规划工具 | 删除，并去掉 `web_search` 里的强制规划提示 |
| 6 | 坏掉的 MCP 注册 | 修复（改 `~/.claude.json` 路径，改前再确认） |
| 7 | Firecrawl 通道 | 合并成单通道，一把 key 供三用 |
| 8 | 默认模型 | `grok-4.20-beta` → `grok-4.3-console` |

## 3. 使用者真实环境（已核对，仅看变量名）

`~/项目/自己的应用/密钥存储/.env` 中相关变量：

```
GROK_API_URL                     # OpenAI 兼容 Grok 中转站
GROK_API_KEY
TAVILY_API_KEYS                  # 多 key，逗号分隔
FIRECRAWL_SCREENSHOT_API_KEYS    # 当前仅截图通道使用；合并后将被三功能复用
```

- 未设 `TAVILY_API_URL` / `FIRECRAWL_API_URL` → 回落官方端点。
- 未设通用 `FIRECRAWL_API_KEY` → 重写前 `web_fetch` 降级与 `extra_sources` 的 Firecrawl 分支是 inert 的；合并通道后由回落机制自动接上。

MCP 当前实际状态：注册命令指向不存在的 `自己的应用/grok-search-launcher.sh`（真实文件在 `自己的应用/GrokSearch/grok-search-launcher.sh`），`claude mcp list` 显示 `✗ Failed to connect`。

## 4. 最终工具清单（8 个，行为原样保留）

| 工具 | 签名（保持不变） | 返回 |
|------|------|------|
| `web_search` | `query, platform="", model="", extra_sources=0` | dict `{session_id, content, sources_count}` |
| `get_sources` | `session_id` | dict `{session_id, sources, sources_count}`（未命中带 `error`） |
| `web_fetch` | `url` | str（Tavily Extract → 失败降级 Firecrawl Scrape；都没配返回中文配置错误） |
| `web_screenshot` | `url, full_page=False` | str（JSON：成功含 `screenshot_url` 等元数据；失败为中文错误串） |
| `web_map` | `url, instructions="", max_depth=1, max_breadth=20, limit=50, timeout=150` | str（JSON：`base_url/results/response_time`） |
| `get_config_info` | 无参 | str（JSON：配置 dump + `connection_test` + `default_model_health` + `tavily_key_cooldown`） |
| `switch_model` | `model` | str（JSON：切换结果，持久化到 `~/.config/grok-search/config.json`） |
| `toggle_builtin_tools` | `action="status"` | str（JSON：改项目级 `.claude/settings.json` 的 `permissions.deny`） |

**删除**：`plan_intent` / `plan_complexity` / `plan_sub_query` / `plan_search_term` / `plan_tool_mapping` / `plan_execution`，及 `web_search` 描述里「Before using this tool, please use the plan_intent tool...」整句。

`web_search` 内部行为保留要点：
- 时间关键词检测 → 注入本地时间上下文（`get_local_time_info` + `_needs_time_context`）。
- `model` 传入时先查 `/models` 缓存校验，无效返回 `无效模型: ...`。
- `extra_sources>0` 时按 Firecrawl/Tavily 配额并行补信源，与 Grok 答案并行 `gather`，失败各自吞掉返回空。
- 信源缓存在服务端 `SourcesCache`（LRU，max 256），`get_sources` 按 `session_id` 取。

## 5. 去 GuDa（品牌清理）

- `config.py`：删除 `GUDA_API_KEY` / `GUDA_BASE_URL` / `_DEFAULT_GUDA_BASE_URL` / `guda_*` 全部派生。`grok_api_url`、`grok_api_key` 改为「未配置即报错」，报错文案改成中性自有提示，不再印 GuDa 的 GitHub 安装命令。`get_config_info()` 的 dict 去掉 `GUDA_BASE_URL`/`GUDA_API_KEY` 两个键。
- 8 处 `meta={"version": "...", "author": "guda.studio"}` → 去掉 `author`，仅留 `version`。
- 默认模型常量：`grok-4.20-beta` → `grok-4.3-console`（env `GROK_MODEL` 与 `config.json` 仍可覆盖，优先级不变）。
- README.md 重写：删「GuDa 用户（推荐）」段、Star History 图、所有 `GuDaStudio/GrokSearch` 链接；改写为反映真实用法（launcher 从 `密钥存储/.env` 加载、OpenAI 兼容中转站、多 Tavily key 轮询、Firecrawl 单通道）。
- 删除 `docs/README_EN.md` 与 `images/`（`title.png`/`wgrok.png`/`wogrok.png` 为原作者营销截图）。
- `config.py` 里 `_SETUP_COMMAND` 删除或改为中性的本地启动说明。

## 6. Firecrawl 合并成单通道（核心适配）

config 新增统一的 Firecrawl key 读取，优先级：

```
FIRECRAWL_API_KEYS（复数，逗号分隔）
  → FIRECRAWL_API_KEY（单数）
  → FIRECRAWL_SCREENSHOT_API_KEYS（向后兼容现有 .env，复数）
  → FIRECRAWL_SCREENSHOT_API_KEY（向后兼容，单数）
```

- 同一份 key 列表供三处使用：`web_fetch` 的 Firecrawl Scrape 降级、`extra_sources` 的 Firecrawl Search、`web_screenshot`。
- 三处统一走 `key_pool` 的多 key failover + 30 分钟 cooldown（现状只有截图有 failover；`web_fetch` 降级与 `extra_sources` 现用单数 key 无轮询，合并后一并获得）。
- `firecrawl_api_url` 统一一个（默认 `https://api.firecrawl.dev/v2`）；删除独立的 `FIRECRAWL_SCREENSHOT_API_URL` 概念（如需仍可由同一 `FIRECRAWL_API_URL` 覆盖）。
- **使用者无需改 .env**：现有 `FIRECRAWL_SCREENSHOT_API_KEYS` 经回落被三功能自动复用。

## 7. 删除的死代码

- `providers/` 整个包，含 `base.py`（`BaseSearchProvider` / `SearchResult` 抽象——仅一个 provider，且 `search()` 实际返回 str 与 `List[SearchResult]` 标注矛盾）。
- `grok.py` 的 `describe_url` / `rank_sources` / `fetch`（从未被调用）。
- `utils.py` 的 `format_extra_sources` / `format_search_results`（无引用）。
- 三个废 prompt：`fetch_prompt` / `url_describe_prompt` / `rank_sources_prompt`（仅服务于上面死方法）。仅保留 `search_prompt`。
- `planning.py` 整文件。

## 8. 新模块结构

把 1033 行、把工具定义与上游 HTTP 调用揉在一起的 `server.py` 按职责拆开：

```
src/grok_search/
  __init__.py          导出 mcp
  server.py            仅 8 个 @mcp.tool 薄封装 + get_config_info 组装 + main()
  config.py            Config 单例（去品牌、默认模型 grok-4.3-console、Firecrawl 单通道）
  logger.py            不变
  key_pool.py          不变（round-robin + failover + cooldown）
  sources.py           答案/信源拆分 + SourcesCache（保留逻辑；并入原 utils 的 extract_unique_urls）
  prompts.py           仅 search_prompt
  grok_client.py       Grok 流式 chat 客户端 + 重试 + 时间注入（原 providers/grok.py 去死方法）
  tavily_client.py     tavily_search / tavily_extract / tavily_map
  firecrawl_client.py  firecrawl_search / firecrawl_scrape / firecrawl_screenshot（统一 key 池）
```

删除：`planning.py`、`providers/`、`utils.py`（其唯一在用函数 `extract_unique_urls` 移入 `sources.py`，`search_prompt` 移入 `prompts.py`）。

模块边界原则：每个 `*_client.py` 只负责对应上游服务的 HTTP 细节，对外暴露纯函数；`server.py` 只做参数转译与编排，不内联 httpx 调用。

## 9. 修复 MCP 注册

重写并跑通后，将 `~/.claude.json` 中 `grok-search` 的 `command` 由 `/Users/chenhuajin/项目/自己的应用/grok-search-launcher.sh`（不存在）改为 `/Users/chenhuajin/项目/自己的应用/GrokSearch/grok-search-launcher.sh`（真实路径），再 `claude mcp list` 确认 `✓`。

**改 `~/.claude.json` 前单独向使用者确认。** launcher 本身逻辑不变（仍从 `密钥存储/.env` 加载、聚合 `TAVILY_API_KEY*` → `TAVILY_API_KEYS`、`uv run --directory` 启动）。

## 10. 验证

- 纯逻辑单测（最易回归的行为保留区）：
  - `sources.py`：函数调用式 / 标题块 / `<details>` 块 / 尾部链接块四种信源拆分；`merge_sources` 去重。
  - `key_pool.py`：round-robin、failover 顺序、cooldown 进出池、全冷却返回 None。
  - `config.py`：去派生后 Tavily/Firecrawl URL 回落官方端点；Firecrawl key 四级回落优先级；`grok_api_url`/`grok_api_key` 未配置抛错。
  - Firecrawl 单通道：三功能读到同一份 key。
- 冒烟测试：导入 server 后 `mcp` 恰好注册这 8 个工具、名字与签名不变。
- 启动验证：`uv run --directory ~/项目/自己的应用/GrokSearch grok-search` 能正常起 stdio。

## 11. git

- `git remote remove origin`（断开 `GuDaStudio/GrokSearch`），保留本地提交历史。
- 重写提交为本地新提交。
- **所有 git 写操作执行前向使用者确认。**

## 12. 不在范围内

- 不改 `密钥存储/.env`（除非使用者单独授权）。
- 不新增上游服务 / 新工具。
- 不改 launcher 的加载逻辑（仅可能修注册路径）。
- 不发布到任何远程仓库。
