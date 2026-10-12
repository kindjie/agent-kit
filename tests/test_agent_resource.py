"""Real process admission and timeout tests; no machine resources needed."""
import contextlib
import fcntl
import io
import json
import os
from pathlib import Path
import subprocess
import signal
import runpy
import sys
import tempfile
import time
import unittest
from unittest import mock

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

  def test_waiter_holds_no_partial_credits_and_small_jobs_backfill(self):
    self.assertEqual(self.configure(3).returncode, 0)
    self.holder('two', '--resource', 'gpu:2', '--timeout', '3')
    self.waiting_holder('waiter', '--resource', 'gpu:2', '--timeout', '3')
    # Synchronize either policy's waiter; the child exit and independent slot
    # flock below decide the contract, rather than its bookkeeping filename.
    root = Path(self.tmp.name) / 'agent-kit/resources'
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
      waiting = False
      for name in ('gpu.priority', 'gpu.turnstile'):
        path = root / name
        if path.exists():
          with path.open('r+') as stream:
            try:
              fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
              waiting = True
      if waiting:
        break
      time.sleep(.02)
    self.assertTrue(waiting, 'waiter did not reach admission')
    self.assertEqual(self.contender().returncode, 0)
    root = Path(self.tmp.name) / 'agent-kit/resources'
    with (root / 'gpu.slots/2.lock').open('r+') as slot:
      fcntl.flock(slot, fcntl.LOCK_EX | fcntl.LOCK_NB)
    self.assertFalse((Path(self.tmp.name) / 'waiter').exists())

  def test_backfill_bounded_then_full_budget_waiter_runs(self):
    self.assertEqual(self.configure(3, 'cpu').returncode, 0)
    active = self.holder('active', '--resource', 'cpu')
    waiter = self.waiting_holder('exclusive', '--resource', 'cpu:3',
                                 '--backfill', '.4')
    self.wait_locked('cpu.priority')
    root = Path(self.tmp.name) / 'agent-kit/resources'
    def set_deadline(value):
      # Control the clock boundary while preserving real independent child
      # and slot-lock oracles. Startup delays cannot expire this phase.
      with (root / 'cpu.turnstile').open('r+') as turnstile:
        fcntl.flock(turnstile, fcntl.LOCK_EX)
        with (root / 'cpu.priority').open('w') as priority:
          priority.write(str(value))
    set_deadline(1e20)
    self.assertEqual(self.contender('--resource', 'cpu').returncode, 0)
    set_deadline(0)
    for _ in range(3):
      self.assertEqual(self.contender('--resource', 'cpu').returncode, 75)
    # The drain stops new jobs without reserving the two idle slots.
    root = Path(self.tmp.name) / 'agent-kit/resources'
    for index in (1, 2):
      with (root / ('cpu.slots/%d.lock' % index)).open('r+') as slot:
        fcntl.flock(slot, fcntl.LOCK_EX | fcntl.LOCK_NB)
    active.terminate()
    active.communicate(timeout=5)
    self.wait_ready('exclusive', waiter)
    self.assertEqual(self.contender('--resource', 'cpu').returncode, 75)
    waiter.terminate()
    waiter.communicate(timeout=5)
    self.assertEqual(self.contender('--resource', 'cpu:3').returncode, 0)

  def test_wait_timeout_releases_priority(self):
    self.assertEqual(self.configure(3).returncode, 0)
    active = self.holder('active', '--resource', 'gpu:2')
    waiter = self.waiting_holder('timed-out', '--wait', '.6', '--resource',
                                 'gpu:2', '--backfill', '.1')
    self.wait_locked('gpu.priority')
    _, error = waiter.communicate(timeout=5)
    self.assertEqual(waiter.returncode, 75, error)
    self.assertFalse((Path(self.tmp.name) / 'timed-out').exists())
    self.assertEqual(self.contender().returncode, 0)
    active.terminate()
    active.communicate(timeout=5)
    self.assertEqual(self.contender('--resource', 'gpu:3').returncode, 0)

  def test_interrupt_releases_priority(self):
    self.assertEqual(self.configure(3).returncode, 0)
    active = self.holder('active', '--resource', 'gpu:2')
    for sig in (signal.SIGINT, signal.SIGTERM):
      with self.subTest(signal=sig):
        waiter = self.waiting_holder('interrupted', '--resource', 'gpu:2',
                                     '--backfill', '.1')
        self.wait_locked('gpu.priority')
        waiter.send_signal(sig)
        _, error = waiter.communicate(timeout=5)
        self.assertEqual(waiter.returncode, 128 + sig, error)
        self.assertFalse((Path(self.tmp.name) / 'interrupted').exists())
        self.assertEqual(self.contender().returncode, 0)
    active.terminate()
    active.communicate(timeout=5)
    self.assertEqual(self.contender('--resource', 'gpu:3').returncode, 0)

  def test_priority_death_does_not_leave_stale_reservation(self):
    self.assertEqual(self.configure(3).returncode, 0)
    self.holder('active', '--resource', 'gpu:2')
    waiter = self.waiting_holder('killed', '--resource', 'gpu:3',
                                 '--backfill', '.1')
    self.wait_locked('gpu.priority')
    time.sleep(.2)
    self.assertEqual(self.contender().returncode, 75)
    waiter.kill()
    waiter.communicate(timeout=5)
    self.assertEqual(self.contender().returncode, 0)

  def history(self):
    path = Path(self.tmp.name) / 'agent-kit/resources/admission.jsonl'
    return [json.loads(line) for line in path.read_text().splitlines()]

  def test_history_lifecycle_private_and_payload_free(self):
    result = subprocess.run(self.command('--resource', 'cpu:1', '--',
                            sys.executable, '-c',
                            'raise SystemExit(7) # sensitive-command-payload'),
                            env=self.env, capture_output=True, timeout=4)
    self.assertEqual(result.returncode, 7, result.stderr)
    rows = self.history()
    self.assertEqual([row['event'] for row in rows],
                     ['request', 'admitted', 'released'])
    self.assertEqual(len({row['request_id'] for row in rows}), 1)
    self.assertTrue(all(row['resources'] == {'cpu': 1} for row in rows))
    self.assertGreaterEqual(rows[1]['wait_seconds'], 0)
    self.assertGreater(rows[2]['held_seconds'], 0)
    self.assertEqual(rows[2]['exit'], 7)
    path = Path(self.tmp.name) / 'agent-kit/resources/admission.jsonl'
    self.assertEqual(path.stat().st_mode & 0o777, 0o600)
    self.assertNotIn('sensitive-command-payload', path.read_text())

  def test_history_wait_timeout_and_child_timeout(self):
    self.holder('active')
    self.assertEqual(self.contender().returncode, 75)
    released = self.history()[-1]
    self.assertEqual(released['event'], 'released')
    self.assertEqual(released['exit'], 75)
    self.assertEqual(released['held_seconds'], 0)
    self.assertGreater(released['wait_seconds'], .1)
    result = subprocess.run(self.command('--resource', 'other', '--timeout',
                            '.1', '--', sys.executable, '-c',
                            'import time; time.sleep(20)'), env=self.env,
                            capture_output=True, timeout=5)
    self.assertEqual(result.returncode, 124)
    self.assertEqual(self.history()[-1]['exit'], 124)

  def test_history_bounded_retention(self):
    self.assertEqual(self.contender().returncode, 0)
    path = Path(self.tmp.name) / 'agent-kit/resources/admission.jsonl'
    old = json.dumps({'event': 'old', 'padding': 'x' * 512}) + '\n'
    path.write_text(old * 2000)
    self.assertEqual(self.contender().returncode, 0)
    self.assertLessEqual(path.stat().st_size, 1024 * 1024)
    self.assertEqual(self.history()[-1]['event'], 'released')

  def test_history_delay_does_not_spend_admission_wait(self):
    self.assertEqual(self.configure(1).returncode, 0)
    root = Path(self.tmp.name) / 'agent-kit/resources'
    functions = runpy.run_path(str(BIN))
    namespace = functions['main'].__globals__
    clock = [100.0]
    with (root / 'gpu.lock').open('r+') as gate:
      fcntl.flock(gate, fcntl.LOCK_EX)
      def delayed_history(*args, **kwargs):
        if args[3] == 'request':
          clock[0] += .2
      def release_on_retry(seconds):
        clock[0] += .01
        fcntl.flock(gate, fcntl.LOCK_UN)
      handlers = {sig: signal.getsignal(sig)
                  for sig in (signal.SIGINT, signal.SIGTERM)}
      try:
        with mock.patch.dict(os.environ, self.env), \
             mock.patch.object(sys, 'argv', [str(BIN), 'run', '--resource',
                               'gpu', '--wait', '.1', '--', 'true']), \
             mock.patch.dict(namespace, admission_event=delayed_history), \
             mock.patch.object(time, 'monotonic', lambda: clock[0]), \
             mock.patch.object(time, 'sleep', release_on_retry):
          result = functions['main']()
      finally:
        for sig, handler in handlers.items():
          signal.signal(sig, handler)
    self.assertEqual(result, 0)

  def test_short_history_writes_preserve_complete_rows(self):
    self.assertEqual(self.contender().returncode, 0)
    root = Path(self.tmp.name) / 'agent-kit/resources'
    path = root / 'admission.jsonl'
    old = json.dumps({'event': 'old', 'padding': 'x' * 512}) + '\n'
    functions = runpy.run_path(str(BIN))
    original = os.pwrite
    for retained in (False, True):
      with self.subTest(retained=retained):
        path.write_text(old * (2000 if retained else 1))
        calls = [0]
        def short_write(fd, data, offset):
          calls[0] += 1
          return original(fd, data[:7] if calls[0] <= 3 else data, offset)
        with mock.patch.object(os, 'pwrite', short_write):
          functions['admission_event'](root, 'first', {'cpu': 1}, 'request')
          functions['admission_event'](root, 'second', {'cpu': 1}, 'request')
        rows = self.history()
        self.assertEqual([row['request_id'] for row in rows[-2:]],
                         ['first', 'second'])
        self.assertTrue(all(row['event'] == 'old' for row in rows[:-2]))
        self.assertLessEqual(path.stat().st_size, 1024 * 1024)

  def test_failed_history_write_discards_only_incomplete_record(self):
    self.assertEqual(self.contender().returncode, 0)
    root = Path(self.tmp.name) / 'agent-kit/resources'
    path = root / 'admission.jsonl'
    old = json.dumps({'event': 'old'}) + '\n'
    functions = runpy.run_path(str(BIN))
    original = os.pwrite
    path.write_text(old)
    calls = [0]
    def failed_write(fd, data, offset):
      calls[0] += 1
      if calls[0] == 1:
        return original(fd, data[:7], offset)
      raise OSError('fixture filesystem full')
    with mock.patch.object(os, 'pwrite', failed_write), \
         contextlib.redirect_stderr(io.StringIO()):
      functions['admission_event'](root, 'lost', {'cpu': 1}, 'request')
    self.assertEqual(path.read_text(), old)
    # A previous writer may have died without running its repair path.
    with path.open('a') as stream:
      stream.write('{"partial":')
    functions['admission_event'](root, 'next', {'cpu': 1}, 'request')
    self.assertEqual([row['event'] for row in self.history()], ['old', 'request'])
    self.assertEqual(self.history()[-1]['request_id'], 'next')

  def test_large_history_event_leaves_space_for_pending_row(self):
    self.assertEqual(self.configure(1).returncode, 0)
    root = Path(self.tmp.name) / 'agent-kit/resources'
    path = root / 'admission.jsonl'
    old = json.dumps({'event': 'old', 'padding': 'x' * 512}) + '\n'
    path.write_text(old * 2000)
    path.chmod(0o600)
    functions = runpy.run_path(str(BIN))
    requests = {'r%063d' % index: 1 for index in range(10000)}
    functions['admission_event'](root, 'fixture', requests, 'request')
    self.assertLessEqual(path.stat().st_size, 1024 * 1024)
    rows = self.history()
    self.assertEqual(rows[-1]['resources'], requests)
    self.assertTrue(all(row['event'] == 'old' for row in rows[:-1]))

  def test_single_oversized_history_event_cannot_exceed_cap(self):
    self.assertEqual(self.configure(1).returncode, 0)
    root = Path(self.tmp.name) / 'agent-kit/resources'
    functions = runpy.run_path(str(BIN))
    requests = {'r%063d' % index: 1 for index in range(20000)}
    error = io.StringIO()
    with contextlib.redirect_stderr(error):
      functions['admission_event'](root, 'fixture', requests, 'request')
    self.assertLessEqual((root / 'admission.jsonl').stat().st_size,
                         1024 * 1024)
    self.assertIn('exceeds history limit', error.getvalue())
    self.assertEqual(self.contender().returncode, 0)

  def test_priority_symlink_refused(self):
    self.assertEqual(self.configure(2).returncode, 0)
    root = Path(self.tmp.name) / 'agent-kit/resources'
    victim = Path(self.tmp.name) / 'victim'
    victim.write_text('preserve')
    (root / 'gpu.priority').symlink_to(victim)
    self.assertEqual(self.contender().returncode, 1)
    self.assertEqual(victim.read_text(), 'preserve')

  def test_history_symlink_preserves_target_and_admission(self):
    self.assertEqual(self.configure(1).returncode, 0)
    root = Path(self.tmp.name) / 'agent-kit/resources'
    target = Path(self.tmp.name) / 'target'
    target.write_text('preserve')
    (root / 'admission.jsonl').symlink_to(target)
    self.assertEqual(self.contender().returncode, 0)
    self.assertEqual(target.read_text(), 'preserve')

  def test_different_resource_orders_do_not_deadlock(self):
    self.assertEqual(self.configure(1, 'alpha').returncode, 0)
    self.assertEqual(self.configure(1, 'beta').returncode, 0)
    blocker = self.holder('blocker', '--resource', 'beta')
    first = self.waiting_holder('first', '--resource', 'alpha',
                                '--resource', 'beta')
    self.wait_locked('alpha.slots/0.lock')
    self.wait_locked('beta.priority')
    second = self.waiting_holder('second', '--resource', 'beta',
                                 '--resource', 'alpha')
    # Even reversed argv must wait at alpha, before acquiring beta.
    self.wait_locked('alpha.priority')
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

  def test_interrupt_running_child_reaches_cleanup(self):
    for sig in (signal.SIGINT, signal.SIGTERM):
      with self.subTest(signal=sig):
        name = 'running-%d' % sig
        active = self.holder(name, '--timeout', '2')
        active.send_signal(sig)
        _, error = active.communicate(timeout=1.5)
        self.assertEqual(active.returncode, 128 + sig, error)
        self.assertEqual(self.history()[-1]['exit'], 128 + sig)
        self.assertEqual(self.contender().returncode, 0)

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
