[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$taskName = 'D6 Controller'
$task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($task) {
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
    Write-Host 'Removed the D6 Controller logon task.'
} else {
    Write-Host 'The D6 Controller scheduled task was not installed.'
}
$runKey = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run'
if (Get-ItemProperty -Path $runKey -Name $taskName -ErrorAction SilentlyContinue) {
    Remove-ItemProperty -Path $runKey -Name $taskName
    Write-Host 'Removed the D6 Controller current-user startup entry.'
}
Write-Host 'User profiles, credentials, and installed files were left in place.'
