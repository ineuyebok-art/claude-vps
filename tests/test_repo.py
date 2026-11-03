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


if __name__ == "__main__":
    unittest.main()
