<div align="center">

<h1>Session Exporter Skills</h1>

<p><b>AI 编码会话导出工具集</b> — Codex · ZCode · DSH 三端会话：定位 · 导出 · 归档</p>

<p><b>简体中文</b> · <a href="README.en.md">English</a></p>

写过的会话，事后往往最难找：当时的判断依据、踩过的坑、最终改成了什么样，都留在聊天记录里。
三个技能把这条链路固化下来——**按标题或会话 ID 定位 → 导出为 Markdown → 脱敏 → 归档进 Obsidian → 提交到 Git**。
全程只读，不改动任何原会话。

<p>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-blue?labelColor=1f2937" alt="MIT License"></a>
  <a href="https://www.python.org/"><img src="https://img.shields.io/badge/Python-3.7+-3776AB?logo=python&logoColor=white&labelColor=1f2937" alt="Python 3.7+"></a>
  <img src="https://img.shields.io/badge/skills-3-059669?labelColor=1f2937" alt="3 skills">
  <img src="https://img.shields.io/badge/Codex%20%C2%B7%20ZCode%20%C2%B7%20DSH-supported-7c3aed?labelColor=1f2937" alt="Codex ZCode DSH">
</p>

<p>
  <b><a href="#输出样例">输出样例</a></b> ·
  <a href="#它做什么">它做什么</a> ·
  <a href="#三个技能">三个技能</a> ·
  <a href="#怎么工作">怎么工作</a> ·
  <a href="#快速开始">快速开始</a> ·
  <a href="#使用示例">使用示例</a> ·
  <a href="#归档到-obsidian-的约定">归档约定</a> ·
  <a href="#常见问题">常见问题</a> ·
  <a href="#项目结构">项目结构</a> ·
  <a href="#文档">文档</a>
</p>

</div>

---

三个 AI 编码工具的会话存在三个互不相通的存储里：Codex 用 SQLite 索引配 rollout JSONL，ZCode 用一张 SQLite 库，DSH 则把每个会话压成多帧 zstd 的 JSONL。想翻旧账，就得分别去啃各自的格式。这套技能把差异吃掉，对外只留一致的用法：**给标题或 ID，拿回一份可读、可检索、可长期保存的 Markdown**。

## 输出样例

导出结果的格式在三个技能间保持一致——会话元信息一段，正文按角色交替、带时间戳，助手调用过哪些工具以一行提示带出：

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

默认只导用户与助手消息，工具调用压缩成一行名称；需要排障细节时再加 `--include-tools` 取出完整入参与输出，需要思考过程时加 `--include-reasoning`。

## 它做什么

- **跨三端定位会话** — 按完整标题精确匹配，或按关键词模糊匹配；同名多条时列出候选项交人确认，绝不擅自代选。
- **导出为干净的 Markdown** — 统一元信息块 + 角色分段，时间戳可读，长输出自动截断并标注省略长度。
- **自动剔除注入噪音** — 各工具都会往会话里塞系统提示、规则更新、运行时上下文、技能目录；导出时按来源字段识别并剔除，留下人真正说过和真正答过的内容。
- **归档进 Obsidian** — 按「对应目录 / `<工具名> 会话文档` / `YYYY-MM-DD 主题.md`」落盘，目录不存在先建，不与其他工具的会话混放。
- **归档前凭据脱敏** — 口令、token、密钥、私钥命中即替换为 `********（已脱敏）`，内网地址与账号名作为排障上下文保留。
- **落盘后自动提交** — 只 `git add` 本次文件，禁用批量添加，避免把仓库里的在途改动一并裹进提交；**推送前必须经人确认**。

**它不是** 会话备份工具，也不做云端同步：只在需要时把某一段会话取出来变成文档，原会话存储一个字节都不动。

## 三个技能

| 技能 | 目标工具 | 会话 ID 形态 | 数据来源 | 脚本 |
| --- | --- | --- | --- | --- |
| `codex-session-exporter-analyzer` | Codex | 裸 UUID | `~/.codex/state_5.sqlite` + `~/.codex/sessions/**/rollout-*.jsonl` | `find_codex_threads.py` · `export_codex_session.py` |
| `zcode-session-exporter-analyzer` | ZCode | `sess_<uuid>` | `~/.zcode/cli/db/db.sqlite`（`session` / `message` / `part` 表） | `find_zcode_sessions.py` · `export_zcode_session.py` |
| `dsh-session-exporter-analyzer` | DSH | `session-<uuid>` | `~/.dsh/sessions/<cwd slug>/<会话 ID>/session.v3.jsonl.zstd` | `find_dsh_sessions.py` · `export_dsh_session.py` |

每个技能各含两个脚本：`find_*` 负责定位并输出候选清单（JSON），`export_*` 负责导出 Markdown 并回报条目统计。

