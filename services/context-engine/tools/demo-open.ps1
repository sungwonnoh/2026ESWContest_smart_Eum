# 시연 화면. 창 여섯을 각각 별도 PowerShell 창으로 띄운다 (Windows).
#
# macOS 는 demo-open.sh, tmux 는 demo-screen.sh. 이쪽은 윈도우용이다.
#
#   .\tools\demo-open.ps1 -MqttHost 172.20.10.3
#   .\tools\demo-open.ps1 -MqttHost 172.20.10.3 -Config ..\..\config\homes\demo.toml
#   .\tools\demo-open.ps1 -Views input,board
#
# 창이 안 열리면 실행 정책 때문이다:
#   Set-ExecutionPolicy -Scope Process -Bypass
#
# 닫을 때는 창마다 Ctrl-C.

param(
    [string]$MqttHost = $(if ($env:MQTT_HOST) { $env:MQTT_HOST } else { "localhost" }),
    [int]$Port = 1883,
    [string]$Config = "",
    [string[]]$Views = @("input", "activity", "decision", "action", "board", "t0")
)

$ErrorActionPreference = "Stop"

# tools\ 의 부모 = services\context-engine
$root = Split-Path -Parent $PSScriptRoot
$monitor = Join-Path $PSScriptRoot "monitor.py"

if (-not (Test-Path $monitor)) {
    Write-Error "monitor.py 를 못 찾았다: $monitor"
    exit 1
}

# 새 창은 이 셸의 환경을 물려받지 않는다. venv 파이썬을 직접 가리킨다
$py = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) {
    $py = "python"
    Write-Host "  venv 가 없다 — 시스템 python 을 쓴다 (paho-mqtt 가 있어야 한다)"
}

# 상대 경로로 준 Config 는 이 스크립트를 부른 위치 기준이다. 절대 경로로 바꿔둔다
if ($Config -and -not [System.IO.Path]::IsPathRooted($Config)) {
    $resolved = Resolve-Path -LiteralPath $Config -ErrorAction SilentlyContinue
    if ($resolved) {
        $Config = $resolved.Path
    } else {
        Write-Host "  설정 파일을 못 찾았다: $Config — 구역·라벨 없이 띄운다"
        $Config = ""
    }
}

foreach ($v in $Views) {
    $cmd = "Set-Location '$root'; & '$py' '$monitor' --host '$MqttHost' --port $Port --view $v"
    if ($Config) { $cmd += " --config '$Config'" }

    Start-Process powershell -ArgumentList '-NoExit', '-Command', $cmd
    Start-Sleep -Milliseconds 200
}

Write-Host "창 $($Views.Count)개를 열었다 — $MqttHost`:$Port"
Write-Host "각 창을 62열 이상으로 넓히세요. 한글이 깨지면 Windows Terminal 로 여세요."