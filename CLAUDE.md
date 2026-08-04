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

- 入口 `grok_search.server:main`;客户端分层 `grok_client` / `tavily_client` / `firecrawl_client`;`key_pool.py` 多 key 轮询 + cooldown,`sources.py` 信源缓存聚合
- **默认值即协议**(2026-08-04 起写进代码):`web_search` 的 `extra_sources` 默认 2(交叉验证),默认模型兜底 `grok-4.3-fast`。运行时实际模型以 `get_config_info` 为准(`~/.config/grok-search/config.json` 可被 `switch_model` 覆盖)
- 改服务端代码后,正在跑的 MCP 会话不会自动生效——需重启 MCP / 重开会话
