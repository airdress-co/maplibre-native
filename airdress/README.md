# This fork of MapLibre Native

This repository is a fork of
[maplibre/maplibre-native](https://github.com/maplibre/maplibre-native). Its
`main` branch does not hold a copy of MapLibre. It holds a series of patches,
the tooling that applies them, and the workflows that test and release the
result for Android and iOS. The patches float: when upstream releases, or
when the globe pull request moves, they are re-applied, and nothing here is
ever rewritten.

## What the series carries

| Group | Directory | What | Who wrote it |
|---|---|---|---|
| infra | `patches/infra/` | the fork's own plumbing: upstream's workflows removed from the built tree, the licence line | us, never offered |
| globe | `patches/globe/` | upstream's open globe pull request, [maplibre/maplibre-native#4533](https://github.com/maplibre/maplibre-native/pull/4533), imported one patch per phase from a recorded head | its author, authorship kept |
| ours | `patches/ours/` | fixes and features of our own, each written as an upstream pull request would be | us, offered upstream per patch |

The globe patches are regenerated, never edited. A fix to the globe is a
patch in `ours/`, on top, so that a new head of the pull request cannot
overwrite it. Once a release of upstream contains the pull request's merge,
the globe group is dropped.

## Layout

| Path | What |
|---|---|
| `patches/series` | the base (`base ios-v7.0.0`), the globe pull request's recorded head (`globe-pr …`), then the patches in order |
| `patches/series.toml` | one entry per patch: group, summary, author, offer state, when to drop it, its tests |
| `airdress/globe-phases.toml` | the globe's phases by name, and the only changes we make to the author's work: how a conflict with the base is resolved |
| `airdress/scripts/apply.sh` | base + series → an applied tree (`git am -3`, one commit per patch), submodules at its gitlinks |
| `airdress/scripts/refresh.sh` | applied tree → the patch files again, byte-identical when nothing moved |
| `airdress/scripts/import-globe.py` | a head of #4533 → `patches/globe/`, one patch per phase; `--check` proves the committed patches are exactly that |
| `airdress/scripts/series-check.py` | files, `series` and entries agree; the series applies; the divergence; the globe is a fresh import |
| `airdress/scripts/doc-numbers.sh` | the lines the patches add carry no internal document numbers |
| `airdress/scripts/compare-builds.py` | two builds of one release agree, apart from `airdress/build-differences.txt` |
| `.github/workflows/fork-ci.yml` | pull requests and `main`: the series checks, the Linux core with the globe on and off, Android, iOS |
| `.github/workflows/fork-release.yml` | a release: applied branch and tags pushed once, built twice and compared, attested, published |
| `.github/workflows/fork-reapply.yml` | daily: a newer upstream release or a new globe head, as a pull request |

Upstream's own workflows never run here: `main` has none of upstream's files,
and `infra/0001` removes upstream's workflows from every applied tree. Every
job runs on GitHub-hosted runners; this repository never has a self-hosted
one.

## How the globe is imported

`import-globe.py --head <sha>` fetches the pull request and upstream `main`,
and finds the phases on the head's first-parent line from its commit titles
(`… (globe phase N)`). A phase that resumes after another one (9a–9f, then
10, then 9g–9i) becomes a second patch, `NN-<name>-2`.

- Every phase but the last is a three-way merge of that phase's own change
  onto the tree so far. A hunk that conflicts keeps the base's side, and the
  commit message lists it.
- The last phase's tree is exactly the whole pull request merged onto
  base + infra, with every conflict resolved by its rule in
  `globe-phases.toml` (`ours`, `theirs` or `union`). A conflict with no rule
  stops the import.

So the applied tree never depends on the deferrals in the earlier phases. The
import is deterministic: each phase keeps the author and date of its last
commit, and the committer is the fork series. CI re-runs it from the recorded
head and fails if a single byte of `patches/globe/` differs.

## Branches and tags

- `main`: the series. It moves forward only, through reviewed pull requests.
- `airdress/<base>-airdress.<n>`: the exact tree a release was built from, the
  base plus one commit per patch and a release commit. Pushed once by the
  release workflow, never moved, never deleted.
- `android-v<version>-airdress.<n>`, `ios-v<version>-airdress.<n>`: the
  release tags at that branch's head, each with its GitHub Release.
- `reapply/<trigger>`: a re-apply pull request. Deleted after merge.

## Making a change

```sh
airdress/scripts/apply.sh --repo applied            # base + series, one commit per patch
cd applied                                          # edit, build and test; amend the commit of
                                                    # the patch you change, or add one at the end
cd -
airdress/scripts/refresh.sh --repo applied --add ours/my-topic   # a new patch of ours
python3 airdress/scripts/series-check.py --apply    # agrees, applies, divergence
```

Add the patch's entry to `patches/series.toml`, then open a pull request to
`main`. Its body carries the range-diff of the applied series before and
after, so a reviewer reads code rather than a diff of diffs.

## Releases

Dispatch `fork-release` on `main` with the release number `n`. The workflow
applies the series, pushes `airdress/<base>-airdress.<n>` and both platform
tags (refused if any exists), builds the Android Maven repository (OpenGL ES)
and the iOS dynamic xcframework (Metal) on two runners each, fails unless
the builds agree, attests each asset, and publishes a GitHub Release per tag
with the asset and `SHA256SUMS`.

## Licence

BSD-2-Clause, as upstream. Every applied tree's `LICENSE.md` keeps every
upstream notice unchanged, with one line above them for this fork's changes.
