import asyncio
import json
import sys
from pathlib import Path

import pytest

src_dir = Path(__file__).parent.parent / "src"
if str(src_dir) not in sys.path:
    sys.path.insert(0, str(src_dir))

from grok_search import grok_cli_client as cli
from grok_search.config import NESTED_ENV


def _ndjson(*events) -> str:
    return "\n".join(json.dumps(e, ensure_ascii=False) for e in events) + "\n"


def test_parse_stream_concatenates_text_and_collects_sources():
    stdout = _ndjson(
        {"type": "thought", "data": "thinking"},
        {"type": "text", "data": "Searching..."},  # 工具调用前的短旁白，应被剥掉
        {"type": "tool_call", "toolCallId": "a", "kind": "search", "status": "in_progress"},
        {"type": "tool_call_update", "toolCallId": "a", "status": "completed",
         "rawOutput": {"action": {"type": "search", "query": "q", "sources": [
             {"type": "url", "url": "https://blog.rust-lang.org/2025/09/18/Rust-1.90.0/"},
             {"type": "url", "url": "https://github.com/rust-lang/rust/releases/tag/1.90.0"},
             {"type": "url", "url": "https://blog.rust-lang.org/2025/09/18/Rust-1.90.0/"},
         ]}}},
        {"type": "text", "data": "Rust 1.90 "},
        {"type": "text", "data": "released 2025-09-18."},
        {"type": "end", "stopReason": "end_turn", "usage": {"input_tokens": 10}},
    )
    text, sources, meta = cli.parse_stream_output(stdout)
    assert text == "Rust 1.90 released 2025-09-18."
    assert meta["dropped_narration_segments"] == 1
    assert [s["url"] for s in sources] == [
        "https://blog.rust-lang.org/2025/09/18/Rust-1.90.0/",
        "https://github.com/rust-lang/rust/releases/tag/1.90.0",
    ]
    assert all(s["provider"] == "grok-cli" for s in sources)
    assert meta["stop_reason"] == "end_turn"
    assert meta["usage"] == {"input_tokens": 10}


def test_parse_stream_ignores_garbage_lines_and_non_http_urls():
    stdout = "warning: something\n" + _ndjson(
        {"type": "text", "data": "ok"},
        {"type": "tool_call_update", "rawOutput": {"url": "file:///etc/passwd", "nested": {"url": "https://a.example"}}},
    ) + "not json {\n"
    text, sources, _ = cli.parse_stream_output(stdout)
    assert text == "ok"
    assert [s["url"] for s in sources] == ["https://a.example"]


def test_parse_stream_falls_back_to_raw_text_when_no_json():
    text, sources, meta = cli.parse_stream_output("  plain answer  \n")
    assert text == "plain answer"
    assert sources == []
    assert meta["parsed_lines"] == 0


def test_build_cli_args_restricts_tools_and_passes_model_effort():
    args = cli.build_cli_args("/x/grok", "hello", "/tmp/wd", model="grok-4.5", effort="medium")
    assert args[0] == "/x/grok"
    assert args[1:3] == ["-p", "hello"]
    assert "--verbatim" in args
    assert args[args.index("--permission-mode") + 1] == "dontAsk"
    assert args[args.index("--tools") + 1] == "web_search,web_fetch"
    assert args[args.index("--output-format") + 1] == "streaming-json"
    assert args[args.index("--cwd") + 1] == "/tmp/wd"
    assert args[args.index("--model") + 1] == "grok-4.5"
    assert args[args.index("--reasoning-effort") + 1] == "medium"
    assert "--no-subagents" in args and "--no-plan" in args


def test_build_cli_args_omits_model_and_effort_when_empty():
    args = cli.build_cli_args("/x/grok", "hello", "/tmp/wd")
    assert "--model" not in args
    assert "--reasoning-effort" not in args


@pytest.fixture
def fake_grok(tmp_path, monkeypatch):
    """一个假的 grok 可执行文件：把收到的参数和环境写到文件，输出固定 NDJSON。"""
    record = tmp_path / "record.json"
    script = tmp_path / "grok"
    script.write_text(f"""#!/usr/bin/env python3
import json, os, sys
json.dump({{"argv": sys.argv[1:], "nested": os.environ.get("{NESTED_ENV}"), "cwd": os.getcwd()}}, open({str(record)!r}, "w"))
print(json.dumps({{"type": "text", "data": "answer from cli"}}))
print(json.dumps({{"type": "tool_call_update", "rawOutput": {{"action": {{"sources": [{{"url": "https://example.com/a"}}]}}}}}}))
print(json.dumps({{"type": "end", "stopReason": "end_turn"}}))
""")
    script.chmod(0o755)
    monkeypatch.setenv("GROK_CLI_PATH", str(script))
    monkeypatch.delenv(NESTED_ENV, raising=False)
    monkeypatch.delenv("GROK_BACKEND", raising=False)
    monkeypatch.setenv("GROK_CLI_MODEL", "grok-4.6")
    monkeypatch.setenv("GROK_CLI_EFFORT", "")
    monkeypatch.setattr(cli.config, "_config_file", tmp_path / "config.json")
    return record


