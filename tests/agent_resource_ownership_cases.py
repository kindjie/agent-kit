"""Synthetic methods only. Import exclusively through the guarded launcher."""
from contextlib import ExitStack
import ctypes
import io
import types
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time
import unittest
from unittest import mock

# The launcher installs these hooks before importing this module or runpy.
guard = sys.modules.get('synthetic_process_guard')
if guard is None:
  raise RuntimeError('fixture requires the outer guarded launcher')
if not all((os.kill is guard.kill,
                             os.killpg is guard.killpg,
                             os.fork is guard.fork,
                             subprocess.Popen is guard.Popen,
                             os.waitid is guard.waitid,
                             ctypes.CDLL is guard.CDLL,
                             os.close is guard.close)):
  guard.fail('missing outer process guards')

import runpy

SOURCE = Path(__file__).resolve().parents[1] / 'bin' / 'agent-resource'


class FakeProcess:
  """Finite typed process state; never delegates to a real process."""
  def __init__(self, events, reaped=False):
    self.pid = 43210
    self.returncode = 7 if reaped else None
    self.events = events
    self.polls = 0
    self.waits = 0

  def poll(self):
    self.polls += 1
    if self.polls > 8:
      guard.fail('excessive fake polls')
    self.events.append(('poll', self.returncode))
    self.returncode = 7
    return 7

  def wait(self, timeout=None):
    self.waits += 1
    if self.waits > 3:
      guard.fail('excessive fake waits')
    self.events.append(('reap', timeout))
    self.returncode = 7
    return 7


