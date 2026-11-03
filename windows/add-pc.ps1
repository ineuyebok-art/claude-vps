# Строка «Добавить ПК»: готовит Windows для работы с сервером claude-vps.
# Запускается из PowerShell строкой из бота (/devices). Повторный запуск безопасен.
[CmdletBinding()]
param(
    [string]$Server,
    [string]$HostKey,
    [string]$Commit = 'main',
    [switch]$Lib   # только загрузить функции: для тестов
)

$ErrorActionPreference = 'Stop'
$script:RepoRaw = 'https://raw.githubusercontent.com/ineuyebok-art/claude-vps'
$script:MutagenVersion = '0.18.1'
$script:MutagenSha256 = '76f8223d5e6b607efdd9516473669ae5492e4f142887352d59bc6934d1f07a2d'
$script:Root = if ($env:CLAUDEVPS_ROOT) { $env:CLAUDEVPS_ROOT } else { 'C:\Claude' }
$script:AppDir = Join-Path $env:LOCALAPPDATA 'ClaudeVPS'
$script:SshDir = Join-Path $env:USERPROFILE '.ssh'
$script:KeyPath = Join-Path $script:SshDir 'claude-vps'
$script:Folders = @('inbox', 'outbox', 'chat')
$script:Utf8 = New-Object System.Text.UTF8Encoding($false)

function Write-Step([string]$Text) { Write-Host "==> $Text" -ForegroundColor Green }

function Update-SessionPath {
    $env:Path = [Environment]::GetEnvironmentVariable('Path', 'Machine') + ';' +
        [Environment]::GetEnvironmentVariable('Path', 'User') + ';' +
        (Join-Path $script:AppDir 'mutagen')
}

function Install-WingetPackage([string]$Id, [string]$Check) {
    if (Find-Tool $Check) { return }
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        throw "Нет winget: обнови «Установщик приложений» в Microsoft Store и запусти строку снова."
    }
    Write-Step "Ставлю $Id"
    & winget install --exact --id $Id --silent --accept-source-agreements --accept-package-agreements | Out-Host
    Update-SessionPath
    if (-not (Find-Tool $Check)) { throw "Не удалось установить $Id. Поставь вручную и запусти строку снова." }
}

function Find-Tool([string]$Name) {
    $command = Get-Command $Name -ErrorAction SilentlyContinue
    if ($command) { return $command.Source }
    $places = @(
        (Join-Path $env:ProgramFiles "TigerVNC\$Name.exe"),
        (Join-Path $env:LOCALAPPDATA "Programs\Microsoft VS Code\bin\$Name.cmd"),
        (Join-Path $env:ProgramFiles "Microsoft VS Code\bin\$Name.cmd"),
        (Join-Path $env:LOCALAPPDATA "Microsoft\WindowsApps\$Name.exe"),
        (Join-Path $script:AppDir "mutagen\$Name.exe")
    )
    foreach ($place in $places) {
        if ($place -and (Test-Path $place)) { return $place }
    }
    return $null
}

function Install-Mutagen {
    if (Find-Tool 'mutagen') { return }
    Write-Step 'Ставлю Mutagen'
    $target = Join-Path $script:AppDir 'mutagen'
    New-Item -ItemType Directory -Force -Path $target | Out-Null
    $name = "mutagen_windows_amd64_v$($script:MutagenVersion).zip"
    $url = "https://github.com/mutagen-io/mutagen/releases/download/v$($script:MutagenVersion)/$name"
    $zip = Join-Path $env:TEMP $name
    Invoke-WebRequest -Uri $url -OutFile $zip -UseBasicParsing
    $actual = (Get-FileHash -Algorithm SHA256 -Path $zip).Hash.ToLowerInvariant()
    if ($actual -ne $script:MutagenSha256) { throw 'Контрольная сумма Mutagen не совпала, установка остановлена.' }
    Expand-Archive -Path $zip -DestinationPath $target -Force
    Remove-Item $zip
    Update-SessionPath
}

function Install-Tool {
    Install-WingetPackage 'Microsoft.VisualStudioCode' 'code'
    Install-WingetPackage 'Microsoft.WindowsTerminal' 'wt'
    Install-WingetPackage 'TigerVNC.TigerVNC' 'vncviewer'
    Install-Mutagen
    if (-not (Get-Command ssh -ErrorAction SilentlyContinue)) {
        throw 'Нет клиента OpenSSH: Параметры → Система → Дополнительные компоненты → Клиент OpenSSH.'
    }
}

function New-SshKey {
    New-Item -ItemType Directory -Force -Path $script:SshDir | Out-Null
    if (Test-Path $script:KeyPath) { return }
    Write-Step 'Создаю SSH-ключ'
    # Пустая парольная фраза передаётся через cmd: PowerShell 5 и 7 по-разному разбирают кавычки.
    $comment = "claude-vps-$env:COMPUTERNAME"
    & cmd.exe /c "ssh-keygen -q -t ed25519 -N `"`" -C $comment -f `"$($script:KeyPath)`""
    if ($LASTEXITCODE -ne 0) { throw 'Не удалось создать SSH-ключ.' }
}

