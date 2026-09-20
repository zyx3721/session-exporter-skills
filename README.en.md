<div align="center">

<h1>Session Exporter Skills</h1>

<p><b>Session export toolkit for AI coding agents</b> — Codex · ZCode · DSH: locate · export · archive</p>

<p><a href="README.md">简体中文</a> · <b>English</b></p>

The sessions you have already lived through are the hardest ones to find again: the reasoning, the dead ends, what finally changed — all of it is buried in chat history.
These three skills turn that into a fixed pipeline — **locate by title or session ID → export as Markdown → redact secrets → archive into Obsidian → commit to Git**.
Everything is read-only; no original session is ever modified.

<p>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-blue?labelColor=1f2937" alt="MIT License"></a>
  <a href="https://www.python.org/"><img src="https://img.shields.io/badge/Python-3.7+-3776AB?logo=python&logoColor=white&labelColor=1f2937" alt="Python 3.7+"></a>
  <img src="https://img.shields.io/badge/skills-3-059669?labelColor=1f2937" alt="3 skills">
  <img src="https://img.shields.io/badge/Codex%20%C2%B7%20ZCode%20%C2%B7%20DSH-supported-7c3aed?labelColor=1f2937" alt="Codex ZCode DSH">
</p>

<p>
  <b><a href="#output-sample">Output sample</a></b> ·
  <a href="#what-it-does">What it does</a> ·
  <a href="#the-three-skills">The three skills</a> ·
  <a href="#how-it-works">How it works</a> ·
  <a href="#quick-start">Quick start</a> ·
  <a href="#usage-examples">Usage</a> ·
  <a href="#obsidian-archiving-conventions">Archiving</a> ·
  <a href="#faq">FAQ</a> ·
  <a href="#repository-layout">Layout</a> ·
  <a href="#documentation">Docs</a>
</p>

</div>

---

Three AI coding tools keep their sessions in three incompatible stores: Codex pairs a SQLite index with rollout JSONL, ZCode uses a single SQLite database, and DSH compresses every session into multi-frame zstd JSONL. Digging up an old conversation means learning each format separately. These skills absorb that difference and expose one consistent interface: **give a title or an ID, get back a readable, greppable, long-lived Markdown document**.

## Output sample

The exported shape is identical across the three skills — a metadata block, then alternating role sections with timestamps, with tool usage reduced to a single hint line:

```markdown
# DSH 会话导出

- **会话 ID**: session-a5b03a33-680f-42d3-8f1c-66311e4d9a0c
- **标题**: 重整项目 README 及英文文档
- **工作目录**: D:\GitHub\我的项目\itdb-new
- **时间范围**: 2026-09-17T15:16:32+08:00 ~ 2026-09-20T09:53:49+08:00
- **导出模式**: 仅用户与助手消息

---

## 🧑 用户　`2026-09-17 15:19:48`

详细分析当前项目，然后帮我重整一下 README.md 文档……

---

## 🤖 助手　`2026-09-17 15:19:51`

> 调用工具: pwsh, read, web_fetch

皇上，臣先做三件事：① 摸清项目结构与构建/部署链路；② 拉取参考项目的 README 规范格式；③ 备份并重写 README.md。
```

By default only user and assistant messages are exported, with tool calls collapsed to a single line of names. Add `--include-tools` when the debugging detail matters, and `--include-reasoning` to keep the model's thinking.

## What it does

- **Locate sessions across three tools** — exact match on the full title, or fuzzy match on keywords; when several sessions share a title, the skill lists candidates for a human to pick and never chooses on its own.
- **Export clean Markdown** — one metadata block plus role sections with human-readable timestamps; oversized outputs are truncated with the omitted length noted.
- **Strip injected noise** — every tool injects system prompts, rule updates, runtime context and skill catalogs into the transcript; these are identified by their source field and removed, leaving what a person actually said and what the assistant actually answered.
- **Archive into Obsidian** — written to `<matching directory>/<tool name> 会话文档/YYYY-MM-DD topic.md`, creating the directory first and never mixing sessions from different tools.
- **Redact credentials before archiving** — passwords, tokens, keys and private keys are replaced with `********（已脱敏）`; internal addresses and account names stay, since they carry the operational context.
- **Commit automatically after writing** — only the files from this run are staged, bulk `git add` is forbidden, and **pushing always waits for explicit confirmation**.

**It is not** a session backup tool and does not sync to any cloud: it pulls one conversation out into a document on demand and never writes a byte back to the original store.

## The three skills

