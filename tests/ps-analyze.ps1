# Runs PSScriptAnalyzer over the Windows scripts. Fails on warnings and errors.
$ErrorActionPreference = 'Stop'
if (-not (Get-Module -ListAvailable PSScriptAnalyzer)) {
    Install-Module PSScriptAnalyzer -Force -Scope CurrentUser
}
Import-Module PSScriptAnalyzer
$root = Split-Path -Parent $PSScriptRoot
$excluded = @('PSAvoidUsingWriteHost', 'PSUseBOMForUnicodeEncodedFile', 'PSUseShouldProcessForStateChangingFunctions')
$files = Get-ChildItem -Path (Join-Path $root 'windows'), $PSScriptRoot -Filter *.ps1 -Recurse
$found = $files | ForEach-Object {
    Invoke-ScriptAnalyzer -Path $_.FullName -Severity Warning, Error -ExcludeRule $excluded
}
$found | Format-Table -AutoSize | Out-String | Write-Output
"Checked $($files.Count) files, $(@($found).Count) findings"
if ($found) { exit 1 }
