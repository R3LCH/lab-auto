# Agent integrations

Use **guap-lab-auto** with [OpenClaw](https://documentation.openclaw.ai/) and [Hermes Agent](https://hermes-agent.nousresearch.com/) via the bundled **AgentSkills** skill `guap-lab-workflow`.

## Quick install (both agents)

```bash
# Linux / macOS
chmod +x scripts/install-agent-skills.sh
./scripts/install-agent-skills.sh

# Windows
.\scripts\install-agent-skills.ps1
```

Then install the CLI:

```bash
pip install guap-lab-auto
playwright install chromium
lab-auto workspace set /path/to/workspace
lab-auto auth login
```

## Guides

| Agent | Doc |
|-------|-----|
| OpenClaw | [openclaw.md](openclaw.md) |
| Hermes | [hermes.md](hermes.md) |

## Skill location in this repo

```
skills/guap-lab-workflow/SKILL.md      # workspace / OpenClaw / copy source
.agents/skills/guap-lab-workflow/      # OpenClaw project-agent path
```

Keep edits in `skills/guap-lab-workflow/` and re-run the install script to sync copies.
