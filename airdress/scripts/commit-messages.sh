#!/usr/bin/env bash
# This repository is public, and so is every word in its history: commit
# messages, the commit messages inside the patch files, pull request titles
# and bodies. None of them may carry the names of the fork maintainers'
# internal documents, trackers or repositories. Those are references nobody
# outside can follow, and they leak how the work is organised.
#
#   airdress/scripts/commit-messages.sh --range A..B [--repo DIR]   commits in a range
#   airdress/scripts/commit-messages.sh --file MSGFILE              one message (commit-msg hook)
#   airdress/scripts/commit-messages.sh --stdin LABEL               any text, e.g. a PR title or body
#
# The patterns are in airdress/internal-patterns.txt: one extended regex per
# line, matched case-insensitively. Exit status 1 names every offending line.
set -euo pipefail
source "$(dirname "$0")/lib/common.sh"

PATTERNS="$SERIES_ROOT/airdress/internal-patterns.txt"
[ -f "$PATTERNS" ] || die "no $PATTERNS"
status=0

# check LABEL: reads text on stdin and reports each line matching any pattern.
check() {
    local label="$1" text n=0 line pat
    text="$(cat)"
    while IFS= read -r line || [ -n "$line" ]; do
        n=$((n + 1))
        # The patterns file's own comments and blank lines are not patterns.
        while IFS= read -r pat || [ -n "$pat" ]; do
            case "$pat" in ''|'#'*) continue ;; esac
            if printf '%s\n' "$line" | grep -qiE -- "$pat"; then
                echo "commit-messages: $label, line $n: matches /$pat/: $line" >&2
                status=1
                break
            fi
        done < "$PATTERNS"
    done <<< "$text"
}

mode="${1:-}"
case "$mode" in
    --range)
        range="${2:?--range takes A..B}"
        repo="."
        [ "${3:-}" != "--repo" ] || repo="${4:?--repo takes a directory}"
        while IFS= read -r c; do
            [ -n "$c" ] || continue
            # Not a pipe: check must run in this shell, or its status is lost.
            check "commit $(git -C "$repo" rev-parse --short=10 "$c")" < <(git -C "$repo" log -1 --format=%B "$c")
        done < <(git -C "$repo" rev-list "$range")
        ;;
    --file)
        # Git's own comment lines in the editor buffer are not the message.
        check "this commit message" < <(grep -v '^#' "${2:?--file takes the message file}" || true)
        ;;
    --stdin)
        check "${2:-input}"
        ;;
    *)
        sed -n '2,15p' "$0"
        exit 2
        ;;
esac

[ "$status" -eq 0 ] && echo "commit-messages: no internal references"
exit "$status"
