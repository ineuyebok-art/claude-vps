# Real Windows checks that need no server. Runs in CI on windows-latest under Windows PowerShell.
$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
$work = Join-Path $env:RUNNER_TEMP ([Guid]::NewGuid())
New-Item -ItemType Directory -Force -Path $work | Out-Null
$env:USERPROFILE = Join-Path $work 'profile'
$env:LOCALAPPDATA = Join-Path $work 'local'
$env:CLAUDEVPS_ROOT = Join-Path $work 'Claude'
New-Item -ItemType Directory -Force -Path $env:USERPROFILE, $env:LOCALAPPDATA | Out-Null
"PowerShell $($PSVersionTable.PSVersion)"

function Assert-True($Condition, [string]$Text) {
    if (-not $Condition) { throw "FAIL: $Text" }
    "ok   $Text"
}

# The script must survive the way the bot's line loads it: downloaded text, not a file.
$source = [IO.File]::ReadAllText((Join-Path $repo 'windows\add-pc.ps1'), [Text.Encoding]::UTF8)
& ([scriptblock]::Create($source.TrimStart([char]0xFEFF))) -Lib
Assert-True $true 'add-pc.ps1 loads as a script block'
. (Join-Path $repo 'windows\add-pc.ps1') -Lib
Assert-True ($script:Root -eq $env:CLAUDEVPS_ROOT) 'root override'
Assert-True (Get-Command Set-Sync -ErrorAction SilentlyContinue) 'functions are available'

New-SshKey
Assert-True (Test-Path $script:KeyPath) 'ssh key created with empty passphrase'
$first = (Get-FileHash $script:KeyPath).Hash
New-SshKey
Assert-True ((Get-FileHash $script:KeyPath).Hash -eq $first) 'second run keeps the key'
$comment = ((Get-Content "$($script:KeyPath).pub" -Raw).Trim() -split ' ')[2]
Assert-True ($comment -like 'claude-vps-*') "key comment names the computer: $comment"

$config = Join-Path $script:SshDir 'config'
[IO.File]::WriteAllText($config, "Host other`n    User me`n")
Set-SshConfig '203.0.113.7' 'ssh-ed25519 AAAAHOST'
Set-SshConfig '203.0.113.8' 'ssh-ed25519 AAAAHOST'
$text = [IO.File]::ReadAllText($config)
Assert-True ($text -match 'Host other') 'foreign ssh config is kept'
Assert-True (([regex]::Matches($text, 'Host claude-vps')).Count -eq 1) 'config block is not duplicated'
Assert-True ($text -match '203\.0\.113\.8' -and $text -notmatch '203\.0\.113\.7') 'config block is replaced'
Assert-True ($text -notmatch 'ForwardAgent yes') 'no agent forwarding'
$bytes = [IO.File]::ReadAllBytes($config)
Assert-True ($bytes[0] -ne 0xEF) 'ssh config has no BOM'
$parsed = & ssh -F $config -G claude-vps
Assert-True ($parsed -contains 'hostname 203.0.113.8') 'OpenSSH parses the config'
Assert-True ($parsed -contains 'user claude') 'OpenSSH sees user claude'
Assert-True ($parsed -contains 'stricthostkeychecking true') 'host key is pinned'

New-RootFolder
foreach ($name in 'inbox', 'outbox', 'chat') {
    Assert-True (Test-Path (Join-Path $script:Root $name)) "folder $name"
}

$sync = Get-SyncArgument 'inbox' 'claude-vps'
Assert-True ($sync -contains '--mode=two-way-safe') 'two-way-safe'
Assert-True ($sync -contains '--ignore=~$*') 'office temp files ignored'
Assert-True ($sync -contains '--ignore=desktop.ini') 'desktop.ini ignored'

$link = Join-Path $work 'test — ярлык.lnk'
New-AppShortcut $link 'C:\Windows\explorer.exe' 'C:\Claude' -HotKey 'CTRL+ALT+V'
Assert-True (Test-Path -LiteralPath $link) 'shortcut with a cyrillic name exists'
Copy-Item -LiteralPath $link (Join-Path $work 'ascii.lnk')
$read = (New-Object -ComObject WScript.Shell).CreateShortcut((Join-Path $work 'ascii.lnk'))
Assert-True ($read.Arguments -eq 'C:\Claude') 'shortcut keeps its arguments'
Assert-True ($read.Hotkey -match 'V') 'shortcut hotkey stored'

