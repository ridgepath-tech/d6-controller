[CmdletBinding()]
param()

$ErrorActionPreference = 'SilentlyContinue'
$taskName = 'D6 Controller'
$task = Get-ScheduledTask -TaskName $taskName
if ($task) {
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
}
$runKey = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run'
if (Get-ItemProperty -Path $runKey -Name $taskName) {
    Remove-ItemProperty -Path $runKey -Name $taskName
}
