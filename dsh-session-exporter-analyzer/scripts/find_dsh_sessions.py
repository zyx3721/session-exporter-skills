#!/usr/bin/env python3
"""按标题或关键词在本机 DSH 会话目录中定位会话。

只读访问 ~/.dsh/sessions/，输出 JSON 数组：
[{"id", "title", "cwd", "time_created", "time_updated"}]

DSH 会话存放为 ~/.dsh/sessions/<工作目录 slug>/<会话 ID>/session.v3.jsonl.zstd，
正文是多帧拼接的 zstd 压缩 JSONL，因此这里只解压每个文件开头的一小段来读元数据，
避免为了列标题而把整份会话（可达数 MB）全部解开。
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

HEAD_BYTES = 512 * 1024
SESSION_FILE_GLOB = "session*.jsonl.zstd"
SESSION_FILE_RE = re.compile(r"^session(?:\.v(\d+))?\.jsonl\.zstd$")


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


def mtime_iso(path: Path) -> str | None:
    return datetime.fromtimestamp(path.stat().st_mtime).astimezone().isoformat(timespec="seconds")


def _decompressed_chunks(path: Path) -> Iterator[bytes]:
    """逐块产出解压后的字节；优先用 zstandard 模块，否则回退到 zstd 命令行。"""
    try:
        import zstandard  # type: ignore
    except ImportError:
        zstandard = None

    if zstandard is not None:
        with open(path, "rb") as raw:
            with zstandard.ZstdDecompressor().stream_reader(raw) as reader:
                while True:
                    chunk = reader.read(64 * 1024)
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
            chunk = proc.stdout.read(64 * 1024)
            if not chunk:
                return
            yield chunk
    finally:
        proc.stdout.close() if proc.stdout else None
        proc.wait()


def read_head(path: Path, limit: int = HEAD_BYTES) -> str:
    buffer = bytearray()
    for chunk in _decompressed_chunks(path):
        buffer.extend(chunk)
        if len(buffer) >= limit:
            break
    return buffer.decode("utf-8", errors="replace")


def iter_session_files(dsh_home: Path) -> list[Path]:
    """每个会话目录只保留一个文件，避免同一会话因旧/新格式并存而重复列出。"""
    root = dsh_home / "sessions"
    if not root.is_dir():
        return []
    grouped: dict[Path, list[Path]] = {}
    for path in root.rglob(SESSION_FILE_GLOB):
        grouped.setdefault(path.parent, []).append(path)
    return sorted(max(paths, key=session_file_rank) for paths in grouped.values())


def emit(payload: Any) -> None:
    """按控制台编码输出 JSON；遇到无法编码的字符时退回 ASCII 转义。"""
    try:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    except UnicodeEncodeError:
        print(json.dumps(payload, ensure_ascii=True, indent=2))


def read_metadata(path: Path) -> dict[str, Any]:
    """读取会话头与标题记录；标题取最后一次出现的 session/title。"""
    meta: dict[str, Any] = {"id": path.parent.name, "title": None, "cwd": None, "time_created": None}
    for line in read_head(path).splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        kind = record.get("type")
        data = record.get("data") or {}
        if kind == "session":
            meta["id"] = record.get("id") or meta["id"]
            meta["cwd"] = record.get("cwd")
            meta["time_created"] = ms_to_iso(record.get("createdAt"))
        elif kind == "session/title":
            title = (data.get("title") or "").strip()
            if title:
                meta["title"] = title
    return meta


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
    parser = argparse.ArgumentParser(description="按标题查找本机 DSH 会话")
    parser.add_argument("title", nargs="?", default=None, help="会话标题（精确匹配）")
    parser.add_argument("--contains", action="store_true", help="标题模糊匹配（包含关键词）")
    parser.add_argument("--all", action="store_true", help="列出全部会话（按更新时间倒序）")
    parser.add_argument("--cwd", default=None, help="只保留工作目录包含该字符串的会话")
    parser.add_argument("--limit", type=int, default=20, help="最多返回条数（默认 20）")
    parser.add_argument(
        "--dsh-home",
        type=Path,
        default=Path.home() / ".dsh",
        help="DSH 数据目录，默认 ~/.dsh",
    )
    args = parser.parse_args()

    if not args.title and not args.all:
        parser.error("请提供标题，或使用 --all 列出全部会话")

    dsh_home = args.dsh_home.expanduser().resolve()
    files = iter_session_files(dsh_home)
    if not files:
        emit({"error": f"未找到 DSH 会话目录: {dsh_home / 'sessions'}"})
        return 1

    rows: list[dict[str, Any]] = []
    for path in files:
        try:
            meta = read_metadata(path)
        except (OSError, RuntimeError) as exc:
            print(f"警告: 跳过 {path.parent.name}: {exc}", file=sys.stderr)
            continue
        meta["time_updated"] = mtime_iso(path)
        rows.append(meta)

    if args.cwd:
        needle = args.cwd.lower()
        rows = [r for r in rows if needle in (r.get("cwd") or "").lower()]

    if not args.all:
        wanted = args.title.strip()
        if args.contains:
            rows = [r for r in rows if wanted.lower() in (r.get("title") or "").lower()]
        else:
            exact = [r for r in rows if (r.get("title") or "") == wanted]
            rows = exact or [r for r in rows if wanted.lower() in (r.get("title") or "").lower()]

    rows.sort(key=lambda r: r.get("time_updated") or "", reverse=True)
    rows = rows[: max(args.limit, 1)]

    result = [
        {
            "id": r.get("id"),
            "title": r.get("title") or "",
            "cwd": r.get("cwd") or "",
            "time_created": r.get("time_created"),
            "time_updated": r.get("time_updated"),
        }
        for r in rows
    ]
    emit(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
