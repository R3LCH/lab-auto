---
name: guap-lab-workflow
description: Sync GUAP student lab tasks with lab-auto, edit reports in local folders, mark REVIEW, convert DOCX to PDF, and submit only after explicit human approval.
version: 1.0.0
platforms: [macos, linux, windows]
metadata: {"openclaw":{"emoji":"🎓","homepage":"https://github.com/your-org/lab_automation","requires":{"bins":["lab-auto"]}},"hermes":{"emoji":"🎓","tags":["guap","education","labs","cli"],"category":"education","requires_toolsets":["terminal"]}}
---

# GUAP lab workflow (`lab-auto`)

Use this skill when the user works on **GUAP** (`pro.guap.ru`) student lab tasks via the **`lab-auto`** CLI (PyPI package `guap-lab-auto`).

> Unofficial tool — not affiliated with GUAP. Portal HTML changes can break parsers.

## When to use

- Sync tasks from the GUAP website into local folders
- Write or update lab reports in the workspace
- Mark work ready for human review (`[REVIEW]`)
- Convert DOCX → PDF and submit PDFs **only when the user explicitly requests submission**

## Prerequisites

```bash
pip install guap-lab-auto
playwright install chromium
lab-auto workspace set /path/to/workspace
lab-auto auth login    # human completes browser login
```

Optional: LibreOffice (`soffice`) or Microsoft Word (Windows) for `lab-auto convert`.

Environment overrides: `LAB_AUTO_ROOT`, `LAB_AUTO_SESSION_KEY` (advanced).

## Source of truth (read first)

| Path | Purpose |
|------|---------|
| `{workspace}/state/works.yaml` | Canonical records (`work_id`, statuses) |
| `{workspace}/state/summary.md` | Overview by subject |
| `{workspace}/state/needs_review.md` | Tasks in `[REVIEW]` |
| `{workspace}/labs/<subject>/...` | Per-task folders |
| `{workspace}/logs/[AI] YYYY-MM-DD.md` | Short action log |

**IDs:** CLI uses `work_id` like `task-178541`; folders look like `[STATUS] Task title [178541]`.

Skip `archived: true` unless the user asks about archived work.

## Standard procedure

1. `lab-auto sync` — refresh from website (do not run parallel with `submit` on the same task)
2. Edit report files inside the task folder under `labs/`
3. `lab-auto review <work-id>` — after AI or user edits the report
4. `lab-auto convert <work-id> --docx report.docx` — if the deliverable is DOCX
5. `lab-auto submit <work-id> --file report.pdf` — **only if the user clearly asked to submit**
6. `lab-auto sync` — reconcile status

## Commands (terminal)

Use the **terminal** toolset / shell. All commands accept `work-id` (`task-178541`), folder name, or relative folder path.

```bash
lab-auto auth login | check | migrate-session | logout
lab-auto workspace set | show | unset <path>
lab-auto sync [--archive]
lab-auto status [--all]
lab-auto review <work-id>
lab-auto convert <work-id> --docx file.docx [--output file.pdf]
lab-auto submit <work-id> --file file.pdf
lab-auto archive | unarchive <work-id>
```

One-shot workspace: `lab-auto --root /path/to/workspace sync`

## Status prefixes

| Prefix | Meaning |
|--------|---------|
| `[REFACTOR]` | Not accepted on website |
| `[REVIEW]` | Local — ready for human review |
| `[SENT]` | Submitted, awaiting teacher |
| `[SENTFAILED]` | Upload failed — suggest `sync` |
| `[DONE]` | Accepted |
| `[UNDONE]` | No website status yet (`—` on GUAP) |
| `[UNKNOWN]` | Unrecognized website status |

`[REVIEW]`, `[SENT]`, and `[SENTFAILED]` stay sticky until the website shows `ожидает проверки` or `принят`.

## Hard rules

1. **Never** run `submit` without explicit user approval after review.
2. **Never** scrape credentials or bypass `lab-auto auth login`.
3. **Never** delete `state/works.yaml` or session files to “fix” issues.
4. If `sync` parses **0 tasks**, suggest `lab-auto auth check` — state is unchanged.
5. Log only brief success/failure lines to `logs/[AI] YYYY-MM-DD.md`.

## Pitfalls

- **Session:** encrypted at `session/storage_state.json`; key in user config dir. Use `lab-auto auth migrate-session` for legacy files.
- **Submit lag:** detail page may lag → `[SENTFAILED]`; run `lab-auto sync`.
- **Concurrency:** avoid `sync` + `submit` on the same task simultaneously.

## Verification

- `lab-auto status` shows expected prefix and `work_id`
- Folder name matches `[STATUS] … [site-id]`
- After submit + sync, website status aligns with `[SENT]` or `[DONE]`

## More detail

- Repo agent guide: `AGENTS.md`
- Command reference: `{baseDir}/references/commands.md`
- OpenClaw setup: `{baseDir}/references/openclaw.md`
- Hermes setup: `{baseDir}/references/hermes.md`
