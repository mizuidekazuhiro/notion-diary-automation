[CmdletBinding()]
param(
    [string]$WorkerUrl = "",
    [string]$AnkiExecutable = "",
    [string]$TaskName = "Anki Notion Revlog Sync",
    [int]$BackfillDays = 7,
    [int]$SessionGapMinutes = 10,
    [int]$MaxAnswerSeconds = 300,
    [switch]$Uninstall
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Split-Path -Parent $scriptRoot
$syncScript = Join-Path $scriptRoot "anki_revlog_sync.py"
$stateDir = Join-Path $env:LOCALAPPDATA "AnkiNotionSync"
$configPath = Join-Path $stateDir "config.json"
$logDir = Join-Path $stateDir "logs"

function Get-TaskFolder {
    $service = New-Object -ComObject "Schedule.Service"
    $service.Connect()
    return @($service, $service.GetFolder("\"))
}

if ($Uninstall) {
    $taskObjects = Get-TaskFolder
    try {
        $taskObjects[1].DeleteTask($TaskName, 0)
        Write-Host "Removed scheduled task: $TaskName"
    } catch {
        if ($_.Exception.Message -notmatch "cannot find|見つかりません") { throw }
        Write-Host "Scheduled task was not registered: $TaskName"
    }
    if (Test-Path -LiteralPath $configPath) {
        Remove-Item -LiteralPath $configPath -Force
        Write-Host "Removed encrypted config: $configPath"
    }
    Write-Host "Logs were retained at: $logDir"
    exit 0
}

if (-not (Test-Path -LiteralPath $syncScript)) {
    throw "Sync script not found: $syncScript"
}
if ($BackfillDays -lt 1 -or $SessionGapMinutes -lt 1 -or $MaxAnswerSeconds -lt 1) {
    throw "BackfillDays, SessionGapMinutes, and MaxAnswerSeconds must be positive integers."
}

$pythonLauncher = Get-Command "py.exe" -ErrorAction SilentlyContinue
if (-not $pythonLauncher) {
    throw "Python launcher (py.exe) was not found. Install Python 3.11 or newer, then rerun this script."
}

if (-not $AnkiExecutable) {
    $ankiCandidates = @(
        (Join-Path $env:LOCALAPPDATA "Programs\Anki\anki.exe"),
        (Join-Path $env:ProgramFiles "Anki\anki.exe"),
        $(if (${env:ProgramFiles(x86)}) { Join-Path ${env:ProgramFiles(x86)} "Anki\anki.exe" })
    ) | Where-Object { $_ -and (Test-Path -LiteralPath $_) }
    $AnkiExecutable = $ankiCandidates | Select-Object -First 1
}
if (-not $AnkiExecutable -or -not (Test-Path -LiteralPath $AnkiExecutable)) {
    throw "Anki executable was not found. Rerun with -AnkiExecutable 'C:\path\to\anki.exe'."
}

if (-not $WorkerUrl) {
    $WorkerUrl = $env:ANKI_NOTION_WORKER_URL
}
if (-not $WorkerUrl) {
    $WorkerUrl = $env:DAILY_LOG_UPSERT_URL
}
if (-not $WorkerUrl) {
    $WorkerUrl = Read-Host "Cloudflare Worker URL (the existing DAILY_LOG_UPSERT_URL is accepted)"
}
if (-not $WorkerUrl -or $WorkerUrl -notmatch '^https?://') {
    throw "A valid http(s) Worker URL is required."
}

$workerTokenSecure = $null
if ($env:WORKERS_BEARER_TOKEN) {
    $workerTokenSecure = ConvertTo-SecureString $env:WORKERS_BEARER_TOKEN -AsPlainText -Force
} else {
    $workerTokenSecure = Read-Host "WORKERS_BEARER_TOKEN (saved encrypted for this Windows user)" -AsSecureString
}
$workerTokenEncrypted = ConvertFrom-SecureString $workerTokenSecure
if (-not $workerTokenEncrypted) {
    throw "WORKERS_BEARER_TOKEN is required."
}

$ankiConnectKeyEncrypted = ""
if ($env:ANKI_CONNECT_KEY) {
    $ankiConnectKeySecure = ConvertTo-SecureString $env:ANKI_CONNECT_KEY -AsPlainText -Force
    $ankiConnectKeyEncrypted = ConvertFrom-SecureString $ankiConnectKeySecure
}

New-Item -ItemType Directory -Path $stateDir -Force | Out-Null
New-Item -ItemType Directory -Path $logDir -Force | Out-Null
$config = [ordered]@{
    worker_url = $WorkerUrl.Trim()
    worker_token_dpapi = $workerTokenEncrypted
    anki_connect_key_dpapi = $ankiConnectKeyEncrypted
    anki_executable = (Resolve-Path -LiteralPath $AnkiExecutable).Path
    profile = ""
    backfill_days = $BackfillDays
    session_gap_minutes = $SessionGapMinutes
    max_answer_seconds = $MaxAnswerSeconds
}
$config | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $configPath -Encoding UTF8

$identity = [System.Security.Principal.WindowsIdentity]::GetCurrent()
$acl = New-Object System.Security.AccessControl.FileSecurity
$acl.SetOwner($identity.User)
$acl.SetAccessRuleProtection($true, $false)
$rule = New-Object System.Security.AccessControl.FileSystemAccessRule(
    $identity.User,
    [System.Security.AccessControl.FileSystemRights]::FullControl,
    [System.Security.AccessControl.AccessControlType]::Allow
)
$acl.AddAccessRule($rule)
Set-Acl -LiteralPath $configPath -AclObject $acl

try {
    $workerUri = [Uri]$WorkerUrl
    $healthUri = "{0}://{1}/health" -f $workerUri.Scheme, $workerUri.Authority
    Invoke-RestMethod -Uri $healthUri -Method Get -TimeoutSec 20 | Out-Null
    Write-Host "Worker connection: OK ($($workerUri.Authority))"
} catch {
    throw "Worker health check failed. Verify the URL and network, then rerun. $($_.Exception.Message)"
}

$doctorArgs = @(
    "-3", $syncScript,
    "--config", $configPath,
    "--log-dir", $logDir,
    "--start-anki",
    "--doctor"
)
& $pythonLauncher.Source @doctorArgs
if ($LASTEXITCODE -ne 0) {
    throw "Anki/AnkiConnect diagnosis failed. See $logDir\anki_revlog_sync.log"
}

$answerSetting = Read-Host "Confirm Anki 'Maximum answer seconds' is set to $MaxAnswerSeconds seconds [y/N]"
if ($answerSetting -notmatch '^(?i:y|yes)$') {
    Write-Warning "Set Maximum answer seconds to $MaxAnswerSeconds in Anki deck options. The sync can run now, but recorded time remains capped by Anki's current setting."
}

$taskObjects = Get-TaskFolder
$service = $taskObjects[0]
$rootFolder = $taskObjects[1]
$task = $service.NewTask(0)
$task.RegistrationInfo.Description = "Sync AnkiWeb, aggregate revlog at 04:00 JST boundaries, and upsert Notion Daily Log."
$task.Principal.UserId = $identity.Name
$task.Principal.LogonType = 3
$task.Principal.RunLevel = 0
$task.Settings.Enabled = $true
$task.Settings.StartWhenAvailable = $true
$task.Settings.RunOnlyIfNetworkAvailable = $true
$task.Settings.DisallowStartIfOnBatteries = $false
$task.Settings.StopIfGoingOnBatteries = $false
$task.Settings.WakeToRun = $true
$task.Settings.MultipleInstances = 2
$task.Settings.ExecutionTimeLimit = "PT30M"

$action = $task.Actions.Create(0)
$action.Path = $pythonLauncher.Source
$escapedScript = '"' + $syncScript.Replace('"', '\"') + '"'
$escapedConfig = '"' + $configPath.Replace('"', '\"') + '"'
$escapedLog = '"' + $logDir.Replace('"', '\"') + '"'
$action.Arguments = "-3 $escapedScript --config $escapedConfig --log-dir $escapedLog --start-anki"
$action.WorkingDirectory = $repoRoot

$logonTrigger = $task.Triggers.Create(9)
$logonTrigger.Enabled = $true
$logonTrigger.Delay = "PT1M"

$hourlyTrigger = $task.Triggers.Create(1)
$hourlyTrigger.Enabled = $true
$hourlyTrigger.StartBoundary = (Get-Date).AddMinutes(2).ToString("s")
$hourlyTrigger.Repetition.Interval = "PT1H"
$hourlyTrigger.Repetition.StopAtDurationEnd = $false

$dailyTrigger = $task.Triggers.Create(2)
$dailyTrigger.Enabled = $true
$dailyTrigger.DaysInterval = 1
$dailyStart = (Get-Date).Date.AddHours(4).AddMinutes(10)
$dailyTrigger.StartBoundary = $dailyStart.ToString("s")

$registered = $rootFolder.RegisterTaskDefinition($TaskName, $task, 6, $identity.Name, $null, 3, $null)
Write-Host "Registered scheduled task: $TaskName"
Write-Host "Triggers: logon (+1 minute), hourly, daily 04:10"
Write-Host "Encrypted config: $configPath"
Write-Host "Logs: $logDir"

$registered.Run($null) | Out-Null
Write-Host "Initial sync started. Check the log after a few minutes: $logDir\anki_revlog_sync.log"
