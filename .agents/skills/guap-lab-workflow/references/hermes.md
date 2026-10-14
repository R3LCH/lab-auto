# Hermes Agent integration

[Hermes Agent](https://hermes-agent.nousresearch.com/) uses AgentSkills-compatible `SKILL.md` files under `~/.hermes/skills/`.

## Install the skill

### Option A — Install script (recommended)

From the repo root:

```bash
# Linux / macOS
./scripts/install-agent-skills.sh

# Windows PowerShell
.\scripts\install-agent-skills.ps1
```

Copies `skills/guap-lab-workflow/` → `~/.hermes/skills/guap-lab-workflow/`.

### Option B — Manual copy

```bash
cp -r skills/guap-lab-workflow ~/.hermes/skills/guap-lab-workflow
```

### Option C — External skill directory (no copy)

Add the repo `skills/` folder to `~/.hermes/config.yaml`:

```yaml
skills:
  external_dirs:
    - /path/to/lab_automation/skills
```

Local `~/.hermes/skills/guap-lab-workflow` wins if the same name exists in both places.

## Use in Hermes

```bash
# Slash command
/guap-lab-workflow sync my labs and list tasks in REVIEW

# Preload skill + terminal tools
hermes chat --toolsets terminal,skills -s guap-lab-workflow -q "Run lab-auto status in my workspace"

# TUI
hermes --tui
```

## Config

Set the default workspace for subprocesses in `~/.hermes/.env` or shell profile:

```bash
export LAB_AUTO_ROOT=/path/to/your/guap-workspace
```

Or pass per command:

```bash
lab-auto --root /path/to/workspace sync
```

## Requirements

- Skill metadata: `requires_toolsets: [terminal]` — enable **terminal** in the session.
- Binaries: `lab-auto` on PATH (`pip install guap-lab-auto`).
- Run `playwright install chromium` once on the machine.
- `lab-auto auth login` must be completed by the user (browser).

## Bundles (optional)

Group with other dev skills:

```bash
hermes bundles create guap-labs \
  --skill guap-lab-workflow \
  -d "GUAP lab sync and report workflow"
```

Then: `/guap-labs help me finish lab 3`

## Human-in-the-loop

Hermes must **not** run `lab-auto submit` unless the user explicitly approves submission.
