"""Independent producer/consumer CLI check using a failed harmless command."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

BIN = Path(__file__).resolve().parents[1] / 'bin'


class SchedulerReceiptsTest(unittest.TestCase):
  def test_failed_execution_exports_measurable_release_without_acceptance(self):
    with tempfile.TemporaryDirectory() as directory:
      root = Path(directory)
      state = root / 'state'

      def scheduler(*args, code=0):
        result = subprocess.run(
          [sys.executable, str(BIN / 'agent-scheduler'), '--state', str(state),
           *map(str, args)], capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, code, result.stderr)
        return result.stdout

      epoch = json.loads(scheduler('init', '--coordinator', 'coordinator'))['epoch']
      grant = json.loads(scheduler(
        'grant', '--coordinator', 'coordinator', '--epoch', epoch,
        '--lane', 'example', '--run-id', 'run-one', '--worker', 'worker',
        '--release-mode', 'automatic', '--scope', 'trusted-foreground-group',
        '--cwd', root, '--timeout', '5', '--', sys.executable,
        '-c', 'raise SystemExit(7) # private-marker'))
      scheduler('run', '--epoch', epoch, '--lane', 'example',
                '--generation', grant['generation'], '--run-id', 'run-one',
                '--worker', 'worker', code=7)
      exported = scheduler('export')
      row = json.loads(exported)['runs'][0]
      self.assertEqual(row['operational'], 'released')
      self.assertEqual(row['semantic'], 'pending')
      self.assertNotIn('private-marker', exported)
      self.assertNotIn(directory, exported)
      path = root / 'receipts.json'
      path.write_text(exported)
      result = subprocess.run(
        [sys.executable, str(BIN / 'agent-efficiency'), '--receipts-only',
         '--scheduler-receipts', str(path), '--since', '1970-01-01T00:00:00Z',
         '--until', '2100-01-01T00:00:00Z'], capture_output=True, text=True,
        timeout=5)
      self.assertEqual(result.returncode, 0, result.stderr)
      report = json.loads(result.stdout)['scheduler_lifecycle']
      self.assertTrue(report['complete'])
      self.assertEqual(report['released_runs'], 1)
      for name in ('terminal_to_release', 'quiescence_to_release'):
        self.assertEqual(report[name]['sample_count'], 1)
        self.assertEqual(report[name]['unknown_count'], 0)
        self.assertGreaterEqual(report[name]['total_seconds'], 0)
      self.assertNotIn(directory, result.stdout)
      self.assertNotIn('private-marker', result.stdout)