function Set-ManagedBlock([string]$Path, [string]$Block) {
    # Свой блок заменяем целиком, чужие строки файла не трогаем.
    $start = '# claude-vps start'
    $end = '# claude-vps end'
    $text = if (Test-Path $Path) { [IO.File]::ReadAllText($Path) } else { '' }
    $pattern = '(?s)' + [regex]::Escape($start) + '.*?' + [regex]::Escape($end) + '\r?\n?'
    $text = [regex]::Replace($text, $pattern, '')
    if ($text.Length -gt 0 -and -not $text.EndsWith("`n")) { $text += "`n" }
    $text += "$start`n$Block`n$end`n"
    [IO.File]::WriteAllText($Path, $text, $script:Utf8)
}

function Set-SshConfig([string]$Address, [string]$Key) {
    New-Item -ItemType Directory -Force -Path $script:SshDir | Out-Null
    $identity = $script:KeyPath -replace '\\', '/'
    $known = (Join-Path $script:AppDir 'known_hosts') -replace '\\', '/'
    New-Item -ItemType Directory -Force -Path $script:AppDir | Out-Null
    [IO.File]::WriteAllText((Join-Path $script:AppDir 'known_hosts'), "$Address $Key`n", $script:Utf8)
    $block = @(
        'Host claude-vps',
        "    HostName $Address",
        '    User claude',
        "    IdentityFile `"$identity`"",
        '    IdentitiesOnly yes',
        "    UserKnownHostsFile `"$known`"",
        '    StrictHostKeyChecking yes',
        '    ForwardAgent no',
        '    ServerAliveInterval 30',
        '    ServerAliveCountMax 4'
    ) -join "`n"
    Set-ManagedBlock (Join-Path $script:SshDir 'config') $block
}

function Test-ServerLogin {
    & ssh -o BatchMode=yes -o ConnectTimeout=8 claude-vps true 2>$null
    return ($LASTEXITCODE -eq 0)
}

function Wait-KeyConfirmed {
    if (Test-ServerLogin) { return }
    $public = (Get-Content "$($script:KeyPath).pub" -Raw).Trim()
    Set-Clipboard -Value $public
    Write-Host ''
    Write-Host 'Ключ скопирован в буфер обмена.' -ForegroundColor Yellow
    Write-Host 'Вставь его в чат с ботом (Ctrl+V) и нажми «Подтвердить». Жду...' -ForegroundColor Yellow
    while (-not (Test-ServerLogin)) { Start-Sleep -Seconds 3 }
    Write-Step 'Ключ подтверждён'
}

function New-RootFolder {
    foreach ($name in $script:Folders) {
        New-Item -ItemType Directory -Force -Path (Join-Path $script:Root $name) | Out-Null
    }
}

function Get-SyncArgument([string]$Name, [string]$Server) {
    $local = Join-Path $script:Root $Name
    return @('sync', 'create', "--name=claude-$Name", '--mode=two-way-safe', '--symlink-mode=ignore',
        '--ignore=~$*', '--ignore=desktop.ini', '--ignore=Thumbs.db', $local, "$($Server):/home/claude/$Name")
}

function Set-Sync {
    Write-Step 'Настраиваю синхронизацию C:\Claude'
    $mutagen = Find-Tool 'mutagen'
    & $mutagen daemon start
    foreach ($name in $script:Folders) {
        & $mutagen sync list "claude-$name" 2>$null | Out-Null
        if ($LASTEXITCODE -ne 0) {
            & $mutagen @(Get-SyncArgument $name 'claude-vps')
            if ($LASTEXITCODE -ne 0) { throw "Не удалось создать синхронизацию $name." }
        }
    }
}

function Save-VncPassword {
    Write-Step 'Сохраняю пароль рабочего стола'
    $text = & ssh claude-vps 'base64 -w0 ~/.vnc/passwd'
    if ($LASTEXITCODE -ne 0) { throw 'Не удалось получить пароль рабочего стола.' }
    $path = Join-Path $script:AppDir 'vnc.passwd'
    [IO.File]::WriteAllBytes($path, [Convert]::FromBase64String($text.Trim()))
    Protect-UserFile $path
}

function Protect-UserFile([string]$Path) {
    # Файл читает только этот пользователь Windows.
    & icacls.exe $Path /inheritance:r /grant:r "$($env:USERNAME):F" | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Не удалось закрыть доступ к $Path." }
}

function Save-RepoFile([string]$Commit) {
    Write-Step 'Скачиваю скрипты'
    $client = New-Object Net.WebClient
    foreach ($name in @('desktop.ps1', 'send-to.ps1', 'paste-shot.ps1')) {
        $client.DownloadFile("$($script:RepoRaw)/$Commit/windows/$name", (Join-Path $script:AppDir $name))
    }
    $client.DownloadFile("$($script:RepoRaw)/$Commit/docs/cheatsheet.html", (Join-Path $script:Root 'Шпаргалка.html'))
}

