#!/usr/bin/env python3
"""Holds the series together.

    airdress/scripts/series-check.py [--apply] [--repo DIR] [--globe]
                                     [--divergence-out FILE]

Always (offline, seconds):
  - patches/series names a base, every listed patch exists, and every
    patch file under patches/ is listed;
  - patches/series.toml has exactly one entry per listed patch, in a group
    matching its directory, and the groups come in order: infra, globe,
    ours;
  - infra patches are never offered; ours carry an offer state; globe
    patches name the pull request and its author;
  - a globe group comes with a globe-pr line naming a full commit id, and
    a globe-pr line with a globe group.

--apply (minutes): apply.sh builds the series in DIR (default ./applied,
without submodules), then refresh.sh on that tree must leave every patch
file byte for byte as committed: a hand-edited patch fails here. The
divergence per group is printed, and written as Markdown with
--divergence-out.

--globe (minutes): import-globe.py --check regenerates the globe group from
the recorded head and base and compares the bytes. A hand-edited
globe/*.patch fails here even when it still applies.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path

SERIES_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = SERIES_ROOT / "airdress" / "scripts"
GROUPS = ("infra", "globe", "ours")
OURS_OFFERS = {"undecided", "owner-declined", "offered-to-author", "offered-upstream", "taken",
               "merged", "superseded"}
OURS_ROUTES = {"", "author-branch", "review-comment", "upstream-pr"}
# Where divergence is counted: the code a reviewer of upstream reads.
DIVERGENCE_ROOTS = ("src/", "include/", "shaders/", "platform/", "test/", "render-test/", "expression-test/")
OURS_SOFT_CEILING = 3000


def patch_dir() -> Path:
    return Path(os.environ.get("PATCH_DIR_OVERRIDE", SERIES_ROOT / "patches"))


def read_series(path: Path) -> tuple[str | None, str | None, list[str]]:
    base = globe_pr = None
    names: list[str] = []
    for line in path.read_text().splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        parts = line.split()
        if parts[0] == "base":
            base = parts[1] if len(parts) > 1 else ""
        elif parts[0] == "globe-pr":
            globe_pr = " ".join(parts[1:])
        else:
            names.append(parts[0])
    return base, globe_pr, names


def check_structure(pdir: Path) -> list[str]:
    errors: list[str] = []
    series_file = pdir / "series"
    if not series_file.exists():
        return ["patches/series does not exist"]
    base, globe_pr, names = read_series(series_file)
    if not base:
        errors.append("patches/series has no 'base <tag or commit>' line")

    listed = set(names)
    if len(listed) != len(names):
        errors.append("patches/series lists a patch twice")
    on_disk = {str(p.relative_to(pdir)) for p in pdir.rglob("*.patch")}
    for name in names:
        if name not in on_disk:
            errors.append(f"patches/series lists {name}, which does not exist")
    for name in sorted(on_disk - listed):
        errors.append(f"patches/{name} is not in patches/series")

    # Groups in order, each patch in its group's directory.
    order = []
    for name in names:
        group = name.split("/", 1)[0] if "/" in name else ""
        if group not in GROUPS:
            errors.append(f"{name}: a patch lives under infra/, globe/ or ours/")
            continue
        order.append(GROUPS.index(group))
    if order != sorted(order):
        errors.append("patches/series must list infra, then globe, then ours")

    has_globe = any(n.startswith("globe/") for n in names)
    if has_globe and not globe_pr:
        errors.append("a globe group needs a 'globe-pr maplibre/maplibre-native#4533 <head>' line")
    if globe_pr:
        m = re.fullmatch(r"maplibre/maplibre-native#4533 ([0-9a-f]{40})", globe_pr)
        if not m:
            errors.append(f"globe-pr must be 'maplibre/maplibre-native#4533 <40-hex head>', not {globe_pr!r}")
        if not has_globe:
            errors.append("patches/series has a globe-pr line and no globe patches")

    toml_file = pdir / "series.toml"
    if not toml_file.exists():
        errors.append("patches/series.toml does not exist")
        return errors
    try:
        entries = tomllib.loads(toml_file.read_text()).get("patch", [])
    except tomllib.TOMLDecodeError as e:
        return errors + [f"patches/series.toml: {e}"]
    by_file: dict[str, dict] = {}
    for e in entries:
        f = e.get("file", "")
        if f in by_file:
            errors.append(f"series.toml has two entries for {f}")
        by_file[f] = e
    for name in names:
        if name not in by_file:
            errors.append(f"series.toml has no entry for {name}")
    for f in by_file:
        if f not in listed:
            errors.append(f"series.toml has an entry for {f}, which patches/series does not list")
    if [e.get("file") for e in entries if e.get("file") in listed] != [n for n in names if n in by_file]:
        errors.append("series.toml's entries are not in patches/series's order")

    for name in names:
        e = by_file.get(name)
        if e is None:
            continue
        group = name.split("/", 1)[0]
        if e.get("group") != group:
            errors.append(f"{name}: group = {e.get('group')!r}, its directory says {group!r}")
        if not e.get("drop_when"):
            errors.append(f"{name}: no drop_when")
        if group == "infra":
            if e.get("offer") != "never":
                errors.append(f"{name}: an infra patch is never offered (offer = \"never\")")
            if not e.get("summary"):
                errors.append(f"{name}: no summary")
        elif group == "globe":
            if e.get("upstream") != "maplibre/maplibre-native#4533":
                errors.append(f"{name}: upstream = \"maplibre/maplibre-native#4533\"")
            if not e.get("author"):
                errors.append(f"{name}: no author (kept from the pull request)")
            if "offer" in e:
                errors.append(f"{name}: a globe patch is the author's; it has no offer state")
        else:
            if e.get("offer") not in OURS_OFFERS:
                errors.append(f"{name}: offer must be one of {sorted(OURS_OFFERS)}")
            if e.get("route", "") not in OURS_ROUTES:
                errors.append(f"{name}: route must be one of {sorted(OURS_ROUTES - {''})}")
            if e.get("offer") in {"offered-to-author", "offered-upstream", "taken", "merged"} and not e.get("link"):
                errors.append(f"{name}: an offer that was made carries its link")
            if not e.get("summary"):
                errors.append(f"{name}: no summary")
            if not e.get("tests"):
                errors.append(f"{name}: no tests")
    return errors


def run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, **kw)


def check_apply(repo: Path) -> tuple[list[str], str]:
    errors: list[str] = []
    proc = run([str(SCRIPTS / "apply.sh"), "--repo", str(repo), "--no-submodules"])
    if proc.returncode != 0:
        return [f"the series does not apply (apply.sh exited {proc.returncode})"], ""

    # A refresh of the tree just built must reproduce every committed byte.
    pdir = patch_dir()
    with tempfile.TemporaryDirectory() as tmp:
        shadow = Path(tmp) / "patches"
        shutil.copytree(pdir, shadow)
        env = {**os.environ, "PATCH_DIR_OVERRIDE": str(shadow)}
        proc = run([str(SCRIPTS / "refresh.sh"), "--repo", str(repo)], env=env, capture_output=True, text=True)
        if proc.returncode != 0:
            errors.append(f"refresh.sh failed on the applied tree: {proc.stderr.strip()}")
        else:
            diff = run(["diff", "-r", "-q", str(pdir), str(shadow)], capture_output=True, text=True)
            if diff.returncode != 0:
                errors.append("a refresh of the applied tree changes the patches (a hand edit, or a patch"
                              " not written by refresh.sh):\n  " + diff.stdout.strip().replace("\n", "\n  "))
    return errors, divergence(repo)


def divergence(repo: Path) -> str:
    base, _, names = read_series(patch_dir() / "series")
    base_id = run(["git", "-C", str(repo), "rev-parse", f"{base}^{{commit}}"], capture_output=True,
                  text=True).stdout.strip()
    commits = run(["git", "-C", str(repo), "rev-list", "--reverse", f"{base_id}..HEAD"], capture_output=True,
                  text=True).stdout.split()
    totals = {g: [0, 0, 0] for g in GROUPS}  # changed lines in existing files, new files, their lines
    rows = []
    for name, c in zip(names, commits):
        group = name.split("/", 1)[0]
        changed = new_files = new_lines = 0
        status = run(["git", "-C", str(repo), "diff", "--no-renames", "--name-status", f"{c}~1", c],
                     capture_output=True, text=True).stdout.splitlines()
        added = {line.split("\t", 1)[1] for line in status if line.startswith("A\t")}
        numstat = run(["git", "-C", str(repo), "diff", "--no-renames", "--numstat", f"{c}~1", c],
                      capture_output=True, text=True).stdout.splitlines()
        for line in numstat:
            a, d, path = line.split("\t", 2)
            if not path.startswith(DIVERGENCE_ROOTS) or a == "-":
                continue
            if path in added:
                new_files += 1
                new_lines += int(a)
            else:
                changed += int(a) + int(d)
        rows.append(f"| `{name}` | {changed} | {new_files} | {new_lines} |")
        t = totals[group]
        t[0] += changed
        t[1] += new_files
        t[2] += new_lines
    out = ["## Divergence from the base", "",
           f"Base `{base}`. Lines in {', '.join(f'`{r}`' for r in DIVERGENCE_ROOTS)}.", "",
           "| Patch | Changed lines in existing files | New files | Their lines |", "|---|---|---|---|",
           *rows, "", "| Group | Changed lines | New files | Their lines |", "|---|---|---|---|"]
    for g in GROUPS:
        out.append(f"| {g} | {totals[g][0]} | {totals[g][1]} | {totals[g][2]} |")
    if totals["ours"][0] > OURS_SOFT_CEILING:
        out += ["", f"**ours is over its soft ceiling of {OURS_SOFT_CEILING} changed lines.**"]
    return "\n".join(out) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--repo", default="applied")
    ap.add_argument("--globe", action="store_true")
    ap.add_argument("--divergence-out")
    args = ap.parse_args()

    errors = check_structure(patch_dir())
    if errors:
        for e in errors:
            print(f"series-check: {e}", file=sys.stderr)
        return 1
    print("series-check: files, series and entries agree")

    if args.apply:
        errs, table = check_apply(Path(args.repo).resolve())
        if errs:
            for e in errs:
                print(f"series-check: {e}", file=sys.stderr)
            return 1
        print("series-check: the series applies, and a refresh reproduces every patch")
        print(table)
        if args.divergence_out:
            Path(args.divergence_out).write_text(table)

    if args.globe:
        _, globe_pr, _ = read_series(patch_dir() / "series")
        if globe_pr:
            with tempfile.TemporaryDirectory() as tmp:
                proc = run([sys.executable, str(SCRIPTS / "import-globe.py"), "--check", "--repo",
                            str(Path(tmp) / "import")])
            if proc.returncode != 0:
                print("series-check: the globe group is not a fresh import of its recorded head", file=sys.stderr)
                return 1
        else:
            print("series-check: no globe group")
    return 0


if __name__ == "__main__":
    sys.exit(main())