class SyntheticOwnershipTest(unittest.TestCase):
  def setUp(self):
    # Registered first, runs last: nested overrides always restore denial.
    self.stack = ExitStack()
    self.addCleanup(self.stack.close)
    self.guard_failure_start = len(guard.failures)
    self.addCleanup(lambda: self.assertEqual(len(guard.failures),
        self.guard_failure_start, 'HARNESSFAIL: fake/kernel safety failure recorded'))
    self.events = []
    self.sleeps = 0
    self.zero_probes = 0
    self.q_schedule = []
    self.closed_after_Q = []
    self.observer_calls = 0
    self.cleanup_calls = 0
    self.clock = 0.0
    self.waitid_calls = 0
    self.active_child = None
    self.waitid_statuses = []
    self.synthetic_live = False
    self.handler_transitions = []
    self.last_injection_fired = False
    self.released_after_Q = []
    self.handlers = {sig: signal.SIG_DFL for sig in
                     (signal.SIGHUP, signal.SIGINT, signal.SIGTERM, signal.SIGCHLD)}
    self.patch(signal, 'signal', self.set_handler)
    self.patch(signal, 'getsignal', lambda sig: self.handlers[sig])
    self.patch(os, 'waitid', self.fake_waitid)
    for obj, name, denied in ((os, 'kill', guard.kill),
                              (os, 'killpg', guard.killpg),
                              (os, 'fork', guard.fork),
                              (subprocess, 'Popen', guard.Popen),
                              (ctypes, 'CDLL', guard.CDLL),
                              (os, 'close', guard.close)):
      self.stack.enter_context(mock.patch.object(obj, name, denied))
    # The source is imported only after all process guards are in place.
    self.ns = runpy.run_path(str(SOURCE))['main'].__globals__
    self.original_functions = {name: self.ns[name] for name in
        ('stop_group', 'wait_group_absent', 'wait_child')}


  def patch(self, obj, name, value):
    return self.stack.enter_context(mock.patch.object(obj, name, value))

  def set_handler(self, sig, handler):
    if sig not in (signal.SIGHUP, signal.SIGINT, signal.SIGTERM):
      guard.fail('unexpected handler mutation')
    previous = self.handlers[sig]
    self.handlers[sig] = handler
    return previous

  def fake_waitid(self, idtype, pid, options):
    self.waitid_calls += 1
    child = self.active_child
    expected = os.WEXITED | os.WNOHANG | os.WNOWAIT
    if (self.waitid_calls > 16 or idtype != getattr(os, 'P_PID', 1) or
        type(pid) is not int or pid != 43210 or options != expected or
        child is None):
      guard.fail('invalid synthetic waitid')
    if child.returncode is not None:
      raise ChildProcessError('synthetic anchor externally reaped')
    status = self.waitid_statuses.pop(0) if self.waitid_statuses else 7
    return (None if status is None else
            types.SimpleNamespace(si_pid=43210, si_code=1, si_status=status))

  def fake_sleep(self, seconds):
    self.sleeps += 1
    if self.sleeps > 8 or seconds < 0 or seconds > 1:
      guard.fail('unbounded fake sleep')
    self.clock += seconds

  def fake_killpg(self, child):
    def send(pid, sig):
      if type(pid) is not int or pid != 43210:
        guard.fail('nonsynthetic group identity')
      if sig not in (0, signal.SIGTERM, signal.SIGKILL):
        guard.fail('unexpected synthetic signal')
      self.events.append(('signal', int(sig), child.returncode))
      if sig == 0:
        self.zero_probes += 1
        if self.zero_probes > 12:
          guard.fail('excessive fake Q probes')
        outcome = self.q_schedule.pop(0) if self.q_schedule else 'absent'
        if outcome == 'present':
          return
        if outcome == 'error':
          raise OSError('synthetic Q syscall error')
        if outcome != 'absent':
          guard.fail('unknown fake Q outcome')
        self.synthetic_live = False
        raise ProcessLookupError()
    return send

  def install_finite_group(self, child, stack=None):
    self.active_child = child
    def patch(obj, name, value):
      if stack is None:
        return self.patch(obj, name, value)
      return stack.enter_context(mock.patch.object(obj, name, value))
    patch(os, 'killpg', self.fake_killpg(child))
    patch(time, 'sleep', self.fake_sleep)
    patch(time, 'monotonic', lambda: self.clock)

  def test_synthetic_already_reaped_refuses_numeric_signals(self):
    child = FakeProcess(self.events, reaped=True)
    self.install_finite_group(child)
    try:
      self.ns['stop_group'](child)
    except ChildProcessError:
      pass
    self.assertEqual([e for e in self.events if e[0] == 'signal'], [])

  def test_synthetic_final_signal_precedes_only_reap(self):
    child = FakeProcess(self.events)
    self.install_finite_group(child)
    self.ns['stop_group'](child)
    nonzero = [e for e in self.events if e[0] == 'signal' and e[1]]
    self.assertTrue(nonzero, self.events)
    self.assertTrue(all(e[2] is None for e in nonzero), self.events)
    self.assertEqual(child.waits, 1)
    reap_at = next(i for i, e in enumerate(self.events) if e[0] == 'reap')
    self.assertTrue(all(i < reap_at for i, e in enumerate(self.events)
                        if e[0] == 'signal' and e[1]), self.events)

  def test_synthetic_second_cleanup_adds_no_signal(self):
    child = FakeProcess(self.events)
    self.install_finite_group(child)
    self.ns['stop_group'](child)
    first = list(self.events)
    try:
      self.ns['stop_group'](child)
    except ChildProcessError:
      pass
    self.assertEqual(self.events, first)

  def run_main(self, *, sigchld=signal.SIG_DFL, threads=1,
               spawn_interrupt=False, cleanup_failure=False, history_failure=False,
               install_interrupt=None, cleanup_interrupt=False, force_timeout=False,
               q_schedule=(), fake_handles=False, observer_faults=0,
               observer_unknown=0, cleanup_exception=None):
    with ExitStack() as run_stack:
      def patch(obj, name, value):
        return run_stack.enter_context(mock.patch.object(obj, name, value))
      self.events = []
      self.waitid_calls = self.sleeps = self.zero_probes = 0
      self.clock = 0.0
      self.handler_transitions = []
      self.released_after_Q = []
      self.closed_after_Q = []
      self.q_schedule = list(q_schedule)
      self.observer_calls = self.cleanup_calls = 0
      child = FakeProcess(self.events)
      self.install_finite_group(child, run_stack)
      self.synthetic_live = False
      self.last_injection_fired = False
      state_calls = []
      spawns = []
      handlers = {signal.SIGINT: signal.SIG_DFL,
                  signal.SIGTERM: signal.SIG_DFL,
                  signal.SIGHUP: signal.SIG_DFL,
                  signal.SIGCHLD: sigchld}
      def set_handler(sig, handler):
        queue_install = getattr(handler, '__name__', '') == 'queue_interrupt'
        phase = 'owned' if self.synthetic_live else 'before_spawn'
        self.handler_transitions.append((sig, self.synthetic_live, queue_install))
        target = install_interrupt
        def inject(boundary):
          if (target == (phase, sig, boundary) and queue_install and
              not self.last_injection_fired):
            self.last_injection_fired = True
            handlers[sig](sig, None)  # CURRENT handler, never a raised stand-in.
        inject('before')
        old = handlers.get(sig, signal.SIG_DFL)
        handlers[sig] = handler
        inject('after')
        return old
      def fake_popen(command, **kwargs):
        if (command != ['fictional'] or kwargs.get('start_new_session') is not True or
            kwargs.get('pass_fds') != ([65432] if fake_handles else [])):
          guard.fail('unexpected synthetic spawn')
        spawns.append(tuple(command))
        self.synthetic_live = True
        if spawn_interrupt:
          handlers[signal.SIGTERM](signal.SIGTERM, None)
        return child
      def fake_state():
        state_calls.append('state')
        return Path('/synthetic-state-unused')
      patch(signal, 'signal', set_handler)
      patch(signal, 'getsignal', lambda sig: handlers.get(sig, signal.SIG_DFL))
      patch(threading, 'active_count', lambda: threads)
      patch(subprocess, 'Popen', fake_popen)
      patch(sys, 'argv', [str(SOURCE), 'run', '--resource', 'gpu',
                               '--', 'fictional'])
      def history(*args, **kwargs):
        if args[-1][-1][1].get('event') == 'released':
          self.released_after_Q.append(not self.synthetic_live)
          if history_failure:
            raise OSError('synthetic terminal telemetry failure')
      run_stack.enter_context(mock.patch.dict(self.ns, secure_directory=fake_state,
          acquire=lambda *a, **kw: [65432] if fake_handles else [],
          admission_event=lambda *a, **kw: None,
          admission_events=history))
      stop = self.original_functions['stop_group']
      def single_cleanup(proc):
        self.cleanup_calls += 1
        if self.cleanup_calls > 1:
          guard.fail('numeric cleanup retried')
        if cleanup_failure:
          raise (cleanup_exception if cleanup_exception is not None else
                 OSError('synthetic cleanup failure'))
        return stop(proc)
      run_stack.enter_context(mock.patch.dict(self.ns, stop_group=single_cleanup))
      observe_Q = self.original_functions['wait_group_absent']
      def finite_observer(pid):
        self.observer_calls += 1
        if self.observer_calls > 8:
          guard.fail('excessive Q observer calls')
        if self.observer_calls <= observer_faults:
          raise OSError('synthetic Q observer failure')
        if self.observer_calls <= observer_faults + observer_unknown:
          return None  # No proof; must never authorize release.
        return observe_Q(pid)
      run_stack.enter_context(mock.patch.dict(self.ns, wait_group_absent=finite_observer))
      if fake_handles:
        def close_fd(fd):
          if type(fd) is not int or fd != 65432:
            guard.fail('nonsynthetic FD close')
          self.closed_after_Q.append((fd, not self.synthetic_live))
        patch(os, 'close', close_fd)
      if cleanup_interrupt:
        send = self.fake_killpg(child)
        def during_cleanup(pid, sig):
          send(pid, sig)
          if sig == signal.SIGTERM:
            handlers[signal.SIGTERM](signal.SIGTERM, None)
        patch(os, 'killpg', during_cleanup)
      if force_timeout:
        def timeout(proc, seconds, pending):
          raise self.ns['subprocess'].TimeoutExpired('synthetic child', seconds)
        run_stack.enter_context(mock.patch.dict(self.ns, wait_child=timeout))
      result = self.ns['main']()
      self.assertEqual(self.cleanup_calls, 1 if spawns else 0)
      if spawns and not cleanup_failure:
        self.assertEqual(child.waits, 1)
        self.assertEqual(child.polls, 0)
        self.assertEqual([e[1] for e in self.events if e[0] == 'signal' and e[1]],
                         [15, 9])
      return result, state_calls, spawns

  def test_synthetic_sigchld_guard_precedes_state_and_spawn(self):
    result, state_calls, spawns = self.run_main(sigchld=signal.SIG_IGN)
    self.assertEqual((result, state_calls, spawns), (1, [], []))

  def test_synthetic_thread_guard_precedes_state_and_spawn(self):
    result, state_calls, spawns = self.run_main(threads=2)
    self.assertEqual((result, state_calls, spawns), (1, [], []))


  def test_synthetic_lost_anchor_refuses_numeric_signals(self):
    child = FakeProcess(self.events)
    self.install_finite_group(child)
    def lost(*args):
      raise ChildProcessError('synthetic external reap')
    self.patch(os, 'waitid', lost)
    with self.assertRaises(ChildProcessError):
      self.ns['stop_group'](child)
    self.assertEqual(self.events, [])

  def test_synthetic_deferred_signal_runs_after_Q_only_once(self):
    child = FakeProcess(self.events)
    self.install_finite_group(child)
    send = self.fake_killpg(child)
    def signal_during_term(pid, sig):
      send(pid, sig)
      if sig == signal.SIGTERM:
        self.handlers[signal.SIGTERM](signal.SIGTERM, None)
    self.patch(os, 'killpg', signal_during_term)
    with self.ns['defer_interrupts']() as pending:
      self.ns['stop_group'](child)
      self.assertEqual([sig for sig, frame in pending], [signal.SIGTERM])
    self.assertTrue(child._resource_group_stopped)
    before = list(self.events)
    self.ns['stop_group'](child)
    self.assertEqual(self.events, before)
    self.assertEqual(self.events[-1], ('signal', 0, 7))

  def test_synthetic_spawn_interrupt_keeps_child_for_cleanup(self):
    result, state, spawns = self.run_main(spawn_interrupt=True)
    self.assertEqual(result, 128 + signal.SIGTERM)
    self.assertEqual(spawns, [('fictional',)])
    self.assertEqual(self.events[-1], ('signal', 0, 7))
    self.assertEqual(len([e for e in self.events if e[0] == 'reap']), 1)

  def test_synthetic_exit7_survives_cleanup_error(self):
    output = io.StringIO()
    with mock.patch.object(sys, 'stderr', output):
      result, state, spawns = self.run_main(cleanup_failure=True)
    self.assertEqual(result, 7)
    self.assertIn('primary exit 7', output.getvalue())
    self.assertEqual([e for e in self.events if e[0] == 'signal'],
                     [('signal', 0, None)])

  def test_synthetic_exit7_survives_terminal_history_error(self):
    output = io.StringIO()
    with mock.patch.object(sys, 'stderr', output):
      result, state, spawns = self.run_main(history_failure=True)
    self.assertEqual(result, 7)
    self.assertIn('primary exit 7', output.getvalue())


  def test_synthetic_timeout_none_waits_without_reaping(self):
    child = FakeProcess(self.events)
    self.install_finite_group(child)
    self.waitid_statuses = [None, 7]
    self.assertEqual(self.ns['wait_child'](child, None, []), 7)
    self.assertEqual((child.polls, child.waits, self.sleeps), (0, 0, 1))

  def test_synthetic_timeout_none_remains_cancellable(self):
    child = FakeProcess(self.events)
    self.install_finite_group(child)
    self.waitid_statuses = [None]
    pending = []
    sleep = self.fake_sleep
    def cancel_at_sleep(seconds):
      sleep(seconds)
      pending.append((signal.SIGTERM, None))
    self.patch(time, 'sleep', cancel_at_sleep)
    with self.assertRaises(self.ns['ResourceInterrupted']) as failure:
      self.ns['wait_child'](child, None, pending)
    self.assertEqual(failure.exception.args, (signal.SIGTERM,))
    self.assertEqual((child.polls, child.waits), (0, 0))

  def test_synthetic_each_initial_install_boundary_cancels_before_spawn(self):
    for sig in (signal.SIGHUP, signal.SIGINT, signal.SIGTERM):
      for boundary in ('before', 'after'):
        with self.subTest(signal=sig, boundary=boundary):
          result, state, spawns = self.run_main(
              install_interrupt=('before_spawn', sig, boundary))
          self.assertTrue(self.last_injection_fired)
          self.assertEqual((result, spawns), (128 + sig, []))
          self.assertFalse(self.synthetic_live)
          self.assertEqual(self.released_after_Q, [True])

  def test_synthetic_no_owned_cleanup_install_window_at_any_boundary(self):
    for sig in (signal.SIGHUP, signal.SIGINT, signal.SIGTERM):
      for boundary in ('before', 'after'):
        with self.subTest(signal=sig, boundary=boundary):
          self.handler_transitions = []
          result, state, spawns = self.run_main(
              install_interrupt=('owned', sig, boundary))
          self.assertEqual(result, 7)
          self.assertEqual(self.cleanup_calls, 1)
          self.assertEqual(self.active_child.waits, 1)
          self.assertEqual(self.active_child.polls, 0)
          self.assertEqual([e[1] for e in self.events
                            if e[0] == 'signal' and e[1]], [15, 9])
          self.assertFalse(self.last_injection_fired)
          self.assertFalse(any(live for _, live, _ in self.handler_transitions))
          self.assertFalse(self.synthetic_live)
          self.assertEqual(self.released_after_Q, [True])

  def test_synthetic_cleanup_cancel_preserves_exit7(self):
    result, state, spawns = self.run_main(cleanup_interrupt=True)
    self.assertEqual(result, 7)
    self.assertFalse(self.synthetic_live)
    self.assertEqual(self.released_after_Q, [True])

  def test_synthetic_cleanup_cancel_preserves_timeout124(self):
    result, state, spawns = self.run_main(cleanup_interrupt=True, force_timeout=True)
    self.assertEqual(result, 124)
    self.assertFalse(self.synthetic_live)
    self.assertEqual(self.released_after_Q, [True])


  def assert_finalization_after_Q(self):
    self.assertFalse(self.synthetic_live)
    self.assertEqual(self.closed_after_Q, [(65432, True)])
    self.assertEqual(self.released_after_Q, [True])
    self.assertFalse(any(live for _, live, _ in self.handler_transitions))
    self.assertEqual(self.cleanup_calls, 1)

  def test_synthetic_closed_stderr_cleanup_error_holds_Q_and_exit7(self):
    class BrokenStderr:
      attempts = 0
      def write(self, value):
        self.attempts += 1
        raise BrokenPipeError('synthetic closed stderr')
      def flush(self):
        raise BrokenPipeError('synthetic closed stderr')
    broken = BrokenStderr()
    with mock.patch.object(sys, 'stderr', broken):
      result, state, spawns = self.run_main(cleanup_failure=True,
          q_schedule=('present', 'present', 'absent'), fake_handles=True)
    self.assertEqual(result, 7)
    self.assertGreater(broken.attempts, 0)
    self.assertEqual(self.zero_probes, 3)
    self.assertEqual([e for e in self.events if e[0] == 'signal' and e[1]], [])
    self.assert_finalization_after_Q()

  def test_synthetic_closed_stderr_Q_syscall_error_preserves_timeout124(self):
    class BrokenStderr:
      def write(self, value):
        raise OSError('synthetic diagnostic sink failure')
      def flush(self):
        pass
    with mock.patch.object(sys, 'stderr', BrokenStderr()):
      result, state, spawns = self.run_main(cleanup_failure=True, force_timeout=True,
          q_schedule=('error', 'present', 'absent'), fake_handles=True)
    self.assertEqual(result, 124)
    self.assertEqual(self.zero_probes, 3)
    self.assert_finalization_after_Q()

  def test_synthetic_diagnostic_format_error_cannot_bypass_Q(self):
    class UnprintableError(OSError):
      def __str__(self):
        raise ValueError('synthetic diagnostic formatting failure')
    result, state, spawns = self.run_main(cleanup_failure=True,
        cleanup_exception=UnprintableError(),
        q_schedule=('present', 'absent'), fake_handles=True)
    self.assertEqual(result, 7)
    self.assertEqual(self.zero_probes, 2)
    self.assert_finalization_after_Q()

  def test_synthetic_repeated_observer_faults_hold_Q_without_numeric_retry(self):
    result, state, spawns = self.run_main(observer_faults=2,
        q_schedule=('present', 'absent'), fake_handles=True)
    self.assertEqual(result, 7)
    self.assertEqual(self.observer_calls, 3)
    self.assertEqual([e[1] for e in self.events if e[0] == 'signal' and e[1]], [15, 9])
    self.assertEqual(len([e for e in self.events if e[0] == 'reap']), 1)
    self.assert_finalization_after_Q()

  def test_synthetic_missing_Q_proof_never_authorizes_release(self):
    result, state, spawns = self.run_main(observer_unknown=2,
        q_schedule=('present', 'absent'), fake_handles=True)
    self.assertEqual(result, 7)
    self.assertEqual(self.observer_calls, 3)
    self.assert_finalization_after_Q()

  def test_synthetic_telemetry_error_after_persistent_Q_preserves_exit7(self):
    result, state, spawns = self.run_main(history_failure=True,
        q_schedule=('present', 'absent'), fake_handles=True)
    self.assertEqual(result, 7)
    self.assert_finalization_after_Q()

  def test_synthetic_throwing_print_and_observer_errors_still_require_Q(self):
    import builtins
    attempts = []
    def fail_print(*args, **kwargs):
      attempts.append(True)
      raise OSError('synthetic diagnostic print failure')
    with mock.patch.object(builtins, 'print', fail_print):
      result, state, spawns = self.run_main(observer_faults=2,
          q_schedule=('present', 'absent'), fake_handles=True)
    self.assertEqual(result, 7)
    self.assertGreaterEqual(len(attempts), 2)
    self.assert_finalization_after_Q()

  def test_synthetic_process_identity_requires_a_plain_owned_pid(self):
    for pid in (True, False, 0, 1, -1, mock.Mock()):
      with self.subTest(pid=pid):
        child = FakeProcess(self.events)
        child.pid = pid
        self.active_child = child
        before = self.waitid_calls
        with self.assertRaises(ChildProcessError):
          self.ns['observe_child'](child)
        self.assertEqual(self.waitid_calls, before)
        self.assertEqual(self.events, [])

  def test_synthetic_shutdown_flush_preserves_primary_failure(self):
    class BrokenStream:
      def flush(self):
        raise BrokenPipeError('synthetic shutdown flush failure')
    stdout, stderr = BrokenStream(), BrokenStream()
    with mock.patch.object(sys, 'stdout', stdout), \
         mock.patch.object(sys, 'stderr', stderr):
      self.assertEqual(self.ns['preserve_primary_exit'](7), 7)
      self.assertIsInstance(sys.stdout, self.ns['PrimaryExitStream'])
      self.assertIsInstance(sys.stderr, self.ns['PrimaryExitStream'])
      sys.stdout.flush()
      sys.stderr.flush()

  def test_synthetic_success_keeps_normal_shutdown_error_reporting(self):
    stdout, stderr = io.StringIO(), io.StringIO()
    with mock.patch.object(sys, 'stdout', stdout), \
         mock.patch.object(sys, 'stderr', stderr):
      self.assertEqual(self.ns['preserve_primary_exit'](0), 0)
      self.assertIs(sys.stdout, stdout)
      self.assertIs(sys.stderr, stderr)
