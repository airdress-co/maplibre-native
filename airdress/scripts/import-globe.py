#!/usr/bin/env python3
"""Imports upstream's globe pull request as one patch per phase.

    airdress/scripts/import-globe.py --head SHA [--repo DIR] [--upstream URL]
                                     [--check] [--no-refresh]

The pull request is its author's work. We carry it and never edit it by
hand: its patches are regenerated from its recorded head, and the only
changes we make are the conflict resolutions named in
airdress/globe-phases.toml.

How a head becomes patches:

1.  apply.sh builds base + the infra group in DIR, without submodules.
2.  The pull request's head and upstream main are fetched there, blobless.
    The phase ends come from the head's first-parent history. A commit titled
    "... (globe phase N)" belongs to phase N, an untitled one to the phase
    after it, and the last phase ends at the head.
3.  Phase k < last is a three-way merge of that phase's own change
    (phase k-1's end -> phase k's end) onto the tree so far. A hunk that
    conflicts there keeps the tree's side and is listed as deferred in the
    commit message: phases 1-11 were written on an older main than the
    base.
4.  The last phase's tree is exactly the whole pull request
    (merge-base(head, main) -> head) merged onto base + infra, with every
    conflict resolved by its rule in globe-phases.toml. A conflict with no
    rule stops the import. So the applied tree after the globe group never
    depends on the deferrals.
5.  Each phase is one commit, with the author and date of the phase's last
    commit and the fork series as committer. The group-3 patches are
    applied on top again, then refresh.sh writes patches/globe/*.patch,
    patches/series and the rest.

--check regenerates into a scratch tree and compares the bytes with the
committed globe patches, leaving the series untouched (series-check.py uses
it). Exit status: 0 ok, 1 error, 3 a conflict with no rule, 4 --check
found a difference.
"""

from __future__ import annotations

import argparse
import fnmatch
import os
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

SERIES_ROOT = Path(__file__).resolve().parents[2]
# Overridable as in lib/common.sh, so a check can run against a copy.
PATCH_DIR = Path(os.environ.get("PATCH_DIR_OVERRIDE", SERIES_ROOT / "patches"))
SERIES_FILE = Path(os.environ.get("SERIES_FILE_OVERRIDE", PATCH_DIR / "series"))
PHASES_FILE = SERIES_ROOT / "airdress" / "globe-phases.toml"
SCRIPTS = SERIES_ROOT / "airdress" / "scripts"
UPSTREAM_DEFAULT = "https://github.com/maplibre/maplibre-native.git"

COMMITTER = {
    "GIT_COMMITTER_NAME": os.environ.get("SERIES_COMMITTER_NAME", "maplibre-native fork series"),
    "GIT_COMMITTER_EMAIL": os.environ.get("SERIES_COMMITTER_EMAIL", "fork-series@noreply.airdress.co"),
}
PURE = {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull}
# "(globe phase 9)", "(globe phase 9a)", "(globe phase 12du)": the author's
# sub-steps run past one letter.
PHASE_RE = re.compile(r"\(globe phase (\d+)[a-z]*\)\s*$")


class Stop(Exception):
    def __init__(self, message: str, status: int = 1):
        super().__init__(message)
        self.status = status


def git(repo: Path, *args: str, env: dict | None = None, input: bytes | None = None,
        check: bool = True) -> subprocess.CompletedProcess:
    full_env = {**os.environ, **PURE, **(env or {})}
    proc = subprocess.run(["git", "-C", str(repo), *args], input=input, env=full_env,
                          capture_output=True)
    if check and proc.returncode != 0:
        raise Stop(f"git {' '.join(args[:3])}… failed: {proc.stderr.decode(errors='replace').strip()}")
    return proc


def out(repo: Path, *args: str, **kw) -> str:
    return git(repo, *args, **kw).stdout.decode().strip()


