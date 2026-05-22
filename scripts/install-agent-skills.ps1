# Install guap-lab-workflow skill for Hermes, OpenClaw, and ~/.agents/skills
$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
$SkillSrc = Join-Path $Root "skills\guap-lab-workflow"
$Destinations = @(
    (Join-Path $env:USERPROFILE ".hermes\skills\guap-lab-workflow"),
    (Join-Path $env:USERPROFILE ".openclaw\skills\guap-lab-workflow"),
    (Join-Path $env:USERPROFILE ".agents\skills\guap-lab-workflow")
)

function Install-Skill($Dest) {
    if (Test-Path $Dest) { Remove-Item -Recurse -Force $Dest }
    $parent = Split-Path -Parent $Dest
    if (-not (Test-Path $parent)) { New-Item -ItemType Directory -Path $parent -Force | Out-Null }
    Copy-Item -Recurse -Force $SkillSrc $Dest
    Write-Host "Installed skill -> $Dest"
}

Write-Host "Installing guap-lab-workflow from $SkillSrc"
foreach ($dest in $Destinations) { Install-Skill $dest }

Write-Host ""
Write-Host "Next steps:"
Write-Host "  pip install guap-lab-auto"
Write-Host "  playwright install chromium"
Write-Host "  lab-auto workspace set C:\path\to\guap-labs"
Write-Host "  lab-auto auth login"
Write-Host ""
Write-Host "Hermes:  /guap-lab-workflow"
Write-Host "OpenClaw: see examples\openclaw.json.example"
