"""Grok 搜索的「本机 CLI 后端」：把 xAI 的 Grok Build 命令行（`grok -p`）当作搜索引擎调用。

为什么要有它：中转站的 Grok API 经常没号 / 没模型，而本机 `grok` 已用 grok.com 账号登录，
自带联网 web_search / web_fetch 工具，不需要任何 API key。

工作方式：
    grok -p <query> --output-format streaming-json ...
逐行解析 NDJSON：
    {"type":"text","data":"..."}                       → 拼成最终答案
    {"type":"tool_call_update", "rawOutput":{...sources:[{url}]}} → 搜索命中的原始 URL（信源）
    {"type":"end", ...}                                → 结束标记 / 用量

防递归：grok CLI 会把 Claude Code 里注册的 MCP（包括本服务）一并挂载。子进程带上
GROK_SEARCH_NESTED=1，嵌套启动的 grok-search 实例看到它就不会再走 CLI 后端。
"""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any

from .config import NESTED_ENV, config
from .grok_client import get_local_time_info
from .logger import log_info

CLI_PROVIDER = "grok-cli"

# 给 grok CLI 的系统提示：只做搜索引擎，不要旁白，答案末尾统一列来源，
# 方便 sources.split_answer_and_sources 把来源块剥离出来。
CLI_SYSTEM_PROMPT = """You are a web research engine sitting behind an API. Your only job is to answer the user's query with fresh, verifiable information from the web.

Mandatory workflow:
1. ALWAYS call web_search first — 1 to 3 focused searches (English plus the query's language when useful). Never answer from memory alone.
2. Use web_fetch only when a precise detail (date, number, version, quote) must be confirmed on the primary page.
3. Prefer authoritative / primary sources: official docs and announcements, papers, reputable media. Note the publication date of time-sensitive facts.
4. Be fast: you are one hop inside another agent's tool call. Stop researching once the question is answered with sources.

Output rules:
- Do NOT narrate your process (no "let me search", no "先查一下", no progress updates). Output only the final answer.
- Answer in the same language as the query. Lead with the direct answer, then the supporting details, in clean Markdown.
- Be compact: usually 150-400 words. Go longer only when the query explicitly asks for depth or a list.
- Every factual claim must be traceable to a source. Finish with a section titled exactly "Sources:" listing one markdown link per line: - [title](url)
- If the web has no reliable answer, say so explicitly instead of guessing."""

# grok CLI 会一边搜一边吐「先查…接着核对…」这类过程旁白。工具调用之前的短文本段按旁白处理丢掉，
# 长于这个阈值的段落视为正文的一部分保留（防止模型「先答一半再补一次搜索」时把答案吞掉）。
NARRATION_MAX_CHARS = 200


class GrokCliError(RuntimeError):
    """grok CLI 调用失败（找不到二进制 / 非零退出 / 超时 / 无输出）。"""


def build_cli_args(cli_path: str, prompt: str, workdir: str, model: str = "", effort: str = "") -> list[str]:
    """拼 grok 命令行参数。单独抽出来方便测试。"""
    args = [
        cli_path,
        "-p", prompt,
        "--verbatim",                       # 提示词原样发送，不做 / 命令或 skill 展开
        "--permission-mode", "dontAsk",     # 无人值守：需要授权的工具直接拒绝，不会卡住
        "--no-subagents",
        "--no-plan",
        "--tools", "web_search,web_fetch",  # 只留联网工具，不给文件 / 终端权限
        "--system-prompt-override", CLI_SYSTEM_PROMPT,
        "--output-format", "streaming-json",
        "--cwd", workdir,
    ]
    if model:
        args += ["--model", model]
    if effort:
        args += ["--reasoning-effort", effort]
    return args


def _collect_urls(node: Any, out: list[str]) -> None:
    """递归收集工具结果里的 url（sources:[{url}] 或任意层级的 "url" 字段）。"""
    if isinstance(node, dict):
        url = node.get("url")
        if isinstance(url, str) and url.startswith(("http://", "https://")):
            out.append(url.strip())
        for key, value in node.items():
            if key == "url":
                continue
            _collect_urls(value, out)
    elif isinstance(node, list):
        for item in node:
            _collect_urls(item, out)