DSH 一路有三处硬骨头，已在脚本里处理妥当：会话文件是**随会话增长追加的多帧 zstd**（一次性解压只能拿到第一帧，故按流式读取，并备了 `zstandard` 模块与 `zstd` 命令行两条回退）；同一会话目录可能同时留有旧格式与迁移后的新格式文件（按版本号取新）；注入消息以 `user/message` 形式混入（按 `data.source.kind` 判别剔除）。

## 怎么工作

```text
      用户提问：“把某个会话导出到 Obsidian”
                    │
                    ▼
        ┌───────────────────────────┐
        │  技能按标题/ID 定位会话      │
        │  find_*  → 候选清单(JSON)  │
        └───────────────────────────┘
                    │
                    ▼
        ┌───────────────────────────┐
        │  只读解析原会话存储          │
        │  剔除注入 · 还原角色与时间    │
        └───────────────────────────┘
                    │
                    ▼
        ┌───────────────────────────┐
        │  生成 Markdown 会话文档      │
        │  凭据脱敏 · 长输出截断        │
        └───────────────────────────┘
                    │
                    ▼
        ┌───────────────────────────┐
        │  归档进 Vault 的会话文档目录  │
        │  <工具名> 会话文档/日期 主题.md│
        └───────────────────────────┘
                    │
                    ▼
        ┌───────────────────────────┐
        │  本地提交（只加本次文件）      │
        │  推送 → 等用户确认            │
        └───────────────────────────┘
```

- **定位**：标题精确匹配优先，为空再退到模糊匹配；仍为空则列出全部会话供挑选。
- **解析**：全程只读，按各工具的记录类型提取用户与助手消息，并剥离系统注入内容。
- **落盘**：目标目录按会话主题判定，而不是机械照搬工作目录——会话内容与所在目录不一致时，以内容为准。
- **提交**：只提交本次落盘的文档，推送留给人拍板。

## 快速开始

只需要 **Python 3.7+**，无第三方依赖（DSH 解压多帧 zstd 时优先用 `zstandard` 模块，没有则回退到 `zstd` 命令行）。

把技能目录复制到对应工具的 skills 目录下即可：

```powershell
# Codex
Copy-Item -Recurse .\codex-session-exporter-analyzer "$env:USERPROFILE\.codex\skills\"

# ZCode
Copy-Item -Recurse .\zcode-session-exporter-analyzer "$env:USERPROFILE\.zcode\skills\"

# DSH
Copy-Item -Recurse .\dsh-session-exporter-analyzer "$env:USERPROFILE\.dsh\skills\"
```

装好后不必记命令——直接用自然语言提出需求，技能会按描述自动触发。

## 使用示例

### 用自然语言

| 说法 | 技能动作 |
| --- | --- |
| “把当前会话导出到 Obsidian” | DSH 读 `DSH_SESSION_ID`，无需提供 ID |
| “分析标题为『重整项目 README 及英文文档』的会话” | 按标题定位后通读分析 |
| “列出 itdb-new 项目的会话” | 列标题 + ID + 目录 + 时间，供挑选 |
| “导出会话 `sess_5afe72b4-…`，含工具调用” | 按 ID 导出，带工具入参与输出 |

### 直接跑脚本

```powershell
# 按标题定位（唯一匹配时取 id）
python ".\dsh-session-exporter-analyzer\scripts\find_dsh_sessions.py" "重整项目 README 及英文文档"

# 关键词模糊匹配 / 列出全部 / 限定工作目录
python ".\dsh-session-exporter-analyzer\scripts\find_dsh_sessions.py" "README" --contains
python ".\dsh-session-exporter-analyzer\scripts\find_dsh_sessions.py" --all
python ".\dsh-session-exporter-analyzer\scripts\find_dsh_sessions.py" --all --cwd "itdb-new"

# 导出为 Markdown（--current 表示当前会话，免输 ID）
python ".\dsh-session-exporter-analyzer\scripts\export_dsh_session.py" --current --no-tools `
  --output ".\work\dsh-session.md"

# 需要工具记录与思考过程时
python ".\dsh-session-exporter-analyzer\scripts\export_dsh_session.py" "<会话 ID>" `
  --include-tools --include-reasoning --output ".\work\dsh-session-full.md"
```

Codex 与 ZCode 的技能用法同构，只是脚本名与会话 ID 形态不同：

```powershell
python ".\codex-session-exporter-analyzer\scripts\find_codex_threads.py" "海康 NVR 监控排错"
python ".\codex-session-exporter-analyzer\scripts\export_codex_session.py" "<UUID>" --no-tools --output ".\work\codex.md"

python ".\zcode-session-exporter-analyzer\scripts\find_zcode_sessions.py" "itdb重构项目硬件数据三模块拆分优化"
python ".\zcode-session-exporter-analyzer\scripts\export_zcode_session.py" "sess_<uuid>" --no-tools --output ".\work\zcode.md"
```

导出成功后脚本会打印 JSON 统计（`entries`、`user_messages`、`assistant_messages`、`tool_calls`），`entries` 大于零才算成功。

