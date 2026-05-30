#!/usr/bin/env bash
# grok-search 启动 shim：加载一个 .env、把所有 TAVILY_API_KEY* 变量聚合成
# TAVILY_API_KEYS 多 key 轮询，再用 uv run 跑本仓库源码启动 MCP 服务。
#
# .env 路径默认取本脚本同目录下的 .env；可用环境变量 GROK_SEARCH_ENV_FILE 覆盖
# （例如在 Claude Code 的 MCP 注册 env 里指向你统一的密钥文件）。

set -euo pipefail

# 仓库目录 = 本脚本所在目录（自定位，不写死绝对路径，便于他人使用/移动仓库）
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# 1. 加载 .env（路径可被 GROK_SEARCH_ENV_FILE 覆盖）
ENV_FILE="${GROK_SEARCH_ENV_FILE:-$SCRIPT_DIR/.env}"
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

# 3. 确保能找到 uv/uvx
export PATH="$HOME/.local/bin:$PATH"

# 4. 启动 grok-search（从本仓库源码的 .venv）
#    用 uv run --directory 而不是 uvx：直接用项目 .venv 跑，源码改了立即生效
#    （uvx 在 --from <local-path> 场景下 --reinstall 不可靠，改了源码常命中老 archive）。
exec uv run --directory "$SCRIPT_DIR" grok-search "$@"
