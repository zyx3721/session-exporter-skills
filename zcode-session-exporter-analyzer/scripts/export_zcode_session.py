#!/usr/bin/env python3
"""将 ZCode 会话导出为 Markdown 文档。

只读访问 ~/.zcode/cli/db/db.sqlite，从 message/part 表提取用户与助手消息，
不修改数据库与原会话。会话 ID 接受 sess_<uuid> 或裸 <uuid>。
"""

import argparse
import hashlib
import json
import re
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

DB_PATH = Path.home() / ".zcode" / "cli" / "db" / "db.sqlite"
TOOL_OUTPUT_MAX = 8000

SYSTEM_REMINDER_RE = re.compile(r"<system-reminder>.*?</system-reminder>", re.S)


def ms_to_iso(ms):
    if not ms:
        return None
    return datetime.fromtimestamp(ms / 1000).astimezone().isoformat(timespec="seconds")


def ms_to_short(ms):
    if not ms:
        return "未知时间"
    return datetime.fromtimestamp(ms / 1000).strftime("%Y-%m-%d %H:%M:%S")


def clean_text(text):
    return SYSTEM_REMINDER_RE.sub("", text or "").strip()


def tool_input_brief(state):
    """工具调用单行摘要：优先取 description/title，否则取 input 的紧凑 JSON。"""
    inp = state.get("input")
    if isinstance(inp, dict):
        for key in ("description", "title", "command", "pattern", "file_path"):
            val = inp.get(key)
            if isinstance(val, str) and val.strip():
                text = val.strip().replace("\n", " ")
                return text[:120] + ("…" if len(text) > 120 else "")
    return ""