@dataclass
class Phase:
    """One contiguous run of a phase's commits on the head's first-parent line.

    The author's phases interleave (9a-9f, then 10a-10d, then 9g-9i), so a
    phase that resumes after another one is a second run: its own patch,
    named <name>-2.
    """
    index: int  # 1-based position in the series, the patch's NN
    number: int  # the phase number in the commit subjects
    run: int  # 1 for the phase's first run, 2 for its second, ...
    name: str
    title: str
    commits: list[str] = field(default_factory=list)  # first-parent, oldest first

    @property
    def end(self) -> str:
        return self.commits[-1]

    @property
    def topic(self) -> str:
        return self.name if self.run == 1 else f"{self.name}-{self.run}"

    @property
    def label(self) -> str:
        return f"phase {self.number}" + ("" if self.run == 1 else f", continued ({self.run})")


def read_series() -> tuple[str, list[str]]:
    base = None
    names = []
    for line in SERIES_FILE.read_text().splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        key = line.split()[0]
        if key == "base":
            base = line.split()[1]
        elif key == "globe-pr":
            continue
        else:
            names.append(key)
    if not base:
        raise Stop("patches/series has no base line")
    return base, names


def read_phases() -> dict:
    data = tomllib.loads(PHASES_FILE.read_text())
    if data.get("pull_request") != "maplibre/maplibre-native#4533":
        raise Stop("globe-phases.toml must name pull_request = maplibre/maplibre-native#4533")
    return data


def apply_infra(repo: Path, base: str, names: list[str], upstream: str) -> None:
    """base + the infra group only, through apply.sh, with a series file of its own."""
    infra = [n for n in names if n.startswith("infra/")]
    with tempfile.TemporaryDirectory() as tmp:
        series = Path(tmp) / "series"
        series.write_text(f"base {base}\n" + "".join(f"{n}\n" for n in infra))
        env = {**os.environ, "SERIES_FILE_OVERRIDE": str(series)}
        proc = subprocess.run([str(SCRIPTS / "apply.sh"), "--repo", str(repo), "--no-submodules",
                               "--upstream", upstream, "--branch", "import/globe"], env=env)
        if proc.returncode != 0:
            raise Stop("apply.sh could not build base + infra")


def fetch_globe(repo: Path, head: str, pr_ref: str) -> None:
    # apply.sh fetched the base at depth 1; merge-bases need the history,
    # commits and trees only.
    if out(repo, "rev-parse", "--is-shallow-repository") == "true":
        git(repo, "fetch", "-q", "--filter=blob:none", "--unshallow", "--no-tags", "upstream")
    git(repo, "fetch", "-q", "--filter=blob:none", "--no-tags", "upstream",
        f"+{pr_ref}:refs/globe/pr", "+refs/heads/main:refs/globe/main")
    fetched = out(repo, "rev-parse", "refs/globe/pr")
    if fetched != head:
        # A recorded head the pull request has since moved past: fetch the
        # commit itself, which upstream serves while it is reachable.
        git(repo, "fetch", "-q", "--filter=blob:none", "--no-tags", "upstream", head, check=False)
        if git(repo, "cat-file", "-e", f"{head}^{{commit}}", check=False).returncode != 0:
            raise Stop(f"{pr_ref} is at {fetched}, and {head} cannot be fetched")


