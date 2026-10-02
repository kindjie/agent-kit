"""Synthetic evidence packets; no scheduler, process or record-store access."""
import copy
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

BIN = Path(__file__).resolve().parents[1] / 'bin'


def packet():
  identity = dict(authority_ref='authority-demo', grant_ref='grant-demo',
                  lane='lane-demo', run_id='run-demo', worker='worker-demo')
  return dict(
    schema_version=1, kind='agent-release-evidence', identity=identity,
    grant=dict(scope='scoped-process-observation', scope_ref='scope-demo',
               granted_at='2026-01-01T00:00:00Z', evidence_ref='grant-record'),
    execution=dict(identity=copy.deepcopy(identity), observer='supervisor-demo',
                   status='exited', exit_code=7,
                   terminal_at='2026-01-01T00:00:01Z',
                   reaped_at='2026-01-01T00:00:02Z',
                   evidence_ref='execution-record'),
    intent=dict(identity=copy.deepcopy(identity), status='ended',
                observed_at='2026-01-01T00:00:03Z',
                evidence_ref='intent-record'),
    quiescence=dict(identity=copy.deepcopy(identity), observer='observer-demo',
                    scope='scoped-process-observation', scope_ref='scope-demo',
                    status='completed', exit_code=0, result='quiescent',
                    started_at='2026-01-01T00:00:03Z',
                    completed_at='2026-01-01T00:00:05Z',
                    evidence_ref='quiescence-record'))


