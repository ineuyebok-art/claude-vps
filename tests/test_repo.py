"""Repository-level invariants."""
import pathlib
import re
import subprocess
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent


class RepoTest(unittest.TestCase):
    def test_no_secrets_in_git(self):
        files = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True,
                               text=True, check=True).stdout.split("\n")
        token = re.compile(r"\b\d{8,10}:[A-Za-z0-9_-]{35}\b")
        for name in filter(None, files):
            path = ROOT / name
            if path.suffix in {".woff2", ".png"} or not path.is_file():
                continue
            self.assertIsNone(token.search(path.read_text(errors="ignore")), name)


    def test_powershell_files_have_bom(self):
        # Windows PowerShell 5.1 reads UTF-8 without BOM as ANSI, which breaks Cyrillic.
        for path in [*ROOT.glob("windows/*.ps1"), ROOT / "tests/windows-smoke.ps1"]:
            self.assertEqual(path.read_bytes()[:3], b"\xef\xbb\xbf", path.name)

    def test_units_reference_existing_helpers(self):
        for unit in (ROOT / "server/systemd").iterdir():
            for match in re.findall(r"/usr/local/lib/claude-vps/([\w-]+)", unit.read_text(encoding="utf-8")):
                self.assertTrue((ROOT / "server/bin" / match).exists(), match)


if __name__ == "__main__":
    unittest.main()
