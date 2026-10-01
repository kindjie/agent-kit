"""Real process admission and timeout tests; no machine resources needed."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

BIN = Path(__file__).resolve().parents[1] / 'bin' / 'agent-resource'


class ResourceTest(unittest.TestCase):
  def setUp(self):
    self.tmp = tempfile.TemporaryDirectory()
    self.addCleanup(self.tmp.cleanup)
    self.env = dict(os.environ, XDG_STATE_HOME=self.tmp.name)

  def command(self, *args):
    return [sys.executable, str(BIN), 'run', '--resource', 'gpu', *args]

  def test_exit_and_timeout(self):
    for code, extra, script in ((7, [], 'raise SystemExit(7)'),
                                (124, ['--timeout', '.1'],
                                 'import time; time.sleep(20)')):
      proc = subprocess.run(self.command(*extra, '--', sys.executable,
                             '-c', script), env=self.env, capture_output=True,
                             timeout=5)
      self.assertEqual(proc.returncode, code, proc.stderr)

  def test_exclusion(self):
    ready = Path(self.tmp.name) / 'ready'
    script = 'import pathlib,time; pathlib.Path(%r).touch(); time.sleep(20)' % str(ready)
    proc = subprocess.Popen(self.command('--', sys.executable, '-c', script),
                            env=self.env, stdout=subprocess.DEVNULL)
    try:
      deadline = time.monotonic() + 3
      while not ready.exists() and time.monotonic() < deadline:
        time.sleep(.02)
      self.assertTrue(ready.exists())
      denied = subprocess.run(self.command('--wait', '.1', '--', 'true'),
                              env=self.env, capture_output=True, timeout=3)
      self.assertEqual(denied.returncode, 75, denied.stderr)
    finally:
      proc.terminate()
      proc.wait(timeout=5)
    accepted = subprocess.run(self.command('--wait', '.2', '--', 'true'),
                              env=self.env, capture_output=True, timeout=3)
    self.assertEqual(accepted.returncode, 0, accepted.stderr)

  def test_invalid_resource(self):
    proc = subprocess.run(self.command('--resource', '../escape', '--', 'true'),
                          env=self.env, capture_output=True, timeout=3)
    self.assertEqual(proc.returncode, 2)

  def test_supervisor_death_keeps_child_lock(self):
    ready = Path(self.tmp.name) / 'ready'
    script = ('import pathlib,time; pathlib.Path(%r).touch(); time.sleep(1)' %
              str(ready))
    proc = subprocess.Popen(self.command('--', sys.executable, '-c', script),
                            env=self.env, stdout=subprocess.DEVNULL)
    try:
      deadline = time.monotonic() + 3
      while not ready.exists() and time.monotonic() < deadline:
        time.sleep(.01)
      self.assertTrue(ready.exists())
      proc.kill()
      proc.wait(timeout=3)
      denied = subprocess.run(self.command('--wait', '.1', '--', 'true'),
                              env=self.env, capture_output=True, timeout=3)
      self.assertEqual(denied.returncode, 75, denied.stderr)
      accepted = subprocess.run(self.command('--wait', '2', '--', 'true'),
                                env=self.env, capture_output=True, timeout=3)
      self.assertEqual(accepted.returncode, 0, accepted.stderr)
    finally:
      if proc.poll() is None:
        proc.terminate()
        proc.wait(timeout=3)

  def test_lock_symlink_refused(self):
    root = Path(self.tmp.name) / 'agent-kit/resources'
    root.mkdir(parents=True, mode=0o700)
    victim = Path(self.tmp.name) / 'victim'
    victim.write_text('preserve')
    (root / 'gpu.lock').symlink_to(victim)
    proc = subprocess.run(self.command('--', 'true'), env=self.env,
                          capture_output=True, timeout=3)
    self.assertEqual(proc.returncode, 1)
    self.assertEqual(victim.read_text(), 'preserve')

  def test_timeout_removes_descendant_that_closed_lock_descriptors(self):
    marker = Path(self.tmp.name) / 'descendant'
    child = ('import os,pathlib,signal,time; '
             'signal.signal(signal.SIGTERM, signal.SIG_IGN); '
             'pathlib.Path(%r).write_text(str(os.getpid())); time.sleep(20)' %
             str(marker))
    leader = ('import subprocess,sys,time; '
              'subprocess.Popen([sys.executable,"-c",%r], close_fds=True); '
              'time.sleep(20)' % child)
    proc = subprocess.run(self.command('--timeout', '.4', '--',
                          sys.executable, '-c', leader), env=self.env,
                          capture_output=True, timeout=5)
    self.assertEqual(proc.returncode, 124, proc.stderr)
    self.assertTrue(marker.exists())
    with self.assertRaises(ProcessLookupError):
      os.kill(int(marker.read_text()), 0)
