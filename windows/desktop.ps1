# Ярлык «Claude — рабочий стол»: туннель + TigerVNC. Закрыл окно — туннель закрылся.
$ErrorActionPreference = 'Stop'
$app = Join-Path $env:LOCALAPPDATA 'ClaudeVPS'

function Get-FreePort {
    $listener = [Net.Sockets.TcpListener]::new([Net.IPAddress]::Loopback, 0)
    $listener.Start()
    $port = $listener.LocalEndpoint.Port
    $listener.Stop()
    return $port
}

function Test-Port([int]$Port) {
    $client = New-Object Net.Sockets.TcpClient
    try {
        $client.Connect('127.0.0.1', $Port)
        return $true
    } catch {
        return $false
    } finally {
        $client.Close()
    }
}

$viewer = Join-Path $env:ProgramFiles 'TigerVNC\vncviewer.exe'
if (-not (Test-Path $viewer)) { $viewer = (Get-Command vncviewer).Source }
$port = Get-FreePort
$forward = "$($port):127.0.0.1:5901"
$tunnel = Start-Process ssh -ArgumentList '-N', '-o', 'ExitOnForwardFailure=yes', '-L', $forward, 'claude-vps' `
    -WindowStyle Hidden -PassThru
try {
    foreach ($attempt in 1..50) {
        if (Test-Port $port) { break }
        if ($tunnel.HasExited) { throw 'Туннель не поднялся.' }
        Start-Sleep -Milliseconds 200
    }
    $arguments = @("127.0.0.1::$port", '-passwd', "`"$(Join-Path $app 'vnc.passwd')`"", '-RemoteResize=1',
        '-AcceptClipboard=1', '-SendClipboard=1', '-SecurityTypes=VncAuth', '-Shared=1')
    Start-Process $viewer -ArgumentList $arguments -PassThru | Wait-Process
} catch {
    Add-Type -AssemblyName System.Windows.Forms
    [void][Windows.Forms.MessageBox]::Show("Рабочий стол не открылся: $($_.Exception.Message)", 'Claude')
} finally {
    if (-not $tunnel.HasExited) { Stop-Process -Id $tunnel.Id -Force }
}
