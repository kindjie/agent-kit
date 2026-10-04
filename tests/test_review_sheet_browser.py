"""Run the pinned file:// browser harness when its tools are installed."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
HARNESS = ROOT / 'tests' / 'review_sheet_browser.cjs'


class ReviewSheetBrowserTest(unittest.TestCase):
  def run_launch_fixture(self, chrome_absent, bundled_present):
    if not shutil.which('node'):
      self.skipTest('Node is absent; browser harness did not run')
    with tempfile.TemporaryDirectory() as folder:
      fixture = Path(folder)
      harness = fixture / HARNESS.name
      shutil.copy2(HARNESS, harness)
      module = fixture / 'node_modules' / 'playwright'
      module.mkdir(parents=True)
      executable = module / 'browser'
      if bundled_present:
        executable.touch()
      source = """
const path = require('node:path');
module.exports = {
  chromium: {
    executablePath: () => path.join(__dirname, 'browser'),
    launch: async options => {
      console.log('LAUNCH: ' + (options.channel || 'bundled'));
      if (options.channel && CHROME_ABSENT)
        throw Error("Chromium distribution 'chrome' is not found");
      return {
        version: () => 'fixture-version',
        newContext: async () => { throw Error('fixture check failed'); },
        close: async () => { console.log('CLOSE: fixture browser'); }
      };
    }
  },
  firefox: {executablePath: () => path.join(__dirname, 'missing-firefox')}
};
""".replace('CHROME_ABSENT', str(chrome_absent).lower())
      (module / 'index.js').write_text(source)
      return subprocess.run(['node', str(harness), str(ROOT)],
                            text=True, capture_output=True, timeout=30)

  def test_available_chrome_failure_closes_without_fallback(self):
    proc = self.run_launch_fixture(False, True)
    self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
    self.assertIn('FAIL: Google Chrome', proc.stderr)
    self.assertIn('RUN: Google Chrome fixture-version', proc.stdout)
    self.assertIn('CLOSE: fixture browser', proc.stdout)
    self.assertNotIn('LAUNCH: bundled', proc.stdout)

  def test_absent_chrome_tries_bundled_chromium(self):
    proc = self.run_launch_fixture(True, True)
    self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
    self.assertIn('SKIP: Google Chrome is absent', proc.stdout)
    self.assertIn('LAUNCH: bundled', proc.stdout)
    self.assertIn('FAIL: bundled Chromium', proc.stderr)

  def test_no_available_browser_exits_skip(self):
    proc = self.run_launch_fixture(True, False)
    self.assertEqual(proc.returncode, 77, proc.stdout + proc.stderr)
    self.assertIn('SKIP: bundled Chromium executable absent', proc.stdout)
    self.assertIn('SKIP: bundled Firefox executable absent', proc.stdout)

  def test_chromium_and_firefox(self):
    if not shutil.which('node'):
      self.skipTest('Node is absent; browser harness did not run')
    proc = subprocess.run(['node', str(HARNESS), str(ROOT)],
                          text=True, capture_output=True,
                          env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'})
    if proc.stdout:
      print(proc.stdout, end='')
    if proc.returncode == 77:
      self.skipTest(proc.stdout.strip() or proc.stderr.strip())
    self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)


if __name__ == '__main__':
  unittest.main()
