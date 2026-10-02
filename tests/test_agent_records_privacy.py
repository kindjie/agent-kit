"""Check published files for private paths and addresses."""

from __future__ import annotations

import os
import re
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class PrivacyTest(unittest.TestCase):
  def test_repository_has_no_private_identifiers(self):
    listed = subprocess.check_output(
      ["git", "-C", str(ROOT), "ls-files", "-z", "--cached", "--others",
       "--exclude-standard"])
    files = [ROOT / raw.decode() for raw in listed.split(b"\0") if raw]
    home_markers = ("/" + "Users" + "/", "/" + "home" + "/")
    address = re.compile(
      r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
    private = []
    denylist_path = os.environ.get("AGENT_RECORDS_PRIVACY_DENYLIST")
    denylist = (Path(denylist_path).read_text().splitlines()
                if denylist_path else [])
    denylist = [word for word in denylist if word and not word.startswith("#")]
    for path in files:
      if path.is_dir() or path.suffix in (".pyc", ".png", ".jpg"):
        continue
      content = path.read_text(encoding="utf-8", errors="ignore")
      for marker in home_markers:
        if marker in content:
          private.append(str(path.relative_to(ROOT)) + ": home path")
      for match in address.finditer(content):
        if (path in (ROOT / "README.md", ROOT / "docs/install.md") and
            match.group() == "git" + "@" + "github.com"):
          continue
        if match.group().split("@", 1)[1] not in (
            "example.test", "example.invalid", "example.com"):
          private.append(str(path.relative_to(ROOT)) + ": address")
      for word in denylist:
        if word in content:
          private.append(str(path.relative_to(ROOT)) + ": denylist match")
    self.assertEqual(private, [])


if __name__ == "__main__":
  unittest.main()