class ReleaseCheckTest(unittest.TestCase):
  @classmethod
  def setUpClass(cls):
    spec = importlib.util.spec_from_file_location(
      'agent_release_check', BIN / 'agent_release_check.py')
    cls.module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = cls.module
    spec.loader.exec_module(cls.module)

  def check(self, data):
    before = copy.deepcopy(data)
    result = self.module.evaluate(data)
    self.assertEqual(data, before, 'validation mutated supplied evidence')
    self.assertTrue(result['advisory_only'])
    self.assertFalse(result['quiescence_verified'])
    self.assertEqual(result['authority'], 'coordinator')
    return result

  def insufficient(self, data):
    result = self.check(data)
    self.assertEqual(result['evidence_status'], 'insufficient')
    self.assertTrue(result['issues'])
    return result

  def coordinator(self, data, decision='released', at='00:00:08'):
    data['coordinator'] = dict(
      identity=copy.deepcopy(data['identity']), decision=decision,
      decided_at='2026-01-01T' + at + 'Z', evidence_ref='decision-record')

  def test_failed_workload_can_have_consistent_release_evidence(self):
    result = self.check(packet())
    self.assertEqual(result['evidence_status'], 'recorded-evidence-consistent')
    self.assertEqual(result['issues'], [])
    self.assertIsNone(result['shadow'])
    self.assertNotIn('runs', result)
    self.assertNotIn('released_at', json.dumps(result))

  def test_success_signal_and_both_scopes(self):
    for scope in ('scoped-process-observation', 'trusted-foreground-group'):
      for signaled in (False, True):
        data = packet()
        data['grant']['scope'] = data['quiescence']['scope'] = scope
        if signaled:
          del data['execution']['exit_code']
          data['execution'].update(status='signaled', signal=15)
        else:
          data['execution']['exit_code'] = 0
        self.assertEqual(self.check(data)['evidence_status'],
                         'recorded-evidence-consistent')

  def test_each_required_field_is_required(self):
    original = packet()
    for section, values in original.items():
      data = copy.deepcopy(original)
      del data[section]
      self.insufficient(data)
      if isinstance(values, dict):
        for field in values:
          with self.subTest(section=section, field=field):
            data = copy.deepcopy(original)
            del data[section][field]
            self.insufficient(data)

  def test_exact_identity_required_at_each_evidence_boundary(self):
    for section in ('execution', 'intent', 'quiescence', 'coordinator'):
      for field in packet()['identity']:
        data = packet()
        self.coordinator(data)
        data[section]['identity'][field] = 'wrong-identity'
        self.insufficient(data)

  def test_unknown_or_nonterminal_statuses_fail(self):
    for section, field, value in (
        ('execution', 'status', 'running'),
        ('execution', 'status', 'completed'),
        ('intent', 'status', 'paused'),
        ('quiescence', 'status', 'running'),
        ('quiescence', 'result', 'unknown'),
        ('quiescence', 'result', 'processes-remain'),
        ('grant', 'scope', 'unverified')):
      data = packet()
      data[section][field] = value
      self.insufficient(data)

  def test_failed_check_not_confused_with_failed_workload(self):
    data = packet()
    data['quiescence']['exit_code'] = 1
    self.insufficient(data)

  def test_inconsistent_terminal_fields_fail(self):
    for fields in (dict(signal=15), dict(exit_code=True),
                   dict(exit_code=-1), dict(status='signaled'),
                   dict(status='signaled', signal=0),
                   dict(status='signaled', signal=15)):
      data = packet()
      data['execution'].update(fields)
      self.insufficient(data)

  def test_boolean_shortcuts_and_wrong_types_fail(self):
    for section, field, value in (
        ('quiescence', 'exit_code', False),
        ('quiescence', 'result', True),
        ('execution', 'reaped_at', True),
        ('identity', 'grant_ref', None),
        ('grant', 'scope_ref', 123)):
      data = packet()
      data[section][field] = value
      self.insufficient(data)
    for section in ('execution', 'intent', 'quiescence'):
      data = packet()
      data[section] = True
      self.insufficient(data)

  def test_scope_and_scope_reference_must_match(self):
    for field in ('scope', 'scope_ref'):
      data = packet()
      data['quiescence'][field] = (
        'trusted-foreground-group' if field == 'scope' else 'other-scope')
      self.insufficient(data)

  def test_self_attestation_is_insufficient(self):
    for field in ('observer', 'evidence_ref'):
      data = packet()
      data['quiescence'][field] = data['execution'][field]
      self.insufficient(data)

  def test_bad_chronology_and_non_utc_fail(self):
    for section, field, value in (
        ('execution', 'terminal_at', '2025-12-31T23:59:59Z'),
        ('execution', 'reaped_at', '2026-01-01T00:00:00Z'),
        ('quiescence', 'started_at', '2026-01-01T00:00:01Z'),
        ('quiescence', 'completed_at', '2026-01-01T00:00:02Z'),
        ('intent', 'observed_at', '2026-01-01T00:00:01Z'),
        ('grant', 'granted_at', '2026-01-01T00:00:00'),
        ('grant', 'granted_at', '2026-01-01T00:00:00+01:00'),
        ('grant', 'granted_at', '2026-02-30T00:00:00Z')):
      data = packet()
      data[section][field] = value
      self.insufficient(data)

  def test_references_are_opaque_and_never_opened(self):
    data = packet()
    data['grant']['evidence_ref'] = '/nonexistent/private-reference'
    data['quiescence']['evidence_ref'] = 'sha256:' + 'a' * 64
    self.assertEqual(self.check(data)['evidence_status'],
                     'recorded-evidence-consistent')
    self.assertNotIn('private-reference', json.dumps(self.check(data)))

  def test_reference_and_observer_bounds(self):
    for value in ('', ' ', 'a\nb', 'x' * 1025):
      data = packet()
      data['quiescence']['evidence_ref'] = value
      self.insufficient(data)

  def test_unknown_fields_versions_and_shapes_fail(self):
    for change in (dict(schema_version=True), dict(schema_version=2),
                   dict(kind='agent-scheduler-receipts'), dict(runs=[])):
      data = packet()
      data.update(change)
      self.insufficient(data)
    data = packet()
    data['execution']['quiescent'] = True
    self.insufficient(data)
    for data in ([], None, 'prose release request'):
      self.insufficient(data)

  def test_shadow_released_matches_with_independent_delay(self):
    data = packet()
    self.coordinator(data)
    shadow = self.check(data)['shadow']
    self.assertEqual(shadow['comparison'], 'released-with-consistent-evidence')
    self.assertTrue(shadow['evidence_ready_by_decision'])
    self.assertEqual(shadow['release_delay_seconds'], 3.0)

  def test_shadow_early_release_diverges(self):
    data = packet()
    self.coordinator(data, at='00:00:04')
    shadow = self.check(data)['shadow']
    self.assertEqual(shadow['comparison'], 'released-before-evidence')
    self.assertFalse(shadow['evidence_ready_by_decision'])
    self.assertIsNone(shadow['release_delay_seconds'])

  def test_shadow_hold_timing_and_insufficient_evidence(self):
    for at in ('00:00:04', '00:00:08'):
      data = packet()
      self.coordinator(data, decision='held', at=at)
      self.assertEqual(self.check(data)['shadow']['comparison'],
                       'held-with-consistent-evidence')
    data['quiescence']['exit_code'] = 2
    result = self.insufficient(data)
    self.assertEqual(result['shadow']['comparison'],
                     'held-with-insufficient-evidence')
    self.assertIsNone(result['shadow']['evidence_ready_by_decision'])
    data['coordinator']['decision'] = 'released'
    self.assertEqual(self.insufficient(data)['shadow']['comparison'],
                     'released-with-insufficient-evidence')

  def test_explicit_intent_time_affects_shadow_readiness(self):
    data = packet()
    data['intent']['observed_at'] = '2026-01-01T00:00:10Z'
    self.coordinator(data)
    shadow = self.check(data)['shadow']
    self.assertEqual(shadow['comparison'], 'released-before-evidence')

  def test_malformed_coordinator_cannot_be_comparison_evidence(self):
    data = packet()
    self.coordinator(data)
    data['coordinator']['decision'] = 'accepted'
    self.assertIsNone(self.insufficient(data)['shadow'])

  def cli(self, raw, code):
    with tempfile.TemporaryDirectory() as directory:
      path = Path(directory) / 'private-input.json'
      path.write_bytes(raw)
      before = path.read_bytes()
      result = subprocess.run(
        [sys.executable, str(BIN / 'agent-release-check'), str(path)],
        capture_output=True, text=True, timeout=5)
      self.assertEqual(result.returncode, code, result.stderr)
      self.assertEqual(path.read_bytes(), before)
      self.assertNotIn(directory, result.stdout + result.stderr)
      self.assertNotIn('private-input', result.stdout + result.stderr)
      return json.loads(result.stdout)

  def test_cli_is_read_only_and_exits_for_evidence_status(self):
    self.cli(json.dumps(packet()).encode(), 0)
    data = packet()
    del data['intent']
    self.cli(json.dumps(data).encode(), 1)

  def test_cli_rejects_duplicate_keys_nonfinite_and_malformed_input(self):
    for raw in (b'{"private-key":1,"private-key":2}', b'{"x":NaN}',
                b'{"x":Infinity}', b'{"x":-Infinity}', b'{"x":1e999}',
                b'{"x":' + b'9' * 5000 + b'}',
                b'private-prose', b'\xff', b'[' * 2000):
      report = self.cli(raw, 2)
      self.assertEqual(report['evidence_status'], 'insufficient')
      self.assertNotIn('private', json.dumps(report))

  def test_cli_input_size_is_bounded(self):
    self.cli(b' ' * 65537, 2)

  def test_missing_input_error_does_not_echo_path(self):
    result = subprocess.run(
      [sys.executable, str(BIN / 'agent-release-check'),
       '/nonexistent/private-path'], capture_output=True, text=True, timeout=5)
    self.assertEqual(result.returncode, 2)
    self.assertNotIn('private-path', result.stdout + result.stderr)

  def test_nonregular_input_is_rejected_without_waiting(self):
    result = subprocess.run(
      [sys.executable, str(BIN / 'agent-release-check'), '/dev/null'],
      capture_output=True, text=True, timeout=5)
    self.assertEqual(result.returncode, 2)

  def test_fifo_is_rejected_without_waiting_for_a_writer(self):
    with tempfile.TemporaryDirectory() as directory:
      path = Path(directory) / 'private-fifo'
      os.mkfifo(path)
      result = subprocess.run(
        [sys.executable, str(BIN / 'agent-release-check'), str(path)],
        capture_output=True, text=True, timeout=5)
      self.assertEqual(result.returncode, 2)
      self.assertNotIn(directory, result.stdout + result.stderr)
      self.assertEqual(json.loads(result.stdout)['evidence_status'],
                       'insufficient')

  def test_invocation_errors_do_not_echo_raw_arguments(self):
    result = subprocess.run(
      [sys.executable, str(BIN / 'agent-release-check'),
       '--private-raw-argument'], capture_output=True, text=True, timeout=5)
    self.assertEqual(result.returncode, 2)
    self.assertNotIn('private-raw-argument', result.stdout + result.stderr)
    self.assertNotIn('Traceback', result.stdout + result.stderr)

  def test_documented_example_is_consistent_and_delay_is_three_seconds(self):
    source = (BIN / 'agent-release-check.md').read_text()
    example = source.split('```json\n', 1)[1].split('\n```', 1)[0]
    result = self.check(json.loads(example))
    self.assertEqual(result['evidence_status'], 'recorded-evidence-consistent')
    self.assertEqual(result['shadow']['release_delay_seconds'], 3.0)


if __name__ == '__main__':
  unittest.main()