async def test_grok_cli_search_runs_subprocess_with_nested_guard(fake_grok):
    answer, sources = await cli.grok_cli_search("what is x", platform="GitHub")
    assert answer == "answer from cli"
    assert sources == [{"url": "https://example.com/a", "provider": "grok-cli"}]
    record = json.loads(fake_grok.read_text())
    assert record["nested"] == "1"
    prompt = record["argv"][record["argv"].index("-p") + 1]
    assert "what is x" in prompt and "GitHub" in prompt and "[Current Time Context]" in prompt
    assert record["argv"][record["argv"].index("--model") + 1] == "grok-4.6"
    assert record["cwd"].endswith("cli-workdir")


async def test_grok_cli_search_explicit_model_overrides_env(fake_grok):
    await cli.grok_cli_search("q", model="grok-4.5")
    record = json.loads(fake_grok.read_text())
    assert record["argv"][record["argv"].index("--model") + 1] == "grok-4.5"


async def test_grok_cli_search_refuses_when_nested(fake_grok, monkeypatch):
    monkeypatch.setenv(NESTED_ENV, "1")
    with pytest.raises(cli.GrokCliError, match="嵌套"):
        await cli.grok_cli_search("q")


async def test_grok_cli_search_raises_on_nonzero_exit(tmp_path, monkeypatch):
    script = tmp_path / "grok"
    script.write_text("#!/bin/sh\necho boom >&2\nexit 3\n")
    script.chmod(0o755)
    monkeypatch.setenv("GROK_CLI_PATH", str(script))
    monkeypatch.delenv(NESTED_ENV, raising=False)
    monkeypatch.setattr(cli.config, "_config_file", tmp_path / "config.json")
    with pytest.raises(cli.GrokCliError, match="退出码 3"):
        await cli.grok_cli_search("q")


async def test_grok_cli_search_times_out(tmp_path, monkeypatch):
    script = tmp_path / "grok"
    script.write_text("#!/bin/sh\nsleep 30\n")
    script.chmod(0o755)
    monkeypatch.setenv("GROK_CLI_PATH", str(script))
    monkeypatch.delenv(NESTED_ENV, raising=False)
    monkeypatch.setattr(cli.config, "_config_file", tmp_path / "config.json")
    monkeypatch.setattr(type(cli.config), "grok_cli_timeout", property(lambda self: 0.5))
    with pytest.raises(cli.GrokCliError, match="未返回"):
        await cli.grok_cli_search("q")


def test_parse_stream_drops_short_narration_before_tool_calls():
    stdout = _ndjson(
        {"type": "text", "data": "先查官方 changelog，"},
        {"type": "text", "data": "确认时间。"},
        {"type": "tool_call", "toolCallId": "a", "kind": "search"},
        {"type": "tool_call_update", "toolCallId": "a", "rawOutput": {"action": {"sources": [{"url": "https://a.example"}]}}},
        {"type": "text", "data": "接着核对功能清单。"},
        {"type": "tool_call", "toolCallId": "b", "kind": "search"},
        {"type": "tool_call_update", "toolCallId": "b", "rawOutput": {}},
        {"type": "text", "data": "**最终答案**：2.1.0 于 2026-01-07 发布。"},
        {"type": "end", "stopReason": "end_turn"},
    )
    text, sources, meta = cli.parse_stream_output(stdout)
    assert text == "**最终答案**：2.1.0 于 2026-01-07 发布。"
    assert meta["tool_calls"] == 2
    assert meta["dropped_narration_segments"] == 2
    assert [s["url"] for s in sources] == ["https://a.example"]


def test_parse_stream_keeps_long_segment_before_a_verification_search():
    long_part = "这是答案的第一部分。" * 30  # 远超 200 字
    stdout = _ndjson(
        {"type": "text", "data": long_part},
        {"type": "tool_call", "toolCallId": "a", "kind": "search"},
        {"type": "text", "data": "补充：核实无误。"},
        {"type": "end", "stopReason": "end_turn"},
    )
    text, _, meta = cli.parse_stream_output(stdout)
    assert text == long_part + "\n\n补充：核实无误。"
    assert meta["dropped_narration_segments"] == 0


def test_parse_stream_without_tool_calls_keeps_everything():
    stdout = _ndjson({"type": "text", "data": "直接回答。"}, {"type": "end"})
    text, _, meta = cli.parse_stream_output(stdout)
    assert text == "直接回答。"
    assert meta["tool_calls"] == 0
