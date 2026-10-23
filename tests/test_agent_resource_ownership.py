"""Keep synthetic kernel hooks out of the native resource-test interpreter."""
from pathlib import Path
import os
import signal
import subprocess
import sys
import threading
import time
import unittest

RUNNER = Path(__file__).with_name('run_agent_resource_ownership.py')


class ResourceOwnershipTest(unittest.TestCase):
  def run_guarded(self, *args):
    # Reaping must belong exclusively to this thread until cleanup finishes.
    self.assertEqual(signal.getsignal(signal.SIGCHLD), signal.SIG_DFL)
    self.assertEqual(threading.active_count(), 1)
    self.assertNotIn(signal.SIGALRM,
                     signal.pthread_sigmask(signal.SIG_BLOCK, []))
    pending, handlers = [], {}
    proc, failure, cleanup_failure = None, None, None
    stdout, stderr = '', ''

    def defer(signum, frame):
      pending.append(signum)

    command = [sys.executable, '-I', '-B', str(RUNNER), *args]
    try:
      for sig in (signal.SIGHUP, signal.SIGINT, signal.SIGTERM):
        handlers[sig] = signal.getsignal(sig)
        signal.signal(sig, defer)
      if pending:
        raise InterruptedError('cancelled before ownership runner spawn')
      try:
        proc = subprocess.Popen(command, stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True, start_new_session=True)
        stdout, stderr = proc.communicate(timeout=30)
      except BaseException as exc:
        failure = exc
      finally:
        if proc is not None and proc.returncode is None:
          cleanup_deadline = time.monotonic() + 3
          try:
            # No group signals, poll, or wait before this final owned-PID KILL.
            if type(proc.pid) is not int or proc.pid <= 1:
              raise RuntimeError('invalid owned runner PID')
            os.kill(proc.pid, signal.SIGKILL)
          except ProcessLookupError:
            pass
          except BaseException as exc:
            cleanup_failure = exc
          try:
            stdout, stderr = proc.communicate(
                timeout=max(0, cleanup_deadline - time.monotonic()))
          except BaseException as exc:
            cleanup_failure = cleanup_failure or exc
          finally:
            # Always attempt the owned reap even if pipe collection failed.
            try:
              proc.wait(timeout=max(0, cleanup_deadline - time.monotonic()))
            except BaseException as exc:
              cleanup_failure = cleanup_failure or exc
    finally:
      for sig, handler in handlers.items():
        try:
          signal.signal(sig, handler)
        except BaseException as exc:
          cleanup_failure = cleanup_failure or exc
    if proc is not None and proc.returncode is None:
      # Stop unittest rather than starting another case after uncertain cleanup.
      raise KeyboardInterrupt('ownership runner was not reaped') from (
          failure or cleanup_failure)
    if failure is not None:
      raise failure
    if cleanup_failure is not None:
      raise cleanup_failure
    if pending:
      raise KeyboardInterrupt('ownership runner interrupted after cleanup')
    self.assertIsNotNone(proc)
    self.assertIsNotNone(proc.returncode)
    return subprocess.CompletedProcess(command, proc.returncode, stdout, stderr)

  def test_ownership_oracles_with_default_denied_kernel(self):
    result = self.run_guarded()
    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
    self.assertIn('Ran 26 tests', result.stderr)
    self.assertNotIn('HARNESSFAIL:', result.stderr)

  def test_swallowed_kernel_denial_still_fails_runner(self):
    result = self.run_guarded('--check-guard')
    self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
    self.assertIn('swallowed denial recorded', result.stderr)
    self.assertIn('denied killpg', result.stderr)


if __name__ == '__main__':
  unittest.main()
