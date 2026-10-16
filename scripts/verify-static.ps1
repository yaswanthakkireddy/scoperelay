param(
    [string]$Manifest = 'docs/delivery/STATIC_SHOWCASE_RELEASE_MANIFEST.json',
    [string]$Payload = 'showcase/public'
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$manifestPath = Join-Path $root $Manifest
$payloadPath = Join-Path $root $Payload
if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) { throw 'Release manifest missing' }
if (-not (Test-Path -LiteralPath $payloadPath -PathType Container)) { throw 'Built payload missing' }
$record = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
$expected = @($record.files_sha256.PSObject.Properties.Name | Sort-Object)
$actual = @(Get-ChildItem -LiteralPath $payloadPath -File | Select-Object -ExpandProperty Name | Sort-Object)
$dirs = @(Get-ChildItem -LiteralPath $payloadPath -Directory)
if ($dirs.Count -ne 0 -or ($expected -join '|') -ne ($actual -join '|')) {
    throw 'Static payload file allowlist mismatch'
}
foreach ($name in $expected) {
    $path = Join-Path $payloadPath $name
    $digest = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($digest -ne $record.files_sha256.$name) { throw "Hash mismatch: $name" }
}
Write-Output "STATIC_HASH_PASS count=$($expected.Count) manifest=$((Get-FileHash -LiteralPath $manifestPath -Algorithm SHA256).Hash.ToLowerInvariant())"
