# 🤖 Agent guide — guap-lab-auto

Instructions for coding agents (Cursor, Claude, Copilot, **OpenClaw**, **Hermes**, etc.) working with this repository or the installed `lab-auto` CLI.

---

## What this project is

- **Package name (PyPI):** `guap-lab-auto`
- **CLI command:** `lab-auto`
- **Purpose:** Sync GUAP student tasks from `https://pro.guap.ru`, mirror them into local folders, track status in YAML, and submit PDF reports after explicit human approval.
- **Not official:** GUAP can change HTML anytime; parsers may break.

---

## Install (agent environment)

### From PyPI

```bash
pip install guap-lab-auto
playwright install chromium
```

### From this repo

```bash
pip install -e ".[dev]"
playwright install chromium
```

### Requirements

- Python **3.11+**
- **Chromium** for Playwright (`playwright install chromium`)
- Network access to `pro.guap.ru` for sync/submit/auth
- Optional: **LibreOffice** (`soffice`) or **Microsoft Word** (Windows) for `lab-auto convert`

---

## Workspace setup

1. Choose a directory for all user data (not the git clone root unless intended).
2. Run:

```bash
lab-auto workspace set /path/to/workspace
lab-auto auth login    # human must complete browser login
lab-auto auth check
lab-auto sync
```

Override for one command: `lab-auto --root /path/to/workspace sync` or env `LAB_AUTO_ROOT`.

---

## Source of truth (read first)

| File | Use |
|------|-----|
| `state/works.yaml` | Canonical task records (`work_id`, statuses, paths) |
| `state/summary.md` | Human-readable overview by subject |
| `state/needs_review.md` | Tasks in `[REVIEW]` |
| `labs/<subject>/...` | Per-task folders (reports, `task.pdf`) |
| `logs/[AI] YYYY-MM-DD.md` | Append short action notes |

**Skip** `archived: true` rows unless the user asks about archived work.

**IDs**

- CLI / YAML: `work_id` → `task-178541` (from task URL)
- `task_site_id` → `178541`
- Folder name: `[STATUS] Task title [178541]`

---

## Standard workflow (do not skip review)

```text
sync → write report in work folder → review → convert (if DOCX) → submit (only if user asked) → sync
```

| Step | Command | Agent notes |
|------|---------|-------------|
| 1 | `lab-auto sync` | Refresh from website; do not run parallel with `submit` on same task |
| 2 | Edit files in `labs/.../` | Keep artifacts inside the work folder |
| 3 | `lab-auto review <work-id>` | Required after AI changes report |
| 4 | `lab-auto convert <id> --docx report.docx` | Only if user has DOCX |
| 5 | `lab-auto submit <id> --file report.pdf` | **Only when user explicitly requests submission** |
| 6 | `lab-auto sync` | Reconcile status |

---

## Hard rules for agents

1. **Never run `submit`** unless the user clearly said the report is reviewed and should be uploaded.
2. **Never automate GUAP login** beyond documented CLI commands (no credential scraping).
3. **Do not delete** `state/works.yaml` or session files to “fix” issues — use `auth login`, `sync`, or documented repair.
4. **Prefer CLI** over reimplementing browser/parsing logic in ad-hoc scripts.
5. **Log briefly** to `logs/[AI] YYYY-MM-DD.md` on success/failure (no verbose step spam).
6. If `sync` reports **0 tasks**, treat as auth/markup problem — suggest `auth check` or `auth login`; state is left unchanged.
7. **Archived works** are frozen until `lab-auto unarchive <id>`.

---

## Command quick reference

```bash
lab-auto auth login | check | migrate-session | logout
lab-auto workspace set | show | unset
lab-auto sync [--archive]
lab-auto status [--all]
lab-auto review <work-id-or-folder>
lab-auto convert <work-id-or-folder> --docx file.docx [--output file.pdf]
lab-auto submit <work-id-or-folder> --file file.pdf
lab-auto archive | unarchive <work-id-or-folder>
```

`work-id-or-folder` can be `task-178541`, folder name, or relative folder path under the workspace.

---

## Status semantics (for merge/sync)

- `[REVIEW]`, `[SENT]`, `[SENTFAILED]` are **sticky** until the website shows `ожидает проверки` or `принят`.
- `[DONE]` is not downgraded to `[REFACTOR]` on a lagging list; resubmit → site `ожидает проверки` → local `[SENT]`.
- `[SENTFAILED]` after submit: suggest `lab-auto sync` to reconcile.

---

## Development (agents modifying this repo)

```bash
pip install -e ".[dev]"
pytest                    # unit + fixture tests (no network)
pytest -m live            # needs LAB_AUTO_ROOT + valid session
```

Layout:

```text
src/lab_auto/     # CLI and services
tests/fixtures/   # captured GUAP HTML
skills/           # optional Cursor skill for lab workflow
```

Key modules: `sync.py`, `submit.py`, `parsers.py`, `state.py`, `paths.py`, `session_store.py`, `browser.py`.

When changing parsers, update fixtures via `scripts/capture_guap_fixtures.py` after a real login.

---

## OpenClaw

1. Install skill: `./scripts/install-agent-skills.sh` or use this repo as workspace (`skills/` + `.agents/skills/`).
2. Enable in `~/.openclaw/openclaw.json` — see `examples/openclaw.json.example`.
3. Set `LAB_AUTO_ROOT` under `skills.entries.guap-lab-workflow.env`.
4. Enable **terminal/shell** tools for the agent.
5. Invoke: `/guap-lab-workflow <request>`.

Details: [docs/integrations/openclaw.md](docs/integrations/openclaw.md)

## Hermes Agent

1. Install skill: `./scripts/install-agent-skills.sh` → `~/.hermes/skills/guap-lab-workflow/`.
2. Or add `skills.external_dirs` — see `examples/hermes-skills.example.yaml`.
3. Set `LAB_AUTO_ROOT` in `~/.hermes/.env`.
4. Run with terminal: `hermes chat --toolsets terminal,skills -s guap-lab-workflow`.
5. Invoke: `/guap-lab-workflow <request>`.

Details: [docs/integrations/hermes.md](docs/integrations/hermes.md)

## AgentSkills skill (canonical)

| Path | Role |
|------|------|
| `skills/guap-lab-workflow/SKILL.md` | Main skill (edit here) |
| `skills/guap-lab-workflow/references/` | Command + integration refs |
| `.agents/skills/guap-lab-workflow/` | OpenClaw project-agent copy (re-sync via install script) |

After editing the skill, run `scripts/install-agent-skills.sh` to update `~/.hermes/skills` and `~/.openclaw/skills`.

---

## Publishing reminder

PyPI distribution name is **`guap-lab-auto`**; entry point remains **`lab-auto`**. See README § Publishing.