## 归档到 Obsidian 的约定

三个技能对内嵌了同一套归档口径，落在本机中央 Vault（Windows 为 `D:\GitHub\Obsidian_jerion\Vault`）：

**位置**：`<对应目录>/<工具名> 会话文档/`

- 归属目录按**会话主题**判定：项目类会话放 `Projects/<项目名>/`，运维类放 `服务器运维/<产品名>/`；
- 子目录不存在时**先创建**，不允许把文件直接丢在项目或产品目录下；
- `Codex 会话文档` / `ZCode 会话文档` / `DSH 会话文档` 互为姊妹目录，各归各家，不混放。

**命名**：`YYYY-MM-DD <主题>.md`

- 日期取**会话开始日**，跨天会话以开始日为准；
- 主题写清这段会话做了什么，可带项目或产品名前缀；
- 不用「会话导出」当文件名，也不必附会话 ID——ID 已在文档元信息里。

**提交**：落盘后在 Vault 仓库内自动提交，且**只添加本次文件**（禁止 `git add .`）；提交信息为 `docs: 新增 <项目> 会话文档（主题）`；**推送必须经人确认**，未获答复只停在本地提交。

## 技术栈与依赖

| 项 | 说明 |
| --- | --- |
| 语言 | Python 3.7+（标准库为主，无第三方依赖） |
| 读取方式 | 全程只读：SQLite 以 `mode=ro` 打开，会话文件不写入 |
| DSH 解压 | 优先 `zstandard` 模块，回退到 PATH 中的 `zstd` 可执行文件 |
| 输出格式 | Markdown（UTF-8 无 BOM） |
| 运行平台 | Windows / macOS / Linux（脚本不含平台特定逻辑） |

## 常见问题

**会话标题记不全怎么办？**

用关键词模糊匹配（`--contains`），或直接列出全部会话按时间认。技能在匹配到多条时会停下来让用户指认，不会自行挑一条。

**导出内容为空？**

多为会话记录已被清理，或该会话的消息全是系统注入内容——两种情况下脚本都会明确报错，不会写出一份空文档。

**DSH 报「缺少 zstd 解压能力」？**

安装 Python 包 `zstandard`，或把 `zstd` 可执行文件加入 PATH 即可。

**导出的文档里没有完整脚本？**

默认模式只导用户与助手消息，脚本正文若是在工具调用中写入文件的，不在此列。加 `--include-tools` 重导一次即可带出工具的入参与输出。

**导入 Obsidian 的笔记会不会带出密码？**

归档前会做凭据扫描与替换；若命中口令、token、密钥，统一替换为 `********（已脱敏）`。

## 项目结构

```text
session-exporter-skills/
├── codex-session-exporter-analyzer/     Codex 会话定位与导出
│   ├── SKILL.md                         技能说明（触发条件、用法、归档约定）
│   └── scripts/
│       ├── find_codex_threads.py        按标题查询 thread UUID
│       └── export_codex_session.py      导出会话为 Markdown
├── zcode-session-exporter-analyzer/     ZCode 会话定位与导出
│   ├── SKILL.md
│   └── scripts/
│       ├── find_zcode_sessions.py       按标题查询会话 ID
│       └── export_zcode_session.py      导出会话为 Markdown
├── dsh-session-exporter-analyzer/       DSH 会话定位与导出
│   ├── SKILL.md
│   └── scripts/
│       ├── find_dsh_sessions.py         按标题/关键词/工作目录定位
│       └── export_dsh_session.py        导出会话为 Markdown（多帧 zstd 流式解压）
├── LICENSE
├── README.md                            中文说明（本文件）
└── README.en.md                         English
```

## 文档

| 先看这个 | 再往下 |
| --- | --- |
| [快速开始](#快速开始) | 把技能复制到各工具的 skills 目录 |
| [三个技能](#三个技能) | 各技能的数据来源、会话 ID 形态与脚本对照 |
| [使用示例](#使用示例) | 自然语言说法与命令行用法 |
| [归档到 Obsidian 的约定](#归档到-obsidian-的约定) | 目录、命名、脱敏与提交流程 |
| 各技能内的 `SKILL.md` | 该工具的完整字段级说明与示例 |
| [English README](README.en.md) | 同样的内容，英文版 |

## 致谢

- [Codex](https://github.com/openai/codex) · ZCode · [DeepSeek Harness](https://github.com/deepseek-ai) — 三端会话存储格式各不相同，本工具集围绕它们各自的真实结构实现。

## 许可证

本项目采用 [MIT License](LICENSE) 开源协议，可自由使用、修改与分发，只需保留版权声明与许可声明。

## 联系方式

- **Email**：416685476@qq.com
- **项目主页**：[github.com/zyx3721](https://github.com/zyx3721)

---

**⭐ 如果这套技能帮您省下了翻旧账的时间，欢迎 Star 支持！**
