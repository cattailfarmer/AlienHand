param(
    [string]$Repository = "https://github.com/godotengine/godot-cpp.git",
    [string]$Branch = "master"
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$target = Join-Path $root "godot-cpp"

if (Test-Path $target) {
    Write-Host "godot-cpp already exists at $target"
    exit 0
}

git clone --depth 1 --branch $Branch $Repository $target
