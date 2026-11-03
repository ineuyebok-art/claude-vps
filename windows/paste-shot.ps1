# Ctrl+Alt+V: скриншот из буфера → C:\Claude\inbox → в окно вписывается @~/inbox/screen-<время>.png
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
$root = if ($env:CLAUDEVPS_ROOT) { $env:CLAUDEVPS_ROOT } else { 'C:\Claude' }

function ConvertTo-SendKey([string]$Text) {
    # Эти символы у SendKeys служебные.
    return [regex]::Replace($Text, '[+^%~(){}\[\]]', { param($match) '{' + $match.Value + '}' })
}

$image = [Windows.Forms.Clipboard]::GetImage()
if (-not $image) { exit 0 }
$name = "screen-$(Get-Date -Format 'yyyyMMdd-HHmmss').png"
$image.Save((Join-Path $root "inbox\$name"), [Drawing.Imaging.ImageFormat]::Png)
$mutagen = Get-Command mutagen -ErrorAction SilentlyContinue
$path = if ($mutagen) { $mutagen.Source } else { Join-Path $env:LOCALAPPDATA 'ClaudeVPS\mutagen\mutagen.exe' }
& $path sync flush claude-inbox | Out-Null
$typed = "@~/inbox/$name "
if ($env:CLAUDEVPS_DRYRUN) {
    ConvertTo-SendKey $typed
} else {
    [Windows.Forms.SendKeys]::SendWait((ConvertTo-SendKey $typed))
}
