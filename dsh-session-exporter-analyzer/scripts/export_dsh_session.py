#!/usr/bin/env python3
"""将 DSH 会话导出为 Markdown 文档。

只读访问 ~/.dsh/sessions/，从会话 JSONL 中提取用户与助手消息，
不修改原会话。会话 ID 接受 session-<uuid> 或裸 <uuid>。

DSH 的会话文件是 session.v3.jsonl.zstd，由多个 zstd 帧拼接而成（随会话增长追加），
因此必须按流式方式解压，一次 decompress 只能拿到第一帧。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

SESSION_FILE_GLOB = "session*.jsonl.zstd"
SESSION_FILE_RE = re.compile(r"^session(?:\.v(\d+))?\.jsonl\.zstd$")
UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
SYSTEM_REMINDER_RE = re.compile(r"<system-reminder>.*?</system-reminder>", re.S)
TOOL_OUTPUT_MAX = 8000


def session_file_rank(path: Path) -> tuple:
    """同一会话目录可能同时留有旧格式与迁移后的新格式文件，按版本号优先取新。"""
    match = SESSION_FILE_RE.match(path.name)
    version = int(match.group(1)) if match and match.group(1) else 0
    try:
        stat = path.stat()
        return (version, stat.st_mtime, stat.st_size)
    except OSError:
        return (version, 0.0, 0)


def ms_to_iso(ms: Any) -> str | None:
    if not ms:
        return None
    return datetime.fromtimestamp(float(ms) / 1000).astimezone().isoformat(timespec="seconds")


def ms_to_short(ms: Any) -> str:
    if not ms:
        return "未知时间"
    return datetime.fromtimestamp(float(ms) / 1000).strftime("%Y-%m-%d %H:%M:%S")


def clean_text(text: Any) -> str:
    return SYSTEM_REMINDER_RE.sub("", text or "").strip()


def decompressed_chunks(path: Path) -> Iterator[bytes]:
    """逐块产出解压后的字节；优先用 zstandard 模块，否则回退到 zstd 命令行。"""
    try:
        import zstandard  # type: ignore
    except ImportError:
        zstandard = None

    if zstandard is not None:
        with open(path, "rb") as raw:
            with zstandard.ZstdDecompressor().stream_reader(raw) as reader:
                while True:
                    chunk = reader.read(256 * 1024)
                    if not chunk:
                        return
                    yield chunk
        return

    exe = shutil.which("zstd")
    if not exe:
        raise RuntimeError(
            "缺少 zstd 解压能力：请安装 python 包 zstandard，或把 zstd 可执行文件加入 PATH"
        )
    proc = subprocess.Popen(
        [exe, "-d", "-c", "-q", str(path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    try:
        assert proc.stdout is not None
        while True:
            chunk = proc.stdout.read(256 * 1024)
            if not chunk:
                return
            yield chunk
    finally:
        if proc.stdout:
            proc.stdout.close()
        proc.wait()


def read_records(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    buffer = ""
    for chunk in decompressed_chunks(path):
        buffer += chunk.decode("utf-8", errors="replace")
        while "\n" in buffer:
            line, buffer = buffer.split("\n", 1)
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(record, dict):
                records.append(record)
    tail = buffer.strip()
    if tail:
        try:
            record = json.loads(tail)
            if isinstance(record, dict):
                records.append(record)
        except json.JSONDecodeError:
            pass
    return records


def locate_session_file(dsh_home: Path, session_id: str) -> Path:
    root = dsh_home / "sessions"
    if not root.is_dir():
        raise FileNotFoundError(f"未找到 DSH 会话目录: {root}")
    matches = sorted(root.rglob(f"{session_id}/{SESSION_FILE_GLOB}"))
    if not matches:
        raise FileNotFoundError(f"未找到会话 {session_id}")
    # 同一 cwd 目录下允许存在多个版本文件（取最新的一个）；
    # 但会话 ID 出现在多个不同工作目录时才是真正的歧义。
    grouped: dict[Path, list[Path]] = {}
    for path in matches:
        grouped.setdefault(path.parent, []).append(path)
    if len(grouped) > 1:
        listed = "\n".join(f"  - {parent}" for parent in sorted(grouped))
        raise RuntimeError(f"会话 ID 匹配到多个工作目录：\n{listed}")
    return max(matches, key=session_file_rank)


def user_texts(data: dict[str, Any]) -> list[str]:
    texts: list[str] = []
    for part in data.get("content") or []:
        if not isinstance(part, dict):
            continue
        if part.get("type") == "text":
            cleaned = clean_text(part.get("text"))
            if cleaned:
                texts.append(cleaned)
    return texts


def assistant_parts(data: dict[str, Any], include_reasoning: bool) -> tuple[list[str], list[str]]:
    message = data.get("message") or {}
    texts: list[str] = []
    reasoning: list[str] = []
    for part in message.get("content") or []:
        if not isinstance(part, dict):
            continue
        kind = part.get("type")
        if kind == "text":
            cleaned = clean_text(part.get("text"))
            if cleaned:
                texts.append(cleaned)
        elif kind == "reasoning" and include_reasoning:
            cleaned = clean_text(part.get("text"))
            if cleaned:
                reasoning.append(cleaned)
    return texts, reasoning


def tool_result_text(data: dict[str, Any]) -> str:
    message = data.get("message") or {}
    chunks: list[str] = []
    for part in message.get("content") or []:
        if not isinstance(part, dict):
            continue
        for inner in part.get("content") or []:
            if isinstance(inner, dict) and inner.get("type") == "text":
                text = inner.get("text")
                if isinstance(text, str) and text.strip():
                    chunks.append(text)
    return "\n\n".join(chunks)


def arguments_brief(raw: Any) -> str:
    """工具调用单行摘要：从 arguments JSON 中挑最像描述/命令的字段。"""
    if not isinstance(raw, str) or not raw.strip():
        return ""
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return raw.strip().replace("\n", " ")[:120]
    if isinstance(parsed, dict):
        for key in ("description", "command", "pattern", "file_path", "query", "url"):
            value = parsed.get(key)
            if isinstance(value, str) and value.strip():
                text = value.strip().replace("\n", " ")
                return text[:120] + ("…" if len(text) > 120 else "")
    return ""


def build_blocks(records: list[dict[str, Any]], include_reasoning: bool, include_tools: bool) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """按顺序把记录归并成用户/助手块，工具调用挂到当前助手块上。"""
    blocks: list[dict[str, Any]] = []
    meta: dict[str, Any] = {
        "id": None,
        "cwd": None,
        "title": None,
        "version": None,
        "agent_preset": None,
        "created_at": None,
        "last_time": None,
    }
    current: dict[str, Any] | None = None

    for record in records:
        kind = record.get("type")
        data = record.get("data") or {}
        if record.get("time"):
            meta["last_time"] = record["time"]

        if kind == "session":
            meta["id"] = record.get("id") or meta["id"]
            meta["cwd"] = record.get("cwd")
            meta["version"] = record.get("version")
            meta["agent_preset"] = record.get("agentPreset")
            meta["created_at"] = record.get("createdAt")
        elif kind == "session/title":
            title = (data.get("title") or "").strip()
            if title:
                meta["title"] = title
        elif kind == "user/message":
            # DSH 会把 AGENTS.md 更新、运行时上下文、技能目录等以 user/message 形式注入，
            # 用 source.kind 区分：只有 kind 为 user（或缺省）才是用户真实输入。
            source_kind = (data.get("source") or {}).get("kind")
            if source_kind and source_kind != "user":
                continue
            texts = user_texts(data)
            if not texts:
                continue
            current = {"role": "user", "time": record.get("time"), "texts": texts,
                       "reasoning": [], "tools": []}
            blocks.append(current)
        elif kind == "assistant/message":
            texts, reasoning = assistant_parts(data, include_reasoning)
            current = {"role": "assistant", "time": record.get("time"), "texts": texts,
                       "reasoning": reasoning, "tools": []}
            blocks.append(current)
        elif kind == "tool/call" and current is not None and current["role"] == "assistant":
            current["tools"].append({
                "name": data.get("name") or "未知工具",
                "brief": arguments_brief(data.get("arguments")),
                "arguments": data.get("arguments"),
                "call_id": data.get("callId"),
                "result": None,
            })
        elif kind == "tool/result" and current is not None and current["role"] == "assistant":
            call_id = ((data.get("message") or {}).get("source") or {}).get("callId")
            text = tool_result_text(data)
            for tool in reversed(current["tools"]):
                if call_id is None or tool.get("call_id") == call_id:
                    tool["result"] = text
                    break

    # 默认导出（--no-tools）下，仅工具调用而无文本正文的助手块只剩一行调用清单，
    # 属于过程噪音，整条剔除；--include-tools 时工具详情本身就是内容，不受此限
    keep = [
        block for block in blocks
        if block["role"] == "user"
        or block["texts"]
        or block["reasoning"]
        or (include_tools and block["tools"])
    ]
    return keep, meta


def render(meta: dict[str, Any], blocks: list[dict[str, Any]], include_tools: bool,
           include_reasoning: bool, output: Path) -> str:
    lines = [
        "# DSH 会话导出",
        "",
        f"- **会话 ID**: {meta.get('id') or '（未知）'}",
        f"- **标题**: {meta.get('title') or '（无标题）'}",
        f"- **工作目录**: {meta.get('cwd') or '（未知）'}",
        f"- **时间范围**: {ms_to_iso(meta.get('created_at'))} ~ {ms_to_iso(meta.get('last_time'))}",
        "- **导出模式**: " + ("含工具记录" if include_tools else "仅用户与助手消息")
        + ("（含思考过程）" if include_reasoning else ""),
        "",
    ]

    for block in blocks:
        label = "🧑 用户" if block["role"] == "user" else "🤖 助手"
        lines.extend(["---", "", f"## {label}　`{ms_to_short(block['time'])}`", ""])

        if block["role"] == "assistant":
            if block["reasoning"]:
                for text in block["reasoning"]:
                    lines.extend(["<details><summary>思考过程</summary>", "", text, "",
                                  "</details>", ""])
            if block["tools"] and not include_tools:
                names = list(dict.fromkeys(tool["name"] for tool in block["tools"]))
                lines.extend(["> 调用工具: " + ", ".join(names), ""])

        for text in block["texts"]:
            lines.extend([text, ""])

        if block["role"] == "assistant" and include_tools:
            for tool in block["tools"]:
                header = f"**工具 {tool['name']}**"
                if tool["brief"]:
                    header += f"：{tool['brief']}"
                lines.extend([header, ""])
                raw = tool.get("arguments")
                if isinstance(raw, str) and raw.strip():
                    lines.extend(["```json", raw.strip(), "```", ""])
                result = tool.get("result")
                if isinstance(result, str) and result.strip():
                    lines.append("```")
                    if len(result) > TOOL_OUTPUT_MAX:
                        lines.append(result[:TOOL_OUTPUT_MAX])
                        lines.append(f"…（输出已截断，完整长度 {len(result)} 字符）")
                    else:
                        lines.append(result)
                    lines.extend(["```", ""])

    return "\n".join(lines).rstrip() + "\n"


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

    parser = argparse.ArgumentParser(description="导出 DSH 会话为 Markdown")
    parser.add_argument("session_id", nargs="?", default=None,
                        help="会话 ID（session-<uuid> 或裸 <uuid>）；省略或加 --current 时读取环境变量 DSH_SESSION_ID")
    parser.add_argument("--current", action="store_true",
                        help="导出当前会话（读取环境变量 DSH_SESSION_ID，无需手动输入 ID）")
    parser.add_argument("--no-tools", action="store_true", default=True,
                        help="工具调用仅保留名称（默认）")
    parser.add_argument("--include-tools", action="store_true", help="导出工具调用的输入与输出")
    parser.add_argument("--include-reasoning", action="store_true", help="附上助手思考过程")
    parser.add_argument("--output", default=None,
                        help="输出 Markdown 路径，默认 .\\work\\dsh-session-<会话 ID>.md")
    parser.add_argument("--dsh-home", type=Path, default=Path.home() / ".dsh",
                        help="DSH 数据目录，默认 ~/.dsh")
    args = parser.parse_args()

    raw_id = (args.session_id or "").strip()
    if args.current or not raw_id:
        raw_id = (os.environ.get("DSH_SESSION_ID") or "").strip()
        if not raw_id:
            print(json.dumps(
                {"error": "未提供会话 ID，且环境变量 DSH_SESSION_ID 为空；请在 DSH 会话内运行，或显式给出会话 ID"},
                ensure_ascii=False))
            return 2
    if raw_id.startswith("session-"):
        session_id = raw_id
    elif UUID_RE.fullmatch(raw_id):
        session_id = f"session-{raw_id}"
    else:
        print(json.dumps({"error": f"会话 ID 格式不合法: {raw_id}"}, ensure_ascii=False))
        return 2

    dsh_home = args.dsh_home.expanduser().resolve()
    try:
        session_file = locate_session_file(dsh_home, session_id)
        records = read_records(session_file)
    except (FileNotFoundError, RuntimeError, OSError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False))
        return 1

    blocks, meta = build_blocks(records, args.include_reasoning, args.include_tools)
    meta["id"] = meta["id"] or session_id
    if not blocks:
        print(json.dumps({"error": "导出内容为空：会话可能已被清理，或消息均为系统注入内容"},
                         ensure_ascii=False))
        return 1

    out_path = Path(args.output) if args.output else Path.cwd() / "work" / f"dsh-session-{session_id}.md"
    out_path = out_path.expanduser().resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        render(meta, blocks, args.include_tools, args.include_reasoning, out_path),
        encoding="utf-8",
    )

    n_user = sum(1 for block in blocks if block["role"] == "user")
    n_asst = sum(1 for block in blocks if block["role"] == "assistant")
    n_tools = sum(len(block["tools"]) for block in blocks)
    print(json.dumps({
        "session_id": meta["id"],
        "title": meta.get("title"),
        "cwd": meta.get("cwd"),
        "output": str(out_path),
        "entries": len(blocks),
        "user_messages": n_user,
        "assistant_messages": n_asst,
        "tool_calls": n_tools,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
