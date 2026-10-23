"""Run ownership regressions in a process with no real child-kernel access."""
from contextlib import ExitStack
import os
import ctypes
from pathlib import Path
import signal
import subprocess
import sys
import types
from unittest import mock

sys.dont_write_bytecode = True
denials = []


class HarnessSafetyViolation(BaseException):
  pass


def fail(message):
  # Product exception handling may catch the exception, never this ledger.
  denials.append(message)
  raise HarnessSafetyViolation('HARNESSFAIL: ' + message)


def denied(name):
  def refuse(*args, **kwargs):
    fail('denied ' + name)
  return refuse


def deadline(sig, frame):
  os.write(2, b'HARNESSFAIL: outer synthetic deadline\n')
  os._exit(124)


if signal.SIGALRM in signal.pthread_sigmask(signal.SIG_BLOCK, []):
  raise RuntimeError('ownership runner requires unblocked SIGALRM')
signal.signal(signal.SIGALRM, deadline)
signal.alarm(20)
guards = ExitStack()
guard = types.SimpleNamespace(fail=fail, failures=denials)
for obj, name in ((os, 'kill'), (os, 'killpg'), (os, 'fork'),
                  (subprocess, 'Popen'), (os, 'waitid'), (ctypes, 'CDLL'), (os, 'close')):
  hook = denied(name)
  setattr(guard, name, hook)
  guards.enter_context(mock.patch.object(obj, name, hook, create=True))
# Deliberately never close guards: denial survives every unittest cleanup.
sys.modules['synthetic_process_guard'] = guard

def run_synthetic_only():
  import importlib
  import unittest

  here = Path(__file__).resolve().parent
  fixture = here / 'agent_resource_ownership_cases.py'
  sys.path.insert(0, str(here))
  module = importlib.import_module('agent_resource_ownership_cases')
  if Path(module.__file__).resolve() != fixture.resolve():
    raise HarnessSafetyViolation('HARNESSFAIL: fixture import shadowed')

  # Literal method selection: no discovery and no real-child/fork proof class.
  methods = (
    'test_synthetic_already_reaped_refuses_numeric_signals',
    'test_synthetic_final_signal_precedes_only_reap',
    'test_synthetic_second_cleanup_adds_no_signal',
    'test_synthetic_sigchld_guard_precedes_state_and_spawn',
    'test_synthetic_thread_guard_precedes_state_and_spawn',
    'test_synthetic_lost_anchor_refuses_numeric_signals',
    'test_synthetic_deferred_signal_runs_after_Q_only_once',
    'test_synthetic_spawn_interrupt_keeps_child_for_cleanup',
    'test_synthetic_exit7_survives_cleanup_error',
    'test_synthetic_exit7_survives_terminal_history_error',
    'test_synthetic_timeout_none_waits_without_reaping',
    'test_synthetic_timeout_none_remains_cancellable',
    'test_synthetic_each_initial_install_boundary_cancels_before_spawn',
    'test_synthetic_no_owned_cleanup_install_window_at_any_boundary',
    'test_synthetic_cleanup_cancel_preserves_exit7',
    'test_synthetic_cleanup_cancel_preserves_timeout124',
    'test_synthetic_closed_stderr_cleanup_error_holds_Q_and_exit7',
    'test_synthetic_closed_stderr_Q_syscall_error_preserves_timeout124',
    'test_synthetic_diagnostic_format_error_cannot_bypass_Q',
    'test_synthetic_repeated_observer_faults_hold_Q_without_numeric_retry',
    'test_synthetic_missing_Q_proof_never_authorizes_release',
    'test_synthetic_telemetry_error_after_persistent_Q_preserves_exit7',
    'test_synthetic_throwing_print_and_observer_errors_still_require_Q',
    'test_synthetic_process_identity_requires_a_plain_owned_pid',
    'test_synthetic_shutdown_flush_preserves_primary_failure',
    'test_synthetic_success_keeps_normal_shutdown_error_reporting',
  )
  suite = unittest.TestSuite(module.SyntheticOwnershipTest(name)
                             for name in methods)
  result = unittest.TextTestRunner(verbosity=2).run(suite)
  if denials:
    print('HARNESSFAIL: process/fake-kernel safety failure recorded:', denials,
          file=sys.stderr)
    return 2
  if result.errors or result.skipped or result.testsRun != len(methods):
    print('HARNESSFAIL: error, skip or incomplete synthetic selection',
          file=sys.stderr)
    return 2
  return 0 if result.wasSuccessful() else 1


try:
  if sys.argv[1:] == ['--check-guard']:
    try:
      os.killpg(True, 0)
    except BaseException:
      pass  # A swallowed violation must still fail through the ledger.
    code = 2 if denials else 0
    print('HARNESSFAIL: swallowed denial recorded:', denials, file=sys.stderr)
  elif sys.argv[1:]:
    raise HarnessSafetyViolation('unexpected ownership-runner arguments')
  else:
    code = run_synthetic_only()
except BaseException as exc:
  print('HARNESSFAIL:', type(exc).__name__, str(exc), file=sys.stderr)
  code = 2
# Keep the deadline armed through sys.exit, atexit and final stream flushing.
sys.exit(code)