def find_phases(repo: Path, head: str, phases_cfg: dict) -> tuple[str, list[Phase]]:
    merge_base = out(repo, "merge-base", head, "refs/globe/main")
    names = {int(k): v for k, v in phases_cfg["phase"].items()}
    log = out(repo, "log", "--first-parent", "--reverse", "--format=%H%x09%s", f"{merge_base}..{head}")
    runs: list[Phase] = []
    seen: dict[int, int] = {}
    pending: list[str] = []
    for line in log.splitlines():
        sha, subject = line.split("\t", 1)
        m = PHASE_RE.search(subject)
        if not m:
            pending.append(sha)
            continue
        n = int(m.group(1))
        if n not in names:
            raise Stop(f"phase {n} ({sha[:10]}) is not in globe-phases.toml; add its name there")
        if not runs or runs[-1].number != n:
            seen[n] = seen.get(n, 0) + 1
            runs.append(Phase(len(runs) + 1, n, seen[n], names[n]["name"], names[n]["title"]))
        # An untitled first-parent commit (a merge of main, a CI re-run)
        # belongs to the run it precedes.
        runs[-1].commits.extend(pending + [sha])
        pending = []
    if not runs:
        raise Stop("no '(globe phase N)' commit between the merge-base and the head")
    runs[-1].commits.extend(pending)  # the head's own trailing commits belong to the last run
    if runs[-1].end != head:
        raise Stop("internal: the last run does not end at the head")
    missing = sorted(set(names) - set(seen))
    if missing:
        raise Stop(f"globe-phases.toml names phases {missing} the head does not have")
    return merge_base, runs


@dataclass
class Conflict:
    path: str
    stages: dict[int, tuple[str, str]]  # stage -> (mode, blob)


def merge_trees(repo: Path, merge_base: str, ours: str, theirs: str) -> tuple[str, list[Conflict]]:
    proc = git(repo, "merge-tree", "--write-tree", "-z", "--no-messages",
               f"--merge-base={merge_base}", ours, theirs, check=False)
    if proc.returncode not in (0, 1):
        raise Stop(f"merge-tree failed: {proc.stderr.decode(errors='replace')}")
    fields = proc.stdout.split(b"\0")
    tree = fields[0].decode()
    conflicts: dict[str, Conflict] = {}
    for item in fields[1:]:
        if not item:
            break
        meta, path = item.decode().split("\t", 1)
        mode, blob, stage = meta.split()
        conflicts.setdefault(path, Conflict(path, {})).stages[int(stage)] = (mode, blob)
    return tree, list(conflicts.values())


def rule_for(path: str, rules: dict, head: str) -> str | None:
    """The rule for a conflicting path under this head, or None.

    A rule is either a string (ours, theirs, union), true for every head, or
    a table {rule, head}: written for one conflict at one head of the pull
    request. Under any other head it does not apply, so a re-import that
    meets the path in conflict again stops for a person to look, instead of
    resolving a conflict the rule was never written for.
    """
    entry = rules.get(path)
    if entry is None:
        for pattern, e in rules.items():
            if fnmatch.fnmatchcase(path, pattern):
                entry = e
                break
    if entry is None:
        return None
    if isinstance(entry, str):
        return entry
    if entry.get("head") and entry["head"] != head:
        return None
    return entry.get("rule")


def resolve(repo: Path, c: Conflict, rule: str) -> tuple[str, str] | None:
    """(mode, blob) for the path, or None to remove it."""
    ours, theirs, base = c.stages.get(2), c.stages.get(3), c.stages.get(1)
    if rule not in ("ours", "theirs", "union"):
        raise Stop(f"unknown rule {rule!r} for {c.path}")
    if ours is None or theirs is None:
        # Modify/delete or add/add without a base: whole-file choice.
        if rule == "union":
            raise Stop(f"{c.path}: 'union' needs both sides; it is a modify/delete conflict")
        return ours if rule == "ours" else theirs
    with tempfile.TemporaryDirectory() as tmp:
        files = {}
        for label, entry in (("ours", ours), ("base", base), ("theirs", theirs)):
            p = Path(tmp) / label
            p.write_bytes(git(repo, "cat-file", "blob", entry[1]).stdout if entry else b"")
            files[label] = str(p)
        proc = subprocess.run(["git", "merge-file", "-p", f"--{rule}", files["ours"], files["base"],
                               files["theirs"]], capture_output=True)
        if proc.returncode < 0:
            raise Stop(f"merge-file failed on {c.path}")
        blob = git(repo, "hash-object", "-w", "--stdin", input=proc.stdout).stdout.decode().strip()
    return (theirs[0] if rule == "theirs" else ours[0], blob)


