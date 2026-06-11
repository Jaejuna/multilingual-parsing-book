#!/usr/bin/env bash
# inspect-file-encoding.sh
#
# Quickly determine the encoding of a text/CSV file on Unix.
#
# Usage:
#   ./inspect-file-encoding.sh <path>
#
# Output:
#   path: ...
#   size: ... bytes
#   file(1) guess: ...
#   first bytes: EF BB BF ...
#   verdict: UTF-8 with BOM
#
# The script combines four signals:
#
#   1. The first 3 bytes — UTF-8 / UTF-16 BOMs are unambiguous.
#   2. `file -bi` — uses libmagic, generally reliable but can be wrong
#      on short files. We print it as a sanity check.
#   3. Strict UTF-8 decode via `iconv -f UTF-8 -t UTF-8`. If iconv
#      complains about an invalid sequence, the file isn't valid UTF-8.
#   4. A cp949 / EUC-KR fallback decode attempt for Korean projects.
#
# Heuristics, not proofs. The hex preview and `file` guess are there so
# a human can override the verdict when in doubt.

set -euo pipefail

if [[ $# -lt 1 ]]; then
    echo "usage: $0 <path>" >&2
    exit 2
fi

path="$1"
if [[ ! -f "$path" ]]; then
    echo "file not found: $path" >&2
    exit 1
fi

size=$(wc -c <"$path" | tr -d ' ')

# Hex preview — first 32 bytes, space-separated.
preview=$(head -c 32 "$path" | xxd -p | tr -d '\n' | sed 's/\(..\)/\1 /g' | sed 's/ $//')

# file(1) guess — informational only.
file_guess=$(file -bi "$path" 2>/dev/null || echo "file(1) unavailable")

verdict="unknown"

# Read first 3 bytes for BOM check, in hex.
first3=$(head -c 3 "$path" | xxd -p)
case "$first3" in
    efbbbf*) verdict="UTF-8 with BOM" ;;
    fffe*)   verdict="UTF-16 LE" ;;
    feff*)   verdict="UTF-16 BE" ;;
esac

if [[ "$verdict" == "unknown" ]]; then
    # Strict UTF-8 attempt. iconv exits non-zero on invalid sequences.
    if iconv -f UTF-8 -t UTF-8 "$path" >/dev/null 2>&1; then
        verdict="UTF-8 (no BOM)"
    elif iconv -f EUC-KR -t UTF-8 "$path" >/dev/null 2>&1; then
        verdict="Probably cp949/EUC-KR"
    else
        verdict="Not valid UTF-8 or EUC-KR — try shift_jis / gb18030 / etc."
    fi
fi

printf '%-12s %s\n' "path:"          "$path"
printf '%-12s %s\n' "size:"          "$size bytes"
printf '%-12s %s\n' "file(1):"       "$file_guess"
printf '%-12s %s\n' "first bytes:"   "$preview"
printf '%-12s %s\n' "verdict:"       "$verdict"
