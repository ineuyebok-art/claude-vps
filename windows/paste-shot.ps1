# Ctrl+Alt+V: скриншот из буфера → C:\Claude\inbox → в окно вписывается @~/inbox/screen-<время>.png
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
$root = if ($env:CLAUDEVPS_ROOT) { $env:CLAUDEVPS_ROOT } else { 'C:\Claude' }

function ConvertTo-SendKey([string]$Text) {
    # Эти символы у SendKeys служебные.
    return [regex]::Replace($Text, '[+^%~(){}\[\]]', { param($match) '{' + $match.Value + '}' })
}

function Wait-Sync {
    # Без сети flush ждал бы вечно: через минуту отдаём результат, файл догонит сам.
    $mutagen = Get-Command mutagen -ErrorAction SilentlyContinue
    $path = if ($mutagen) { $mutagen.Source } else { Join-Path $env:LOCALAPPDATA 'ClaudeVPS\mutagen\mutagen.exe' }
    $process = Start-Process $path -ArgumentList 'sync', 'flush', 'claude-inbox' -WindowStyle Hidden -PassThru
    if (-not $process.WaitForExit(60000)) { Stop-Process -Id $process.Id -Force }
}

$image = [Windows.Forms.Clipboard]::GetImage()
if (-not $image) { exit 0 }
$name = "screen-$(Get-Date -Format 'yyyyMMdd-HHmmss').png"
$image.Save((Join-Path $root "inbox\$name"), [Drawing.Imaging.ImageFormat]::Png)
Wait-Sync
$typed = "@~/inbox/$name "
if ($env:CLAUDEVPS_DRYRUN) {
    ConvertTo-SendKey $typed
} else {
    [Windows.Forms.SendKeys]::SendWait((ConvertTo-SendKey $typed))
}
