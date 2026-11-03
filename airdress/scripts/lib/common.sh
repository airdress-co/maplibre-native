# Shared by the series scripts. Sourced, not executed.

# The series directory: the root of this repository's main branch.
SERIES_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
# Both overridable, so import-globe.py --check can regenerate into a copy.
PATCH_DIR="${PATCH_DIR_OVERRIDE:-$SERIES_ROOT/patches}"
SERIES_FILE="${SERIES_FILE_OVERRIDE:-$PATCH_DIR/series}"

UPSTREAM_URL_DEFAULT="https://github.com/maplibre/maplibre-native.git"

# Every commit the tooling makes carries the same committer, and its date is
# the author date, so applying the same series to the same base gives the same
# commit ids on any machine.
export GIT_COMMITTER_NAME="${SERIES_COMMITTER_NAME:-maplibre-native fork series}"
export GIT_COMMITTER_EMAIL="${SERIES_COMMITTER_EMAIL:-fork-series@noreply.airdress.co}"

# Git output the patch files depend on must not depend on whoever runs it.
git_pure() {
    GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_SYSTEM=/dev/null git "$@"
}

# read_lines NAME CMD...: CMD's output lines into the array NAME. macOS ships
# bash 3.2, which has no mapfile; the release builds iOS there.
read_lines() {
    local __name="$1" __line
    shift
    eval "$__name=()"
    while IFS= read -r __line; do
        eval "$__name+=(\"\$__line\")"
    done < <("$@")
}

# abspath DIR: an existing directory's absolute path (no GNU realpath on macOS).
abspath() {
    (cd "$1" && pwd -P)
}

die() {
    echo "error: $*" >&2
    exit 1
}

# fetch_ref DIR REF: make an upstream tag (or a commit id) available in DIR.
# A tag is fetched shallowly; a three-way `git am` onto a new base also needs
# the old base's blobs, so a re-apply fetches both.
fetch_ref() {
    local dir="$1" ref="$2"
    if git -C "$dir" rev-parse -q --verify "$ref^{commit}" >/dev/null 2>&1; then
        return 0
    fi
    if [[ "$ref" =~ ^[0-9a-f]{40}$ ]]; then
        git -C "$dir" fetch -q --no-tags --depth=1 upstream "$ref" \
            || die "cannot fetch $ref from $(git -C "$dir" remote get-url upstream)"
    else
        git -C "$dir" fetch -q --no-tags --depth=1 upstream "refs/tags/$ref:refs/tags/$ref" \
            || die "cannot fetch tag $ref from $(git -C "$dir" remote get-url upstream)"
    fi
}

# base_commit DIR REF: the commit a base names, a tag or a commit id.
base_commit() {
    git -C "$1" rev-parse "$2^{commit}"
}

# series_value KEY: the value on a `KEY value…` line of patches/series.
series_value() {
    local key="$1" file="${2:-$SERIES_FILE}"
    awk -v k="$key" '$1 == k { $1 = ""; sub(/^ /, ""); print; found = 1; exit } END { if (!found) exit 1 }' "$file"
}

# series_base: the upstream tag (or commit) on the `base` line.
series_base() {
    series_value base "${1:-$SERIES_FILE}" || die "${1:-$SERIES_FILE} has no 'base <tag>' line"
}

# series_names: the patch paths under patches/, in order.
series_names() {
    local file="${1:-$SERIES_FILE}"
    awk '/^[[:space:]]*(#|$)/ { next } $1 == "base" || $1 == "globe-pr" { next } { print $1 }' "$file"
}

# group_of NAME: infra, globe or ours, from the patch's directory.
group_of() {
    case "$1" in
        infra/*) echo infra ;;
        globe/*) echo globe ;;
        ours/*) echo ours ;;
        *) die "a patch lives under infra/, globe/ or ours/: $1" ;;
    esac
}
