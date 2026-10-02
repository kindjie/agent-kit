"""Validate supplied release evidence, without observing or changing a lane."""
import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import math
import os
import re
import stat

MAX_BYTES = 65536
IDENTITY = ('authority_ref', 'grant_ref', 'lane', 'run_id', 'worker')
SCOPES = ('scoped-process-observation', 'trusted-foreground-group')
UTC_STAMP = re.compile(
  r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z\Z')


class InputError(ValueError):
  """An intentionally input-free diagnostic."""


@dataclass(frozen=True)
class Identity:
  authority_ref: str
  grant_ref: str
  lane: str
  run_id: str
  worker: str


@dataclass(frozen=True)
class Evidence:
  identity: Identity
  scope: str
  scope_ref: str
  grant_ref: str
  execution_ref: str
  intent_ref: str
  quiescence_ref: str
  execution_observer: str
  quiescence_observer: str
  terminal_status: str
  terminal_value: int
  granted_at: datetime
  terminal_at: datetime
  reaped_at: datetime
  intent_at: datetime
  check_started_at: datetime
  check_completed_at: datetime

  @property
  def ready_at(self):
    return max(self.reaped_at, self.intent_at, self.check_completed_at)


@dataclass(frozen=True)
class Decision:
  identity: Identity
  decision: str
  decided_at: datetime
  evidence_ref: str


class Validator:
  def __init__(self):
    self.issues = []

  def issue(self, field, reason):
    self.issues.append(field + ': ' + reason)

  def record(self, value, field, required, optional=()):
    if not isinstance(value, dict):
      self.issue(field, 'object required')
      return {}
    if set(required) - value.keys():
      self.issue(field, 'required fields missing')
    if value.keys() - set(required) - set(optional):
      self.issue(field, 'unknown fields')
    return value

  def text(self, value, field):
    if (not isinstance(value, str) or not value.strip() or
        any(ord(char) < 32 or ord(char) == 127 for char in value)):
      self.issue(field, 'nonempty bounded reference or label required')
      return None
    try:
      valid = len(value.encode('utf-8')) <= 1024
    except UnicodeError:
      valid = False
    if not valid:
      self.issue(field, 'nonempty bounded reference or label required')
      return None
    return value

  def enum(self, value, field, choices):
    if not isinstance(value, str) or value not in choices:
      self.issue(field, 'unsupported status or scope')
      return None
    return value

  def number(self, value, field, low, high):
    if type(value) is not int or not low <= value <= high:
      self.issue(field, 'integer outside permitted range')
      return None
    return value

  def timestamp(self, value, field):
    if isinstance(value, str) and UTC_STAMP.fullmatch(value):
      try:
        return datetime.fromisoformat(value[:-1]).replace(tzinfo=timezone.utc)
      except ValueError:
        pass
    self.issue(field, 'UTC timestamp required')
    return None

  def identity(self, value, field, expected=None):
    row = self.record(value, field, IDENTITY)
    parts = [self.text(row.get(key), field + '.' + key) for key in IDENTITY]
    if None in parts:
      return None
    result = Identity(*parts)
    if expected is not None and result != expected:
      self.issue(field, 'identity mismatch')
    return result

  def ordered(self, earlier, later, field):
    if earlier is not None and later is not None and earlier > later:
      self.issue(field, 'inconsistent chronology')

  def parse(self, data):
    root = self.record(data, 'evidence',
                       ('schema_version', 'kind', 'identity', 'grant',
                        'execution', 'intent', 'quiescence'), ('coordinator',))
    self.number(root.get('schema_version'), 'schema_version', 1, 1)
    self.enum(root.get('kind'), 'kind', ('agent-release-evidence',))
    ident = self.identity(root.get('identity'), 'identity')
    grant = self.record(root.get('grant'), 'grant',
                        ('scope', 'scope_ref', 'granted_at', 'evidence_ref'))
    scope = self.enum(grant.get('scope'), 'grant.scope', SCOPES)
    scope_ref = self.text(grant.get('scope_ref'), 'grant.scope_ref')
    grant_ref = self.text(grant.get('evidence_ref'), 'grant.evidence_ref')
    granted = self.timestamp(grant.get('granted_at'), 'grant.granted_at')
    execution = self.record(root.get('execution'), 'execution',
                            ('identity', 'observer', 'status', 'terminal_at',
                             'reaped_at', 'evidence_ref'),
                            ('exit_code', 'signal'))
    self.identity(execution.get('identity'), 'execution.identity', ident)
    observer = self.text(execution.get('observer'), 'execution.observer')
    status = self.enum(execution.get('status'), 'execution.status',
                       ('exited', 'signaled'))
    terminal_value = None
    if status is not None:
      value_key = 'exit_code' if status == 'exited' else 'signal'
      other_key = 'signal' if status == 'exited' else 'exit_code'
      terminal_value = self.number(execution.get(value_key),
                                   'execution.' + value_key,
                                   0 if status == 'exited' else 1, 255)
      if other_key in execution:
        self.issue('execution', 'terminal status and fields conflict')
    terminal = self.timestamp(execution.get('terminal_at'),
                              'execution.terminal_at')
    reaped = self.timestamp(execution.get('reaped_at'), 'execution.reaped_at')
    execution_ref = self.text(execution.get('evidence_ref'),
                              'execution.evidence_ref')
    intent = self.record(root.get('intent'), 'intent',
                         ('identity', 'status', 'observed_at', 'evidence_ref'))
    self.identity(intent.get('identity'), 'intent.identity', ident)
    self.enum(intent.get('status'), 'intent.status', ('ended',))
    intent_at = self.timestamp(intent.get('observed_at'), 'intent.observed_at')
    intent_ref = self.text(intent.get('evidence_ref'), 'intent.evidence_ref')
    check = self.record(root.get('quiescence'), 'quiescence',
                        ('identity', 'observer', 'scope', 'scope_ref', 'status',
                         'exit_code', 'result', 'started_at', 'completed_at',
                         'evidence_ref'))
    self.identity(check.get('identity'), 'quiescence.identity', ident)
    checker = self.text(check.get('observer'), 'quiescence.observer')
    check_scope = self.enum(check.get('scope'), 'quiescence.scope', SCOPES)
    check_scope_ref = self.text(check.get('scope_ref'), 'quiescence.scope_ref')
    if check_scope != scope or check_scope_ref != scope_ref:
      self.issue('quiescence', 'scope mismatch')
    self.enum(check.get('status'), 'quiescence.status', ('completed',))
    self.number(check.get('exit_code'), 'quiescence.exit_code', 0, 0)
    self.enum(check.get('result'), 'quiescence.result', ('quiescent',))
    started = self.timestamp(check.get('started_at'), 'quiescence.started_at')
    completed = self.timestamp(check.get('completed_at'),
                               'quiescence.completed_at')
    check_ref = self.text(check.get('evidence_ref'), 'quiescence.evidence_ref')
    if checker is not None and checker == observer:
      self.issue('quiescence.observer', 'separate observer record required')
    if check_ref is not None and check_ref == execution_ref:
      self.issue('quiescence.evidence_ref', 'separate check record required')
    for earlier, later, field in (
        (granted, terminal, 'execution.terminal_at'),
        (terminal, reaped, 'execution.reaped_at'),
        (reaped, intent_at, 'intent.observed_at'),
        (reaped, started, 'quiescence.started_at'),
        (started, completed, 'quiescence.completed_at')):
      self.ordered(earlier, later, field)
    evidence = None
    if not self.issues:
      evidence = Evidence(
        ident, scope, scope_ref, grant_ref, execution_ref, intent_ref,
        check_ref, observer, checker, status, terminal_value, granted,
        terminal, reaped, intent_at, started, completed)
    decision = None
    if 'coordinator' in root:
      previous = len(self.issues)
      row = self.record(root['coordinator'], 'coordinator',
                        ('identity', 'decision', 'decided_at', 'evidence_ref'))
      decision_ident = self.identity(row.get('identity'),
                                     'coordinator.identity', ident)
      action = self.enum(row.get('decision'), 'coordinator.decision',
                         ('released', 'held'))
      at = self.timestamp(row.get('decided_at'), 'coordinator.decided_at')
      ref = self.text(row.get('evidence_ref'), 'coordinator.evidence_ref')
      self.ordered(granted, at, 'coordinator.decided_at')
      if len(self.issues) == previous and ident is not None:
        decision = Decision(decision_ident, action, at, ref)
      else:
        evidence = None
    return evidence, decision


def report(issues, evidence=None, decision=None):
  shadow = None
  if decision is not None:
    ready = (evidence.ready_at <= decision.decided_at
             if evidence is not None else None)
    released = decision.decision == 'released'
    if evidence is None:
      comparison = decision.decision + '-with-insufficient-evidence'
    elif released and not ready:
      comparison = 'released-before-evidence'
    else:
      comparison = decision.decision + '-with-consistent-evidence'
    shadow = dict(
      actual_decision=decision.decision,
      comparison=comparison,
      evidence_ready_by_decision=ready,
      release_delay_seconds=(decision.decided_at - evidence.ready_at
                             ).total_seconds() if ready and released else None)
  return dict(
    schema_version=1, kind='agent-release-advisory', advisory_only=True,
    authority='coordinator', quiescence_verified=False,
    evidence_status=('recorded-evidence-consistent' if evidence is not None
                     else 'insufficient'),
    issues=issues, shadow=shadow,
    limits=['Supplied JSON consistency only; references are not dereferenced.',
            'Observer labels cannot prove independence or actual quiescence.',
            'Reference immutability and coordinator decisions are unverified.',
            'No release, acceptance, retry or reservation change '
            'is authorized.'])


def evaluate(data):
  validator = Validator()
  evidence, decision = validator.parse(data)
  return report(validator.issues, evidence, decision)


def _object(pairs):
  result = {}
  for key, value in pairs:
    if key in result:
      raise InputError('duplicate JSON keys')
    result[key] = value
  return result


def _constant(_value):
  raise InputError('nonfinite JSON number')


def _float(value):
  result = float(value)
  if not math.isfinite(result):
    raise InputError('nonfinite JSON number')
  return result


def _integer(value):
  if len(value.lstrip('-')) > 64:
    raise InputError('JSON integer exceeds 64 digit limit')
  return int(value)


def load(path):
  try:
    descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
    with os.fdopen(descriptor, 'rb') as stream:
      if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
        raise InputError('regular evidence file required')
      raw = stream.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
      raise InputError('evidence file exceeds 64 KiB limit')
    return json.loads(raw.decode('utf-8'), object_pairs_hook=_object,
                      parse_constant=_constant, parse_float=_float,
                      parse_int=_integer)
  except InputError:
    raise
  except (OSError, UnicodeError, ValueError, RecursionError,
          OverflowError) as exc:
    raise InputError('unreadable or invalid evidence JSON') from exc


class Parser(argparse.ArgumentParser):
  def error(self, message):
    raise InputError('one evidence file argument required')


def main(argv=None):
  parser = Parser(description=(
    'Advisory consistency check of supplied versioned release evidence; '
    'never observes processes or changes reservations.'))
  parser.add_argument('evidence_file', metavar='FILE')
  try:
    args = parser.parse_args(argv)
    result = evaluate(load(args.evidence_file))
    consistent = result['evidence_status'] == 'recorded-evidence-consistent'
    code = 0 if consistent else 1
  except InputError as exc:
    result = report(['input: ' + str(exc)])
    code = 2
  print(json.dumps(result, sort_keys=True, allow_nan=False))
  return code