$settings = Join-Path $work 'code\settings.json'
Merge-JsonSetting $settings @{ a = 1 }
Merge-JsonSetting $settings @{ b = @{ c = 2 } }
$json = [IO.File]::ReadAllText($settings) | ConvertFrom-Json
Assert-True ($json.a -eq 1 -and $json.b.c -eq 2) 'settings are merged'

$secret = Join-Path $work 'secret'
Set-Content $secret 'x'
Protect-UserFile $secret
$acl = (Get-Acl $secret).Access
Assert-True (@($acl).Count -eq 1) 'password file is readable by one user only'

# Real Mutagen on Windows, local folders.
Install-Mutagen
$mutagen = Find-Tool 'mutagen'
Assert-True $mutagen 'mutagen installed and checksum verified'
& $mutagen version
& $mutagen daemon start
$alpha = Join-Path $work 'alpha'
$beta = Join-Path $work 'beta'
New-Item -ItemType Directory -Force -Path $alpha, $beta | Out-Null
[IO.File]::WriteAllText((Join-Path $alpha 'файл.txt'), 'привет')
[IO.File]::WriteAllText((Join-Path $alpha '~$temp.docx'), 'x')
& $mutagen sync create --name=claude-inbox --mode=two-way-safe '--ignore=~$*' --ignore=desktop.ini $alpha $beta
& $mutagen sync flush claude-inbox
Assert-True (Test-Path (Join-Path $beta 'файл.txt')) 'mutagen syncs a cyrillic name'
Assert-True (-not (Test-Path -LiteralPath (Join-Path $beta '~$temp.docx'))) 'mutagen ignores office temp'

# send-to.ps1 with the real flush.
$env:Path = "$(Split-Path $mutagen);$env:Path"
$inbox = Join-Path $script:Root 'inbox'
$sample = Join-Path $work 'мой отчёт.txt'
Set-Content -LiteralPath $sample 'данные'
powershell -NoProfile -STA -ExecutionPolicy Bypass -File (Join-Path $repo 'windows\send-to.ps1') $sample
Assert-True ($LASTEXITCODE -eq 0) 'send-to exits cleanly'
Assert-True (Test-Path -LiteralPath (Join-Path $inbox 'мой_отчёт.txt')) 'send-to copies to inbox'
powershell -NoProfile -STA -ExecutionPolicy Bypass -File (Join-Path $repo 'windows\send-to.ps1') $sample
Assert-True (Test-Path -LiteralPath (Join-Path $inbox 'мой_отчёт-1.txt')) 'send-to does not overwrite'

# paste-shot.ps1: image in the clipboard to a file in inbox.
Add-Type -AssemblyName System.Windows.Forms, System.Drawing
$shot = @'
Add-Type -AssemblyName System.Windows.Forms, System.Drawing
$bitmap = New-Object Drawing.Bitmap 8, 8
[Windows.Forms.Clipboard]::SetImage($bitmap)
'@
powershell -NoProfile -STA -Command $shot
$env:CLAUDEVPS_DRYRUN = '1'
$typed = powershell -NoProfile -STA -ExecutionPolicy Bypass -File (Join-Path $repo 'windows\paste-shot.ps1')
Assert-True ($typed -match '^@\{~\}/inbox/screen-\d{8}-\d{6}\.png') "paste-shot types the mention: $typed"
Assert-True (Get-ChildItem $inbox -Filter 'screen-*.png') 'paste-shot saves the png'

# Which winget IDs exist (informational: this decides the IDs used in add-pc.ps1).
foreach ($id in 'Microsoft.VisualStudioCode', 'Microsoft.WindowsTerminal', 'TigerVNC.TigerVNC') {
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        & winget show --exact --id $id --accept-source-agreements --disable-interactivity | Select-Object -First 3
        "winget $id exit $LASTEXITCODE"
    } else {
        'winget is not on this runner'
    }
}
& $mutagen sync terminate --all
& $mutagen daemon stop
'All Windows smoke checks passed'