function New-AppShortcut {
    param([string]$Path, [string]$Target, [string]$Arguments = '', [string]$HotKey = '', [int]$Style = 1,
        [string]$Icon = '')
    $shell = New-Object -ComObject WScript.Shell
    $link = $shell.CreateShortcut($Path)
    $link.TargetPath = $Target
    $link.Arguments = $Arguments
    $link.WindowStyle = $Style
    if ($HotKey) { $link.Hotkey = $HotKey }
    if ($Icon) { $link.IconLocation = $Icon }
    $link.Save()
}

function Get-HiddenPowerShellArgument([string]$Script) {
    return "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$(Join-Path $script:AppDir $Script)`""
}

function New-Shortcut {
    Write-Step 'Создаю ярлыки'
    $desktop = [Environment]::GetFolderPath('Desktop')
    $programs = [Environment]::GetFolderPath('Programs')
    $startup = [Environment]::GetFolderPath('Startup')
    $sendTo = [Environment]::GetFolderPath('SendTo')
    $powershell = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
    $code = Find-Tool 'code'
    $codeExe = Join-Path (Split-Path (Split-Path $code)) 'Code.exe'
    New-AppShortcut (Join-Path $desktop 'Claude — терминал.lnk') (Find-Tool 'wt') 'ssh -t claude-vps cc main'
    New-AppShortcut (Join-Path $desktop 'Claude — VS Code.lnk') $codeExe `
        '--folder-uri vscode-remote://ssh-remote+claude-vps/home/claude/projects'
    New-AppShortcut (Join-Path $desktop 'Claude — рабочий стол.lnk') $powershell `
        (Get-HiddenPowerShellArgument 'desktop.ps1') -Style 7 -Icon (Find-Tool 'vncviewer')
    New-AppShortcut (Join-Path $desktop 'Claude — файлы.lnk') (Join-Path $env:SystemRoot 'explorer.exe') $script:Root
    New-AppShortcut (Join-Path $sendTo 'Claude.lnk') $powershell (Get-HiddenPowerShellArgument 'send-to.ps1') -Style 7
    New-AppShortcut (Join-Path $programs 'Claude — скриншот.lnk') $powershell `
        (Get-HiddenPowerShellArgument 'paste-shot.ps1') -HotKey 'CTRL+ALT+V' -Style 7
    New-AppShortcut (Join-Path $startup 'Claude — синхронизация.lnk') (Find-Tool 'mutagen') 'daemon start' -Style 7
    (New-Object -ComObject Shell.Application).Namespace($script:Root).Self.InvokeVerb('pintohome')
}

function Merge-JsonSetting([string]$Path, [hashtable]$Settings) {
    # settings.json с комментариями не разобрать: тогда файл не трогаем и говорим об этом.
    $current = New-Object PSObject
    if (Test-Path $Path) {
        try {
            $current = [IO.File]::ReadAllText($Path) | ConvertFrom-Json
        } catch {
            Write-Warning "Не удалось прочитать $Path. Настройки VS Code не изменены."
            return
        }
    }
    foreach ($key in $Settings.Keys) {
        $current | Add-Member -NotePropertyName $key -NotePropertyValue $Settings[$key] -Force
    }
    New-Item -ItemType Directory -Force -Path (Split-Path $Path) | Out-Null
    [IO.File]::WriteAllText($Path, ($current | ConvertTo-Json -Depth 10), $script:Utf8)
}

function Set-VsCode {
    Write-Step 'Настраиваю VS Code'
    $code = Find-Tool 'code'
    & $code --install-extension ms-vscode-remote.remote-ssh --force | Out-Null
    $settings = Join-Path $env:APPDATA 'Code\User\settings.json'
    Merge-JsonSetting $settings @{
        'remote.SSH.remotePlatform'     = @{ 'claude-vps' = 'linux' }
        'remote.SSH.defaultExtensions'  = @('ms-vscode.live-server', 'tomoki1207.pdf')
        'security.workspace.trust.enabled' = $false
    }
}

function Invoke-Main([string]$Address, [string]$Key, [string]$Revision) {
    if (-not $Address -or -not $Key) { throw 'Запусти строку из бота: /devices → «Добавить ПК».' }
    [Console]::OutputEncoding = [Text.Encoding]::UTF8
    New-Item -ItemType Directory -Force -Path $script:AppDir | Out-Null
    Update-SessionPath
    Install-Tool
    New-SshKey
    Set-SshConfig $Address $Key
    Wait-KeyConfirmed
    New-RootFolder
    Set-Sync
    Save-VncPassword
    Save-RepoFile $Revision
    Set-VsCode
    New-Shortcut
    Write-Host ''
    Write-Host 'Готово. На рабочем столе четыре ярлыка «Claude — …», папка C:\Claude синхронизируется.' -ForegroundColor Green
}

if (-not $Lib) { Invoke-Main $Server $HostKey $Commit }
