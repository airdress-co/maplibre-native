#!/usr/bin/env bash
# Builds an applied tree: the upstream base named in patches/series, with every
# patch applied in order as one commit each (git am -3), then the submodules
# checked out at the applied tree's gitlinks.
#
#   airdress/scripts/apply.sh [--repo DIR] [--base REF] [--branch NAME]
#                             [--release N] [--no-submodules] [--upstream URL]
#
#   --repo DIR        the git repository to apply in (default: ./applied).
#                     Created and fetched from upstream if it does not exist.
#   --base REF        apply onto this tag (or commit id) instead of the series'.
#   --branch NAME     the local branch to (re)create (default: applied/<base>).
#   --release N       a release: also commit both platforms' versions as
#                     <upstream version>-airdress.N (platform/android/VERSION,
#                     platform/ios/VERSION).
#   --no-submodules   skip `git submodule update` (the series checks need none).
#   --upstream URL    where upstream's tags come from.
#
# On a conflict the `git am` is left in progress in DIR, so the patch that
# stopped can be resolved there and continued; the exit status is then 3.
set -euo pipefail
source "$(dirname "$0")/lib/common.sh"

repo="$PWD/applied"
base=""
branch=""
release=""
submodules=1
upstream="$UPSTREAM_URL_DEFAULT"

while [ $# -gt 0 ]; do
    case "$1" in
        --repo) repo="$2"; shift 2 ;;
        --base) base="$2"; shift 2 ;;
        --branch) branch="$2"; shift 2 ;;
        --release) release="$2"; shift 2 ;;
        --no-submodules) submodules=0; shift ;;
        --upstream) upstream="$2"; shift 2 ;;
        -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
        *) die "unknown argument: $1" ;;
    esac
done

[ -n "$base" ] || base="$(series_base)"
[ -n "$branch" ] || branch="applied/$base"
read_lines names series_names

for name in "${names[@]+"${names[@]}"}"; do
    group_of "$name" >/dev/null
    [ -f "$PATCH_DIR/$name" ] || die "patches/series lists $name, which does not exist"
done

mkdir -p "$repo"
repo="$(abspath "$repo")"
# The tree must be its own repository (or worktree), never a directory inside
# another one: the checkout below is forced.
if [ "$(git -C "$repo" rev-parse --show-toplevel 2>/dev/null)" != "$repo" ]; then
    [ -z "$(ls -A "$repo")" ] || die "$repo is not empty and not a repository of its own"
    # No template: a hook installed by the caller's git template would run the
    # applied tree's own pre-commit configuration on the series' commits.
    git -C "$repo" init -q --template=
fi
[ "$repo" != "$SERIES_ROOT" ] || die "refusing to apply into the series repository itself"
am_dir="$(git -C "$repo" rev-parse --absolute-git-dir)/rebase-apply"
git -C "$repo" remote get-url upstream >/dev/null 2>&1 \
    || git -C "$repo" remote add upstream "$upstream"
fetch_ref "$repo" "$base"

if [ -d "$am_dir" ]; then
    die "$repo has a git am in progress; finish or abort it first"
fi
git -C "$repo" checkout -q -f -B "$branch" "$(base_commit "$repo" "$base")"

if [ "${#names[@]}" -gt 0 ]; then
    patch_paths=()
    for name in "${names[@]+"${names[@]}"}"; do patch_paths+=("$PATCH_DIR/$name"); done
    if ! git_pure -C "$repo" -c user.name="$GIT_COMMITTER_NAME" -c user.email="$GIT_COMMITTER_EMAIL" \
            am -q -3 --keep-cr --committer-date-is-author-date "${patch_paths[@]}"; then
        next="$(cat "$am_dir/next" 2>/dev/null || echo 0)"
        echo "conflict: patch $next of ${#names[@]} (${names[$((next - 1))]:-?}) does not apply to $base" >&2
        echo "${names[$((next - 1))]:-?}" > "$(dirname "$am_dir")/series-stopped-at"
        echo "resolve in $repo, then 'git am --continue' and airdress/scripts/refresh.sh" >&2
        exit 3
    fi
fi

if [ -n "$release" ]; then
    [[ "$release" =~ ^[1-9][0-9]*$ ]] || die "--release takes the release number n, not $release"
    for platform in android ios; do
        file="$repo/platform/$platform/VERSION"
        [ -f "$file" ] || die "the base has no platform/$platform/VERSION"
        upstream_version="$(tr -d '[:space:]' < "$file")"
        case "$upstream_version" in
            *-airdress.*) die "platform/$platform/VERSION already carries a fork version: $upstream_version" ;;
        esac
        echo "$upstream_version-airdress.$release" > "$file"
        git -C "$repo" add "platform/$platform/VERSION"
    done
    head_date="$(git -C "$repo" log -1 --format=%aI)"
    GIT_AUTHOR_NAME="$GIT_COMMITTER_NAME" GIT_AUTHOR_EMAIL="$GIT_COMMITTER_EMAIL" \
        GIT_AUTHOR_DATE="$head_date" GIT_COMMITTER_DATE="$head_date" \
        git_pure -C "$repo" commit -q -m "Release android $(cat "$repo/platform/android/VERSION"), ios $(cat "$repo/platform/ios/VERSION")"
fi

if [ "$submodules" -eq 1 ]; then
    # Only the gitlinks the applied tree pins, nothing newer.
    git -C "$repo" submodule update -q --init --recursive --depth=1 \
        || git -C "$repo" submodule update -q --init --recursive
fi

echo "applied ${#names[@]} patches onto $base: $(git -C "$repo" rev-parse HEAD) (branch $branch, tree $(git -C "$repo" rev-parse 'HEAD^{tree}'))"
