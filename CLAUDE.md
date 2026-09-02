# CLAUDE.md

联网搜索 MCP Server,注册名 **grok-search**:Grok(AI 搜索)+ Tavily(抓取/站点映射)+ Firecrawl(截图/降级)。`~/CLAUDE.md`「证据协议」反复用的就是它。**公开仓库** `chenhuajinchj/cyxj-groksearch`——提交前确认不带密钥或个人信息。

## 常用命令

```bash
uv sync                              # 装依赖
uv run --extra dev pytest -v         # 跑测试
uv run --directory . grok-search     # 本地 stdio 调试
./grok-search-launcher.sh            # 生产启动(聚合所有 TAVILY_API_KEY* 做多 key 轮询)
```

## 结构与坑

- 入口 `grok_search.server:main`;客户端分层 `grok_client`(OpenAI 兼容 API)/ `grok_cli_client`(本机 `grok` CLI 子进程)/ `tavily_client` / `firecrawl_client`;`key_pool.py` 多 key 轮询 + cooldown,`sources.py` 信源缓存聚合
- **默认值即协议**(2026-08-04 起写进代码):`web_search` 的 `extra_sources` 默认 5(交叉验证,2026-09-02 由 2 调高),默认模型兜底 `grok-4.3-fast`。运行时实际模型以 `get_config_info` 为准(`~/.config/grok-search/config.json` 可被 `switch_model` 覆盖)
- **Grok 后端选择**(2026-09-02 起):`config.resolve_grok_backend()` → `cli` / `api` / `none`。默认 `auto`:有 `grok` 命令走 CLI(零 API key,推理强度默认 medium,xhigh 一次两分钟),CLI 挂了回落 API。CLI 输出是 streaming-json,`parse_stream_output` 拼文本 + 收 URL + 剥旁白(工具调用前 <200 字的段)
- **防递归红线**:grok CLI 会挂载 Claude 的全部 MCP(含本服务),子进程带 `GROK_SEARCH_NESTED=1`,嵌套实例拒走 CLI。别删这个环境变量
- 改服务端代码后,正在跑的 MCP 会话不会自动生效——需重启 MCP / 重开会话
