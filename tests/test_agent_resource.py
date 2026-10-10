"""Real process admission and timeout tests; no machine resources needed."""
import fcntl
import os
from pathlib import Path
import subprocess
import signal
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

  def configure(self, slots, resource='gpu'):
    return subprocess.run([sys.executable, str(BIN), 'capacity',
                           '--resource', resource, '--credits', str(slots),
                           '--wait', '.2'], env=self.env,
                          capture_output=True, timeout=4)

  def waiting_holder(self, name, *extra):
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
    return proc

  def wait_ready(self, name, proc):
    ready = Path(self.tmp.name) / name
    deadline = time.monotonic() + 3
    while (not ready.exists() and proc.poll() is None and
           time.monotonic() < deadline):
      time.sleep(.02)
    self.assertTrue(ready.exists(), 'holder did not start')

  def holder(self, name, *extra):
    proc = self.waiting_holder(name, *extra)
    self.wait_ready(name, proc)
    return proc

  def wait_locked(self, relative):
    path = Path(self.tmp.name) / 'agent-kit/resources' / relative
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
      if path.exists():
        with path.open('r+') as stream:
          try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
          except BlockingIOError:
            return
      time.sleep(.02)
    self.fail('%s was not locked' % relative)

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

  def test_turnstile_gates_admission_with_free_slots(self):
    import fcntl
    self.assertEqual(self.configure(3, 'cpu').returncode, 0)
    root = Path(self.tmp.name) / 'agent-kit/resources'
    self.assertEqual(self.contender('--resource', 'cpu').returncode, 0)
    with (root / 'cpu.turnstile').open('r+') as turnstile:
      fcntl.flock(turnstile, fcntl.LOCK_EX | fcntl.LOCK_NB)
      self.assertEqual(self.contender('--resource', 'cpu').returncode, 75)
    self.assertEqual(self.contender('--resource', 'cpu').returncode, 0)

  def test_weighted_capacity_and_mixed_resources(self):
    self.assertEqual(self.configure(4).returncode, 0)
    self.holder('two', '--resource', 'gpu:2')
    self.holder('one')
    self.assertEqual(self.contender('--resource', 'gpu:2').returncode, 75)
    self.assertEqual(self.contender().returncode, 0)
    self.assertEqual(self.contender('--resource', 'gpu:5').returncode, 1)
    self.assertEqual(self.contender('--resource', 'gpu',
                                    '--resource', 'build').returncode, 0)

  def test_partial_credits_retained_while_waiting(self):
    self.assertEqual(self.configure(3).returncode, 0)
    self.holder('two', '--resource', 'gpu:2')
    self.waiting_holder('waiter', '--resource', 'gpu:2')
    self.wait_locked('gpu.turnstile')
    self.wait_locked('gpu.slots/2.lock')
    self.assertEqual(self.contender().returncode, 75)
    self.assertFalse((Path(self.tmp.name) / 'waiter').exists())

  def test_full_budget_waiter_not_overtaken(self):
    self.assertEqual(self.configure(3, 'cpu').returncode, 0)
    active = self.holder('active', '--resource', 'cpu')
    waiter = self.waiting_holder('exclusive', '--resource', 'cpu:3')
    self.wait_locked('cpu.turnstile')
    self.wait_locked('cpu.slots/1.lock')
    self.wait_locked('cpu.slots/2.lock')
    marker = Path(self.tmp.name) / 'overtook'
    for _ in range(3):
      later = subprocess.run(self.command('--wait', '.15', '--resource',
                             'cpu', '--', sys.executable, '-c',
                             'from pathlib import Path; Path(%r).touch()' %
                             str(marker)), env=self.env, capture_output=True,
                             timeout=4)
      self.assertEqual(later.returncode, 75, later.stderr)
    self.assertFalse(marker.exists())
    active.terminate()
    active.communicate(timeout=5)
    self.wait_ready('exclusive', waiter)
    self.assertEqual(self.contender('--resource', 'cpu').returncode, 75)
    waiter.terminate()
    waiter.communicate(timeout=5)
    self.assertEqual(self.contender('--resource', 'cpu:3').returncode, 0)

  def test_wait_timeout_releases_partial_slots_and_turnstile(self):
    self.assertEqual(self.configure(3).returncode, 0)
    active = self.holder('active', '--resource', 'gpu:2')
    waiter = self.waiting_holder('timed-out', '--wait', '4', '--resource',
                                 'gpu:2')
    self.wait_locked('gpu.turnstile')
    self.wait_locked('gpu.slots/2.lock')
    _, error = waiter.communicate(timeout=6)
    self.assertEqual(waiter.returncode, 75, error)
    self.assertFalse((Path(self.tmp.name) / 'timed-out').exists())
    self.assertEqual(self.contender().returncode, 0)
    active.terminate()
    active.communicate(timeout=5)
    self.assertEqual(self.contender('--resource', 'gpu:3').returncode, 0)

  def test_interrupt_releases_partial_slots_and_turnstile(self):
    self.assertEqual(self.configure(3).returncode, 0)
    active = self.holder('active', '--resource', 'gpu:2')
    for sig in (signal.SIGINT, signal.SIGTERM):
      with self.subTest(signal=sig):
        waiter = self.waiting_holder('interrupted', '--resource', 'gpu:2')
        self.wait_locked('gpu.turnstile')
        self.wait_locked('gpu.slots/2.lock')
        waiter.send_signal(sig)
        _, error = waiter.communicate(timeout=5)
        self.assertEqual(waiter.returncode, 128 + sig, error)
        self.assertFalse((Path(self.tmp.name) / 'interrupted').exists())
        self.assertEqual(self.contender().returncode, 0)
    active.terminate()
    active.communicate(timeout=5)
    self.assertEqual(self.contender('--resource', 'gpu:3').returncode, 0)

  def test_different_resource_orders_do_not_deadlock(self):
    self.assertEqual(self.configure(1, 'alpha').returncode, 0)
    self.assertEqual(self.configure(1, 'beta').returncode, 0)
    blocker = self.holder('blocker', '--resource', 'beta')
    first = self.waiting_holder('first', '--resource', 'alpha',
                                '--resource', 'beta')
    self.wait_locked('alpha.slots/0.lock')
    self.wait_locked('beta.turnstile')
    second = self.waiting_holder('second', '--resource', 'beta',
                                 '--resource', 'alpha')
    # Even reversed argv must wait at alpha, before acquiring beta.
    self.wait_locked('alpha.turnstile')
    blocker.terminate()
    blocker.communicate(timeout=5)
    self.wait_ready('first', first)
    self.assertFalse((Path(self.tmp.name) / 'second').exists())
    first.terminate()
    first.communicate(timeout=5)
    self.wait_ready('second', second)
    second.terminate()
    second.communicate(timeout=5)
    self.assertEqual(self.contender('--resource', 'beta',
                                    '--resource', 'alpha').returncode, 0)

  def test_child_receives_granted_credits(self):
    self.assertEqual(self.configure(8, 'cpu').returncode, 0)
    self.assertEqual(self.configure(3, 'build.fast-job').returncode, 0)
    env = dict(self.env, AGENT_RESOURCE_CPU_CREDITS='99')
    script = ('import os; print(os.environ["AGENT_RESOURCE_CPU_CREDITS"]); '
              'print(os.environ["AGENT_RESOURCE_BUILD_FAST_JOB_CREDITS"]); '
              'print(os.environ["AGENT_RESOURCE_GPU_CREDITS"])')
    result = subprocess.run(self.command('--resource', 'cpu:8',
                            '--resource', 'build.fast-job:2', '--resource',
                            'gpu', '--', sys.executable, '-c', script),
                            env=env, capture_output=True, timeout=4)
    self.assertEqual(result.returncode, 0, result.stderr)
    self.assertEqual(result.stdout.decode().splitlines(), ['8', '2', '1'])

  def test_turnstile_symlink_refused(self):
    self.assertEqual(self.configure(2).returncode, 0)
    root = Path(self.tmp.name) / 'agent-kit/resources'
    victim = Path(self.tmp.name) / 'victim'
    victim.write_text('preserve')
    (root / 'gpu.turnstile').symlink_to(victim)
    self.assertEqual(self.contender().returncode, 1)
    self.assertEqual(victim.read_text(), 'preserve')

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

  def test_credit_variable_names_must_not_collide(self):
    for first, second in (('a.b', 'a-b'), ('gpu', 'GPU')):
      with self.subTest(first=first, second=second):
        proc = subprocess.run(self.command('--resource', first, '--resource',
                                           second, '--', 'true'),
                              env=self.env, capture_output=True, timeout=3)
        self.assertEqual(proc.returncode, 2)

  def test_resource_names_fold_to_lowercase(self):
    # Case-insensitive filesystems would alias CPU and cpu lock files, so a
    # name is one resource whatever its case.
    self.assertEqual(self.configure(1, 'CPU').returncode, 0)
    self.holder('upper', '--resource', 'Cpu')
    self.assertEqual(self.contender('--resource', 'cpu').returncode, 75)
    names = os.listdir(Path(self.tmp.name) / 'agent-kit/resources')
    self.assertIn('cpu.capacity', names)
    self.assertNotIn('CPU.capacity', names)

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
