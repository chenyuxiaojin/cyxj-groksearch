#!/usr/bin/env bash
# grok-search 启动 shim：从密钥存储 .env 加载所有 Tavily key，
# 拼成 TAVILY_API_KEYS 后用本地源码的 GrokSearch 启动 MCP 服务。

set -euo pipefail

# 1. 加载密钥存储 .env（中文路径，bash 直接 exec 没问题）
ENV_FILE="/Users/chenhuajin/项目/自己的应用/密钥存储/.env"
if [[ -f "$ENV_FILE" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +a
fi

# 2. 扫描所有 TAVILY_API_KEY* 变量（排除复数 TAVILY_API_KEYS 本身）
#    无论命名是 _BACKUP / _2 / _3 / _DEV，都自动加入轮询池。
keys=()
while IFS= read -r varname; do
  val="${!varname:-}"
  [[ -n "$val" ]] && keys+=("$val")
done < <(compgen -A variable | grep -E '^TAVILY_API_KEY' | grep -v '^TAVILY_API_KEYS$' | sort)

if [[ ${#keys[@]} -gt 0 ]]; then
  old_ifs="$IFS"
  IFS=','
  export TAVILY_API_KEYS="${keys[*]}"
  IFS="$old_ifs"
fi

# 3. 确保能找到 uvx
export PATH="$HOME/.local/bin:$PATH"

# 4. 启动 grok-search（从本地源码 venv）
#    用 uv run 而不是 uvx：
#    - uvx 会把源码打包成 archive 缓存到 ~/.cache/uv/archive-v0/<hash>/
#      archive hash 是基于源码 tarball 算的，源码改了 hash 也跟着变，
#      但 uvx 的 --reinstall 在 --from <local-path> 场景下不可靠
#      （实测改了 server.py 还是命中老 archive，新 tool 不注册）
#    - uv run --directory 直接用项目 .venv 跑，源码改了立即生效
exec uv run --directory "/Users/chenhuajin/项目/自己的应用/GrokSearch" grok-search "$@"
