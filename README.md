# 🎓 guap-lab-auto

**Unofficial CLI for GUAP students** — sync tasks from [pro.guap.ru](https://pro.guap.ru), mirror them into local folders, track state in YAML, convert DOCX → PDF, and submit reports via Playwright.

This repository also ships an **[AgentSkills](https://agentskills.io/) skill** (`guap-lab-workflow`) so coding agents ([OpenClaw](https://documentation.openclaw.ai/), [Hermes](https://hermes-agent.nousresearch.com/), Cursor, etc.) run **`lab-auto` commands** against your workspace instead of scraping GUAP in the browser themselves.

[English](README.md) · [Русский](README.ru.md)

> ⚠️ Not affiliated with GUAP. HTML on the portal can change and break parsers. Use at your own risk.

| | |
|---|---|
| 📦 **PyPI** | `guap-lab-auto` |
| ⌨️ **Command** | `lab-auto` |
| 🤖 **Agent skill** | `guap-lab-workflow` → `skills/guap-lab-workflow/` |
| 🐍 **Python** | 3.11+ |
| 🌐 **Portal** | `https://pro.guap.ru/inside/student/tasks/` |

---

## ✨ Features

- 🔄 **Sync** — scrape task list (100 rows/page), update `state/works.yaml`, rename `labs/<subject>/[STATUS] …/` folders
- 📥 **Downloads** — `task.pdf` (assignment); `reports/site-report-<id>.pdf` on first sync when GUAP has a submission (`не принят` / `ожидает проверки` / `принят`)
- 📝 **Task descriptions** — Markdown in `works.yaml` and generated `task.md`, preserving paragraphs, line breaks, lists, and links. Each sync fetches every active task's detail page once, including tasks with a cached PDF. `task.md` is regenerated when its content changes; local edits may be overwritten. Archived tasks are left untouched.
- **Teachers** — task links identify teacher profiles; the full name and unique positions are fetched once and persisted in `state/teachers.yaml`, shared across tasks and future syncs. `works.yaml` stores the task's teacher, and `task.md` includes the full name and a report-ready label (position, surname and initials). Failed profile loads are retried on a later sync; the task-link name remains available meanwhile.
  The `subjects` list in `teachers.yaml` explicitly records each subject's name, site ID, teacher name, unique positions, profile URL and report label. Once assigned, a subject keeps its teacher; only new subjects need a lookup. Existing cached profiles are reused for new subjects too.
- 🏷️ **Status mapping** — GUAP labels → `[UNDONE]` / `[REFACTOR]` / `[SENT]` / `[DONE]` / `[UNKNOWN]`; local-only `[REVIEW]` / `[SENTFAILED]`
- 📋 **State files** — `works.yaml`, `summary.md`, `needs_review.md`, append-only logs
- 🔐 **Session** — Fernet-encrypted Playwright `storage_state`; SSO via `auth login` (headed browser)
- 📄 **Convert** — DOCX → PDF (LibreOffice / Windows Word)
- 📤 **Submit** — upload PDF on task detail page after `[REVIEW]`
- 🗄️ **Archive** — `sync --archive` or `archive` / `unarchive` for tasks gone from the list
- 🤖 **Bundled agent skill** — [`skills/guap-lab-workflow/`](skills/guap-lab-workflow/SKILL.md) documents commands, workspace layout, and guardrails so agents invoke `lab-auto` correctly

---

## 🤖 Agent skill (`guap-lab-workflow`)

The CLI is the only supported integration surface for automation. The skill teaches agents **which `lab-auto` subcommands to run**, where `works.yaml` and lab folders live, and when human login or explicit approval is required (e.g. no unsupervised `submit`).

| Path | Purpose |
|------|---------|
| [`skills/guap-lab-workflow/SKILL.md`](skills/guap-lab-workflow/SKILL.md) | Main skill — workflow, prerequisites, rules |
| [`skills/guap-lab-workflow/references/`](skills/guap-lab-workflow/references/) | Command cheat sheet, OpenClaw/Hermes notes |
| [`.agents/skills/guap-lab-workflow/`](.agents/skills/guap-lab-workflow/) | Copy for Cursor / compatible agent loaders |
| [`AGENTS.md`](AGENTS.md) | Maintainer-oriented agent guide for this repo |
| [`docs/integrations/`](docs/integrations/) | OpenClaw & Hermes setup |

**Requirements for agents:** `lab-auto` on `PATH`, Chromium (`playwright install chromium`), and a configured workspace. The skill does not replace the CLI — it orchestrates it.

### Install the skill

```bash
# Linux / macOS — copies into OpenClaw workspace and ~/.hermes/skills/
./scripts/install-agent-skills.sh

# Windows
.\scripts\install-agent-skills.ps1
```

Or symlink / copy `skills/guap-lab-workflow/` into your agent’s skills directory manually.

| Agent | Invoke after install |
|-------|----------------------|
| OpenClaw | `/guap-lab-workflow` (slash command) |
| Hermes | `/guap-lab-workflow` or `hermes -s guap-lab-workflow` |
| Cursor | Load via `.agents/skills/` or project rules pointing at the skill |

Example configs: [`examples/openclaw.json.example`](examples/openclaw.json.example), [`examples/hermes-skills.example.yaml`](examples/hermes-skills.example.yaml).

---

## 🚀 Quick start

### Install

```bash
pip install guap-lab-auto
playwright install chromium
```

```bash
uv tool install guap-lab-auto
playwright install chromium
```

### First run

```bash
lab-auto workspace set ~/guap-labs
lab-auto auth login
lab-auto auth check
lab-auto sync
lab-auto status
```

`auth login` uses a visible Chromium window; `sync`, `submit`, and `auth check` run headless.

---

## 📁 Workspace

Additional task materials are downloaded to each work's `materials/` directory when
an HTTP(S) response identifies a file by its filename or MIME type. Redirects are
followed; HTML landing pages and ambiguous responses remain external links in
`task.md`. Public Yandex Disk links use the official public API, for single files
only; folders, authentication and captcha are not handled. No GUAP cookies are
sent to external hosts. Downloads are limited to 100 MiB, with a 20-second socket
timeout and a 60-second streaming limit.

Existing files use conditional HTTP requests (ETag/Last-Modified), or Yandex
resource versions, to detect updates. Without validators, a cached file is reused
until its source URL changes or the local file is removed. Errors retain the last
local copy and its original source, log a warning, and do not stop sync. Files are
not unpacked, executed or removed automatically.

User data stays outside the package:

```
workspace/
├── labs/
│   └── <subject>/
│       └── [SENT] Lab title [178541]/
│           ├── task.md
│           ├── task.pdf
│           └── reports/
│               └── site-report-5283063.pdf
├── materials/
│   └── <subject>/
│       └── Subject material.pdf
├── state/
│   ├── works.yaml
│   ├── materials.yaml
│   ├── summary.md
│   └── needs_review.md
├── session/
│   └── storage_state.json    # encrypted
└── logs/
```

**Root resolution:** `--root` → `LAB_AUTO_ROOT` → `lab-auto workspace set` → current directory.

| Platform | Config |
|----------|--------|
| Windows | `%APPDATA%\lab-auto\config.yaml` |
| Linux / macOS | `~/.config/lab-auto/config.yaml` |

Session key: `%APPDATA%\lab-auto\session.key` or `~/.config/lab-auto/session.key`.

`sync` loads the GUAP Materials page once and stores each shared subject file once
under `materials/<subject>/`. Its independent state lives in `state/materials.yaml`.
Works use the GUAP subject ID when available, with normalized exact-name matching
for legacy records; semantic and fuzzy matching are not used. A removed entry is
marked `present: false` and retained on disk. If the page is unavailable, the
previous materials state is left unchanged.

---

## ⌨️ Commands

Typer help: `lab-auto --help`, `lab-auto auth --help`, `lab-auto <command> --help`.

**Globals:** `--root` / `LAB_AUTO_ROOT`, `-v` / `--verbose`.

### 📂 Workspace

| Command | Description |
|---------|-------------|
| `workspace set <path>` | Save default workspace directory |
| `workspace show` | Print active + saved workspace and config path |
| `workspace unset` | Clear saved default |

### 🔑 Auth

| Command | Description |
|---------|-------------|
| `auth login` | SSO in headed browser; save encrypted session |
| `auth check` | Headless check: can open task list |
| `auth import-cookie <file>` | Import Playwright storage JSON or cookie list |
| `auth migrate-session` | Re-wrap legacy plaintext session as encrypted |
| `auth logout` | Remove `session/storage_state.json` |

### 🔄 Workflow

| Command | Description |
|---------|-------------|
| `sync` | Fetch list + details; merge `works.yaml`; download missing PDFs |
| `sync --archive` | Mark tasks removed from site as `archived: true` instead of dropping |
| `status` | Print active works grouped by subject |
| `status --all` | Include archived rows |
| `review <id>` | Set local status `[REVIEW]` and rename folder |
| `convert <id> --docx PATH` | DOCX → PDF (`--output` optional) |
| `submit <id> --file PATH` | Upload PDF; set `[SENT]` or `[SENTFAILED]` |
| `archive <id>` | `archived: true`, hide from default `status` |
| `unarchive <id>` | Restore to active list |

`<id>`: `task-178541` or a unique substring of the folder title.

**Typical sequence:** `sync` → edit report in work folder → `review` → `convert` → `submit` → `sync`.

---

## 🏷️ Folder statuses

| Prefix | GUAP / local |
|--------|----------------|
| `[UNDONE]` | Status column `—` (not submitted) |
| `[REFACTOR]` | `не принят` |
| `[SENT]` | `ожидает проверки` |
| `[DONE]` | `принят` |
| `[UNKNOWN]` | Any other portal status string |
| `[REVIEW]` | Local only — you marked ready to check |
| `[SENTFAILED]` | Local only — last `submit` failed |

**IDs:** `work_id` = `task-<site_id>`; folder = `[STATUS] <title> [<site_id>]`.

`sync` renames folders when mapped status changes. Report import runs once per work when `reports/` is empty and site status is `не принят`, `ожидает проверки`, or `принят`.

---

## 🔐 Session

- Password is prompted once in `auth login` and not written to disk.
- Cookies: `workspace/session/storage_state.json` (encrypted wrapper).
- `auth migrate-session` upgrades old plaintext exports.

---

## 🧪 Development

```bash
git clone https://github.com/R3LCH/lab_auto.git
cd lab_auto
python -m venv .venv
# Windows: .\.venv\Scripts\Activate.ps1
# Unix:    source .venv/bin/activate
pip install -e ".[dev]"
playwright install chromium
pytest
```

Live tests (network + saved session):

```bash
export LAB_AUTO_ROOT=~/guap-labs
pytest -m live
```

### Fixtures

Default: synthetic HTML in `tests/conftest.py`. Optional local capture (gitignored):

```bash
python scripts/capture_guap_fixtures.py --task-url "https://pro.guap.ru/inside/student/tasks/<id>"
```

Do not commit `tests/fixtures/task_list.html` or `task_detail.html`. See [tests/fixtures/README.md](tests/fixtures/README.md).

🤖 Agent skill source lives under `skills/guap-lab-workflow/` — see [AGENTS.md](AGENTS.md) when changing CLI behavior agents depend on.

---

## 📜 License

MIT — [LICENSE](LICENSE).
