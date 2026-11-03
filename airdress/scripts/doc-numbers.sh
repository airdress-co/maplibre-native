#!/usr/bin/env bash
# Every patch is written so it could be offered upstream as it is, so the
# lines a patch adds carry no internal document numbers (spec, research or
# runbook ids), not even in comments, and an offerable patch names no
# Airdress thing. Patch file names and the branch being built are held to the
# first rule too. Upstream's own files are never read.
#
#   airdress/scripts/doc-numbers.sh [BRANCH]
#
# Infra patches (offer = "never") may name Airdress; they are never offered.
# Globe patches are the pull request's author's work and are checked like an
# offerable patch: if one ever named us, the import would be wrong.
set -euo pipefail
source "$(dirname "$0")/lib/common.sh"

doc_number='(^|[^[:alnum:]])(spec|rdr|rcp|rb)[-_]?[0-9]{3}([^[:alnum:]]|$)'
status=0

never_offered() {
    awk -v f="$1" '
        /^\[\[patch\]\]/ { file = ""; offer = "" }
        $1 == "file" { gsub(/"/, "", $3); file = $3 }
        $1 == "offer" { gsub(/"/, "", $3); offer = $3 }
        file == f && offer == "never" { found = 1 }
        END { exit found ? 0 : 1 }' "$PATCH_DIR/series.toml"
}

# report NAME ADDED PATTERN WHAT: the added lines matching PATTERN, numbered among the added lines.
report() {
    printf '%s\n' "$2" | grep -inE "$3" | sed "s|^|doc-numbers: $1: $4: added line |" >&2
}

branch="${1:-}"
if [ -n "$branch" ] && echo "$branch" | grep -qiE "$doc_number"; then
    echo "doc-numbers: branch name '$branch' carries a document number" >&2
    status=1
fi

while IFS= read -r f; do
    name="${f#"$PATCH_DIR"/}"
    if echo "$name" | grep -qiE "$doc_number"; then
        echo "doc-numbers: patch file name $name carries a document number" >&2
        status=1
    fi
    # Added lines only: '+' inside a hunk, never the '+++' header.
    added="$(awk '/^diff --git /{h=0} /^@@/{h=1; next} h && /^\+/ {print substr($0, 2)}' "$f")"
    if printf '%s\n' "$added" | grep -qiE "$doc_number"; then
        report "$name" "$added" "$doc_number" "document number"
        status=1
    fi
    if ! never_offered "$name" && printf '%s\n' "$added" | grep -qi 'airdress'; then
        report "$name" "$added" 'airdress' "an offerable patch names Airdress"
        status=1
    fi
done < <(find "$PATCH_DIR" -name '*.patch' -type f | sort)

[ "$status" -eq 0 ] && echo "doc-numbers: no document numbers in the patches' added lines"
exit "$status"
