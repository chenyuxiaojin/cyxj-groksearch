"""进程级共享 httpx.AsyncClient：连接池 + keep-alive 复用。

之前每次工具调用都临时新建 AsyncClient，每个请求都要重做 TCP + TLS 握手。
现在按事件循环缓存客户端，同一 loop 内所有请求（Grok / Tavily / Firecrawl /
配置探针）复用同一个连接池，对同一主机的连续请求可以直接走 keep-alive 连接。

各调用方通过 per-request 的 timeout 参数覆盖默认超时，不再需要各自建客户端。
"""

import asyncio

import httpx

_LIMITS = httpx.Limits(
    max_connections=64,
    max_keepalive_connections=20,
    keepalive_expiry=30.0,
)
_DEFAULT_TIMEOUT = httpx.Timeout(connect=6.0, read=90.0, write=10.0, pool=None)

_clients: dict[int, httpx.AsyncClient] = {}


def get_client() -> httpx.AsyncClient:
    """返回绑定到当前事件循环的共享客户端（懒创建）。

    按 loop 区分是为了兼容测试等多 loop 场景——httpx 客户端不能跨 loop 使用。
    stdio 服务器整个生命周期只有一个 loop，所以实际只会创建一个客户端。
    """
    loop_id = id(asyncio.get_running_loop())
    client = _clients.get(loop_id)
    if client is None or client.is_closed:
        client = httpx.AsyncClient(
            limits=_LIMITS,
            timeout=_DEFAULT_TIMEOUT,
            follow_redirects=True,
        )
        _clients[loop_id] = client
    return client
