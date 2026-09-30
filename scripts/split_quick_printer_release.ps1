param(
    [Parameter(Mandatory = $true)]
    [string]$ArchivePath,

    [ValidateRange(1048576, 536000000)]
    [long]$PartBytes = 500000000
)

$ErrorActionPreference = "Stop"

$archive = Get-Item -LiteralPath $ArchivePath
if (-not $archive.Name.EndsWith(".zip", [StringComparison]::OrdinalIgnoreCase)) {
    throw "ArchivePath must point to a ZIP file."
}

$outputDirectory = $archive.DirectoryName
$buffer = New-Object byte[] (8MB)
$source = [System.IO.File]::OpenRead($archive.FullName)
$partRecords = [System.Collections.Generic.List[object]]::new()

try {
    $partNumber = 1
    while ($source.Position -lt $source.Length) {
        $partName = "{0}.part{1:D3}" -f $archive.Name, $partNumber
        $partPath = Join-Path $outputDirectory $partName
        if (Test-Path -LiteralPath $partPath) {
            throw "Part already exists: $partPath"
        }

        $remaining = [Math]::Min($PartBytes, $source.Length - $source.Position)
        $part = [System.IO.File]::Open(
            $partPath,
            [System.IO.FileMode]::CreateNew,
            [System.IO.FileAccess]::Write,
            [System.IO.FileShare]::None
        )
        try {
            while ($remaining -gt 0) {
                $requested = [int][Math]::Min($buffer.Length, $remaining)
                $read = $source.Read($buffer, 0, $requested)
                if ($read -le 0) {
                    throw "Unexpected end of archive while creating $partName"
                }
                $part.Write($buffer, 0, $read)
                $remaining -= $read
            }
        }
        finally {
            $part.Dispose()
        }

        $partHash = (Get-FileHash -LiteralPath $partPath -Algorithm SHA256).Hash.ToLowerInvariant()
        $partInfo = Get-Item -LiteralPath $partPath
        $partRecords.Add([pscustomobject]@{
            Name = $partName
            Length = $partInfo.Length
            Sha256 = $partHash
        })
        Write-Host ("Created {0} ({1:N0} bytes)" -f $partName, $partInfo.Length)
        $partNumber++
    }
}
finally {
    $source.Dispose()
}

$archiveHash = (Get-FileHash -LiteralPath $archive.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
$manifestPath = "$($archive.FullName).parts.sha256"
$manifestLines = [System.Collections.Generic.List[string]]::new()
$manifestLines.Add("# source-name=$($archive.Name)")
$manifestLines.Add("# source-bytes=$($archive.Length)")
$manifestLines.Add("# source-sha256=$archiveHash")
$manifestLines.Add("# part-bytes=$PartBytes")
$manifestLines.Add("# part-count=$($partRecords.Count)")
foreach ($record in $partRecords) {
    $manifestLines.Add("$($record.Sha256) *$($record.Name)")
}
[System.IO.File]::WriteAllLines($manifestPath, $manifestLines, [System.Text.UTF8Encoding]::new($false))

[pscustomobject]@{
    Archive = $archive.FullName
    ArchiveBytes = $archive.Length
    ArchiveSha256 = $archiveHash
    PartCount = $partRecords.Count
    PartManifest = $manifestPath
}
