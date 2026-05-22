#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SKILL_SRC="${ROOT}/skills/guap-lab-workflow"
HERMES_DEST="${HOME}/.hermes/skills/guap-lab-workflow"
OPENCLAW_DEST="${HOME}/.openclaw/skills/guap-lab-workflow"
AGENTS_DEST="${HOME}/.agents/skills/guap-lab-workflow"

install_one() {
  local dest="$1"
  mkdir -p "$(dirname "${dest}")"
  rm -rf "${dest}"
  cp -R "${SKILL_SRC}" "${dest}"
  echo "Installed skill -> ${dest}"
}

echo "Installing guap-lab-workflow skill from ${SKILL_SRC}"
install_one "${HERMES_DEST}"
install_one "${OPENCLAW_DEST}"
install_one "${AGENTS_DEST}"

echo ""
echo "Next steps:"
echo "  pip install guap-lab-auto"
echo "  playwright install chromium"
echo "  lab-auto workspace set ~/guap-labs"
echo "  lab-auto auth login"
echo ""
echo "Hermes:  /guap-lab-workflow  or  hermes -s guap-lab-workflow"
echo "OpenClaw: enable skill guap-lab-workflow in openclaw.json (see examples/openclaw.json.example)"
