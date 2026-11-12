param([string]$GoExe = "go")
$ErrorActionPreference = "Stop"
$UartBuildRoot = $PSScriptRoot
if ($GoExe -eq "go" -and -not (Get-Command go -ErrorAction SilentlyContinue)) {
    $GoExe = Join-Path $UartBuildRoot "build\go-sdk\go\bin\go.exe"
}
if (-not (Get-Command $GoExe -ErrorAction SilentlyContinue)) {
    throw "找不到 Go 工具鏈，請安裝 Go 1.24 以上或以 -GoExe 指定。Linux 使用者只需編譯後的執行檔。"
}
$UartDist = Join-Path $UartBuildRoot "dist"
New-Item -ItemType Directory -Force -Path $UartDist | Out-Null
$UartBuildEnvironment = @{}
foreach ($UartKey in @("GOOS", "GOARCH", "GOAMD64", "CGO_ENABLED", "GOTOOLCHAIN")) {
    $UartBuildEnvironment[$UartKey] = [Environment]::GetEnvironmentVariable($UartKey, "Process")
}
try {
    $env:GOOS = "linux"
    $env:GOARCH = "amd64"
    $env:GOAMD64 = "v1"
    $env:CGO_ENABLED = "0"
    $env:GOTOOLCHAIN = "local"
    Push-Location (Join-Path $UartBuildRoot "go-client")
    try {
        & $GoExe build -trimpath -ldflags "-s -w" -o (Join-Path $UartDist "uart-linux-amd64") ./cmd/uart
        if ($LASTEXITCODE -ne 0) { throw "Go Client 編譯失敗。" }
        $UartCompiler = & $GoExe version
    } finally { Pop-Location }
    $UartBinary = Join-Path $UartDist "uart-linux-amd64"
    $UartHash = (Get-FileHash -LiteralPath $UartBinary -Algorithm SHA256).Hash.ToLowerInvariant()
    @{ version = 1; compiler = $UartCompiler; goos = "linux"; goarch = "amd64"; goamd64 = "v1"; cgo_enabled = 0; sha256 = $UartHash } |
        ConvertTo-Json | Set-Content -LiteralPath (Join-Path $UartDist "uart-linux-amd64.build.json") -Encoding utf8
    Write-Output "已建立 Linux amd64 獨立 Client：$UartBinary"
    Write-Output "SHA-256：$UartHash"
} finally {
    foreach ($UartKey in $UartBuildEnvironment.Keys) {
        [Environment]::SetEnvironmentVariable($UartKey, $UartBuildEnvironment[$UartKey], "Process")
    }
}
