#!/usr/bin/env python3
"""Find local Codex threads by title without exposing rollout paths."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="按标题查询本地 Codex 会话。")
    parser.add_argument("title", help="完整标题或关键词")
    parser.add_argument(
        "--contains",
        action="store_true",
        help="按不区分大小写的标题关键词匹配",
    )
    parser.add_argument(
        "--codex-home",
        type=Path,
        default=Path.home() / ".codex",
        help="Codex 数据目录，默认 ~/.codex",
    )
    parser.add_argument("--limit", type=int, default=20, help="最大返回数，默认 20")
    return parser.parse_args()


def display_time(value: Any) -> str:
    if value is None:
        return ""
    try:
        timestamp = float(value)
        if timestamp > 100_000_000_000:
            timestamp /= 1000
        return datetime.fromtimestamp(timestamp, tz=timezone.utc).astimezone().isoformat(
            timespec="seconds"
        )
    except (TypeError, ValueError, OSError):
        return str(value)


def main() -> int:
    args = parse_args()
    title = args.title.strip()
    if not title:
        print("错误: 标题不能为空", file=sys.stderr)
        return 2
    if args.limit <= 0:
        print("错误: --limit 必须为正数", file=sys.stderr)
        return 2

    database = args.codex_home.expanduser().resolve() / "state_5.sqlite"
    if not database.is_file():
        print(f"错误: 未找到 Codex 状态库: {database}", file=sys.stderr)
        return 1

    where = "LOWER(COALESCE(title, '')) LIKE LOWER(?)" if args.contains else "title = ? COLLATE NOCASE"
    parameter = f"%{title}%" if args.contains else title
    query = f"""
        SELECT id, title, updated_at, cwd, archived
        FROM threads
        WHERE {where}
        ORDER BY updated_at DESC
        LIMIT ?
    """
    try:
        connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        try:
            rows = connection.execute(query, (parameter, args.limit)).fetchall()
        finally:
            connection.close()
    except sqlite3.Error as exc:
        print(f"错误: 无法查询状态库: {exc}", file=sys.stderr)
        return 1

    result = [
        {
            "id": row["id"],
            "title": row["title"] or "",
            "updated_at": display_time(row["updated_at"]),
            "cwd": row["cwd"] or "",
            "archived": bool(row["archived"]),
        }
        for row in rows
    ]
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
