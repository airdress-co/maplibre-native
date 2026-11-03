"""commit-messages.sh refuses internal references and passes ordinary text.

    python3 -m unittest discover -s airdress/test
"""

import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "airdress/scripts/commit-messages.sh"


def lint(text: str) -> int:
    return subprocess.run([str(SCRIPT), "--stdin", "test"], input=text.encode(),
                          capture_output=True).returncode


class Refused(unittest.TestCase):
    def test_internal_references_are_refused(self):
        for text in [
            "fix: per SPEC-185",
            "see spec_042 and RDR-043",
            "RB-066 step 7",
            "finishes 185-A.4",
            "Tasks-done: 185-0.2",
            "tracked in AIR-159",
            "written in airdress-specs",
            "from .spec-workflow/specs",
            "the steps in runbooks/RB-089-front.md",
        ]:
            with self.subTest(text=text):
                self.assertEqual(lint(text), 1)

    def test_ordinary_text_passes(self):
        for text in [
            "Import the globe pull request (#4533) at bec8a12440, one patch per phase",
            "android-v13.7.1-airdress.1 and ios-v7.0.0-airdress.1",
            "Bump MapLibre to 2026.10.19-globe; see maplibre/maplibre-native#4746",
            "fork-ci: install ccache for the linux preset's compiler launcher",
            "the airdress/globe-phases.toml rules",
            "a 2026-11-03 measurement, 1.5 ms per frame",
        ]:
            with self.subTest(text=text):
                self.assertEqual(lint(text), 0)


if __name__ == "__main__":
    unittest.main()
