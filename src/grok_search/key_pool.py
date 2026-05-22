"""Tavily API Key 轮询池：进程内 round-robin + 失败 cooldown，线程安全。

行为：
- pick_tavily_key(keys): 从未在 cooldown 中的 key 里按 round-robin 返回下一个
- mark_key_failed(key): 把这个 key 暂时移出池（默认 30 分钟），让其他 key 接管
- cooldown_status(keys): 暴露当前池健康度（用于 get_config_info 显示）
"""

import itertools
import os
import sys
import threading
import time

_lock = threading.Lock()
_state: dict = {"keys": None, "cycle": None}
_cooldown: dict[str, float] = {}  # key -> unix ts when allowed again
DEFAULT_COOLDOWN_SECONDS = 1800  # 30 分钟


def _debug_enabled() -> bool:
    return os.getenv("GROK_DEBUG", "false").lower() in ("true", "1", "yes")


def mask_tail(key: str | None, n: int = 4) -> str:
    if not key:
        return "<none>"
    return key[-n:] if len(key) >= n else "***"


def _active_keys(keys: list[str]) -> list[str]:
    now = time.time()
    return [k for k in keys if _cooldown.get(k, 0) <= now]


def pick_tavily_key(keys: list[str]) -> str | None:
    """从 keys 中按 round-robin 返回下一个**未在 cooldown 中**的 key。
    所有 key 都在 cooldown 时返回 None。"""
    if not keys:
        return None
    with _lock:
        active = _active_keys(keys)
        if not active:
            picked_none = True
            picked = None
        else:
            picked_none = False
            if _state["keys"] != active:
                _state["keys"] = list(active)
                _state["cycle"] = itertools.cycle(_state["keys"])
            picked = next(_state["cycle"])
            idx = _state["keys"].index(picked) + 1
            total_active = len(_state["keys"])
        total_all = len(keys)

    if _debug_enabled():
        if picked_none:
            print(
                f"[grok-search] all {total_all} tavily keys in cooldown",
                file=sys.stderr,
                flush=True,
            )
        else:
            suffix = f" (active {total_active}/{total_all})" if total_active < total_all else ""
            print(
                f"[grok-search] tavily key #{idx}/{total_active} picked (...{mask_tail(picked)}){suffix}",
                file=sys.stderr,
                flush=True,
            )
    return picked


def pick_failover_key(keys: list[str], label: str = "key") -> str | None:
    """Failover 选 key：按列表顺序返回**第一个**未在 cooldown 中的 key。
    与 round-robin 不同：主 key 健康时始终用主 key，挂了才退到下一个。
    所有 key 都在 cooldown 时返回 None。共享 _cooldown，可与 pick_tavily_key 混用。
    label 仅用于 debug 日志区分（如 "firecrawl-screenshot"）。"""
    if not keys:
        return None
    now = time.time()
    picked = None
    idx = -1
    for i, k in enumerate(keys):
        if _cooldown.get(k, 0) <= now:
            picked = k
            idx = i
            break
    if _debug_enabled():
        if picked is None:
            print(
                f"[grok-search] all {len(keys)} {label}s in cooldown",
                file=sys.stderr,
                flush=True,
            )
        else:
            print(
                f"[grok-search] {label} #{idx + 1}/{len(keys)} picked (...{mask_tail(picked)}) [failover]",
                file=sys.stderr,
                flush=True,
            )
    return picked


def mark_key_failed(key: str | None, cooldown_seconds: int = DEFAULT_COOLDOWN_SECONDS) -> None:
    """把某个 key 标记为失败，临时移出池 cooldown_seconds 秒。
    典型场景：Tavily 返回 401/403/429（额度耗尽 / 被吊销 / 速率限制）。"""
    if not key:
        return
    with _lock:
        _cooldown[key] = time.time() + cooldown_seconds
        # 强制下次重建 cycle，把该 key 排除
        _state["keys"] = None
        _state["cycle"] = None
    if _debug_enabled():
        print(
            f"[grok-search] tavily key (...{mask_tail(key)}) marked failed, cooldown {cooldown_seconds}s",
            file=sys.stderr,
            flush=True,
        )


def cooldown_status(keys: list[str]) -> dict:
    """返回当前 key pool 状态，给 get_config_info 用。"""
    now = time.time()
    cooling = []
    for k in keys:
        until = _cooldown.get(k, 0)
        if until > now:
            cooling.append({"tail4": mask_tail(k), "remaining_sec": int(until - now)})
    return {
        "total": len(keys),
        "active": len(keys) - len(cooling),
        "cooling_down": cooling,
    }
