[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$taskName = 'D6 Controller'
$executable = Join-Path $PSScriptRoot 'D6Controller.exe'
if (-not (Test-Path -LiteralPath $executable)) {
    throw "D6Controller.exe was not found beside this script."
}

$arguments = '--host 127.0.0.1 --port 8765'
$action = New-ScheduledTaskAction -Execute $executable -Argument $arguments
$trigger = New-ScheduledTaskTrigger -AtLogOn
$principal = New-ScheduledTaskPrincipal `
    -UserId ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name) `
    -LogonType Interactive `
    -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet `
    -MultipleInstances IgnoreNew `
    -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Seconds 0)

try {
    Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force -ErrorAction Stop | Out-Null
} catch {
    # Standard users may be blocked by local task policy. HKCU Run is the
    # supported no-admin fallback and is still scoped to the current user.
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue
    $runKey = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run'
    New-Item -Path $runKey -Force | Out-Null
    Set-ItemProperty -Path $runKey -Name $taskName -Value ('"{0}" {1}' -f $executable, $arguments)
}