def open_db():
    if not DB_PATH.exists():
        print(json.dumps({"error": f"未找到 ZCode 数据库: {DB_PATH}"}, ensure_ascii=False))
        sys.exit(1)
    try:
        return sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    except sqlite3.Error as exc:
        print(json.dumps({"error": f"数据库连接失败: {exc}"}, ensure_ascii=False))
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(description="导出 ZCode 会话为 Markdown")
    parser.add_argument("session_id", help="会话 ID（sess_<uuid> 或裸 <uuid>）")
    parser.add_argument("--no-tools", action="store_true", default=True, help="工具调用仅保留名称（默认）")
    parser.add_argument("--include-tools", action="store_true", help="导出工具调用的输入与输出")
    parser.add_argument("--include-reasoning", action="store_true", help="附上助手思考过程")
    parser.add_argument("--output", required=True, help="输出 Markdown 路径")
    args = parser.parse_args()

    sid_raw = args.session_id.strip()
    if sid_raw.startswith("sess_"):
        sid = sid_raw
    else:
        sid = f"sess_{sid_raw}"
    if not re.fullmatch(r"sess_[0-9a-fA-F-]{36}", sid):
        print(json.dumps({"error": f"会话 ID 格式不合法: {sid_raw}"}, ensure_ascii=False))
        sys.exit(1)

    con = open_db()
    try:
        row = con.execute(
            "SELECT title, directory, time_created, time_updated FROM session WHERE id = ?",
            (sid,),
        ).fetchone()
        if not row:
            print(json.dumps({"error": f"未找到会话 {sid}"}, ensure_ascii=False))
            sys.exit(1)
        title, directory, time_created, time_updated = row

        messages = con.execute(
            "SELECT id, time_created, data FROM message WHERE session_id = ? ORDER BY sequence",
            (sid,),
        ).fetchall()

        parts = {}
        for mid, pdata in con.execute(
            "SELECT message_id, data FROM part WHERE session_id = ? ORDER BY sequence",
            (sid,),
        ):
            try:
                parts.setdefault(mid, []).append(json.loads(pdata))
            except json.JSONDecodeError:
                continue
    finally:
        con.close()

    out_lines = []
    out_lines.append("# ZCode 会话导出")
    out_lines.append("")
    out_lines.append(f"- **会话 ID**: {sid}")
    out_lines.append(f"- **标题**: {title or '（无标题）'}")
    out_lines.append(f"- **工作目录**: {directory or '（未知）'}")
    out_lines.append(f"- **时间范围**: {ms_to_iso(time_created)} ~ {ms_to_iso(time_updated)}")
    out_lines.append(f"- **导出模式**: {'含工具记录' if args.include_tools else '仅用户与助手消息'}"
                     + ("（含思考过程）" if args.include_reasoning else ""))
    out_lines.append("")

    entries = 0
    for mid, msg_time, mdata in messages:
        try:
            msg = json.loads(mdata)
        except json.JSONDecodeError:
            continue
        role = msg.get("role")
        if role not in ("user", "assistant"):
            continue
        # synthetic 标志表示 harness 注入消息（todo 提醒、上下文注入等），不是用户真实输入
        if msg.get("synthetic"):
            continue
        plist = parts.get(mid, [])

        texts, tool_parts, reasoning_parts, file_parts = [], [], [], []
        for p in plist:
            ptype = p.get("type")
            if ptype == "text":
                cleaned = clean_text(p.get("text"))
                if cleaned:
                    texts.append(cleaned)
            elif ptype == "tool":
                tool_parts.append(p)
            elif ptype == "reasoning":
                reasoning_parts.append(p)
            elif ptype == "file":
                file_parts.append(p)

        # 剔除系统注入后无正文且无内容的消息
        if role == "user" and not texts and not file_parts:
            continue
        # 默认导出（--no-tools / --include-tools）下，无文本正文的助手消息只渲染
        # 一行调用清单（reasoning 默认不渲染，不能据此保留），属于过程噪音整条剔除；
        # --include-tools / --include-reasoning 时工具详情与思考过程本身就是内容，不受此限
        if role == "assistant" and not args.include_tools and not args.include_reasoning \
                and not texts and not file_parts:
            continue
        if role == "assistant" and not texts and not tool_parts and not file_parts:
            continue

        label = "🧑 用户" if role == "user" else "🤖 助手"
        out_lines.append("---")
        out_lines.append("")
        out_lines.append(f"## {label}　`{ms_to_short(msg_time)}`")
        out_lines.append("")

        if role == "user" and file_parts:
            out_lines.append(f"> 附件: {len(file_parts)} 个文件（"
                             + ", ".join(p.get("mime") or "未知类型" for p in file_parts) + "）")
            out_lines.append("")

        if role == "assistant":
            if args.include_reasoning:
                for p in reasoning_parts:
                    rtext = clean_text(p.get("text"))
                    if rtext:
                        out_lines.append("<details><summary>思考过程</summary>")
                        out_lines.append("")
                        out_lines.append(rtext)
                        out_lines.append("")
                        out_lines.append("</details>")
                        out_lines.append("")
            if tool_parts and not args.include_tools:
                out_lines.append("> 调用工具: " + ", ".join(
                    dict.fromkeys(p.get("tool") or "未知工具" for p in tool_parts)))
                out_lines.append("")

        for text in texts:
            out_lines.append(text)
            out_lines.append("")

        if role == "assistant" and args.include_tools:
            for p in tool_parts:
                state = p.get("state") or {}
                name = p.get("tool") or "未知工具"
                brief = tool_input_brief(state)
                out_lines.append(f"**工具 {name}**（{state.get('status') or '未知状态'}）"
                                 + (f"：{brief}" if brief else ""))
                out_lines.append("")
                inp = state.get("input")
                if isinstance(inp, dict) and inp:
                    out_lines.append("```json")
                    out_lines.append(json.dumps(inp, ensure_ascii=False, indent=2))
                    out_lines.append("```")
                    out_lines.append("")
                output = state.get("output")
                if isinstance(output, str) and output.strip():
                    out_lines.append("```")
                    if len(output) > TOOL_OUTPUT_MAX:
                        out_lines.append(output[:TOOL_OUTPUT_MAX])
                        out_lines.append(f"…（输出已截断，完整长度 {len(output)} 字符）")
                    else:
                        out_lines.append(output)
                    out_lines.append("```")
                    out_lines.append("")

        entries += 1

    if entries == 0:
        print(json.dumps({"error": "导出内容为空：会话可能已被清理，或消息均为系统注入内容"}, ensure_ascii=False))
        sys.exit(1)

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(out_lines), encoding="utf-8")

    n_user = sum(1 for line in out_lines if line.startswith("## 🧑 用户"))
    n_asst = sum(1 for line in out_lines if line.startswith("## 🤖 助手"))
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print(json.dumps({
        "session_id": sid,
        "title": title,
        "output": str(out_path),
        "entries": entries,
        "user_messages": n_user,
        "assistant_messages": n_asst,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