def write_resolved(repo: Path, tree: str, resolved: dict[str, tuple[str, str] | None]) -> str:
    with tempfile.TemporaryDirectory() as tmp:
        env = {"GIT_INDEX_FILE": str(Path(tmp) / "index")}
        git(repo, "read-tree", tree, env=env)
        for path, entry in sorted(resolved.items()):
            if entry is None:
                git(repo, "update-index", "--force-remove", "--", path, env=env)
            else:
                git(repo, "update-index", "--add", "--cacheinfo", f"{entry[0]},{entry[1]},{path}", env=env)
        return out(repo, "write-tree", env=env)


def commit(repo: Path, tree: str, parent: str, source: str, message: str) -> str:
    fmt = out(repo, "log", "-1", "--format=%an%x00%ae%x00%aI", source)
    name, email, date = fmt.split("\0")
    env = {**COMMITTER, "GIT_AUTHOR_NAME": name, "GIT_AUTHOR_EMAIL": email,
           "GIT_AUTHOR_DATE": date, "GIT_COMMITTER_DATE": date}
    return out(repo, "commit-tree", tree, "-p", parent, env=env, input=message.encode())


def phase_message(ph: Phase, head: str, deferred: list[str], resolved: list[tuple[str, str]],
                  total: int) -> str:
    first, last = ph.commits[0][:10], ph.commits[-1][:10]
    title = ph.title if ph.run == 1 else f"{ph.title} (continued)"
    lines = [f"Globe {ph.index:02d}/{total:02d}, {ph.label}: {title}", "",
             f"Imported from maplibre/maplibre-native#4533 at {head},",
             f"first-parent commits {first}..{last} ({len(ph.commits)}).", ""]
    if deferred:
        lines.append("Conflicting hunks kept on the base's side here; the last phase")
        lines.append("carries the pull request's final version of each:")
        lines += [f"  {p}" for p in deferred]
        lines.append("")
    if resolved:
        lines.append("Conflicts resolved by the rules in airdress/globe-phases.toml:")
        lines += [f"  {p}: {r}" for p, r in resolved]
        lines.append("")
    return "\n".join(lines)


def import_globe(repo: Path, head: str, upstream: str) -> list[str]:
    cfg = read_phases()
    base, names = read_series()
    rules: dict = cfg.get("resolve", {})
    ours_group = [n for n in names if n.startswith("ours/")]
    apply_infra(repo, base, names, upstream)
    fetch_globe(repo, head, cfg.get("ref", "refs/pull/4533/head"))
    merge_base, phases = find_phases(repo, head, cfg)
    infra_head = out(repo, "rev-parse", "HEAD")

    tip = infra_head
    prev_end = out(repo, "merge-base", phases[0].commits[0], "refs/globe/main")
    for ph in phases:
        is_last = ph is phases[-1]
        if is_last:
            tree, conflicts = merge_trees(repo, merge_base, infra_head, head)
        else:
            tree, conflicts = merge_trees(repo, prev_end, tip, ph.end)
        resolved: dict[str, tuple[str, str] | None] = {}
        deferred, by_rule, unruled = [], [], []
        for c in conflicts:
            rule = rule_for(c.path, rules, head)
            if is_last:
                if rule is None:
                    unruled.append(c.path)
                    continue
                resolved[c.path] = resolve(repo, c, rule)
                by_rule.append((c.path, rule))
            else:
                resolved[c.path] = resolve(repo, c, rule if rule == "ours" else "ours")
                deferred.append(c.path)
        if unruled:
            raise Stop("the whole pull request conflicts with the base in files no rule covers:\n  "
                       + "\n  ".join(unruled)
                       + "\nresolve each by adding a rule to airdress/globe-phases.toml [resolve]"
                         " (ours, theirs or union), then import again", status=3)
        tree = write_resolved(repo, tree, resolved) if resolved else tree
        tip = commit(repo, tree, tip, ph.end, phase_message(ph, head, deferred, by_rule, len(phases)))
        prev_end = ph.end
        note = f", {len(deferred)} deferred" if deferred else ""
        note += f", {len(by_rule)} by rule" if by_rule else ""
        print(f"{ph.index:02d} {ph.label} {ph.topic}: {len(ph.commits)} commits{note}")
    git(repo, "update-ref", "HEAD", tip)
    git(repo, "checkout", "-q", "-f", "HEAD")

    if ours_group:
        paths = [str(PATCH_DIR / n) for n in ours_group]
        proc = git(repo, "-c", "user.name=fork series", "-c", "user.email=fork-series@noreply.airdress.co",
                   "am", "-q", "-3", "--keep-cr", "--committer-date-is-author-date", *paths,
                   env=COMMITTER, check=False)
        if proc.returncode != 0:
            raise Stop("a group-3 patch no longer applies on the new globe group; resolve it in "
                       f"{repo} (git am in progress) and run refresh.sh", status=3)

    globe_names = [f"globe/{ph.index:02d}-{ph.topic}.patch" for ph in phases]
    return [n for n in names if n.startswith("infra/")] + globe_names + ours_group


