param([Parameter(Mandatory)][string]$set)
# Archive the current takes, then load the chosen project from <data>\sets\<set>. No restart needed.
$py = if ($env:VOICE_STUDIO_PYTHON) { $env:VOICE_STUDIO_PYTHON } else { "python" }
Push-Location (Join-Path $PSScriptRoot "..")
try { & $py -m voice_studio switch $set } finally { Pop-Location }
