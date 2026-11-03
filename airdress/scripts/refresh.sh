#!/usr/bin/env bash
# Writes the patch files back from an applied tree: one patch per commit
# between the base and HEAD, in order, named after patches/series.
#
#   airdress/scripts/refresh.sh [--repo DIR] [--base REF] [--add GROUP/TOPIC]...
#
#   --repo DIR          the applied tree (default: ./applied).
#   --base REF          the tag (or commit) the tree was applied onto (default:
#                       the series' base). A re-apply passes the new one;
#                       series is rewritten to name it.
#   --add GROUP/TOPIC   a commit past the last listed patch is a new patch in
#                       GROUP (infra or ours), named GROUP/NNNN-TOPIC.patch.
#                       One --add per new commit, in order. The globe group is
#                       written by import-globe.sh, never added here.
#
# A file is rewritten only when its bytes change, so a refresh of an
# unchanged tree touches nothing. Patch files no longer in the series are
# removed.
set -euo pipefail
source "$(dirname "$0")/lib/common.sh"

repo="$PWD/applied"
base=""
adds=()

while [ $# -gt 0 ]; do
    case "$1" in
        --repo) repo="$2"; shift 2 ;;
        --base) base="$2"; shift 2 ;;
        --add) adds+=("$2"); shift 2 ;;
        -h|--help) sed -n '2,19p' "$0"; exit 0 ;;
        *) die "unknown argument: $1" ;;
    esac
done

[ -n "$base" ] || base="$(series_base)"
globe_pr="$(series_value globe-pr || true)"
mapfile -t names < <(series_names)
base_id="$(base_commit "$repo" "$base")" || die "$repo has no $base"
mapfile -t commits < <(git -C "$repo" rev-list --reverse "$base_id..HEAD")

for c in "${commits[@]}"; do
    subject="$(git -C "$repo" log -1 --format=%s "$c")"
    case "$subject" in
        "Release android "*) die "HEAD includes a release commit ($subject); refresh a working tree, not a release" ;;
    esac
done

next_number() {
    local group="$1" n=0 name num
    for name in "${names[@]}"; do
        [ "$(group_of "$name")" = "$group" ] || continue
        num="$(basename "$name" | cut -d- -f1)"
        [ "$((10#$num))" -gt "$n" ] && n="$((10#$num))"
    done
    printf '%04d' $((n + 1))
}

for add in "${adds[@]}"; do
    group="${add%%/*}"
    topic="${add#*/}"
    case "$group" in infra|ours) ;; *) die "--add takes infra/TOPIC or ours/TOPIC, not $add" ;; esac
    [[ "$topic" =~ ^[a-z0-9]+(-[a-z0-9]+)*$ ]] || die "a topic is lowercase words joined by '-': $topic"
    # A new infra patch goes after the last infra patch, a new ours patch at
    # the end; the commits must already be in that order.
    names+=("$group/$(next_number "$group")-$topic.patch")
done

if [ "${#commits[@]}" -ne "${#names[@]}" ]; then
    die "$repo has ${#commits[@]} commits on $base, the series names ${#names[@]} patches (use --add GROUP/TOPIC for a new one, or remove a dropped one from patches/series first)"
fi

write_if_changed() {
    local target="$1" tmp="$2"
    if [ -f "$target" ] && cmp -s "$tmp" "$target"; then
        rm -f "$tmp"
        return 1
    fi
    chmod 644 "$tmp"
    mkdir -p "$(dirname "$target")"
    mv "$tmp" "$target"
    return 0
}

changed=0
for i in "${!commits[@]}"; do
    tmp="$(mktemp "$PATCH_DIR/.refresh.XXXXXX")"
    git_pure -C "$repo" -c core.abbrev=12 -c diff.renames=true -c diff.algorithm=myers \
        -c diff.submodule=short \
        format-patch -1 --stdout --no-signature --zero-commit --no-numbered --full-index --binary \
        "${commits[$i]}" > "$tmp"
    if write_if_changed "$PATCH_DIR/${names[$i]}" "$tmp"; then
        echo "wrote patches/${names[$i]}"
        changed=1
    fi
done

tmp="$(mktemp "$PATCH_DIR/.refresh.XXXXXX")"
{
    echo "# The upstream base the series applies to, the globe pull request's"
    echo "# recorded head, then the patches in order: infra, globe, ours."
    echo "# Maintained by airdress/scripts/refresh.sh and import-globe.sh; see"
    echo "# airdress/README.md."
    echo "base $base"
    [ -z "$globe_pr" ] || echo "globe-pr $globe_pr"
    printf '%s\n' "${names[@]}"
} > "$tmp"
if write_if_changed "$SERIES_FILE" "$tmp"; then
    echo "wrote patches/series"
    changed=1
fi

while IFS= read -r f; do
    rel="${f#"$PATCH_DIR"/}"
    keep=0
    for name in "${names[@]}"; do [ "$rel" = "$name" ] && keep=1; done
    if [ "$keep" -eq 0 ]; then
        rm -f "$f"
        echo "removed patches/$rel"
        changed=1
    fi
done < <(find "$PATCH_DIR" -name '*.patch' -type f | sort)

[ "$changed" -eq 1 ] || echo "unchanged: ${#names[@]} patches on $base"
