# ПКМ → «Отправить → Claude»: копия в C:\Claude\inbox, в буфере @~/inbox/имя — останется Ctrl+V.
param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Paths)
$ErrorActionPreference = 'Stop'
$root = if ($env:CLAUDEVPS_ROOT) { $env:CLAUDEVPS_ROOT } else { 'C:\Claude' }
$inbox = Join-Path $root 'inbox'

function Get-InboxName([string]$Name, [string]$Folder) {
    # Пробелы оборвали бы упоминание @файл, существующий файл затирать нельзя.
    $clean = $Name -replace '\s+', '_'
    $stem = [IO.Path]::GetFileNameWithoutExtension($clean)
    $extension = [IO.Path]::GetExtension($clean)
    $candidate = $clean
    $number = 1
    while (Test-Path -LiteralPath (Join-Path $Folder $candidate)) {
        $candidate = "$stem-$number$extension"
        $number++
    }
    return $candidate
}

function Sync-Inbox {
    $mutagen = Get-Command mutagen -ErrorAction SilentlyContinue
    $path = if ($mutagen) { $mutagen.Source } else { Join-Path $env:LOCALAPPDATA 'ClaudeVPS\mutagen\mutagen.exe' }
    & $path sync flush claude-inbox | Out-Null
}

$mentions = foreach ($path in $Paths) {
    $name = Get-InboxName (Split-Path $path -Leaf) $inbox
    Copy-Item -LiteralPath $path -Destination (Join-Path $inbox $name) -Recurse
    "@~/inbox/$name"
}
Sync-Inbox
Set-Clipboard -Value ($mentions -join ' ')
