"""Explicit-state cooperative exclusive-lane scheduler pilot (stdlib only).

This is a local logical authority for cooperating callers, not containment or
authentication against other processes owned by the same user. Automatic
release requires an expressly trusted foreground group: no detached children,
changed process groups, remote work, or unrelated machine-resource consumers.
Recovery never signals stored PIDs and never derives release from PID absence.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import copy
import ctypes
from datetime import datetime, timezone
import fcntl
import json
import math
import os
from pathlib import Path
import re
import signal
import stat
import subprocess
import sys
import threading
import time
import uuid

STAMPS = ('granted_at', 'started_at', 'terminal_at', 'quiescent_at',
          'released_at', 'semantic_at')
IDENTITY = ('epoch', 'lane', 'generation', 'run_id', 'worker')
OPERATIONS = {'granted', 'launching', 'running', 'stopping', 'quiescent',
              'released', 'recovery-required'}
MAX_JSON = 16 * 1024 * 1024
MAX_RUNS = 128
MAX_PAYLOAD = 16 * 1024
MAX_EVIDENCE = 1024


class SchedulerError(Exception):
  """A refused operation; occupancy must not be guessed or repaired."""


def stamp():
  return datetime.now(timezone.utc).isoformat(timespec='microseconds')


def boot_id():
  if sys.platform.startswith('linux'):
    value = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
  elif sys.platform == 'darwin':
    class Timeval(ctypes.Structure):
      _fields_ = [('seconds', ctypes.c_long), ('microseconds', ctypes.c_int)]
    result = Timeval()
    size = ctypes.c_size_t(ctypes.sizeof(result))
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.sysctlbyname(b'kern.boottime', ctypes.byref(result),
                        ctypes.byref(size), None, 0) != 0:
      raise SchedulerError('native boot identity unavailable; read denied')
    value = '%s:%s' % (result.seconds, result.microseconds)
  else:
    raise SchedulerError('boot identity unavailable on this platform')
  if not value:
    raise SchedulerError('boot identity unavailable')
  return value


def identifier(value, label):
  if not isinstance(value, str) or not re.fullmatch(
      r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}', value):
    raise SchedulerError(label + ' must be a simple nonempty identifier')
  return value


def positive(value):
  try:
    number = float(value)
  except (TypeError, ValueError):
    raise SchedulerError('timeout must be finite and positive')
  if not math.isfinite(number) or number <= 0:
    raise SchedulerError('timeout must be finite and positive')
  return number


def evidence(value):
  if not isinstance(value, str) or not value.strip() or '\x00' in value:
    raise SchedulerError('a nonempty evidence reference is required')
  if len(encoded(value)) > MAX_EVIDENCE:
    raise SchedulerError('evidence reference exceeds the pilot size limit')
  return value


def encoded(value):
  return (json.dumps(value, sort_keys=True, separators=(',', ':'),
                     allow_nan=False) + '\n').encode('utf-8')


def decode(raw):
  def pairs(items):
    result = {}
    for key, value in items:
      if key in result:
        raise ValueError('duplicate JSON key')
      result[key] = value
    return result
  try:
    return json.loads(raw, object_pairs_hook=pairs,
                      parse_constant=lambda value: (_ for _ in ()).throw(
                        ValueError('nonfinite JSON number')))
  except (UnicodeError, ValueError) as exc:
    raise SchedulerError('invalid scheduler JSON: ' + str(exc))


def safe_file(dir_fd, name, flags, mode=0o600):
  fd = os.open(name, flags | os.O_NOFOLLOW | os.O_NONBLOCK, mode,
               dir_fd=dir_fd)
  info = os.fstat(fd)
  if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or
      info.st_mode & 0o077 or info.st_nlink != 1):
    os.close(fd)
    raise SchedulerError('scheduler files must be private owned regular files')
  return fd


def read_json(dir_fd, name):
  try:
    fd = safe_file(dir_fd, name, os.O_RDONLY)
  except FileNotFoundError:
    return None
  with os.fdopen(fd, 'rb') as handle:
    raw = handle.read(MAX_JSON + 1)
  if len(raw) > MAX_JSON:
    raise SchedulerError('scheduler state exceeds the pilot size limit')
  return decode(raw)


def validate(data):
  """Reject incomplete/corrupt authority rather than deriving vacant lanes."""
  try:
    if data['schema_version'] != 1 or data['kind'] != 'agent-scheduler-state':
      raise ValueError('unsupported schema')
    for key in ('scheduler_id', 'epoch', 'coordinator'):
      identifier(data[key], key)
    if not isinstance(data['boot_id'], str) or not data['boot_id']:
      raise ValueError('missing boot identity')
    lanes, runs = data['lanes'], data['runs']
    if not isinstance(lanes, dict) or not isinstance(runs, dict):
      raise ValueError('missing lane/run map')
    for name, lane in lanes.items():
      identifier(name, 'lane')
      if (type(lane['generation']) is not int or lane['generation'] < 1 or
          lane['active'] is not None and lane['active'] not in runs):
        raise ValueError('invalid lane reservation')
      if lane['active'] is not None:
        active = runs[lane['active']]
        if (active['lane'] != name or active['operational'] == 'released' or
            active['generation'] != lane['generation']):
          raise ValueError('active lane does not match its current run')
    if len(runs) > MAX_RUNS:
      raise ValueError('pilot retained-run limit exceeded')
    for key, row in runs.items():
      if key != row['run_id'] or row['lane'] not in lanes:
        raise ValueError('invalid run identity')
      for field in ('run_id', 'worker', 'epoch', 'lane'):
        identifier(row[field], field)
      if (type(row['generation']) is not int or row['generation'] < 1 or
          row['generation'] > lanes[row['lane']]['generation']):
        raise ValueError('invalid generation')
      if row['operational'] not in OPERATIONS:
        raise ValueError('unknown operational state')
      if row['semantic'] not in ('pending', 'accepted', 'rejected',
                                  'needs-investigation'):
        raise ValueError('unknown semantic state')
      if row['release_mode'] not in ('manual', 'shadow', 'automatic'):
        raise ValueError('unknown release mode')
      if row['scope'] not in ('trusted-foreground-group', 'unverified'):
        raise ValueError('unknown process scope')
      if (row['release_mode'] == 'automatic' and
          row['scope'] != 'trusted-foreground-group'):
        raise ValueError('automatic unverified scope')
      payload = row['payload']
      command = payload['command']
      if (not isinstance(command, list) or not command or
          any(not isinstance(v, str) or '\x00' in v for v in command) or
          not os.path.isabs(command[0]) or
          not os.path.isabs(payload['cwd'])):
        raise ValueError('invalid exact command payload')
      positive(payload['timeout'])
      if len(encoded(payload)) > MAX_PAYLOAD:
        raise ValueError('payload size limit exceeded')
      stamps = row['timestamps']
      if set(stamps) != set(STAMPS) or not stamps['granted_at']:
        raise ValueError('invalid timestamp fields')
      for value in stamps.values():
        if value is not None:
          if not isinstance(value, str):
            raise ValueError('invalid timestamp type')
          parsed = datetime.fromisoformat(value)
          if parsed.tzinfo is None:
            raise ValueError('timestamp lacks timezone')
      if type(row['launch_allowed']) is not bool:
        raise ValueError('invalid launch permission')
      if row['launch_allowed'] != (row['operational'] == 'granted'):
        raise ValueError('launch permission inconsistent with lifecycle')
      released = row['operational'] == 'released'
      if released != bool(stamps['released_at']):
        raise ValueError('release timestamp inconsistent with lifecycle')
      if not released and lanes[row['lane']]['active'] != key:
        raise ValueError('unreleased run is missing its reservation')
      if not released and row['generation'] != lanes[row['lane']]['generation']:
        raise ValueError('unreleased run has stale generation')
      if released and lanes[row['lane']]['active'] == key:
        raise ValueError('released run retains an active reservation')
      if row['semantic'] == 'pending' and stamps['semantic_at'] is not None:
        raise ValueError('pending semantic decision has a timestamp')
      if row['semantic'] != 'pending' and stamps['semantic_at'] is None:
        raise ValueError('semantic decision lacks timestamp')
      for field in ('release_evidence', 'semantic_evidence'):
        if row.get(field) is not None:
          evidence(row[field])
    if not isinstance(data['outbox'], list):
      raise ValueError('missing notification outbox')
    for notification in data['outbox']:
      if notification['run_id'] not in runs or not notification['id']:
        raise ValueError('invalid notification reference')
  except (KeyError, TypeError, ValueError) as exc:
    raise SchedulerError('invalid scheduler state: ' + str(exc))
  return data


class Scheduler:
  def __init__(self, root):
    self.root = Path(root)

  def _checkpoint(self, point):
    """Fault-injection seam for crash tests; no CLI/environment bypass."""

  @contextmanager
  def _locked(self, write=False, create=False, pending=False):
    if create:
      self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
    dir_fd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    lock_fd = None
    try:
      info = os.fstat(dir_fd)
      if info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise SchedulerError('state directory must be owned by you and 0700')
      flags = os.O_RDONLY
      if create:
        initialized = False
        for name in ('state.json', 'pending.json'):
          try:
            os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
            initialized = True
          except FileNotFoundError:
            pass
        if not initialized:
          flags = os.O_RDWR | os.O_CREAT
      lock_fd = safe_file(dir_fd, 'authority.lock', flags)
      fcntl.flock(lock_fd, fcntl.LOCK_EX if write else fcntl.LOCK_SH)
      journal = read_json(dir_fd, 'pending.json')
      if journal is not None and not pending:
        raise SchedulerError('pending transaction requires explicit recovery')
      data = read_json(dir_fd, 'state.json')
      if data is None and not create and not pending:
        raise SchedulerError('state is not initialized')
      if data is not None:
        validate(data)
      yield dir_fd, data
    finally:
      if lock_fd is not None:
        os.close(lock_fd)
      os.close(dir_fd)

  def _atomic(self, dir_fd, name, data):
    raw = encoded(data)
    if len(raw) > MAX_JSON:
      raise SchedulerError('scheduler JSON exceeds the pilot size limit')
    temp = '.write-' + uuid.uuid4().hex
    fd = safe_file(dir_fd, temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
    try:
      with os.fdopen(fd, 'wb') as handle:
        handle.write(raw)
        handle.flush()
        os.fsync(handle.fileno())
      os.replace(temp, name, src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
      if name == 'state.json':
        self._checkpoint('state-replaced')
      os.fsync(dir_fd)
      if name == 'state.json':
        self._checkpoint('state-durable')
    finally:
      try:
        os.unlink(temp, dir_fd=dir_fd)
      except FileNotFoundError:
        pass

  def _commit(self, dir_fd, before, after):
    validate(after)
    if before == after:
      return
    journal = {'before': before, 'after': after}
    # Preflight BOTH persisted objects before creating any transaction intent.
    source = after if before is None else before
    recovery = self._recovery_target(source, 'x' * (MAX_EVIDENCE - 2))
    reserved_journal = dict(journal, recovery_evidence='x' * (MAX_EVIDENCE - 2))
    if max(len(encoded(after)), len(encoded(reserved_journal)),
           len(encoded(recovery))) > MAX_JSON:
      raise SchedulerError('scheduler transaction exceeds the pilot size limit')
    self._atomic(dir_fd, 'pending.json', journal)
    self._checkpoint('journal-durable')
    self._atomic(dir_fd, 'state.json', after)
    os.unlink('pending.json', dir_fd=dir_fd)
    os.fsync(dir_fd)
    self._checkpoint('journal-cleared')

  def _owner(self, data, coordinator, epoch=None, current_boot=False):
    if data['coordinator'] != coordinator:
      raise SchedulerError('coordinator identity does not match')
    if epoch is not None and data['epoch'] != epoch:
      raise SchedulerError('ownership epoch does not match')
    if current_boot and data['boot_id'] != boot_id():
      raise SchedulerError('boot changed: reconcile and transfer epoch first')

  def _recovery_target(self, source, proof):
    target = copy.deepcopy(source)
    target['recovery_evidence'] = proof
    for row in target['runs'].values():
      if row['operational'] != 'released':
        row['launch_allowed'] = False
        row['operational'] = 'recovery-required'
    return target

  def _admission_budget(self, data):
    # Each retained run reserves 8 KiB beyond its existing bytes for two
    # bounded evidence refs, supervisor metadata, timestamps and two outbox
    # events/acks. Another 4 KiB covers bounded transfer/recovery metadata.
    # One-third of the read cap leaves room for both journal snapshots and
    # its recovery marker. The pilot never prunes/reuses historical run IDs.
    if (len(data['runs']) > MAX_RUNS or
        len(encoded(data)) + 8192 * len(data['runs']) + 4096 > MAX_JSON // 3):
      raise SchedulerError('pilot history/completion budget exhausted')

  def _row(self, data, identity):
    row = data['runs'].get(identity.get('run_id'))
    if row is None or any(row[key] != identity.get(key) for key in IDENTITY):
      raise SchedulerError('exact epoch/lane/generation/run/worker required')
    return row

  def _lane_lock(self, dir_fd, lane, create=False):
    fd = safe_file(dir_fd, 'lane-' + lane + '.lock',
                   os.O_RDWR | (os.O_CREAT if create else 0))
    try:
      fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
      os.close(fd)
      raise SchedulerError('supervisor or inherited child still holds lane')
    return fd

  def _notify(self, data, row, event):
    ident = ':'.join((data['scheduler_id'], row['epoch'], row['run_id'], event))
    if not any(item['id'] == ident for item in data['outbox']):
      data['outbox'].append({'id': ident, 'run_id': row['run_id'],
                             'event': event, 'at': stamp(),
                             'acked_at': None})

  def _release(self, data, row, proof):
    lane = data['lanes'][row['lane']]
    if (lane['active'] != row['run_id'] or
        lane['generation'] != row['generation']):
      raise SchedulerError('run no longer owns the exact lane reservation')
    row['launch_allowed'] = False
    row['operational'] = 'released'
    row['release_evidence'] = proof
    row['timestamps']['released_at'] = stamp()
    data['lanes'][row['lane']]['active'] = None
    self._notify(data, row, 'released')

  def init(self, coordinator):
    identifier(coordinator, 'coordinator')
    with self._locked(write=True, create=True) as (dir_fd, data):
      if data is not None:
        self._owner(data, coordinator)
        return copy.deepcopy(data)
      data = dict(schema_version=1, kind='agent-scheduler-state',
                  scheduler_id=uuid.uuid4().hex, epoch=uuid.uuid4().hex,
                  coordinator=coordinator, boot_id=boot_id(),
                  lanes={}, runs={}, outbox=[])
      self._commit(dir_fd, None, data)
      return copy.deepcopy(data)

  def grant(self, coordinator, epoch, lane, run_id, worker, command, cwd,
            timeout=3600, release_mode='manual', scope='unverified'):
    for value, label in ((lane, 'lane'), (run_id, 'run'), (worker, 'worker')):
      identifier(value, label)
    if (not isinstance(command, list) or not command or
        any(not isinstance(v, str) or '\x00' in v for v in command) or
        not os.path.isabs(command[0])):
      raise SchedulerError('exact command requires an absolute executable')
    if not os.path.isabs(cwd) or not Path(cwd).is_dir():
      raise SchedulerError('cwd must be an existing absolute directory')
    if release_mode not in ('manual', 'shadow', 'automatic'):
      raise SchedulerError('unknown release mode')
    if scope not in ('trusted-foreground-group', 'unverified'):
      raise SchedulerError('unknown process scope')
    if release_mode == 'automatic' and scope != 'trusted-foreground-group':
      raise SchedulerError('automatic release requires expressly trusted scope')
    payload = dict(command=command[:], cwd=cwd, timeout=positive(timeout))
    if len(encoded(payload)) > MAX_PAYLOAD:
      raise SchedulerError('exact command payload exceeds 16 KiB pilot limit')
    with self._locked(write=True) as (dir_fd, data):
      self._owner(data, coordinator, epoch, current_boot=True)
      existing = data['runs'].get(run_id)
      if existing is not None:
        if (existing['epoch'] == epoch and existing['lane'] == lane and
            existing['worker'] == worker and existing['payload'] == payload and
            existing['release_mode'] == release_mode and
            existing['scope'] == scope):
          return copy.deepcopy(existing)
        raise SchedulerError('run identity cannot be reused '
                             'for another request')
      previous = data['lanes'].get(lane)
      if previous is not None and previous['active'] is not None:
        raise SchedulerError('lane retains an operational reservation')
      guard = self._lane_lock(dir_fd, lane, create=previous is None)
      os.close(guard)
      before = copy.deepcopy(data)
      generation = 1 if previous is None else previous['generation'] + 1
      row = dict(epoch=epoch, lane=lane, generation=generation, run_id=run_id,
                 worker=worker, payload=payload, release_mode=release_mode,
                 scope=scope, launch_allowed=True, operational='granted',
                 semantic='pending', semantic_evidence=None,
                 release_evidence=None, supervisor=None, terminal_code=None,
                 timestamps={key: None for key in STAMPS})
      row['timestamps']['granted_at'] = stamp()
      data['runs'][run_id] = row
      data['lanes'][lane] = dict(generation=generation, active=run_id)
      self._admission_budget(data)
      self._commit(dir_fd, before, data)
      return copy.deepcopy(row)

  def status(self):
    with self._locked() as (_, data):
      return copy.deepcopy(data)

  def export(self):
    data = self.status()
    keys = ('run_id', 'epoch', 'generation', 'lane', 'operational', 'semantic',
            'timestamps')
    return dict(schema_version=1, kind='agent-scheduler-receipts',
                scheduler_id=data['scheduler_id'],
                runs=[{key: copy.deepcopy(row[key]) for key in keys}
                      for row in data['runs'].values()])

  def outbox(self):
    return [item for item in self.status()['outbox']
            if item['acked_at'] is None]

  def ack(self, coordinator, epoch, notification):
    with self._locked(write=True) as (dir_fd, data):
      self._owner(data, coordinator, epoch)
      before = copy.deepcopy(data)
      item = next((item for item in data['outbox']
                   if item['id'] == notification), None)
      if item is None:
        raise SchedulerError('unknown notification')
      if item['acked_at'] is None:
        item['acked_at'] = stamp()
      self._commit(dir_fd, before, data)
      return copy.deepcopy(item)

  def cancel(self, coordinator, identity):
    with self._locked(write=True) as (dir_fd, data):
      self._owner(data, coordinator)
      row = self._row(data, identity)
      before = copy.deepcopy(data)
      if row['operational'] == 'released':
        return copy.deepcopy(row)
      if row['operational'] == 'granted':
        row['timestamps']['quiescent_at'] = stamp()
        self._release(data, row, 'cancelled-before-launch')
      elif row['operational'] in ('running', 'stopping'):
        row['launch_allowed'] = False
        row['operational'] = 'stopping'
      else:
        raise SchedulerError('cancel cannot resolve unknown or quiescent scope')
      self._commit(dir_fd, before, data)
      return copy.deepcopy(row)

  def release(self, coordinator, identity, proof):
    evidence(proof)
    with self._locked(write=True) as (dir_fd, data):
      self._owner(data, coordinator)
      row = self._row(data, identity)
      if row['operational'] == 'released':
        return copy.deepcopy(row)
      self._owner(data, coordinator, current_boot=True)
      if row['operational'] != 'quiescent':
        raise SchedulerError('release requires separately verified quiescence')
      guard = self._lane_lock(dir_fd, row['lane'])
      try:
        before = copy.deepcopy(data)
        self._release(data, row, proof)
        self._commit(dir_fd, before, data)
        return copy.deepcopy(row)
      finally:
        os.close(guard)

  def semantic(self, coordinator, identity, decision, proof):
    evidence(proof)
    if decision not in ('accepted', 'rejected', 'needs-investigation'):
      raise SchedulerError('unknown semantic decision')
    with self._locked(write=True) as (dir_fd, data):
      self._owner(data, coordinator)
      row = self._row(data, identity)
      if row['semantic'] != 'pending':
        if row['semantic'] == decision and row['semantic_evidence'] == proof:
          return copy.deepcopy(row)
        raise SchedulerError('semantic decision already recorded differently')
      before = copy.deepcopy(data)
      row['semantic'] = decision
      row['semantic_evidence'] = proof
      row['timestamps']['semantic_at'] = stamp()
      self._notify(data, row, 'semantic')
      self._commit(dir_fd, before, data)
      return copy.deepcopy(row)

  def reconcile(self, coordinator, identity, proof):
    """Operator assertion of external proof, never automatic PID recovery."""
    evidence(proof)
    with self._locked(write=True) as (dir_fd, data):
      self._owner(data, coordinator)
      row = self._row(data, identity)
      if row['operational'] == 'released':
        return copy.deepcopy(row)
      guard = self._lane_lock(dir_fd, row['lane'])
      try:
        before = copy.deepcopy(data)
        row['launch_allowed'] = False
        if row['timestamps']['quiescent_at'] is None:
          row['timestamps']['quiescent_at'] = stamp()
        self._release(data, row, proof)
        self._commit(dir_fd, before, data)
        return copy.deepcopy(row)
      finally:
        os.close(guard)

  def transfer(self, coordinator, epoch, new_coordinator, proof):
    identifier(new_coordinator, 'new coordinator')
    evidence(proof)
    with self._locked(write=True) as (dir_fd, data):
      self._owner(data, coordinator, epoch)
      if any(lane['active'] is not None for lane in data['lanes'].values()):
        raise SchedulerError('ownership transfer requires drained reservations')
      guards = []
      try:
        for lane in sorted(data['lanes']):
          guards.append(self._lane_lock(dir_fd, lane))
        before = copy.deepcopy(data)
        data['epoch'] = uuid.uuid4().hex
        data['coordinator'] = new_coordinator
        data['boot_id'] = boot_id()
        data['transfer_evidence'] = proof
        self._commit(dir_fd, before, data)
        return copy.deepcopy(data)
      finally:
        for fd in guards:
          os.close(fd)

  def recover(self, coordinator, epoch, proof):
    """Single-owner journal recovery rolls back and preserves all holds."""
    evidence(proof)
    with self._locked(write=True, pending=True) as (dir_fd, current):
      journal = read_json(dir_fd, 'pending.json')
      if journal is None:
        raise SchedulerError('no pending transaction to recover')
      if (not isinstance(journal, dict) or
          not {'before', 'after'} <= journal.keys()):
        raise SchedulerError('incomplete journal needs manual inspection')
      before = journal['before']
      after = validate(journal['after'])
      if before is not None:
        validate(before)
      source = after if before is None else before
      self._owner(source, coordinator, epoch)
      recovery_proof = journal.get('recovery_evidence')
      if recovery_proof is not None:
        evidence(recovery_proof)
      target = (None if recovery_proof is None else
                self._recovery_target(source, recovery_proof))
      if current not in (before, after, target):
        raise SchedulerError('state does not match the transaction journal')
      guards = []
      try:
        for lane in sorted(set(source['lanes']) | set(after['lanes'])):
          guards.append(self._lane_lock(dir_fd, lane))
        if target is None:
          target = self._recovery_target(source, proof)
          journal['recovery_evidence'] = proof
          self._atomic(dir_fd, 'pending.json', journal)
        self._atomic(dir_fd, 'state.json', target)
        os.unlink('pending.json', dir_fd=dir_fd)
        os.fsync(dir_fd)
        return copy.deepcopy(target)
      finally:
        for fd in guards:
          os.close(fd)

  def _update_run(self, identity, change):
    with self._locked(write=True) as (dir_fd, data):
      row = self._row(data, identity)
      before = copy.deepcopy(data)
      change(data, row)
      self._commit(dir_fd, before, data)

  def run(self, identity):
    """Launch only the stored payload, once, under atomic registration."""
    guard, proc = None, None
    old_handlers = {}
    interrupted = []

    def on_signal(signum, frame):
      if not interrupted:
        interrupted.append(signum)

    if threading.current_thread() is threading.main_thread():
      for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        old_handlers[sig] = signal.signal(sig, on_signal)
    try:
      with self._locked(write=True) as (dir_fd, data):
        row = self._row(data, identity)
        if row['epoch'] != data['epoch'] or data['boot_id'] != boot_id():
          raise SchedulerError('launch grant belongs to another epoch or boot')
        if row['operational'] != 'granted' or not row['launch_allowed']:
          raise SchedulerError('launch is revoked or already consumed')
        lane = data['lanes'][row['lane']]
        if (lane['active'] != row['run_id'] or
            lane['generation'] != row['generation']):
          raise SchedulerError('launch run does not own current reservation')
        guard = self._lane_lock(dir_fd, row['lane'])
        before = copy.deepcopy(data)
        row['launch_allowed'] = False
        row['operational'] = 'launching'
        self._commit(dir_fd, before, data)
        # Persist no-replay intent BEFORE spawn while holding authority.lock.
        # Cancellation/release cannot interleave between checking and spawning.
        if interrupted:
          raise SchedulerError('interrupted before launch; '
                               'recovery hold retained')
        payload = copy.deepcopy(row['payload'])
        proc = subprocess.Popen(payload['command'], cwd=payload['cwd'],
                                start_new_session=True, pass_fds=(guard,))
        before = copy.deepcopy(data)
        row['operational'] = 'running'
        row['timestamps']['started_at'] = stamp()
        row['supervisor'] = dict(pid=os.getpid(), child_pid=proc.pid,
                                pgid=proc.pid, boot_id=data['boot_id'])
        self._commit(dir_fd, before, data)
      code, quiescent = self._supervise(proc, identity, payload['timeout'],
                                        interrupted)
      with self._locked(write=True) as (dir_fd, data):
        row = self._row(data, identity)
        lane = data['lanes'][row['lane']]
        if (lane['active'] != row['run_id'] or
            lane['generation'] != row['generation'] or
            row['operational'] not in ('running', 'stopping')):
          raise SchedulerError('supervisor no longer owns its reservation')
        self._checkpoint('finalization-locked')
        # Hold authority.lock throughout guard close/reacquisition and release.
        # A coordinator cannot reconcile the gap or grant a successor here.
        os.close(guard)
        guard = None
        before = copy.deepcopy(data)
        if quiescent and row['scope'] == 'trusted-foreground-group':
          try:
            guard = self._lane_lock(dir_fd, row['lane'])
          except SchedulerError:
            quiescent = False
        else:
          quiescent = False
        row['launch_allowed'] = False
        if quiescent:
          row['timestamps']['quiescent_at'] = stamp()
          row['operational'] = 'quiescent'
          if row['release_mode'] == 'automatic':
            self._release(data, row, 'live-supervisor-foreground-quiescence')
        else:
          row['operational'] = 'recovery-required'
        self._commit(dir_fd, before, data)
      return code
    except BaseException:
      # A failed registration/receipt write must not turn cleanup into release.
      # A live Popen child handle is usable; persisted PIDs never are.
      if proc is not None:
        self._stop_live_group(proc)
      raise
    finally:
      if guard is not None:
        os.close(guard)
      for sig, handler in old_handlers.items():
        signal.signal(sig, handler)

  def _supervise(self, proc, identity, timeout, interrupted):
    deadline = time.monotonic() + timeout
    terminal, code = False, None
    while True:
      result = proc.poll()
      if result is not None and not terminal:
        code = result if result >= 0 else 128 - result
        self._terminal(identity, code)
        terminal = True
      alive = group_alive(proc.pid)
      if terminal and not alive:
        return code, True
      status = self.status()
      row = self._row(status, identity)
      cancel = row['operational'] == 'stopping'
      if interrupted or cancel or time.monotonic() >= deadline:
        code = 128 + interrupted[0] if interrupted else 125 if cancel else 124
        quiet = self._stop_live_group(proc)
        if not terminal:
          self._terminal(identity, code)
        return code, quiet
      time.sleep(.1)

  def _terminal(self, identity, code):
    def update(data, row):
      if row['timestamps']['terminal_at'] is None:
        row['timestamps']['terminal_at'] = stamp()
        row['terminal_code'] = code
    self._update_run(identity, update)

  def _stop_live_group(self, proc):
    # poll() returning None verifies this unreaped direct child still owns its
    # PID. Keep it unreaped through BOTH signals, preventing group-ID reuse.
    # If the leader was already reaped, do not signal a remembered group ID.
    if proc.poll() is None:
      try:
        os.killpg(proc.pid, signal.SIGTERM)
        time.sleep(.3)
        os.killpg(proc.pid, signal.SIGKILL)
      except ProcessLookupError:
        pass
      except PermissionError:
        # macOS may refuse SIGKILL after TERM has already emptied the group,
        # while the direct child is still an unreaped zombie. Reap only if it
        # exited, then independently verify the foreground group disappeared.
        return proc.poll() is not None and not group_alive(proc.pid)
      proc.wait()
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
      if not group_alive(proc.pid):
        return True
      time.sleep(.03)
    return False


def group_alive(pgid):
  try:
    os.killpg(pgid, 0)
    return True
  except ProcessLookupError:
    return False
  except PermissionError:
    raise SchedulerError('process-group observation denied; '
                         'retain recovery hold')


def parser():
  p = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
  p.add_argument('--state', required=True, help='explicit private pilot state')
  sub = p.add_subparsers(dest='action', required=True)
  init = sub.add_parser('init')
  init.add_argument('--coordinator', required=True)
  grant = sub.add_parser('grant')
  grant.add_argument('--coordinator', required=True)
  grant.add_argument('--epoch', required=True)
  grant.add_argument('--lane', required=True)
  grant.add_argument('--run-id', required=True)
  grant.add_argument('--worker', required=True)
  grant.add_argument('--cwd', required=True)
  grant.add_argument('--timeout', type=float, default=3600)
  grant.add_argument('--release-mode',
                     choices=('manual', 'shadow', 'automatic'),
                     default='manual')
  grant.add_argument('--scope', choices=('trusted-foreground-group',
                                        'unverified'), default='unverified')
  grant.add_argument('command', nargs=argparse.REMAINDER)
  for name in ('run', 'cancel', 'release', 'semantic', 'reconcile'):
    command = sub.add_parser(name)
    for key in IDENTITY:
      command.add_argument('--' + key.replace('_', '-'), required=True,
                           type=int if key == 'generation' else str)
    if name != 'run':
      command.add_argument('--coordinator', required=True)
    if name in ('release', 'semantic', 'reconcile'):
      command.add_argument('--evidence', required=True)
    if name == 'semantic':
      command.add_argument('--decision', required=True,
                           choices=('accepted', 'rejected',
                                    'needs-investigation'))
    if name == 'reconcile':
      command.add_argument('--confirm-quiescent', action='store_true',
                           required=True,
                           help='explicit operator assertion of external proof')
  for name in ('status', 'export', 'outbox'):
    sub.add_parser(name)
  for name in ('ack', 'recover', 'transfer'):
    command = sub.add_parser(name)
    command.add_argument('--coordinator', required=True)
    command.add_argument('--epoch', required=True)
    if name == 'ack':
      command.add_argument('--notification', required=True)
    else:
      command.add_argument('--evidence', required=True)
    if name == 'transfer':
      command.add_argument('--new-coordinator', required=True)
  return p


def main(argv=None):
  args = parser().parse_args(argv)
  scheduler = Scheduler(args.state)
  if args.action == 'init':
    result = scheduler.init(args.coordinator)
  elif args.action == 'grant':
    command = args.command[1:] if args.command[:1] == ['--'] else args.command
    result = scheduler.grant(args.coordinator, args.epoch, args.lane,
                              args.run_id, args.worker, command, args.cwd,
                              args.timeout, args.release_mode, args.scope)
  elif args.action in ('run', 'cancel', 'release', 'semantic', 'reconcile'):
    identity = {key: getattr(args, key) for key in IDENTITY}
    if args.action == 'run':
      return scheduler.run(identity)
    if args.action == 'cancel':
      result = scheduler.cancel(args.coordinator, identity)
    elif args.action == 'semantic':
      result = scheduler.semantic(args.coordinator, identity, args.decision,
                                   args.evidence)
    else:
      result = getattr(scheduler, args.action)(args.coordinator, identity,
                                               args.evidence)
  elif args.action in ('status', 'export', 'outbox'):
    result = getattr(scheduler, args.action)()
  elif args.action == 'ack':
    result = scheduler.ack(args.coordinator, args.epoch, args.notification)
  elif args.action == 'recover':
    result = scheduler.recover(args.coordinator, args.epoch, args.evidence)
  else:
    result = scheduler.transfer(args.coordinator, args.epoch,
                                 args.new_coordinator, args.evidence)
  print(json.dumps(result, indent=2, sort_keys=True))
  return 0
