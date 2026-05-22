# Hermes Agent + guap-lab-auto

## Overview

Hermes loads **guap-lab-workflow** from `~/.hermes/skills/` (or `skills.external_dirs`). The skill is exposed as **`/guap-lab-workflow`** and via `skills_list` / `skill_view`.

## Setup

### 1. Install CLI

```bash
pip install guap-lab-auto
playwright install chromium
```

### 2. Install the skill

```bash
./scripts/install-agent-skills.sh
```

Or copy manually:

```bash
cp -r skills/guap-lab-workflow ~/.hermes/skills/guap-lab-workflow
```

Or point Hermes at this repo (no copy) — merge `examples/hermes-skills.example.yaml` into `~/.hermes/config.yaml`.

### 3. Workspace env

In `~/.hermes/.env` or your shell:

```bash
export LAB_AUTO_ROOT=/path/to/your/guap-workspace
```

### 4. GUAP session (human)

```bash
lab-auto workspace set "$LAB_AUTO_ROOT"
lab-auto auth login
lab-auto auth check
```

## Usage

```bash
# Interactive TUI with terminal + skills
hermes --tui

# One-shot
hermes chat --toolsets terminal,skills -s guap-lab-workflow \
  -q "Run lab-auto status and summarize REVIEW tasks"

# Slash in chat
/guap-lab-workflow sync from GUAP and list REFACTOR labs
```

Skill metadata requires **`terminal`** toolset (`metadata.hermes.requires_toolsets`).

## Bundles

```bash
hermes bundles create guap-labs --skill guap-lab-workflow \
  -d "GUAP lab automation"
```

## Rules for Hermes

- Never run `lab-auto submit` without explicit user approval
- Read `state/works.yaml` before changing reports
- Run `lab-auto review` after editing a report

See also: `skills/guap-lab-workflow/references/hermes.md`
