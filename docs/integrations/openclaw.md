# OpenClaw + guap-lab-auto

## Overview

OpenClaw discovers the **guap-lab-workflow** skill (AgentSkills format) and uses it to run `lab-auto` via the agent’s terminal tools.

## Setup

### 1. Install CLI

```bash
pip install guap-lab-auto
playwright install chromium
```

### 2. Install the skill

**Repo as workspace** — clone and open as OpenClaw workspace; skills load from `skills/` and `.agents/skills/`.

**Global install:**

```bash
./scripts/install-agent-skills.sh
# installs to ~/.openclaw/skills/guap-lab-workflow
```

Or:

```bash
openclaw skills install /path/to/lab_automation/skills/guap-lab-workflow --global
```

### 3. Configure `~/.openclaw/openclaw.json`

Copy from `examples/openclaw.json.example`:

- Enable `guap-lab-workflow` in `agents.defaults.skills`
- Set `skills.entries.guap-lab-workflow.env.LAB_AUTO_ROOT` to your data workspace

### 4. GUAP session (human)

```bash
lab-auto workspace set /path/to/workspace
lab-auto auth login
lab-auto auth check
```

## Usage

- Slash: `/guap-lab-workflow sync and show tasks needing review`
- Ensure the agent has **shell/terminal** tools enabled
- The skill gates on `lab-auto` being on `PATH` (`metadata.openclaw.requires.bins`)

## Allowlist (multi-agent)

```json5
{
  agents: {
    list: [
      { id: "student", skills: ["guap-lab-workflow"] },
    ],
  },
}
```

## Security

- Do not put GUAP passwords in `openclaw.json`; use `lab-auto auth login`
- Session cookies are encrypted on disk — see main README
- Treat third-party skills as untrusted; this skill is shipped with the repo

See also: `skills/guap-lab-workflow/references/openclaw.md`