def write_series(base: str, head: str, names: list[str], path: Path) -> None:
    path.write_text(
        "# The upstream base the series applies to, the globe pull request's\n"
        "# recorded head, then the patches in order: infra, globe, ours.\n"
        "# Maintained by airdress/scripts/refresh.sh and import-globe.sh; see\n"
        "# airdress/README.md.\n"
        f"base {base}\n"
        f"globe-pr maplibre/maplibre-native#4533 {head}\n"
        + "".join(f"{n}\n" for n in names))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--head", help="the pull request head to import (default: the series' globe-pr)")
    ap.add_argument("--repo", default="import-globe", help="the scratch tree (default ./import-globe)")
    ap.add_argument("--upstream", default=UPSTREAM_DEFAULT)
    ap.add_argument("--check", action="store_true", help="regenerate and compare, change nothing")
    ap.add_argument("--no-refresh", action="store_true", help="build the tree, do not write patches")
    args = ap.parse_args()

    base, names = read_series()
    head = args.head
    if not head:
        for line in SERIES_FILE.read_text().splitlines():
            if line.startswith("globe-pr "):
                head = line.split()[2]
        if not head:
            raise Stop("no --head, and patches/series has no globe-pr line")
    if not re.fullmatch(r"[0-9a-f]{40}", head):
        raise Stop(f"--head is a full commit id, not {head}")

    repo = Path(args.repo).resolve()
    new_names = import_globe(repo, head, args.upstream)
    if args.no_refresh:
        print(f"built {repo} at {out(repo, 'rev-parse', 'HEAD')}")
        return 0

    if args.check:
        with tempfile.TemporaryDirectory() as tmp:
            shadow = Path(tmp) / "patches"
            shutil.copytree(PATCH_DIR, shadow)
            for f in (shadow / "globe").glob("*.patch"):
                f.unlink()
            write_series(base, head, new_names, shadow / "series")
            env = {**os.environ, "PATCH_DIR_OVERRIDE": str(shadow)}
            subprocess.run([str(SCRIPTS / "refresh.sh"), "--repo", str(repo)], env=env, check=True,
                           stdout=subprocess.DEVNULL)
            diff = subprocess.run(["diff", "-r", "-q", str(PATCH_DIR), str(shadow)], capture_output=True)
            if diff.returncode != 0:
                print(diff.stdout.decode(), end="")
                print("the globe patches differ from a fresh import of the recorded head", file=sys.stderr)
                return 4
        print(f"globe group equals a fresh import of {head[:12]} onto {base}")
        return 0

    write_series(base, head, new_names, SERIES_FILE)
    subprocess.run([str(SCRIPTS / "refresh.sh"), "--repo", str(repo)], check=True)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Stop as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(e.status)
