"""Planted failures for series-check.py's offline checks.

    python3 -m unittest discover -s airdress/test

Each test copies patches/ and breaks one thing. The checks that need an
applied tree (a patch that does not apply, a hand-edited globe patch) run
in CI's series job against the real series instead.
"""

import importlib.util
import os
import shutil
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("series_check", ROOT / "airdress/scripts/series-check.py")
series_check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(series_check)


class Planted(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.pdir = Path(self.tmp.name) / "patches"
        shutil.copytree(ROOT / "patches", self.pdir)

    def tearDown(self):
        self.tmp.cleanup()

    def errors(self):
        return series_check.check_structure(self.pdir)

    def assertFlags(self, fragment):
        errs = self.errors()
        self.assertTrue(any(fragment in e for e in errs), f"no error containing {fragment!r} in {errs}")

    def edit_series(self, fn):
        p = self.pdir / "series"
        p.write_text(fn(p.read_text()))

    def edit_toml(self, fn):
        p = self.pdir / "series.toml"
        p.write_text(fn(p.read_text()))

    def test_the_committed_series_is_clean(self):
        self.assertEqual(self.errors(), [])

    def test_an_unlisted_patch(self):
        (self.pdir / "ours").mkdir(exist_ok=True)
        (self.pdir / "ours/0001-stray.patch").write_text("x\n")
        self.assertFlags("ours/0001-stray.patch is not in patches/series")

    def test_a_listed_patch_that_does_not_exist(self):
        self.edit_series(lambda s: s + "ours/0001-missing.patch\n")
        self.assertFlags("lists ours/0001-missing.patch, which does not exist")

    def test_a_missing_entry(self):
        self.edit_toml(lambda s: s.replace('file = "infra/0002-packaging.patch"', 'file = "infra/0009-other.patch"'))
        self.assertFlags("series.toml has no entry for infra/0002-packaging.patch")

    def test_groups_out_of_order(self):
        # A patch of ours listed before the infra group.
        (self.pdir / "ours").mkdir(exist_ok=True)
        (self.pdir / "ours/0001-early.patch").write_text("x\n")
        def first(s):
            lines = s.splitlines()
            i = next(i for i, l in enumerate(lines) if l.startswith("infra/"))
            lines.insert(i, "ours/0001-early.patch")
            return "\n".join(lines) + "\n"
        self.edit_series(first)
        self.assertFlags("infra, then globe, then ours")

    def test_an_offered_infra_patch(self):
        self.edit_toml(lambda s: s.replace('offer = "never"', 'offer = "undecided"', 1))
        self.assertFlags("an infra patch is never offered")

    def test_a_globe_group_without_its_head(self):
        if not any(self.pdir.glob("globe/*.patch")):
            self.skipTest("no globe group yet")
        self.edit_series(lambda s: "\n".join(l for l in s.splitlines() if not l.startswith("globe-pr")) + "\n")
        self.assertFlags("a globe group needs a 'globe-pr")

    def test_a_short_globe_head(self):
        if not any(self.pdir.glob("globe/*.patch")):
            self.skipTest("no globe group yet")
        self.edit_series(lambda s: s.replace("#4533 ", "#4533 abc ", 1).replace("abc " + s.split("#4533 ")[1][:40], "abc"))
        self.assertFlags("globe-pr must be")


if __name__ == "__main__":
    unittest.main()
