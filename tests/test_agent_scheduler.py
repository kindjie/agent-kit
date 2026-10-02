"""Private-state pilot lifecycle tests with real foreground child processes."""
import importlib.util
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / 'bin/agent_scheduler.py'
BIN = ROOT / 'bin/agent-scheduler'


class SchedulerTest(unittest.TestCase):
  def setUp(self):
    spec = importlib.util.spec_from_file_location('scheduler', MODULE)
    self.mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(self.mod)
    self.tmp = tempfile.TemporaryDirectory()
    self.addCleanup(self.tmp.cleanup)
    self.base = Path(self.tmp.name)
    self.state = self.base / 'private-state'
    self.scheduler = self.mod.Scheduler(self.state)
    self.initial = self.scheduler.init('coordinator')
    self.epoch = self.initial['epoch']

  def grant(self, run_id='run-1', script='raise SystemExit(0)', **extra):
    values = dict(coordinator='coordinator', epoch=self.epoch, lane='pilot',
                  run_id=run_id, worker='worker', cwd=str(self.base),
                  command=[sys.executable, '-c', script], timeout=3,
                  release_mode='automatic', scope='trusted-foreground-group')
    values.update(extra)
    return self.scheduler.grant(**values)

  def identity(self, grant):
    return {key: grant[key] for key in
            ('epoch', 'lane', 'generation', 'run_id', 'worker')}

  def row(self, run_id='run-1'):
    return next(r for r in self.scheduler.export()['runs']
                if r['run_id'] == run_id)

  def cli(self, *args, **kw):
    return subprocess.run([sys.executable, str(BIN), '--state',
                           str(self.state), *args], capture_output=True,
                          timeout=8, **kw)

  def start(self, grant):
    args = []
    for key, value in self.identity(grant).items():
      args.extend(['--' + key.replace('_', '-'), str(value)])
    return subprocess.Popen([sys.executable, str(BIN), '--state',
                             str(self.state), 'run', *args],
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.PIPE)

  def until(self, predicate, seconds=4):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
      if predicate():
        return
      time.sleep(.02)
    self.fail('condition did not become true')

  def test_explicit_init_and_no_default_state(self):
    result = subprocess.run([sys.executable, str(BIN), 'status'],
                            capture_output=True, timeout=3)
    self.assertEqual(result.returncode, 2)
    self.assertEqual(self.scheduler.init('coordinator'), self.initial)
    with self.assertRaises(self.mod.SchedulerError):
      self.scheduler.init('other-coordinator')
    self.assertEqual(self.state.stat().st_mode & 0o777, 0o700)
    for name in ('authority.lock', 'state.json'):
      self.assertEqual((self.state / name).stat().st_mode & 0o777, 0o600)

  def test_grant_exclusion_payload_binding_and_duplicates(self):
    first = self.grant()
    self.assertEqual(self.grant(), first)
    for extra in ({'script': 'raise SystemExit(9)'}, {'worker': 'other'},
                  {'run_id': 'run-2'}, {'epoch': 'obsolete'}):
      with self.assertRaises(self.mod.SchedulerError):
        self.grant(**extra)
    identity = self.identity(first)
    with self.assertRaises(self.mod.SchedulerError):
      self.scheduler.run(dict(identity, worker='other'))
    with self.assertRaises(self.mod.SchedulerError):
      self.scheduler.run(dict(identity, generation=first['generation'] + 1))
    self.assertEqual(self.row()['operational'], 'granted')

  def test_failure_releases_without_semantic_review_and_no_replay(self):
    grant = self.grant(script='raise SystemExit(7)')
    identity = self.identity(grant)
    self.assertEqual(self.scheduler.run(identity), 7)
    row = self.row()
    self.assertEqual((row['operational'], row['semantic']),
                     ('released', 'pending'))
    stamps = row['timestamps']
    self.assertTrue(all(stamps[key] for key in
                        ('started_at', 'terminal_at', 'quiescent_at',
                         'released_at')))
    self.assertLessEqual(stamps['terminal_at'], stamps['quiescent_at'])
    self.assertLessEqual(stamps['quiescent_at'], stamps['released_at'])
    self.assertIsNone(stamps['semantic_at'])
    with self.assertRaises(self.mod.SchedulerError):
      self.scheduler.run(identity)
    self.scheduler.semantic('coordinator', identity, 'rejected', 'review-ref')
    accepted = self.row()
    self.assertEqual(accepted['semantic'], 'rejected')
    self.assertEqual(accepted['timestamps']['released_at'],
                     stamps['released_at'])
    self.scheduler.semantic('coordinator', identity, 'rejected', 'review-ref')
    with self.assertRaises(self.mod.SchedulerError):
      self.scheduler.semantic('coordinator', identity, 'accepted', 'different')
    self.grant(run_id='run-2')

  def test_manual_and_shadow_release_receipts(self):
    for mode in ('manual', 'shadow'):
      grant = self.grant(run_id=mode, release_mode=mode)
      identity = self.identity(grant)
      self.assertEqual(self.scheduler.run(identity), 0)
      self.assertEqual(self.row(mode)['operational'], 'quiescent')
      with self.assertRaises(self.mod.SchedulerError):
        self.grant(run_id='contender-' + mode)
      self.scheduler.release('coordinator', identity, 'verified-ref')
      before = self.row(mode)
      self.scheduler.release('coordinator', identity, 'verified-ref')
      self.assertEqual(self.row(mode), before)

  def test_automatic_requires_explicit_trusted_scope(self):
    with self.assertRaises(self.mod.SchedulerError):
      self.grant(scope='unverified')
    grant = self.grant(scope='unverified', release_mode='manual')
    identity = self.identity(grant)
    self.assertEqual(self.scheduler.run(identity), 0)
    self.assertEqual(self.row()['operational'], 'recovery-required')
    with self.assertRaises(self.mod.SchedulerError):
      self.scheduler.release('coordinator', identity, 'mere-exit-code')
    self.scheduler.reconcile('coordinator', identity, 'external-quiescence')
    self.assertEqual(self.row()['operational'], 'released')

  def test_cancel_before_launch_revokes_permanently(self):
    marker = self.base / 'must-not-exist'
    grant = self.grant(script='from pathlib import Path; Path(%r).touch()' %
                       str(marker))
    identity = self.identity(grant)
    self.scheduler.cancel('coordinator', identity)
    before = self.row()
    self.scheduler.cancel('coordinator', identity)
    self.assertEqual(self.row(), before)
    with self.assertRaises(self.mod.SchedulerError):
      self.scheduler.run(identity)
    self.assertFalse(marker.exists())
    self.assertIsNone(self.row()['timestamps']['started_at'])

  def test_old_generation_and_wrong_worker_cannot_release_new_run(self):
    first = self.grant()
    old = self.identity(first)
    self.scheduler.cancel('coordinator', old)
    second = self.grant(run_id='run-2')
    self.assertGreater(second['generation'], first['generation'])
    self.scheduler.release('coordinator', old, 'duplicate-old-release')
    self.assertEqual(self.row('run-2')['operational'], 'granted')
    with self.assertRaises(self.mod.SchedulerError):
      self.scheduler.cancel('coordinator', dict(self.identity(second),
                                               worker='other'))
    self.assertEqual(self.row('run-2')['operational'], 'granted')

  def test_readers_are_read_only_and_export_omits_private_payload(self):
    self.grant(script='raise SystemExit(73)')
    before = {p.name: p.read_bytes() for p in self.state.iterdir()}
    exported = self.scheduler.export()
    self.scheduler.status()
    self.scheduler.outbox()
    after = {p.name: p.read_bytes() for p in self.state.iterdir()}
    self.assertEqual(before, after)
    self.assertEqual(exported['schema_version'], 1)
    self.assertEqual(exported['kind'], 'agent-scheduler-receipts')
    serialized = json.dumps(exported)
    for private in ('SystemExit', str(self.base), 'worker', 'command',
                    'evidence'):
      self.assertNotIn(private, serialized)

  def test_outbox_ack_is_idempotent_without_occupancy_change(self):
    grant = self.grant()
    self.scheduler.cancel('coordinator', self.identity(grant))
    events = self.scheduler.outbox()
    self.assertEqual(len(events), 1)
    before = self.row()
    self.scheduler.ack('coordinator', self.epoch, events[0]['id'])
    self.scheduler.ack('coordinator', self.epoch, events[0]['id'])
    self.assertEqual(self.scheduler.outbox(), [])
    self.assertEqual(self.row(), before)

  def test_reboot_invalidates_launch_and_epoch_transfer_requires_drain(self):
    grant = self.grant()
    identity = self.identity(grant)
    with patch.object(self.mod, 'boot_id', return_value='different-boot'):
      with self.assertRaises(self.mod.SchedulerError):
        self.scheduler.run(identity)
      with self.assertRaises(self.mod.SchedulerError):
        self.scheduler.transfer('coordinator', self.epoch, 'next', 'drain-ref')
      self.scheduler.reconcile('coordinator', identity, 'reboot-drain-ref')
      transferred = self.scheduler.transfer('coordinator', self.epoch,
                                            'next', 'drain-ref')
      self.assertNotEqual(transferred['epoch'], self.epoch)
      with self.assertRaises(self.mod.SchedulerError):
        self.grant(run_id='run-2')

  def test_unsafe_paths_and_corrupt_state_fail_closed(self):
    lock = self.state / 'authority.lock'
    lock.unlink()
    victim = self.base / 'victim'
    victim.write_text('preserve')
    lock.symlink_to(victim)
    with self.assertRaises((self.mod.SchedulerError, OSError)):
      self.scheduler.export()
    self.assertEqual(victim.read_text(), 'preserve')
    lock.unlink()
    lock.write_text('')
    lock.chmod(0o600)
    (self.state / 'state.json').write_text('{broken')
    with self.assertRaises(self.mod.SchedulerError):
      self.grant()

  def test_missing_stable_locks_are_never_recreated_for_existing_state(self):
    grant = self.grant()
    self.scheduler.cancel('coordinator', self.identity(grant))
    lane_lock = self.state / 'lane-pilot.lock'
    lane_lock.unlink()
    with self.assertRaises((self.mod.SchedulerError, OSError)):
      self.grant(run_id='successor')
    self.assertFalse(lane_lock.exists())
    lock = self.state / 'authority.lock'
    lock.unlink()
    with self.assertRaises((self.mod.SchedulerError, OSError)):
      self.scheduler.init('coordinator')
    self.assertFalse(lock.exists())

  def test_durable_transition_crashes_need_explicit_recovery(self):
    grant = self.grant()
    identity = self.identity(grant)
    for checkpoint in ('journal-durable', 'state-replaced', 'state-durable'):
      with self.subTest(checkpoint=checkpoint):
        code = (
          'import sys; sys.path.insert(0,%r); '
          'from agent_scheduler import Scheduler; '
          's=Scheduler(%r); '
          's._checkpoint=lambda point: __import__("os")._exit(97) '
          'if point==%r else None; s.cancel("coordinator",%r)' %
          (str(MODULE.parent), str(self.state), checkpoint, identity))
        crashed = subprocess.run([sys.executable, '-c', code], timeout=3)
        self.assertEqual(crashed.returncode, 97)
        with self.assertRaises(self.mod.SchedulerError):
          self.scheduler.export()
        self.scheduler.recover('coordinator', self.epoch, 'inspected-crash')
        self.assertEqual(self.row(identity['run_id'])['operational'],
                         'recovery-required')
        with self.assertRaises(self.mod.SchedulerError):
          self.scheduler.run(identity)
        self.scheduler.reconcile('coordinator', identity, 'checked-no-child')
        grant = self.grant(run_id='after-' + checkpoint)
        identity = self.identity(grant)
    with patch.object(self.scheduler, '_checkpoint', side_effect=lambda point:
                      (_ for _ in ()).throw(RuntimeError('after commit'))
                      if point == 'journal-cleared' else None):
      with self.assertRaises(RuntimeError):
        self.scheduler.cancel('coordinator', identity)
    self.assertEqual(self.row(grant['run_id'])['operational'], 'released')

  def test_registered_launch_serializes_with_cancel(self):
    grant = self.grant(script='import time; time.sleep(20)')
    identity = self.identity(grant)
    entered, proceed, cancel_done = (threading.Event() for _ in range(3))
    actual = self.mod.subprocess.Popen
    failures = []

    def paused_spawn(*args, **kwargs):
      entered.set()
      self.assertTrue(proceed.wait(3))
      return actual(*args, **kwargs)

    def launch():
      try:
        self.scheduler.run(identity)
      except BaseException as exc:
        failures.append(exc)

    def cancel():
      try:
        self.scheduler.cancel('coordinator', identity)
      except BaseException as exc:
        failures.append(exc)
      finally:
        cancel_done.set()

    with patch.object(self.mod.subprocess, 'Popen', side_effect=paused_spawn):
      worker = threading.Thread(target=launch)
      worker.start()
      self.assertTrue(entered.wait(3))
      revoker = threading.Thread(target=cancel)
      revoker.start()
      self.assertFalse(cancel_done.wait(.1))
      proceed.set()
      revoker.join(4)
      worker.join(6)
      self.assertFalse(worker.is_alive())
      self.assertFalse(revoker.is_alive())
    self.assertEqual(failures, [])
    self.assertEqual(self.row()['operational'], 'released')
    self.assertIsNotNone(self.row()['timestamps']['started_at'])

  def test_real_supervisor_death_never_releases_even_after_child_exit(self):
    ready = self.base / 'ready'
    script = ('from pathlib import Path; import time; '
              'Path(%r).touch(); time.sleep(.6)' % str(ready))
    grant = self.grant(script=script)
    supervisor = self.start(grant)
    try:
      self.until(ready.exists)
      supervisor.kill()
      supervisor.communicate(timeout=3)
      time.sleep(.8)
      with self.assertRaises(self.mod.SchedulerError):
        self.grant(run_id='contender')
      self.assertIn(self.row()['operational'], ('launching', 'running'))
      with self.assertRaises(self.mod.SchedulerError):
        self.scheduler.run(self.identity(grant))
    finally:
      if supervisor.poll() is None:
        supervisor.kill()
        supervisor.communicate(timeout=3)

  def test_real_leader_exit_surviving_foreground_child_keeps_reservation(self):
    ready = self.base / 'child-ready'
    child = ('from pathlib import Path; import time; Path(%r).touch(); '
             'time.sleep(.8)' % str(ready))
    leader = ('import subprocess,sys; subprocess.Popen('
              '[sys.executable,"-c",%r], close_fds=True)' % child)
    grant = self.grant(script=leader)
    supervisor = self.start(grant)
    try:
      self.until(ready.exists)
      with self.assertRaises(self.mod.SchedulerError):
        self.grant(run_id='contender')
      self.assertIsNone(self.row()['timestamps']['released_at'])
      self.assertEqual(supervisor.communicate(timeout=5)[1], b'')
      self.assertEqual(supervisor.returncode, 0)
      self.assertEqual(self.row()['operational'], 'released')
    finally:
      if supervisor.poll() is None:
        supervisor.kill()
        supervisor.communicate(timeout=3)

  def test_real_timeout_and_repeated_interrupt_cleanup(self):
    for kind in ('timeout', 'interrupt'):
      ready = self.base / ('ready-' + kind)
      script = ('from pathlib import Path; import time; Path(%r).touch(); '
                'time.sleep(20)' % str(ready))
      grant = self.grant(run_id=kind, script=script,
                         timeout=.25 if kind == 'timeout' else 3)
      supervisor = self.start(grant)
      try:
        self.until(ready.exists)
        if kind == 'interrupt':
          supervisor.send_signal(signal.SIGINT)
          time.sleep(.05)
          if supervisor.poll() is None:
            supervisor.send_signal(signal.SIGTERM)
        _, stderr = supervisor.communicate(timeout=5)
        self.assertEqual(supervisor.returncode,
                         124 if kind == 'timeout' else 130, stderr)
        self.assertEqual(self.row(kind)['operational'], 'released')
      finally:
        if supervisor.poll() is None:
          supervisor.kill()
          supervisor.communicate(timeout=3)

  def test_independent_lane_and_status_remain_responsive_during_run(self):
    ready = self.base / 'running'
    first = self.grant(script='from pathlib import Path; import time; '
                        'Path(%r).touch(); time.sleep(20)' % str(ready))
    supervisor = self.start(first)
    try:
      self.until(ready.exists)
      self.assertEqual(self.scheduler.status()['runs']['run-1']['operational'],
                       'running')
      second = self.grant(run_id='other-lane', lane='independent')
      self.assertIsNone(supervisor.poll())
      self.assertEqual(self.scheduler.run(self.identity(second)), 0)
      self.assertEqual(self.row('other-lane')['operational'], 'released')
      self.assertIsNone(supervisor.poll())
      stopping = self.scheduler.cancel('coordinator', self.identity(first))
      self.assertEqual(stopping['operational'], 'stopping')
      supervisor.communicate(timeout=5)
      self.assertEqual(self.row()['operational'], 'released')
    finally:
      if supervisor.poll() is None:
        supervisor.kill()
        supervisor.communicate(timeout=3)

  def test_finalization_serializes_reconcile_and_successor_grant(self):
    first = self.grant()
    identity = self.identity(first)
    entered, proceed, reconciled = (threading.Event() for _ in range(3))
    failures, successor = [], []

    def checkpoint(point):
      if point == 'finalization-locked':
        entered.set()
        self.assertTrue(proceed.wait(3))

    def finish():
      try:
        self.scheduler.run(identity)
      except BaseException as exc:
        failures.append(exc)

    def reconcile_and_grant():
      try:
        self.scheduler.reconcile('coordinator', identity, 'external-proof')
        successor.append(self.grant(run_id='successor'))
      except BaseException as exc:
        failures.append(exc)
      finally:
        reconciled.set()

    with patch.object(self.scheduler, '_checkpoint', side_effect=checkpoint):
      worker = threading.Thread(target=finish)
      worker.start()
      self.assertTrue(entered.wait(3))
      coordinator = threading.Thread(target=reconcile_and_grant)
      coordinator.start()
      self.assertFalse(reconciled.wait(.1))
      proceed.set()
      worker.join(4)
      coordinator.join(4)
      self.assertFalse(worker.is_alive())
      self.assertFalse(coordinator.is_alive())
    self.assertEqual(failures, [])
    self.assertEqual(self.row()['operational'], 'released')
    self.assertEqual(self.row('successor')['operational'], 'granted')
    data = self.scheduler.status()
    with self.assertRaises(self.mod.SchedulerError):
      self.scheduler._release(data, data['runs']['run-1'], 'stale-finalizer')
    self.assertEqual(data['lanes']['pilot']['active'], 'successor')

  def test_concurrent_duplicate_cancellation_is_one_durable_release(self):
    grant = self.grant()
    identity = self.identity(grant)
    rows, failures = [], []

    def cancel():
      try:
        rows.append(self.scheduler.cancel('coordinator', identity))
      except BaseException as exc:
        failures.append(exc)

    contenders = [threading.Thread(target=cancel) for _ in range(6)]
    for thread in contenders:
      thread.start()
    for thread in contenders:
      thread.join(4)
      self.assertFalse(thread.is_alive())
    self.assertEqual(failures, [])
    self.assertEqual(len(rows), 6)
    self.assertTrue(all(row == rows[0] for row in rows))
    self.assertEqual(len(self.scheduler.outbox()), 1)

  def test_pre_spawn_crash_consumes_launch_permission_without_replay(self):
    marker = self.base / 'must-not-launch'
    grant = self.grant(script='from pathlib import Path; Path(%r).touch()' %
                       str(marker))
    identity = self.identity(grant)
    code = (
      'import sys,os; sys.path.insert(0,%r); '
      'from agent_scheduler import Scheduler; s=Scheduler(%r); '
      's._checkpoint=lambda point: os._exit(97) '
      'if point=="journal-cleared" else None; s.run(%r)' %
      (str(MODULE.parent), str(self.state), identity))
    crashed = subprocess.run([sys.executable, '-c', code], timeout=3)
    self.assertEqual(crashed.returncode, 97)
    self.assertEqual(self.row()['operational'], 'launching')
    self.assertFalse(marker.exists())
    with self.assertRaises(self.mod.SchedulerError):
      self.scheduler.run(identity)
    self.scheduler.reconcile('coordinator', identity, 'inspected-no-spawn')
    with self.assertRaises(self.mod.SchedulerError):
      self.scheduler.run(identity)

  def test_detectable_detached_child_retains_recovery_hold(self):
    ready = self.base / 'detached-ready'
    child = ('from pathlib import Path; import time; Path(%r).touch(); '
             'time.sleep(.6)' % str(ready))
    leader = ('import subprocess,sys; subprocess.Popen('
              '[sys.executable,"-c",%r], start_new_session=True, '
              'close_fds=False)' % child)
    grant = self.grant(script=leader)
    supervisor = self.start(grant)
    try:
      self.until(ready.exists)
      supervisor.communicate(timeout=4)
      self.assertEqual(self.row()['operational'], 'recovery-required')
      self.assertIsNone(self.row()['timestamps']['released_at'])
      with self.assertRaises(self.mod.SchedulerError):
        self.grant(run_id='contender')
      with self.assertRaises(self.mod.SchedulerError):
        self.scheduler.cancel('coordinator', self.identity(grant))
      time.sleep(.8)
      self.assertEqual(self.row()['operational'], 'recovery-required')
      self.scheduler.reconcile('coordinator', self.identity(grant),
                                 'observed-detached-child-terminated')
      self.assertEqual(self.row()['operational'], 'released')
    finally:
      if supervisor.poll() is None:
        supervisor.kill()
        supervisor.communicate(timeout=3)

  def test_observation_denial_never_releases_or_signals_stored_pids(self):
    grant = self.grant()
    identity = self.identity(grant)
    with patch.object(self.mod, 'group_alive', side_effect=
                      self.mod.SchedulerError('denied')):
      with self.assertRaises(self.mod.SchedulerError):
        self.scheduler.run(identity)
    self.assertIsNone(self.row()['timestamps']['released_at'])
    with patch.object(self.mod.os, 'killpg') as forbidden:
      self.scheduler.reconcile('coordinator', identity, 'external-scope-proof')
      forbidden.assert_not_called()

  def test_invalid_storage_schema_does_not_guess_vacant_lane(self):
    grant = self.grant()
    data = self.scheduler.status()
    data['lanes']['pilot']['active'] = None
    (self.state / 'state.json').write_text(json.dumps(data))
    with self.assertRaises(self.mod.SchedulerError):
      self.scheduler.export()
    with self.assertRaises(self.mod.SchedulerError):
      self.scheduler.run(self.identity(grant))
    data['lanes']['pilot']['active'] = 'run-1'
    data['lanes']['pilot']['generation'] += 1
    (self.state / 'state.json').write_text(json.dumps(data))
    with self.assertRaises(self.mod.SchedulerError):
      self.scheduler.run(self.identity(grant))

  def test_uninitialized_reader_creates_nothing(self):
    missing = self.base / 'missing'
    with self.assertRaises(FileNotFoundError):
      self.mod.Scheduler(missing).status()
    self.assertFalse(missing.exists())

  def test_size_limit_rejects_state_and_larger_journal_before_writes(self):
    grant = self.grant()
    before = {p.name: p.read_bytes() for p in self.state.iterdir()}
    state_size = len(self.mod.encoded(self.scheduler.status()))
    # A state fitting the read limit still needs a larger before/after journal.
    with patch.object(self.mod, 'MAX_JSON', state_size + 100):
      with self.assertRaises(self.mod.SchedulerError):
        self.scheduler.cancel('coordinator', self.identity(grant))
    self.assertEqual(before, {p.name: p.read_bytes()
                             for p in self.state.iterdir()})
    with self.assertRaises(self.mod.SchedulerError):
      self.scheduler.semantic('coordinator', self.identity(grant),
                                 'accepted', 'x' * self.mod.MAX_EVIDENCE)
    self.assertEqual(before, {p.name: p.read_bytes()
                             for p in self.state.iterdir()})

  def test_recovery_journal_uses_marker_with_reserved_headroom(self):
    grant = self.grant()
    identity = self.identity(grant)
    data = self.scheduler.status()
    pair_size = 2 * len(self.mod.encoded(data))
    with patch.object(self.mod, 'MAX_JSON', pair_size + 1500):
      with patch.object(self.scheduler, '_checkpoint', side_effect=lambda point:
                        (_ for _ in ()).throw(RuntimeError('crash'))
                        if point == 'journal-durable' else None):
        with self.assertRaises(RuntimeError):
          self.scheduler.cancel('coordinator', identity)
      self.scheduler.recover('coordinator', self.epoch, 'proof')
      self.assertEqual(self.row()['operational'], 'recovery-required')
      self.assertFalse((self.state / 'pending.json').exists())

  def test_interrupted_recovery_can_resume_without_releasing_hold(self):
    grant = self.grant()
    identity = self.identity(grant)
    for action, point in (('cancel', 'journal-durable'),
                           ('recover', 'state-replaced')):
      with patch.object(self.scheduler, '_checkpoint', side_effect=lambda stage:
                        (_ for _ in ()).throw(RuntimeError('crash'))
                        if stage == point else None):
        with self.assertRaises(RuntimeError):
          if action == 'cancel':
            self.scheduler.cancel('coordinator', identity)
          else:
            self.scheduler.recover('coordinator', self.epoch, 'first-proof')
      with self.assertRaises(self.mod.SchedulerError):
        self.scheduler.export()
    self.scheduler.recover('coordinator', self.epoch, 'retry-proof')
    self.assertEqual(self.row()['operational'], 'recovery-required')
    self.assertIsNone(self.row()['timestamps']['released_at'])
    with self.assertRaises(self.mod.SchedulerError):
      self.scheduler.run(identity)

  def test_grant_reserves_future_completion_and_recovery_capacity(self):
    before = {p.name: p.read_bytes() for p in self.state.iterdir()}
    with patch.object(self.mod, 'MAX_JSON', 15000):
      with self.assertRaises(self.mod.SchedulerError):
        self.grant()
    # A lane lock may have been provisioned, but authoritative bytes and
    # transaction state remain unchanged. Lock files are stable and retained.
    for name, raw in before.items():
      self.assertEqual((self.state / name).read_bytes(), raw)
    self.assertFalse((self.state / 'pending.json').exists())
    with self.assertRaises(self.mod.SchedulerError):
      self.grant(script='x' * self.mod.MAX_PAYLOAD)
    grant = self.grant()
    with patch.object(self.mod, 'MAX_RUNS', 1):
      self.scheduler.cancel('coordinator', self.identity(grant))
      with self.assertRaises(self.mod.SchedulerError):
        self.grant(run_id='too-many')


if __name__ == '__main__':
  unittest.main()
