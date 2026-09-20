#!/usr/bin/env python3
"""按标题在 ZCode 本机数据库中定位会话。

只读访问 ~/.zcode/cli/db/db.sqlite，输出 JSON 数组：
[{"id", "title", "directory", "time_created", "time_updated"}]
"""

import argparse
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

DB_PATH = Path.home() / ".zcode" / "cli" / "db" / "db.sqlite"


def ms_to_iso(ms):
    if not ms:
        return None
    return datetime.fromtimestamp(ms / 1000).astimezone().isoformat(timespec="seconds")


def main():
    parser = argparse.ArgumentParser(description="按标题查找 ZCode 会话")
    parser.add_argument("title", nargs="?", default=None, help="会话标题（精确匹配）")
    parser.add_argument("--contains", action="store_true", help="标题模糊匹配（包含关键词）")
    parser.add_argument("--all", action="store_true", help="列出全部会话（按更新时间倒序）")
    parser.add_argument("--limit", type=int, default=20, help="最多返回条数（默认 20）")
    args = parser.parse_args()

    if not args.title and not args.all:
        parser.error("请提供标题，或使用 --all 列出全部会话")

    if not DB_PATH.exists():
        print(json.dumps({"error": f"未找到 ZCode 数据库: {DB_PATH}"}, ensure_ascii=False))
        sys.exit(1)

    try:
        con = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    except sqlite3.Error as exc:
        print(json.dumps({"error": f"数据库连接失败: {exc}"}, ensure_ascii=False))
        sys.exit(1)

    try:
        if args.all:
            rows = con.execute(
                "SELECT id, title, directory, time_created, time_updated "
                "FROM session ORDER BY time_updated DESC LIMIT ?",
                (args.limit,),
            ).fetchall()
        elif args.contains:
            rows = con.execute(
                "SELECT id, title, directory, time_created, time_updated "
                "FROM session WHERE title LIKE ? ORDER BY time_updated DESC LIMIT ?",
                (f"%{args.title}%", args.limit),
            ).fetchall()
        else:
            rows = con.execute(
                "SELECT id, title, directory, time_created, time_updated "
                "FROM session WHERE title = ? ORDER BY time_updated DESC LIMIT ?",
                (args.title, args.limit),
            ).fetchall()
    finally:
        con.close()

    result = [
        {
            "id": r[0],
            "title": r[1],
            "directory": r[2],
            "time_created": ms_to_iso(r[3]),
            "time_updated": ms_to_iso(r[4]),
        }
        for r in rows
    ]
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
