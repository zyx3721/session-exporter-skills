#!/usr/bin/env python3
"""Export one local Codex thread from state_5.sqlite and its rollout JSONL."""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable


THREAD_ID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Entry:
    timestamp: str
    kind: str
    title: str
    body: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="从本地 Codex state_5.sqlite 与 rollout JSONL 导出指定会话。"
    )
    parser.add_argument("thread_id", help="Codex thread/session UUID")
    parser.add_argument(
        "--codex-home",
        type=Path,
        default=Path.home() / ".codex",
        help="Codex 数据目录，默认 ~/.codex",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Markdown 输出路径；默认当前目录 codex-session-<id>.md",
    )
    parser.add_argument(
        "--max-field-chars",
        type=int,
        default=20_000,
        help="单条正文最大字符数，0 表示不截断，默认 20000",
    )
    parser.add_argument(
        "--no-tools",
        action="store_true",
        help="不导出工具调用和工具输出",
    )
    return parser.parse_args()


def query_thread(db_path: Path, thread_id: str) -> dict[str, Any] | None:
    if not db_path.is_file():
        return None
    connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        row = connection.execute(
            "SELECT * FROM threads WHERE id = ?", (thread_id,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        connection.close()


def find_rollout(codex_home: Path, thread_id: str, thread: dict[str, Any] | None) -> Path:
    if thread:
        stored_path = Path(str(thread.get("rollout_path") or "")).expanduser()
        if stored_path.is_file():
            return stored_path

    matches = sorted((codex_home / "sessions").glob(f"**/*{thread_id}*.jsonl"))
    if not matches:
        raise FileNotFoundError(f"未找到会话 {thread_id} 对应的 rollout JSONL")
    if len(matches) > 1:
        paths = "\n".join(f"  - {path}" for path in matches)
        raise RuntimeError(f"会话 ID 匹配到多个 rollout：\n{paths}")
    return matches[0]


def stringify(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)


def message_text(payload: dict[str, Any]) -> str:
    message = payload.get("message")
    if isinstance(message, str):
        return message.strip()

    parts: list[str] = []
    for item in payload.get("content") or []:
        if not isinstance(item, dict):
            continue
        text = item.get("text") or item.get("input_text") or item.get("output_text")
        if isinstance(text, str) and text.strip():
            parts.append(text.strip())
    return "\n\n".join(parts)


def tool_call_body(payload: dict[str, Any]) -> str:
    for key in ("arguments", "input", "action", "command"):
        if key in payload:
            return stringify(payload[key])
    return stringify(payload)


def tool_output_body(payload: dict[str, Any]) -> str:
    for key in ("output", "result", "content"):
        if key in payload:
            return stringify(payload[key])
    return stringify(payload)


def normalize_record(record: dict[str, Any], include_tools: bool) -> Entry | None:
    timestamp = str(record.get("timestamp") or "")
    record_type = str(record.get("type") or "")
    payload = record.get("payload")
    if not isinstance(payload, dict):
        return None
    payload_type = str(payload.get("type") or "")

    if record_type == "event_msg" and payload_type == "user_message":
        return Entry(timestamp, "user", "用户", message_text(payload))
    if record_type == "event_msg" and payload_type == "agent_message":
        phase = str(payload.get("phase") or "assistant")
        return Entry(timestamp, "assistant", f"助手 ({phase})", message_text(payload))
    if record_type == "turn_context":
        turn_id = str(payload.get("turn_id") or "")
        body = {
            "turn_id": turn_id,
            "cwd": payload.get("cwd"),
            "model": payload.get("model"),
            "current_date": payload.get("current_date"),
            "timezone": payload.get("timezone"),
        }
        return Entry(timestamp, "turn", f"Turn {turn_id}", stringify(body))
    if record_type == "event_msg" and payload_type in {
        "context_compacted",
        "task_complete",
        "turn_aborted",
        "thread_rolled_back",
    }:
        return Entry(timestamp, "status", f"状态: {payload_type}", stringify(payload))
    if record_type == "compacted":
        return Entry(timestamp, "compacted", "上下文压缩记录", stringify(payload))

    if not include_tools or record_type != "response_item":
        return None
    if payload_type in {"function_call", "custom_tool_call", "local_shell_call", "web_search_call"}:
        name = str(payload.get("name") or payload.get("action") or payload_type)
        call_id = str(payload.get("call_id") or payload.get("id") or "")
        return Entry(timestamp, "tool_call", f"工具调用: {name} [{call_id}]", tool_call_body(payload))
    if payload_type in {"function_call_output", "custom_tool_call_output"}:
        call_id = str(payload.get("call_id") or payload.get("id") or "")
        return Entry(timestamp, "tool_output", f"工具输出 [{call_id}]", tool_output_body(payload))
    return None


def read_rollout(path: Path, include_tools: bool) -> tuple[dict[str, Any], list[Entry], int]:
    metadata: dict[str, Any] = {}
    entries: list[Entry] = []
    invalid_lines = 0
    with path.open("r", encoding="utf-8") as source:
        for line_number, line in enumerate(source, start=1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                print(f"警告: 第 {line_number} 行不是有效 JSON: {exc}", file=sys.stderr)
                invalid_lines += 1
                continue
            if not isinstance(record, dict):
                continue
            if record.get("type") == "session_meta" and not metadata:
                payload = record.get("payload")
                if isinstance(payload, dict):
                    metadata = payload
            entry = normalize_record(record, include_tools)
            if entry and entry.body.strip():
                entries.append(entry)
    return metadata, entries, invalid_lines


def truncate(text: str, limit: int) -> str:
    if limit <= 0 or len(text) <= limit:
        return text
    omitted = len(text) - limit
    return f"{text[:limit]}\n\n...[单字段截断 {omitted} 字符]"


def fence_for(text: str) -> str:
    longest = max((len(match.group(0)) for match in re.finditer(r"`+", text)), default=0)
    return "`" * max(3, longest + 1)


def timestamp_display(value: str) -> str:
    if not value:
        return "时间未知"
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.astimezone().isoformat(timespec="seconds")
    except ValueError:
        return value


def count_kinds(entries: Iterable[Entry]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for entry in entries:
        counts[entry.kind] = counts.get(entry.kind, 0) + 1
    return counts


def render_markdown(
    thread_id: str,
    db_path: Path,
    rollout_path: Path,
    thread: dict[str, Any] | None,
    metadata: dict[str, Any],
    entries: list[Entry],
    invalid_lines: int,
    max_field_chars: int,
) -> str:
    counts = count_kinds(entries)
    lines = [
        f"# Codex 会话导出: {thread_id}",
        "",
        "## 元数据",
        "",
        f"- 状态库: `{db_path}`",
        f"- Rollout: `{rollout_path}`",
        f"- 标题: {thread.get('title', '') if thread else ''}",
        f"- 工作目录: `{(thread or metadata).get('cwd', '')}`",
        f"- 创建时间: `{metadata.get('timestamp', thread.get('created_at', '') if thread else '')}`",
        f"- 模型提供方: `{(thread or metadata).get('model_provider', '')}`",
        f"- 解析失败行数: {invalid_lines}",
        f"- 条目统计: `{json.dumps(counts, ensure_ascii=False, sort_keys=True)}`",
        "",
        "## 会话内容",
        "",
    ]
    for index, entry in enumerate(entries, start=1):
        body = truncate(entry.body, max_field_chars)
        lines.extend(
            [
                f"### {index}. {entry.title}",
                "",
                f"时间: `{timestamp_display(entry.timestamp)}`",
                "",
            ]
        )
        if entry.kind in {"tool_call", "tool_output", "turn", "status", "compacted"}:
            fence = fence_for(body)
            lines.extend([fence + "text", body, fence, ""])
        else:
            lines.extend([body, ""])
    return "\n".join(lines).rstrip() + "\n"


def main() -> int:
    args = parse_args()
    thread_id = args.thread_id.strip()
    if not THREAD_ID_RE.fullmatch(thread_id):
        print(f"错误: 非法的 thread ID: {thread_id}", file=sys.stderr)
        return 2
    if args.max_field_chars < 0:
        print("错误: --max-field-chars 不能为负数", file=sys.stderr)
        return 2

    codex_home = args.codex_home.expanduser().resolve()
    db_path = codex_home / "state_5.sqlite"
    try:
        thread = query_thread(db_path, thread_id)
        rollout_path = find_rollout(codex_home, thread_id, thread)
        metadata, entries, invalid_lines = read_rollout(
            rollout_path, include_tools=not args.no_tools
        )
    except (FileNotFoundError, RuntimeError, sqlite3.Error, OSError) as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 1

    output = args.output or Path.cwd() / f"codex-session-{thread_id}.md"
    output = output.expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    markdown = render_markdown(
        thread_id,
        db_path,
        rollout_path,
        thread,
        metadata,
        entries,
        invalid_lines,
        args.max_field_chars,
    )
    output.write_text(markdown, encoding="utf-8")
    print(json.dumps({
        "thread_id": thread_id,
        "rollout_path": str(rollout_path),
        "output": str(output),
        "entries": len(entries),
        "counts": count_kinds(entries),
        "invalid_lines": invalid_lines,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
