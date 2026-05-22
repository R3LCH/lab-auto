# lab-auto command reference

Install: `pip install guap-lab-auto` · CLI: `lab-auto` · Python 3.11+

## Auth

| Command | Description |
|---------|-------------|
| `lab-auto auth login` | Interactive GUAP login (stores encrypted session) |
| `lab-auto auth check` | Validate session; may upgrade encryption |
| `lab-auto auth migrate-session` | Encrypt legacy session without browser |
| `lab-auto auth import-cookie <file.json>` | Import Playwright storage or cookie list |
| `lab-auto auth logout` | Delete workspace session file |

## Workspace

| Command | Description |
|---------|-------------|
| `lab-auto workspace set <path>` | Save default workspace |
| `lab-auto workspace show` | Show active + saved paths |
| `lab-auto workspace unset` | Clear saved default |

Override: `--root <path>` or `LAB_AUTO_ROOT=<path>`

## Workflow

| Command | Description |
|---------|-------------|
| `lab-auto sync` | Sync tasks from GUAP |
| `lab-auto sync --archive` | Keep removed tasks as archived |
| `lab-auto status` | List active works |
| `lab-auto status --all` | Include archived |
| `lab-auto review <ref>` | Mark `[REVIEW]` and rename folder |
| `lab-auto convert <ref> --docx f.docx` | DOCX → PDF in work folder |
| `lab-auto submit <ref> --file f.pdf` | Upload PDF (after user approval) |
| `lab-auto archive <ref>` | Archive work |
| `lab-auto unarchive <ref>` | Restore archived work |

`<ref>` = `task-178541`, folder name, or path under workspace.