| Skill | Target tool | Session ID shape | Data source | Scripts |
| --- | --- | --- | --- | --- |
| `codex-session-exporter-analyzer` | Codex | bare UUID | `~/.codex/state_5.sqlite` + `~/.codex/sessions/**/rollout-*.jsonl` | `find_codex_threads.py` · `export_codex_session.py` |
| `zcode-session-exporter-analyzer` | ZCode | `sess_<uuid>` | `~/.zcode/cli/db/db.sqlite` (`session` / `message` / `part` tables) | `find_zcode_sessions.py` · `export_zcode_session.py` |
| `dsh-session-exporter-analyzer` | DSH | `session-<uuid>` | `~/.dsh/sessions/<cwd slug>/<session id>/session.v3.jsonl.zstd` | `find_dsh_sessions.py` · `export_dsh_session.py` |

Each skill ships two scripts: `find_*` locates sessions and prints candidates as JSON, `export_*` writes the Markdown and reports entry counts.

The DSH path has three genuinely hard edges, all handled in the script: the session file is **multi-frame zstd appended as the session grows** (a single decompress call returns only the first frame, so it is read as a stream, with the `zstandard` module and the `zstd` CLI as two fallbacks); one session directory may hold both an old-format and a migrated new-format file (the higher version wins); and injected messages arrive disguised as `user/message` records (separated by `data.source.kind`).

## How it works

```text
      User asks: "export a session into Obsidian"
                    │
                    ▼
        ┌───────────────────────────┐
        │  Locate by title or ID     │
        │  find_*  → candidate JSON  │
        └───────────────────────────┘
                    │
                    ▼
        ┌───────────────────────────┐
        │  Read the session store    │
        │  (read-only)               │
        │  drop injections, restore  │
        │  roles and timestamps      │
        └───────────────────────────┘
                    │
                    ▼
        ┌───────────────────────────┐
        │  Render Markdown           │
        │  redact secrets, truncate  │
        └───────────────────────────┘
                    │
                    ▼
        ┌───────────────────────────┐
        │  Archive into the Vault    │
        │  <tool> 会话文档/date topic.md│
        └───────────────────────────┘
                    │
                    ▼
        ┌───────────────────────────┐
        │  Commit locally (this file)│
        │  push → wait for approval  │
        └───────────────────────────┘
```

- **Locate**: exact title match first, fuzzy matching as a fallback, and a full listing when both come up empty.
- **Parse**: strictly read-only; user and assistant messages are extracted per record type and injected content is stripped.
- **Write**: the target directory follows the session's subject rather than its working directory — when the two disagree, the subject wins.
- **Commit**: only the documents written in this run are staged; the push decision stays with the human.

## Quick start

