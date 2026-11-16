<#
Uploads the server code to a cloud VM and installs/updates it as a service, from Windows.

  powershell -ExecutionPolicy Bypass -File deploy\upload.ps1 -Target azureuser@20.51.33.10 -Key C:\keys\kart-vm.pem

Needs the built-in Windows OpenSSH client (ssh/scp) and tar (both ship with Windows 10/11).
#>
param(
    [Parameter(Mandatory = $true)][string]$Target,   # user@public-ip
    [string]$Key = "",                                # path to the private key (.pem) from the cloud portal
    [int]$TcpPort = 5555,
    [int]$UdpPort = 5556
)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$tmp = Join-Path $env:TEMP "kartarena-server.tar"
$sshArgs = @("-o", "StrictHostKeyChecking=accept-new")
if ($Key) { $sshArgs += @("-i", $Key) }

Write-Host "==> packing server files"
Push-Location $root
tar -cf $tmp --exclude=__pycache__ common server run_server.py deploy
Pop-Location

Write-Host "==> uploading to $Target"
scp @sshArgs $tmp "${Target}:kartarena-server.tar"

Write-Host "==> installing on the VM"
ssh @sshArgs $Target "rm -rf ~/kartarena-src && mkdir -p ~/kartarena-src && tar -xf ~/kartarena-server.tar -C ~/kartarena-src && sudo bash ~/kartarena-src/deploy/setup_server.sh $TcpPort $UdpPort"

$ip = ($Target -split "@")[-1]
Write-Host ""
Write-Host "==> checking the server from this PC"
python (Join-Path $root "tools\netcheck.py") "${ip}:${TcpPort}:${UdpPort}" --count 50
