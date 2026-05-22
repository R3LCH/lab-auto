# OpenClaw integration

[OpenClaw](https://documentation.openclaw.ai/) loads [AgentSkills](https://agentskills.io/) folders with `SKILL.md`.

## Option A — This repo as the agent workspace

Clone the repo and use it as the OpenClaw workspace. Skills load from:

| Precedence | Path in repo |
|------------|----------------|
| Workspace | `skills/guap-lab-workflow/` |
| Project agent | `.agents/skills/guap-lab-workflow/` |

Enable in `openclaw.json`:

```json5
{
  agents: {
    defaults: {
      skills: ["guap-lab-workflow"],
    },
  },
  skills: {
    entries: {
      "guap-lab-workflow": {
        enabled: true,
        env: {
          LAB_AUTO_ROOT: "/path/to/your/guap-workspace",
        },
      },
    },
  },
}
```

Slash command: `/guap-lab-workflow` (when `user-invocable` is enabled in frontmatter).

## Option B — Install for all local agents

```bash
openclaw skills install /path/to/lab_automation/skills/guap-lab-workflow --global
# or from ClawHub when published
```

## Option C — Extra skill directory

```json5
{
  skills: {
    load: {
      extraDirs: ["/path/to/lab_automation/skills"],
    },
  },
}
```

## Requirements (gating)

The skill declares `metadata.openclaw.requires.bins: ["lab-auto"]`. Install the CLI before enabling:

```bash
pip install guap-lab-auto
playwright install chromium
```

## Terminal tools

The agent needs shell access to run `lab-auto`. Enable your OpenClaw agent’s terminal/exec toolset alongside this skill.

## Human-in-the-loop

OpenClaw must **not** auto-run `lab-auto submit` unless the user explicitly requests it in the same conversation.
