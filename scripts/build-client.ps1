param([string]$Go = "go")
$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent
Push-Location $root
$oldOS, $oldArch, $oldCgo, $oldAMD = $env:GOOS, $env:GOARCH, $env:CGO_ENABLED, $env:GOAMD64
try {
    $env:CGO_ENABLED = "0"
    $env:GOAMD64 = "v1"
    foreach ($target in @(@("linux", "amd64", ""), @("linux", "arm64", ""), @("windows", "amd64", ".exe"))) {
        $env:GOOS, $env:GOARCH = $target[0], $target[1]
        & $Go build -trimpath -o "dist/uart-client-$($target[0])-$($target[1])$($target[2])" ./cmd/uart-client
        if ($LASTEXITCODE -ne 0) { throw "Build failed" }
    }
    Get-ChildItem dist/uart-client-* | Get-FileHash -Algorithm SHA256
} finally {
    $env:GOOS, $env:GOARCH, $env:CGO_ENABLED, $env:GOAMD64 = $oldOS, $oldArch, $oldCgo, $oldAMD
    Pop-Location
}