All you need is **Python 3.7+** — no third-party dependencies (for DSH's multi-frame zstd the `zstandard` module is preferred, falling back to the `zstd` CLI).

Copy each skill directory into the matching tool's skills folder:

```powershell
# Codex
Copy-Item -Recurse .\codex-session-exporter-analyzer "$env:USERPROFILE\.codex\skills\"

# ZCode
Copy-Item -Recurse .\zcode-session-exporter-analyzer "$env:USERPROFILE\.zcode\skills\"

# DSH
Copy-Item -Recurse .\dsh-session-exporter-analyzer "$env:USERPROFILE\.dsh\skills\"
```

Once installed there is nothing to memorize — just state what you want in plain language and the skill triggers from its description.

## Usage examples

### In natural language

| What you say | What the skill does |
| --- | --- |
| "Export the current session to Obsidian" | DSH reads `DSH_SESSION_ID`; no ID needed |
| "Analyze the session titled '重整项目 README 及英文文档'" | Locates by title, then reads it |
| "List the sessions of the itdb-new project" | Prints title + ID + directory + time to pick from |
| "Export session `sess_5afe72b4-…` with tool calls" | Exports by ID, including tool inputs and outputs |

### Running the scripts directly

```powershell
# Locate by title (take the id when there is exactly one match)
python ".\dsh-session-exporter-analyzer\scripts\find_dsh_sessions.py" "重整项目 README 及英文文档"

# Fuzzy keyword match / list everything / narrow to one working directory
python ".\dsh-session-exporter-analyzer\scripts\find_dsh_sessions.py" "README" --contains
python ".\dsh-session-exporter-analyzer\scripts\find_dsh_sessions.py" --all
python ".\dsh-session-exporter-analyzer\scripts\find_dsh_sessions.py" --all --cwd "itdb-new"

# Export to Markdown (--current means the current session, no ID needed)
python ".\dsh-session-exporter-analyzer\scripts\export_dsh_session.py" --current --no-tools `
  --output ".\work\dsh-session.md"

# Include tool records and reasoning
python ".\dsh-session-exporter-analyzer\scripts\export_dsh_session.py" "<session id>" `
  --include-tools --include-reasoning --output ".\work\dsh-session-full.md"
```

The Codex and ZCode skills work the same way, differing only in script names and session ID shape:

```powershell
python ".\codex-session-exporter-analyzer\scripts\find_codex_threads.py" "海康 NVR 监控排错"
python ".\codex-session-exporter-analyzer\scripts\export_codex_session.py" "<UUID>" --no-tools --output ".\work\codex.md"

python ".\zcode-session-exporter-analyzer\scripts\find_zcode_sessions.py" "itdb重构项目硬件数据三模块拆分优化"
python ".\zcode-session-exporter-analyzer\scripts\export_zcode_session.py" "sess_<uuid>" --no-tools --output ".\work\zcode.md"
```

On success the script prints a JSON summary (`entries`, `user_messages`, `assistant_messages`, `tool_calls`); an `entries` count above zero means the export worked.

## Obsidian archiving conventions

All three skills embed the same archiving rules, targeting the local central Vault (on Windows, `D:\GitHub\Obsidian_jerion\Vault`):

**Location**: `<matching directory>/<tool name> 会话文档/`

- The owning directory follows the **session's subject**: project sessions go to `Projects/<project>/`, operations sessions to `服务器运维/<product>/`;
- The subdirectory **must be created first** if missing; dropping files straight into the project or product folder is not allowed;
- `Codex 会话文档` / `ZCode 会话文档` / `DSH 会话文档` are sibling directories — each tool's sessions stay in its own.

**Naming**: `YYYY-MM-DD <topic>.md`

- The date is the **session start date**, which is what a multi-day session should be filed under;
- The topic says what the session actually did, optionally prefixed with the project or product name;
- Never name a file "session export", and no session ID suffix is needed — the ID already lives in the metadata block.

**Committing**: after writing, commit inside the Vault repository and **stage only this run's files** (bulk `git add .` is forbidden); the message follows `docs: 新增 <project> 会话文档（topic）`; **pushing requires explicit human confirmation** — without an answer, the commit stays local.

## Tech stack and dependencies

| Item | Notes |
| --- | --- |
| Language | Python 3.7+ (standard library only, no third-party packages) |
| Access mode | Read-only throughout: SQLite opened with `mode=ro`, session files never written |
| DSH decompression | `zstandard` module preferred, falling back to a `zstd` executable on `PATH` |
| Output format | Markdown (UTF-8, no BOM) |
| Platforms | Windows / macOS / Linux (no platform-specific logic in the scripts) |

## FAQ

**I don't remember the full session title.**

Use fuzzy matching (`--contains`), or list every session and pick by time. When several sessions match, the skill stops and asks rather than choosing one itself.

**The export came out empty.**

Usually the session record has been cleaned up, or all of its messages were injected content — either way the script fails loudly instead of writing an empty document.

**DSH reports "缺少 zstd 解压能力".**

Install the Python `zstandard` package, or put a `zstd` executable on `PATH`.

**The exported document is missing the full script.**

The default mode exports only user and assistant messages, so a script written to a file through a tool call is not included. Re-export with `--include-tools` to bring in tool inputs and outputs.

**Could a password end up in my Obsidian notes?**

Credentials are scanned and replaced before archiving: passwords, tokens and keys become `********（已脱敏）`.

## Repository layout

```text
session-exporter-skills/
├── codex-session-exporter-analyzer/     Locate and export Codex sessions
│   ├── SKILL.md                         Skill description (triggers, usage, archival rules)
│   └── scripts/
│       ├── find_codex_threads.py        Look up thread UUIDs by title
│       └── export_codex_session.py      Export a session as Markdown
├── zcode-session-exporter-analyzer/     Locate and export ZCode sessions
│   ├── SKILL.md
│   └── scripts/
│       ├── find_zcode_sessions.py       Look up session IDs by title
│       └── export_zcode_session.py      Export a session as Markdown
├── dsh-session-exporter-analyzer/       Locate and export DSH sessions
│   ├── SKILL.md
│   └── scripts/
│       ├── find_dsh_sessions.py         Locate by title, keyword or working directory
│       └── export_dsh_session.py        Export as Markdown (streaming multi-frame zstd)
├── LICENSE
├── README.md                            简体中文
└── README.en.md                         English (this file)
```

## Documentation

| Start here | Then |
| --- | --- |
| [Quick start](#quick-start) | Copy each skill into the matching tool's skills folder |
| [The three skills](#the-three-skills) | Data sources, session ID shapes and scripts per tool |
| [Usage examples](#usage-examples) | Natural-language phrasings and command lines |
| [Obsidian archiving conventions](#obsidian-archiving-conventions) | Directory, naming, redaction and commit flow |
| Each skill's `SKILL.md` | Full field-level description and examples for that tool |
| [简体中文 README](README.md) | The same content in Chinese |

## Acknowledgements

- [Codex](https://github.com/openai/codex) · ZCode · [DeepSeek Harness](https://github.com/deepseek-ai) — the three session stores differ completely; this toolkit is built around how each of them actually works.

## License

Released under the [MIT License](LICENSE): use, modify and distribute freely, as long as the copyright and licence notice are kept.

## Contact

- **Email**: 416685476@qq.com
- **Project home**: [github.com/zyx3721](https://github.com/zyx3721)

---

**⭐ If these skills save you a trip down memory lane, a star is appreciated!**
