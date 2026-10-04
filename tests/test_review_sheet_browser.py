"""Run the pinned file:// browser harness when its tools are installed."""
import os
from pathlib import Path
import shutil
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]
HARNESS = ROOT / 'tests' / 'review_sheet_browser.cjs'


class ReviewSheetBrowserTest(unittest.TestCase):
  def test_chromium_and_firefox(self):
    if not shutil.which('node'):
      self.skipTest('Node is absent; browser harness did not run')
    proc = subprocess.run(['node', str(HARNESS), str(ROOT)],
                          text=True, capture_output=True,
                          env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'})
    if proc.returncode == 77:
      self.skipTest(proc.stdout.strip() or proc.stderr.strip())
    self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)


if __name__ == '__main__':
  unittest.main()
