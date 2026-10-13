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


def shared_test_clock():
  # Independent platform clock oracle; local fixture deadlines stay local.
  return (time.clock_gettime_ns(time.CLOCK_UPTIME_RAW) / 1e9
          if sys.platform == 'darwin' else time.monotonic())


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
        for path in (root / 'cpu.queue').glob('*.ticket'):
          row = json.loads(path.read_text())
          row['enqueued'] = value
          row['grace'] = .001
          path.write_text(json.dumps(row))
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
    self.assertEqual(self.tickets(0, 'gpu'), [])
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
        self.assertEqual(self.tickets(0, 'gpu'), [])
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
    self.assertEqual(self.tickets(0, 'gpu'), [])

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
                     ['request', 'resource_wait', 'resource_acquired',
                      'admitted', 'resource_released', 'released'])
    self.assertEqual(len({row['request_id'] for row in rows}), 1)
    self.assertTrue(all(row['resources'] == {'cpu': 1} for row in rows))
    self.assertGreaterEqual(rows[3]['wait_seconds'], 0)
    self.assertGreater(rows[-1]['held_seconds'], 0)
    self.assertEqual(rows[-1]['exit'], 7)
    self.assertLessEqual(rows[1]['monotonic'], rows[2]['monotonic'])
    self.assertLessEqual(rows[2]['monotonic'], rows[4]['monotonic'])
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

  def paused_waiter(self, name, weight, *extra, clock_origin=None):
    paused, resume = (Path(self.tmp.name) / (name + suffix)
                      for suffix in ('.paused', '.resume'))
    marker = Path(self.tmp.name) / name
    script = """import pathlib,runpy,sys,time
functions=runpy.run_path(%r)
origin=%r
if origin is not None:
  local_seconds,local_ns=time.monotonic,time.monotonic_ns
  time.monotonic=lambda: local_seconds()+origin
  time.monotonic_ns=lambda: local_ns()+int(origin*1000000000)
namespace=functions['main'].__globals__
original=namespace['retry']
def paused_retry(deadline, *args):
  pathlib.Path(%r).touch()
  while not pathlib.Path(%r).exists():
    original(deadline, *args)
  original(deadline, *args)
namespace['retry']=paused_retry
sys.argv=%r
sys.exit(functions['main']())
""" % (str(BIN), clock_origin, str(paused), str(resume),
       self.command('--resource', 'cpu:' + str(weight), *extra, '--',
                    sys.executable, '-c', 'import pathlib,time; '
                    'pathlib.Path(%r).touch(); time.sleep(20)' % str(marker))[1:])
    proc = subprocess.Popen([sys.executable, '-c', script], env=self.env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    def cleanup():
      if proc.poll() is None:
        proc.terminate()
      proc.communicate(timeout=5)
    self.addCleanup(cleanup)
    self.wait_ready(name + '.paused', proc)
    return proc, resume

  def tickets(self, count, name='cpu'):
    root = Path(self.tmp.name) / 'agent-kit/resources'
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
      paths = sorted((root / (name + '.queue')).glob('*.ticket'))
      if len(paths) == count:
        return paths
      time.sleep(.02)
    self.fail('registered queue did not contain %d tickets' % count)

  def age_tickets(self, paths):
    root = Path(self.tmp.name) / 'agent-kit/resources'
    with (root / 'cpu.turnstile').open('r+') as gate:
      fcntl.flock(gate, fcntl.LOCK_EX)
      for path in paths:
        row = json.loads(path.read_text())
        row['enqueued'] = max(0, shared_test_clock() - 100)
        row['grace'] = 30
        path.write_text(json.dumps(row))

  def status(self, name='cpu'):
    return subprocess.run([sys.executable, str(BIN), 'status', '--resource',
                           name], env=self.env, capture_output=True, timeout=2)

  def test_ticket_age_starts_only_after_atomic_registration(self):
    self.assertEqual(self.configure(1, 'cpu').returncode, 0)
    self.holder('active', '--resource', 'cpu')
    root = Path(self.tmp.name) / 'agent-kit/resources'
    with (root / 'cpu.turnstile').open('r+') as gate:
      fcntl.flock(gate, fcntl.LOCK_EX)
      waiter, resume = self.paused_waiter('registering', 1)
      self.assertEqual(list((root / 'cpu.queue').glob('*.ticket')), [])
      released = shared_test_clock()
    resume.touch()
    path = self.tickets(1)[0]
    self.assertGreaterEqual(json.loads(path.read_text())['enqueued'], released)
    waiter.terminate()
    waiter.communicate(timeout=5)
    self.tickets(0)

  def test_scanning_disappearing_ticket_never_recreates_it(self):
    self.assertEqual(self.configure(1, 'cpu').returncode, 0)
    self.holder('active', '--resource', 'cpu')
    head, _ = self.paused_waiter('head', 1)
    path = self.tickets(1)[0]
    functions = runpy.run_path(str(BIN))
    namespace = functions['live_tickets'].__globals__
    original = namespace['secure_open']
    def disappearing(candidate, *args, **kwargs):
      candidate.unlink()
      return original(candidate, *args, **kwargs)
    with mock.patch.dict(namespace, secure_open=disappearing):
      self.assertEqual(functions['live_tickets'](path.parent, prune=True), [])
    self.assertFalse(path.exists())
    head.terminate()
    head.communicate(timeout=5)

  def test_malformed_live_ticket_refuses_without_traceback_or_slots(self):
    self.assertEqual(self.configure(3, 'cpu').returncode, 0)
    self.holder('active', '--resource', 'cpu')
    head, _ = self.paused_waiter('head', 3)
    path = self.tickets(1)[0]
    root = Path(self.tmp.name) / 'agent-kit/resources'
    with (root / 'cpu.turnstile').open('r+') as gate:
      fcntl.flock(gate, fcntl.LOCK_EX)
      path.write_text('{incomplete')
    result = self.contender('--resource', 'cpu')
    self.assertEqual(result.returncode, 1)
    self.assertNotIn(b'Traceback', result.stderr)
    for index in (1, 2):
      with (root / 'cpu.slots' / ('%d.lock' % index)).open('r+') as slot:
        fcntl.flock(slot, fcntl.LOCK_EX | fcntl.LOCK_NB)
    head.terminate()
    head.communicate(timeout=5)
    self.assertEqual(self.contender('--resource', 'cpu').returncode, 0)

  def test_fitting_registered_head_cannot_be_overtaken(self):
    self.assertEqual(self.configure(3, 'cpu').returncode, 0)
    active = self.holder('active', '--resource', 'cpu')
    head, resume = self.paused_waiter('head', 3)
    self.tickets(1)
    active.terminate()
    active.communicate(timeout=5)
    # Head is registered but deliberately not polling. All credits are free;
    # a younger one-credit child must still not steal its admission.
    self.assertEqual(self.contender('--resource', 'cpu').returncode, 75)
    self.assertFalse((Path(self.tmp.name) / 'head').exists())
    resume.touch()
    self.wait_ready('head', head)

  def test_aged_successor_drains_without_a_fresh_window(self):
    self.assertEqual(self.configure(3, 'cpu').returncode, 0)
    active = self.holder('active', '--resource', 'cpu')
    first, _ = self.paused_waiter('first', 3)
    self.tickets(1)
    second, resume = self.paused_waiter('second', 3)
    paths = self.tickets(2)
    self.age_tickets(paths)
    first.kill()
    first.communicate(timeout=5)
    self.assertEqual(self.contender('--resource', 'cpu').returncode, 75)
    remaining = self.tickets(1)
    self.assertEqual(remaining, paths[1:])
    for index in (1, 2):
      with (Path(self.tmp.name) / 'agent-kit/resources/cpu.slots' /
            ('%d.lock' % index)).open('r+') as slot:
        fcntl.flock(slot, fcntl.LOCK_EX | fcntl.LOCK_NB)
    active.terminate()
    active.communicate(timeout=5)
    resume.touch()
    self.wait_ready('second', second)

  def test_ticket_registration_order_and_private_cleanup(self):
    self.assertEqual(self.configure(1, 'cpu').returncode, 0)
    self.holder('active', '--resource', 'cpu')
    first, _ = self.paused_waiter('first', 1)
    before = self.tickets(1)
    second, _ = self.paused_waiter('second', 1)
    paths = self.tickets(2)
    self.assertEqual(paths[:1], before)
    self.assertEqual(paths[0].stat().st_mode & 0o777, 0o600)
    self.assertLessEqual(json.loads(paths[0].read_text())['enqueued'],
                         json.loads(paths[1].read_text())['enqueued'])
    for proc in (first, second):
      proc.terminate()
      proc.communicate(timeout=5)
    self.assertEqual(self.tickets(0), [])

  def test_status_groups_live_weights_and_explains_drain(self):
    self.assertEqual(self.configure(3, 'cpu').returncode, 0)
    self.holder('active', '--resource', 'cpu')
    self.paused_waiter('head', 3)
    self.age_tickets(self.tickets(1))
    self.paused_waiter('younger', 2)
    self.tickets(2)
    result = self.status()
    self.assertEqual(result.returncode, 0, result.stderr)
    row = json.loads(result.stdout)['resources']['cpu']
    self.assertEqual((row['capacity'], row['reserved'], row['free']), (3, 1, 2))
    self.assertEqual(row['queued_by_credits'], {'2': 1, '3': 1})
    self.assertEqual(row['head']['credits'], 3)
    self.assertEqual(row['head']['phase'], 'drain')
    self.assertGreaterEqual(row['head']['age_seconds'], 99)
    self.assertIsNone(row['eta_seconds'])
    self.assertIn('legacy', row['coverage'])

  def test_status_fresh_capacity_is_idle_without_creating_state(self):
    self.assertEqual(self.configure(3, 'cpu').returncode, 0)
    root = Path(self.tmp.name) / 'agent-kit/resources'
    before = {str(p): p.read_bytes() for p in root.rglob('*') if p.is_file()}
    self.assertFalse((root / 'cpu.turnstile').exists())
    result = self.status()
    self.assertEqual(result.returncode, 0, result.stderr)
    row = json.loads(result.stdout)['resources']['cpu']
    self.assertEqual((row['capacity'], row['reserved'], row['free']), (3, 0, 3))
    self.assertEqual(row['queued_by_credits'], {})
    self.assertNotIn('unknown', row)
    self.assertEqual({str(p): p.read_bytes() for p in root.rglob('*')
                      if p.is_file()}, before)

  def test_fresh_status_needs_exclusive_gate_to_establish_idle(self):
    self.assertEqual(self.configure(3, 'cpu').returncode, 0)
    root = Path(self.tmp.name) / 'agent-kit/resources'
    with (root / 'cpu.lock').open('r+') as gate:
      # A first caller can hold the shared gate before creating its turnstile.
      fcntl.flock(gate, fcntl.LOCK_SH)
      result = self.status()
    row = json.loads(result.stdout)['resources']['cpu']
    self.assertIsNone(row['capacity'])
    self.assertIsNone(row['queued_by_credits'])
    self.assertIn('unknown', row)
    self.assertFalse((root / 'cpu.turnstile').exists())

  def test_contended_stage_history_has_one_prechild_lock_budget(self):
    self.assertEqual(self.configure(1, 'r0').returncode, 0)
    root = Path(self.tmp.name) / 'agent-kit/resources'
    journal = root / 'admission.jsonl'
    journal.write_text('')
    journal.chmod(0o600)
    attempts, marker = (Path(self.tmp.name) / name
                        for name in ('journal-attempts', 'child-started'))
    resources = [value for n in range(8) for value in
                 ('--resource', 'r%d' % n)]
    script = """import fcntl,os,pathlib,runpy,sys
journal_inode=os.stat(%r).st_ino
original=fcntl.flock
def observed(fd, flags):
  if os.fstat(fd).st_ino==journal_inode:
    with open(%r, 'a') as stream:
      stream.write('attempt\\n')
  return original(fd, flags)
fcntl.flock=observed
sys.argv=%r
sys.exit(runpy.run_path(%r)['main']())
""" % (str(journal), str(attempts),
       self.command(*resources, '--', sys.executable, '-c',
                    'import pathlib; pathlib.Path(%r).touch()' % str(marker))[1:],
       str(BIN))
    with journal.open('r+') as held:
      fcntl.flock(held, fcntl.LOCK_EX)
      proc = subprocess.Popen([sys.executable, '-c', script], env=self.env,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE)
      try:
        deadline = time.monotonic() + 5
        while proc.poll() is None and time.monotonic() < deadline:
          count = (len(attempts.read_text().splitlines())
                   if attempts.exists() else 0)
          if marker.exists() or count >= 8:
            break
          time.sleep(.01)
        # Real held journal + kernel-call counter, not a subsecond wall bound:
        # child starts before additive per-row lock waits accumulate.
        if not marker.exists():
          self.assertGreaterEqual(count, 8, 'journal observer did not reach barrier')
        self.assertTrue(marker.exists(), 'journal attempts delayed child start')
        _, error = proc.communicate(timeout=10)
        self.assertEqual(proc.returncode, 0, error)
      finally:
        if proc.poll() is None:
          proc.terminate()
        proc.communicate(timeout=10)

  @unittest.skipUnless(sys.platform == 'darwin', 'macOS clock compatibility')
  def test_ticket_order_and_age_ignore_process_local_clock_origins(self):
    self.assertEqual(self.configure(3, 'cpu').returncode, 0)
    self.holder('active', '--resource', 'cpu')
    self.paused_waiter('first', 3, clock_origin=1000)
    first = self.tickets(1)[0]
    self.paused_waiter('second', 3, clock_origin=-1000)
    paths = self.tickets(2)
    self.assertEqual(paths[0], first)
    rows = [json.loads(path.read_text()) for path in paths]
    shared_now = time.clock_gettime_ns(time.CLOCK_UPTIME_RAW) / 1e9
    self.assertTrue(all(0 <= shared_now - row['enqueued'] < 10 for row in rows))
    result = self.status()
    self.assertEqual([row['position'] for row in
                      json.loads(result.stdout)['resources']['cpu']['queued']],
                     [1, 2])

  def test_darwin_queue_clock_uses_explicit_shared_uptime_clock(self):
    functions = runpy.run_path(str(BIN))
    with mock.patch.object(sys, 'platform', 'darwin'), \
         mock.patch.object(time, 'CLOCK_UPTIME_RAW', 123, create=True), \
         mock.patch.object(time, 'clock_gettime_ns', return_value=456) as clock, \
         mock.patch.object(time, 'monotonic_ns', side_effect=AssertionError):
      self.assertEqual(functions['queue_clock_ns'](), 456)
    clock.assert_called_once_with(123)

  def test_missing_darwin_shared_clock_refuses_cleanly(self):
    functions = runpy.run_path(str(BIN))
    with mock.patch.object(sys, 'platform', 'darwin'), \
         mock.patch.object(time, 'CLOCK_UPTIME_RAW', None, create=True):
      with self.assertRaisesRegex(OSError, 'macOS 10.12'):
        functions['queue_clock_ns']()

  def test_missing_shared_clock_main_returns_cleanly_without_child(self):
    functions = runpy.run_path(str(BIN))
    marker = Path(self.tmp.name) / 'must-not-start'
    handlers = {sig: signal.getsignal(sig)
                for sig in (signal.SIGINT, signal.SIGTERM)}
    error = io.StringIO()
    try:
      with mock.patch.dict(os.environ, self.env), \
           mock.patch.object(sys, 'platform', 'darwin'), \
           mock.patch.object(time, 'CLOCK_UPTIME_RAW', None, create=True), \
           mock.patch.object(sys, 'argv', self.command('--', sys.executable,
                             '-c', 'import pathlib; pathlib.Path(%r).touch()'
                             % str(marker))[1:]), \
           contextlib.redirect_stderr(error):
        result = functions['main']()
    finally:
      for sig, handler in handlers.items():
        signal.signal(sig, handler)
    self.assertEqual(result, 1)
    self.assertIn('macOS 10.12', error.getvalue())
    self.assertNotIn('Traceback', error.getvalue())
    self.assertFalse(marker.exists())
    self.assertEqual(self.history()[-1]['reason'], 'error')

  def test_missing_shared_clock_status_preserves_unknown_queue_fields(self):
    self.assertEqual(self.configure(3, 'cpu').returncode, 0)
    self.holder('active', '--resource', 'cpu')
    self.paused_waiter('head', 3)
    self.tickets(1)
    root = Path(self.tmp.name) / 'agent-kit/resources'
    functions = runpy.run_path(str(BIN))
    namespace = functions['resource_status'].__globals__
    def unavailable():
      raise OSError('shared clock unavailable')
    with mock.patch.dict(namespace, queue_clock=unavailable):
      row = functions['resource_status'](root, 'cpu')
    self.assertIsNone(row['queued_by_credits'])
    self.assertIsNone(row['queued'])
    self.assertIn('unknown', row)

  def test_batched_history_short_writes_retain_whole_rows_and_cap(self):
    self.assertEqual(self.configure(1).returncode, 0)
    root = Path(self.tmp.name) / 'agent-kit/resources'
    functions = runpy.run_path(str(BIN))
    events = [({'cpu': 1}, {'event': 'resource_wait', 'padding': 'x' * 70000})
              for _ in range(20)]
    events.append(({'cpu': 1}, {'event': 'admitted'}))
    original = os.pwrite
    calls = [0]
    def short_write(fd, data, offset):
      calls[0] += 1
      return original(fd, data[:7] if calls[0] <= 5 else data, offset)
    with mock.patch.object(os, 'pwrite', short_write):
      functions['admission_events'](root, 'batch', events)
    rows = self.history()
    self.assertEqual(rows[-1]['event'], 'admitted')
    self.assertTrue(all(row['request_id'] == 'batch' for row in rows))
    self.assertTrue(all(len(row['padding']) == 70000 for row in rows[:-1]))
    self.assertLessEqual((root / 'admission.jsonl').stat().st_size, 1024 * 1024)

  def test_foreign_legacy_priority_flock_is_never_overridden(self):
    self.assertEqual(self.configure(3).returncode, 0)
    self.holder('active', '--resource', 'gpu:2')
    root = Path(self.tmp.name) / 'agent-kit/resources'
    with (root / 'gpu.priority').open('r+') as owner:
      fcntl.flock(owner, fcntl.LOCK_EX)
      for timestamp, result in ((0, 75), (1e20, 0)):
        with self.subTest(timestamp=timestamp):
          owner.seek(0)
          owner.truncate()
          owner.write(str(timestamp))
          owner.flush()
          self.assertEqual(self.contender().returncode, result)
          with (root / 'gpu.priority').open('r+') as probe:
            with self.assertRaises(BlockingIOError):
              fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
    self.assertEqual(self.contender().returncode, 0)

  def test_status_existing_empty_capacity_uses_default_one(self):
    self.assertEqual(self.contender('--resource', 'cpu').returncode, 0)
    root = Path(self.tmp.name) / 'agent-kit/resources'
    self.assertEqual((root / 'cpu.capacity').read_bytes(), b'')
    result = self.status()
    self.assertEqual(result.returncode, 0, result.stderr)
    row = json.loads(result.stdout)['resources']['cpu']
    self.assertEqual((row['capacity'], row['reserved'], row['free']), (1, 0, 1))
    self.assertNotIn('unknown', row)

  def test_status_unconfigured_creates_nothing(self):
    before = set(Path(self.tmp.name).rglob('*'))
    result = self.status('unconfigured')
    self.assertEqual(result.returncode, 0, result.stderr)
    row = json.loads(result.stdout)['resources']['unconfigured']
    self.assertIsNone(row['capacity'])
    self.assertEqual(set(Path(self.tmp.name).rglob('*')), before)

  def test_status_never_prunes_dead_tickets_or_recreates_slots(self):
    self.assertEqual(self.configure(3, 'cpu').returncode, 0)
    self.holder('active', '--resource', 'cpu')
    head, _ = self.paused_waiter('head', 3)
    paths = self.tickets(1)
    head.kill()
    head.communicate(timeout=5)
    root = Path(self.tmp.name) / 'agent-kit/resources'
    (root / 'cpu.slots/2.lock').unlink()
    before = {str(p): p.read_bytes() for p in root.rglob('*') if p.is_file()}
    result = self.status()
    self.assertEqual(result.returncode, 0, result.stderr)
    row = json.loads(result.stdout)['resources']['cpu']
    self.assertEqual(row['queued_by_credits'], {})
    self.assertEqual({str(p): p.read_bytes() for p in root.rglob('*')
                      if p.is_file()}, before)
    self.assertTrue(paths[0].exists())

  def test_status_capacity_read_is_guarded_against_reconfiguration(self):
    self.assertEqual(self.configure(2, 'cpu').returncode, 0)
    self.assertEqual(self.contender('--resource', 'cpu').returncode, 0)
    root = Path(self.tmp.name) / 'agent-kit/resources'
    functions = runpy.run_path(str(BIN))
    namespace = functions['resource_status'].__globals__
    original = namespace['secure_open']
    changed = [False]
    def replace_before_guard(path, *args, **kwargs):
      if path.name == 'cpu.turnstile' and not changed[0]:
        changed[0] = True
        self.assertEqual(self.configure(3, 'cpu').returncode, 0)
      return original(path, *args, **kwargs)
    with mock.patch.dict(namespace, secure_open=replace_before_guard):
      row = functions['resource_status'](root, 'cpu')
    self.assertTrue(changed[0])
    self.assertEqual((row['capacity'], row['reserved'], row['free']), (3, 0, 3))
    self.assertNotIn('unknown', row)

  def test_status_busy_turnstile_is_bounded_unknown(self):
    self.assertEqual(self.configure(1, 'cpu').returncode, 0)
    self.holder('active', '--resource', 'cpu')
    self.paused_waiter('head', 1)
    self.tickets(1)
    root = Path(self.tmp.name) / 'agent-kit/resources'
    with (root / 'cpu.turnstile').open('r+') as gate:
      fcntl.flock(gate, fcntl.LOCK_EX)
      result = self.status()
    self.assertEqual(result.returncode, 0, result.stderr)
    row = json.loads(result.stdout)['resources']['cpu']
    self.assertIsNone(row['free'])
    self.assertIsNone(row['capacity'])
    self.assertIsNone(row['queued_by_credits'])
    self.assertIsNone(row['queued'])

  def test_status_unsafe_files_are_unknown_and_preserved(self):
    self.assertEqual(self.configure(1, 'cpu').returncode, 0)
    root = Path(self.tmp.name) / 'agent-kit/resources'
    target = Path(self.tmp.name) / 'private-target'
    target.write_text('preserve')
    (root / 'cpu.capacity').unlink()
    (root / 'cpu.capacity').symlink_to(target)
    result = self.status()
    self.assertEqual(result.returncode, 0, result.stderr)
    self.assertIsNone(json.loads(result.stdout)['resources']['cpu']['capacity'])
    self.assertEqual(target.read_text(), 'preserve')

  def test_notice_once_for_long_cpu_gpu_wait(self):
    self.assertEqual(self.configure(1, 'cpu').returncode, 0)
    self.holder('active', '--resource', 'cpu')
    result = subprocess.run(self.command('--resource', 'cpu', '--resource',
                            'gpu', '--notice-after', '.01', '--wait', '.3',
                            '--', 'true'), env=self.env,
                            capture_output=True, timeout=3)
    self.assertEqual(result.returncode, 75)
    self.assertEqual(result.stderr.count(b'Long resource wait:'), 1)
    self.assertIn(b'cpu:1', result.stderr)
    self.assertIn(b'gpu:1', result.stderr)
    self.assertIn(b'SteamOS', result.stderr)
    self.assertIn(b'VM', result.stderr)
    self.assertIn(b'alternative build machine', result.stderr)
    self.assertIn(b'compatible', result.stderr)
    self.assertIn(b'unknown', result.stderr.lower())
    self.assertNotIn(self.tmp.name.encode(), result.stderr)
    self.assertIn(b'agent-resource status', result.stderr)

  def test_custom_resource_wait_emits_no_host_reminder(self):
    self.assertEqual(self.configure(1, 'custom').returncode, 0)
    self.holder('custom-active', '--resource', 'custom')
    result = subprocess.run(self.command('--resource', 'custom', '--notice-after',
                            '.01', '--wait', '.2', '--', 'true'), env=self.env,
                            capture_output=True, timeout=3)
    self.assertEqual(result.returncode, 75)
    self.assertNotIn(b'Long resource wait:', result.stderr)
    self.assertNotIn(b'SteamOS', result.stderr)

  def test_immediate_jobs_emit_no_notice(self):
    result = subprocess.run(self.command('--notice-after', '.001', '--',
                            'true'), env=self.env, capture_output=True, timeout=3)
    self.assertEqual(result.returncode, 0, result.stderr)
    self.assertNotIn(b'Long resource wait:', result.stderr)

  def test_terminal_reason_distinguishes_admission_and_child_75(self):
    self.assertEqual(self.configure(1).returncode, 0)
    active = self.holder('active')
    self.assertEqual(self.contender().returncode, 75)
    self.assertEqual(self.history()[-1]['reason'], 'admission_timeout')
    active.terminate()
    active.communicate(timeout=5)
    result = subprocess.run(self.command('--', sys.executable, '-c',
                            'raise SystemExit(75)'), env=self.env,
                            capture_output=True, timeout=3)
    self.assertEqual(result.returncode, 75)
    self.assertEqual(self.history()[-1]['reason'], 'child_exit')
    self.assertEqual(self.history()[-1]['schema'], 2)
    self.assertEqual(self.history()[-1]['policy'], 'oldest-ticket-v1')
    stages = [r for r in self.history()
              if r['request_id'] == self.history()[-1]['request_id']
              and r['event'].startswith('resource_')]
    self.assertEqual([r['event'] for r in stages],
                     ['resource_wait', 'resource_acquired', 'resource_released'])
    self.assertTrue(all(isinstance(r['monotonic'], float) for r in stages))
    self.assertTrue(all(r['capacity'] == 1 for r in stages))
    self.assertNotIn('raise SystemExit', json.dumps(stages))

  def test_priority_short_writes_publish_complete_deadline(self):
    self.assertEqual(self.configure(2).returncode, 0)
    self.holder('active')
    root = Path(self.tmp.name) / 'agent-kit/resources'
    functions = runpy.run_path(str(BIN))
    namespace = functions['acquire'].__globals__
    original = os.pwrite
    def short_write(fd, data, offset):
      return original(fd, data[:1], offset)
    def inspect_wait(deadline):
      self.assertEqual(float((root / 'gpu.priority').read_text()), 130.0)
      with (root / 'gpu.priority').open('r+') as priority:
        with self.assertRaises(BlockingIOError):
          fcntl.flock(priority, fcntl.LOCK_EX | fcntl.LOCK_NB)
      raise TimeoutError('fixture admission stop')
    with mock.patch.object(os, 'pwrite', short_write), \
         mock.patch.object(time, 'monotonic', return_value=100.0), \
         mock.patch.dict(namespace, retry=inspect_wait,
                         queue_clock_ns=lambda: 100000000000):
      with self.assertRaises(TimeoutError):
        functions['acquire'](root, [('gpu', 2)], 200.0)
    self.assertEqual(self.contender().returncode, 0)

  def test_priority_zero_write_rejects_and_releases_turn(self):
    self.assertEqual(self.configure(2).returncode, 0)
    root = Path(self.tmp.name) / 'agent-kit/resources'
    functions = runpy.run_path(str(BIN))
    with mock.patch.object(os, 'pwrite', return_value=0):
      with self.assertRaisesRegex(OSError, 'write made no progress'):
        functions['acquire'](root, [('gpu', 1)], time.monotonic() + .2)
    self.assertEqual(self.contender('--resource', 'gpu:2').returncode, 0)

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
