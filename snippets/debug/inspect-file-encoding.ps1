<#
.SYNOPSIS
    Quickly determine the encoding of a text/CSV file on Windows.

.DESCRIPTION
    Prints the first 32 bytes as hex and a best-guess encoding label.
    Use this when "the file looks broken in Excel but fine in VSCode"
    or vice versa.

    Detection heuristics, in order:
      1. UTF-8 BOM (EF BB BF)        → "UTF-8 with BOM"
      2. UTF-16 LE BOM (FF FE)       → "UTF-16 LE"
      3. UTF-16 BE BOM (FE FF)       → "UTF-16 BE"
      4. Strict UTF-8 decode works   → "UTF-8 (no BOM)"
      5. Otherwise                   → "Probably cp949/EUC-KR"
                                       (on Korean Windows that's the
                                       most common non-UTF-8 source)

    Heuristics are NOT proofs — short files can pass strict UTF-8 by
    coincidence. The hex dump is included so you can verify by eye.

.PARAMETER Path
    File to inspect. Required.

.EXAMPLE
    PS> .\inspect-file-encoding.ps1 .\glossary.csv

    Path        : C:\work\glossary.csv
    Size        : 28473 bytes
    Encoding    : UTF-8 with BOM
    First bytes : EF BB BF 6B 6F 2D 4B 52 2C 65 6E 2D 55 53 2C 66 ...
#>

param(
    [Parameter(Mandatory = $true, Position = 0)]
    [string]$Path
)

if (-not (Test-Path $Path -PathType Leaf)) {
    Write-Error "File not found: $Path"
    exit 1
}

# Read raw bytes — using File.ReadAllBytes avoids PowerShell's text
# pipeline, which would attempt its own encoding detection and hide
# what we're trying to inspect.
$bytes = [System.IO.File]::ReadAllBytes($Path)

# Slice the first 32 bytes for display. PowerShell range slicing is
# inclusive on both ends; clamp so short files don't error.
$end = [Math]::Min(31, $bytes.Length - 1)
$preview = if ($bytes.Length -gt 0) {
    ($bytes[0..$end] | ForEach-Object { "{0:X2}" -f $_ }) -join ' '
} else {
    "(empty file)"
}

$encoding = "Unknown"

if ($bytes.Length -ge 3 -and $bytes[0] -eq 0xEF -and $bytes[1] -eq 0xBB -and $bytes[2] -eq 0xBF) {
    $encoding = "UTF-8 with BOM"
} elseif ($bytes.Length -ge 2 -and $bytes[0] -eq 0xFF -and $bytes[1] -eq 0xFE) {
    $encoding = "UTF-16 LE"
} elseif ($bytes.Length -ge 2 -and $bytes[0] -eq 0xFE -and $bytes[1] -eq 0xFF) {
    $encoding = "UTF-16 BE"
} else {
    # Try strict UTF-8 — UTF8Encoding with throwOnInvalidBytes=true.
    $utf8Strict = [System.Text.UTF8Encoding]::new($false, $true)
    try {
        $null = $utf8Strict.GetString($bytes)
        $encoding = "UTF-8 (no BOM)"
    } catch [System.Text.DecoderFallbackException] {
        # Strict UTF-8 failed → most likely cp949 in this environment.
        $encoding = "Probably cp949/EUC-KR (strict UTF-8 failed)"
    }
}

[PSCustomObject]@{
    Path        = (Resolve-Path $Path).Path
    Size        = "$($bytes.Length) bytes"
    Encoding    = $encoding
    'First bytes' = $preview
} | Format-List