def parse_stream_output(stdout: str) -> tuple[str, list[dict], dict]:
    """解析 grok --output-format streaming-json 的 NDJSON。

    返回 (答案文本, 信源列表, 元信息)。信源按首次出现顺序去重。
    非 JSON 行忽略；如果一行 JSON 都没有，把整段 stdout 当作纯文本答案返回。
    """
    segments: list[str] = []      # 以 tool_call 为界切开的文本段
    current: list[str] = []
    urls: list[str] = []
    meta: dict = {"parsed_lines": 0, "tool_calls": 0}

    for raw_line in stdout.splitlines():
        line = raw_line.strip()
        if not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        meta["parsed_lines"] += 1
        etype = event.get("type")
        if etype == "text":
            data = event.get("data")
            if isinstance(data, str):
                current.append(data)
        elif etype == "tool_call":
            meta["tool_calls"] += 1
            if current:
                segments.append("".join(current))
                current = []
        elif etype == "tool_call_update":
            _collect_urls(event.get("rawOutput"), urls)
        elif etype == "end":
            meta["stop_reason"] = event.get("stopReason")
            usage = event.get("usage")
            if isinstance(usage, dict):
                meta["usage"] = usage

    if meta["parsed_lines"] == 0:
        return stdout.strip(), [], meta
    segments.append("".join(current))

    # 最后一段一定是正文；前面的段只有够长才保留（短的是「正在查…」旁白）
    kept = [seg.strip() for seg in segments[:-1] if len(seg.strip()) >= NARRATION_MAX_CHARS]
    kept.append(segments[-1].strip())
    answer = "\n\n".join(seg for seg in kept if seg)
    meta["dropped_narration_segments"] = len(segments) - len(kept)

    seen: set[str] = set()
    sources: list[dict] = []
    for url in urls:
        if url in seen:
            continue
        seen.add(url)
        sources.append({"url": url, "provider": CLI_PROVIDER})
    return answer, sources, meta


def _build_prompt(query: str, platform: str) -> str:
    prompt = get_local_time_info() + "\n" + query
    if platform:
        prompt += "\n\nFocus the web search on this platform: " + platform
    return prompt


async def grok_cli_search(query: str, platform: str = "", model: str = "", ctx=None) -> tuple[str, list[dict]]:
    """用本机 grok CLI 搜索。返回 (答案, 信源列表)；失败抛 GrokCliError。"""
    cli_path = config.grok_cli_path
    if not cli_path:
        raise GrokCliError("未找到 grok 命令：请安装 Grok Build CLI，或用 GROK_CLI_PATH 指定路径")
    if config.grok_cli_nested:
        raise GrokCliError("检测到嵌套调用（GROK_SEARCH_NESTED），拒绝再次启动 grok CLI")

    workdir = config.grok_cli_workdir
    args = build_cli_args(
        cli_path,
        _build_prompt(query, platform),
        str(workdir),
        model or config.grok_cli_model,
        config.grok_cli_effort,
    )
    env = {**os.environ, NESTED_ENV: "1"}
    timeout = config.grok_cli_timeout
    await log_info(ctx, f"grok-cli argv: {args[:1] + args[2:]}", config.debug_enabled)

    proc = await asyncio.create_subprocess_exec(
        *args,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
        cwd=str(workdir),
    )
    try:
        stdout_bytes, stderr_bytes = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        try:
            await asyncio.wait_for(proc.wait(), timeout=5)
        except asyncio.TimeoutError:
            pass
        raise GrokCliError(f"grok CLI 超过 {timeout:.0f}s 未返回，已终止")

    stdout = stdout_bytes.decode("utf-8", errors="replace")
    stderr = stderr_bytes.decode("utf-8", errors="replace").strip()
    if proc.returncode != 0:
        raise GrokCliError(f"grok CLI 退出码 {proc.returncode}: {stderr[-500:] or stdout[-500:]}")

    answer, sources, meta = parse_stream_output(stdout)
    await log_info(
        ctx,
        f"grok-cli done: stop={meta.get('stop_reason')} chars={len(answer)} sources={len(sources)} usage={meta.get('usage')}",
        config.debug_enabled,
    )
    if not answer:
        raise GrokCliError(f"grok CLI 没有返回文本（stderr: {stderr[-300:]}）")
    return answer, sources
