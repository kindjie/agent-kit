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
    prefix = [] if '--resource' in args else ['--resource', 'gpu']
    return [sys.executable, str(BIN), 'run', *prefix, *args]

  def configure(self, slots):
    return subprocess.run([sys.executable, str(BIN), 'capacity',
                           '--resource', 'gpu', '--credits', str(slots),
                           '--wait', '.2'], env=self.env,
                          capture_output=True, timeout=4)

  def holder(self, name, *extra):
    ready = Path(self.tmp.name) / name
    script = ('import pathlib,time; pathlib.Path(%r).touch(); time.sleep(20)'
              % str(ready))
    proc = subprocess.Popen(self.command(*extra, '--', sys.executable,
                                        '-c', script), env=self.env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    def cleanup():
      if proc.poll() is None:
        proc.terminate()
      proc.communicate(timeout=5)
    self.addCleanup(cleanup)
    deadline = time.monotonic() + 3
    while (not ready.exists() and proc.poll() is None and
           time.monotonic() < deadline):
      time.sleep(.02)
    self.assertTrue(ready.exists(), 'holder did not start')
    return proc

  def contender(self, *extra):
    return subprocess.run(self.command('--wait', '.15', *extra, '--',
                                       'true'), env=self.env,
                          capture_output=True, timeout=4)

  def test_two_slots_and_release(self):
    self.assertEqual(self.configure(2).returncode, 0)
    first = self.holder('first')
    self.holder('second')
    self.assertEqual(self.contender().returncode, 75)
    first.terminate()
    first.communicate(timeout=5)
    self.assertEqual(self.contender().returncode, 0)

  def test_exclusive_conflicts_both_directions(self):
    self.assertEqual(self.configure(2).returncode, 0)
    shared = self.holder('shared')
    self.assertEqual(self.contender('--resource', 'gpu:2').returncode, 75)
    shared.terminate()
    shared.communicate(timeout=5)
    self.holder('exclusive', '--resource', 'gpu:2')
    self.assertEqual(self.contender().returncode, 75)

  def test_capacity_cannot_change_during_run(self):
    self.assertEqual(self.configure(2).returncode, 0)
    self.holder('active')
    self.assertEqual(self.configure(3).returncode, 75)
    self.holder('second')
    self.assertEqual(self.contender().returncode, 75)

  def test_legacy_exclusive_lock_blocks_slots(self):
    import fcntl
    self.assertEqual(self.configure(2).returncode, 0)
    root = Path(self.tmp.name) / 'agent-kit/resources'
    with (root / 'gpu.lock').open('r+') as gate:
      fcntl.flock(gate, fcntl.LOCK_EX | fcntl.LOCK_NB)
      self.assertEqual(self.contender().returncode, 75)
    self.holder('shared')
    with (root / 'gpu.lock').open('r+') as gate:
      with self.assertRaises(BlockingIOError):
        fcntl.flock(gate, fcntl.LOCK_EX | fcntl.LOCK_NB)

  def test_weighted_capacity_and_mixed_resources(self):
    self.assertEqual(self.configure(4).returncode, 0)
    self.holder('two', '--resource', 'gpu:2')
    self.holder('one')
    self.assertEqual(self.contender('--resource', 'gpu:2').returncode, 75)
    self.assertEqual(self.contender().returncode, 0)
    self.assertEqual(self.contender('--resource', 'gpu:5').returncode, 1)
    self.assertEqual(self.contender('--resource', 'gpu',
                                    '--resource', 'build').returncode, 0)

  def test_partial_credits_not_retained_while_waiting(self):
    self.assertEqual(self.configure(3).returncode, 0)
    self.holder('two', '--resource', 'gpu:2')
    waiter = subprocess.Popen(self.command('--wait', '.8', '--resource',
                                           'gpu:2', '--', 'true'),
                              env=self.env, stderr=subprocess.PIPE)
    self.addCleanup(lambda: waiter.communicate(timeout=4))
    # A two-credit waiter must leave the spare credit usable.
    time.sleep(.2)
    self.assertEqual(self.contender().returncode, 0)
    self.assertEqual(waiter.wait(timeout=3), 75)

  def test_capacity_and_slot_symlinks_refused(self):
    self.assertEqual(self.configure(2).returncode, 0)
    root = Path(self.tmp.name) / 'agent-kit/resources'
    victim = Path(self.tmp.name) / 'victim'
    victim.write_text('preserve')
    (root / 'gpu.capacity').unlink()
    (root / 'gpu.capacity').symlink_to(victim)
    self.assertEqual(self.contender().returncode, 1)
    self.assertEqual(self.configure(3).returncode, 1)
    self.assertEqual(victim.read_text(), 'preserve')
    (root / 'gpu.capacity').unlink()
    self.assertEqual(self.configure(2).returncode, 0)
    (root / 'gpu.slots').symlink_to(self.tmp.name)
    self.assertEqual(self.contender().returncode, 1)

  def test_invalid_capacity(self):
    for slots in ('0', '-1', '1.5', '65'):
      self.assertEqual(self.configure(slots).returncode, 2)

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
