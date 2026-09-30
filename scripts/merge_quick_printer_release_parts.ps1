param(
    [string]$DownloadDirectory = $PSScriptRoot
)

$ErrorActionPreference = "Stop"

$manifests = @(Get-ChildItem -LiteralPath $DownloadDirectory -Filter "*.zip.parts.sha256" -File)
if ($manifests.Count -ne 1) {
    throw "Expected exactly one *.zip.parts.sha256 file in $DownloadDirectory; found $($manifests.Count)."
}

$manifest = $manifests[0]
$lines = Get-Content -LiteralPath $manifest.FullName -Encoding UTF8
$metadata = @{}
$partRecords = [System.Collections.Generic.List[object]]::new()

foreach ($line in $lines) {
    if ($line -match '^# ([^=]+)=(.*)$') {
        $metadata[$matches[1]] = $matches[2]
        continue
    }
    if ($line -match '^([0-9a-fA-F]{64}) \*(.+)$') {
        $partRecords.Add([pscustomobject]@{
            Sha256 = $matches[1].ToLowerInvariant()
            Name = $matches[2]
        })
    }
}

foreach ($key in @("source-name", "source-bytes", "source-sha256", "part-count")) {
    if (-not $metadata.ContainsKey($key)) {
        throw "Missing manifest metadata: $key"
    }
}

if ($partRecords.Count -ne [int]$metadata["part-count"]) {
    throw "Part count does not match the manifest."
}

foreach ($record in $partRecords) {
    $partPath = Join-Path $DownloadDirectory $record.Name
    if (-not (Test-Path -LiteralPath $partPath -PathType Leaf)) {
        throw "Missing part: $($record.Name)"
    }
    $actual = (Get-FileHash -LiteralPath $partPath -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actual -ne $record.Sha256) {
        throw "Part SHA-256 mismatch: $($record.Name)"
    }
    Write-Host "Verified $($record.Name)"
}

$outputPath = Join-Path $DownloadDirectory $metadata["source-name"]
$expectedBytes = [long]$metadata["source-bytes"]
$expectedHash = $metadata["source-sha256"].ToLowerInvariant()

if (Test-Path -LiteralPath $outputPath -PathType Leaf) {
    $existing = Get-Item -LiteralPath $outputPath
    $existingHash = (Get-FileHash -LiteralPath $outputPath -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($existing.Length -eq $expectedBytes -and $existingHash -eq $expectedHash) {
        Write-Host "The verified ZIP already exists: $outputPath"
        exit 0
    }
    throw "An invalid ZIP already exists. Move it aside, then run this script again: $outputPath"
}

$temporaryPath = "$outputPath.assembling"
if (Test-Path -LiteralPath $temporaryPath) {
    throw "A previous temporary file exists. Move it aside, then run again: $temporaryPath"
}

$buffer = New-Object byte[] (8MB)
$output = [System.IO.File]::Open(
    $temporaryPath,
    [System.IO.FileMode]::CreateNew,
    [System.IO.FileAccess]::Write,
    [System.IO.FileShare]::None
)

try {
    foreach ($record in $partRecords) {
        $partPath = Join-Path $DownloadDirectory $record.Name
        $input = [System.IO.File]::OpenRead($partPath)
        try {
            while (($read = $input.Read($buffer, 0, $buffer.Length)) -gt 0) {
                $output.Write($buffer, 0, $read)
            }
        }
        finally {
            $input.Dispose()
        }
    }
}
finally {
    $output.Dispose()
}

$temporary = Get-Item -LiteralPath $temporaryPath
if ($temporary.Length -ne $expectedBytes) {
    throw "Merged ZIP size mismatch: expected $expectedBytes, got $($temporary.Length)."
}

$actualHash = (Get-FileHash -LiteralPath $temporaryPath -Algorithm SHA256).Hash.ToLowerInvariant()
if ($actualHash -ne $expectedHash) {
    throw "Merged ZIP SHA-256 mismatch: expected $expectedHash, got $actualHash."
}

Move-Item -LiteralPath $temporaryPath -Destination $outputPath
Write-Host "Verified ZIP created: $outputPath"
Write-Host "SHA-256: $actualHash"
