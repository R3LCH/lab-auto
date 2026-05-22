# 🎓 guap-lab-auto

**Unofficial CLI for GUAP students** — sync lab tasks from [pro.guap.ru](https://pro.guap.ru), organize work on disk, convert DOCX → PDF, and submit reports after human review.

> ⚠️ Not affiliated with GUAP. The portal UI can change and break automation. Use at your own risk.

| | |
|---|---|
| 📦 **PyPI** | `guap-lab-auto` |
| ⌨️ **Command** | `lab-auto` |
| 🐍 **Python** | 3.11+ |
| 🌐 **Portal** | GUAP student tasks |

---

## ✨ Features

- 🔄 **Sync** tasks into `labs/<subject>/[STATUS] Title [id]/`
- 📋 **YAML state** + Markdown summaries for humans and agents
- 🔐 **Encrypted session** (Fernet) — cookies never stored as plain JSON
- 📤 **Submit PDFs** via Playwright after you mark work `[REVIEW]`
- 📄 **DOCX → PDF** (LibreOffice, or Microsoft Word on Windows)
- 🗄️ **Archive** tasks that disappear from the website
- 🤖 **OpenClaw & Hermes** — AgentSkills skill `guap-lab-workflow` (`/guap-lab-workflow`)

---

## 🤖 AI agents (OpenClaw & Hermes)

This repo ships an [AgentSkills](https://agentskills.io/) skill so agents run **`lab-auto`** instead of scraping GUAP directly.

| Agent | Install skill | Invoke |
|-------|---------------|--------|
| [OpenClaw](https://documentation.openclaw.ai/) | `skills/` in workspace or `./scripts/install-agent-skills.sh` | `/guap-lab-workflow` + terminal tools |
| [Hermes](https://hermes-agent.nousresearch.com/) | same script → `~/.hermes/skills/` | `/guap-lab-workflow` or `hermes -s guap-lab-workflow` |

```bash
# Linux / macOS
./scripts/install-agent-skills.sh

# Windows
.\scripts\install-agent-skills.ps1
```

Config examples: `examples/openclaw.json.example`, `examples/hermes-skills.example.yaml`  
Full guides: [docs/integrations/](docs/integrations/) · [AGENTS.md](AGENTS.md)

---

## 🚀 Quick start

### Install

```bash
pip install guap-lab-auto
playwright install chromium
```

With [uv](https://docs.astral.sh/uv/):

```bash
uv tool install guap-lab-auto
playwright install chromium
```

From Git (no PyPI):

```bash
pip install "guap-lab-auto @ git+https://github.com/your-org/lab_automation.git"
playwright install chromium
```

### First run

```bash
lab-auto workspace set ~/guap-labs
lab-auto auth login
lab-auto sync
lab-auto status
```

---

## 📁 Workspace

Data lives in your workspace (not inside the package):

```
workspace/
├── labs/          # one folder per task
├── state/         # works.yaml, summary.md
├── session/       # encrypted browser session
└── logs/          # action logs
```

**Resolution order:** `--root` / `LAB_AUTO_ROOT` → saved default (`lab-auto workspace set`) → current directory.

| Platform | Saved config |
|----------|----------------|
| Windows | `%APPDATA%\lab-auto\config.yaml` |
| Linux / macOS | `~/.config/lab-auto/config.yaml` |

---

## ⌨️ Commands

| Area | Command |
|------|---------|
| 🔑 Auth | `lab-auto auth login` · `check` · `migrate-session` · `import-cookie` · `logout` |
| 📂 Workspace | `lab-auto workspace set\|show\|unset <path>` |
| 🔄 Sync | `lab-auto sync` · `sync --archive` |
| 📊 Status | `lab-auto status` · `status --all` |
| ✅ Workflow | `lab-auto review <id>` · `convert <id> --docx f.docx` · `submit <id> --file f.pdf` |
| 🗄️ Archive | `lab-auto archive <id>` · `unarchive <id>` |

Use `lab-auto --help` and `lab-auto <command> --help` for details.

---

## 🏷️ Folder statuses

| Prefix | Meaning |
|--------|---------|
| `[REFACTOR]` | Not accepted on the website |
| `[REVIEW]` | Ready for your review (local) |
| `[SENT]` | Submitted, awaiting teacher |
| `[SENTFAILED]` | Upload failed (retry or `sync`) |
| `[DONE]` | Accepted |
| `[UNKNOWN]` | Unrecognized website status |

**IDs:** CLI / `works.yaml` use `task-178541`; folders look like `[SENT] Lab title [178541]`.

---

## 🔐 Session security

- Cookies stored in `session/storage_state.json` (encrypted).
- Key: `%APPDATA%\lab-auto\session.key` (Windows) or `~/.config/lab-auto/session.key` (Unix).
- Upgrade legacy files: `lab-auto auth migrate-session`
- Password is **not** saved (browser login only).

---

## 🧪 Development

```bash
git clone https://github.com/your-org/lab_automation.git
cd lab_automation
python -m venv .venv
source .venv/bin/activate   # Windows: .\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
playwright install chromium
pytest
```

Optional live test (real GUAP session):

```bash
export LAB_AUTO_ROOT=~/guap-labs
pytest -m live
```

### Test fixtures (local only)

Tests use **synthetic** HTML in `tests/conftest.py` (safe to commit). To capture real pages locally (gitignored):

```bash
python scripts/capture_guap_fixtures.py --task-url "https://pro.guap.ru/inside/student/tasks/<id>"
```

Never commit `tests/fixtures/task_list.html` or `task_detail.html`. See `tests/fixtures/README.md`.

🤖 **AI agents:** see [AGENTS.md](AGENTS.md).

---

## 📦 Publishing to PyPI

Maintainers only.

1. Create accounts on [PyPI](https://pypi.org) and [TestPyPI](https://test.pypi.org).
2. Ensure the name `guap-lab-auto` is still available.
3. Update `[project.urls]` in `pyproject.toml` with your real GitHub URL.
4. Build and upload:

```bash
pip install build twine
python -m build
twine upload dist/*
```

Dry run on TestPyPI:

```bash
twine upload --repository testpypi dist/*
pip install --index-url https://test.pypi.org/simple/ guap-lab-auto
```

With uv:

```bash
uv build
uv publish
```

Bump `version` in `pyproject.toml` for each release.

---

## 📜 License

MIT — see [LICENSE](LICENSE).
