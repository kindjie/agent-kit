"""Independent synthetic ledger expectations across duplicate transcripts."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

PATH = Path(__file__).resolve().parents[1] / 'bin/agent_efficiency.py'


class EfficiencyTest(unittest.TestCase):
  def setUp(self):
    spec = importlib.util.spec_from_file_location('efficiency', PATH)
    self.mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(self.mod)
    self.tmp = tempfile.TemporaryDirectory()
    self.addCleanup(self.tmp.cleanup)
    self.base = Path(self.tmp.name)

  def write(self, name, events):
    path = self.base / name
    path.write_text('\n'.join(json.dumps(e) for e in events) + '\n')
    return path

  def test_cross_file_dedup_and_provider_math(self):
    codex = {'timestamp': '2026-01-01T12:00:00Z',
             'type': 'token_usage_record', 'payload': {
               'thread_id': 'a', 'response_id': 'r1', 'usage': {
                 'input_tokens': 100, 'cached_input_tokens': 80,
                 'output_tokens': 10, 'total_tokens': 110}}}
    claude = {'timestamp': '2026-01-01T12:01:00Z', 'type': 'assistant',
              'sessionId': 'c', 'message': {'id': 'm1', 'model': 'example',
              'usage': {'input_tokens': 5, 'cache_read_input_tokens': 30,
                        'cache_creation_input_tokens': 15,
                        'output_tokens': 7}}}
    paths = [('codex', self.write('a.jsonl', [codex])),
             ('codex', self.write('copy.jsonl', [codex])),
             ('claude', self.write('c.jsonl', [claude, claude]))]
    report = self.mod.audit(paths, self.mod.timestamp('2026-01-01T11:00:00Z'),
                            self.mod.timestamp('2026-01-01T13:00:00Z'))
    self.assertEqual(report['totals']['total_tokens'], 167)
    self.assertEqual(report['totals']['cached_input_tokens'], 110)
    self.assertEqual(report['totals']['output_tokens'], 17)
    self.assertEqual(report['duplicate_records'], 2)

  def test_cumulative_baseline_and_reset(self):
    events = []
    for hour, n in ((10, 100), (12, 160), (13, 20)):
      events.append({'timestamp': f'2026-01-01T{hour}:00:00Z',
                     'type': 'event_msg', 'payload': {'type': 'token_count',
                     'info': {'total_token_usage': {'input_tokens': n,
                       'cached_input_tokens': 0, 'output_tokens': 0,
                       'total_tokens': n}}}})
    report = self.mod.audit([('codex', self.write('old.jsonl', events))],
                            self.mod.timestamp('2026-01-01T11:00:00Z'),
                            self.mod.timestamp('2026-01-01T14:00:00Z'))
    self.assertEqual(report['totals']['total_tokens'], 80)
    self.assertIn('counter_resets', report['diagnostics'])

  def test_request_records_override_cumulative(self):
    path = self.write('new.jsonl', [
      {'timestamp': '2026-01-01T12:00:00Z', 'type': 'event_msg',
       'payload': {'type': 'token_count', 'info': {'total_token_usage':
         {'input_tokens': 1000, 'output_tokens': 0, 'total_tokens': 1000}}}},
      {'timestamp': '2026-01-01T12:00:00Z', 'type': 'token_usage_record',
       'payload': {'response_id': 'r', 'thread_id': 'a', 'usage':
         {'input_tokens': 9, 'output_tokens': 1, 'total_tokens': 10}}}])
    report = self.mod.audit([('codex', path)],
                            self.mod.timestamp('2026-01-01T11:00:00Z'),
                            self.mod.timestamp('2026-01-01T13:00:00Z'))
    self.assertEqual(report['totals']['total_tokens'], 10)

  def test_partial_input_is_visible(self):
    path = self.write('broken.jsonl', [])
    path.write_text('{broken\n')
    report = self.mod.audit([('codex', path)], 0, 9999999999)
    self.assertFalse(report['complete'])
    self.assertEqual(report['diagnostics']['malformed_lines'], 1)

  def test_compaction_payload_timing_and_half_open_window(self):
    path = self.write('compact.jsonl', [
      {'timestamp': '2026-01-01T12:00:00Z', 'type': 'event_msg',
       'payload': {'type': 'item_completed',
                   'item': {'type': 'ContextCompaction'},
                   'started_at_ms': 1000, 'completed_at_ms': 11000}}])
    report = self.mod.audit([('codex', path)],
                            self.mod.timestamp('2026-01-01T11:00:00Z'),
                            self.mod.timestamp('2026-01-01T13:00:00Z'))
    self.assertEqual(report['compaction_agent_seconds'], 10)
    report = self.mod.audit([('codex', path)],
                            self.mod.timestamp('2026-01-01T11:00:00Z'),
                            self.mod.timestamp('2026-01-01T12:00:00Z'))
    self.assertEqual(report['compaction_agent_seconds'], 0)

  def test_claude_children_keep_their_identity(self):
    events = []
    for ident in ('parent', 'child'):
      event = {'timestamp': '2026-01-01T12:00:00Z', 'type': 'assistant',
               'sessionId': 'parent', 'message': {'id': ident, 'model': 'm',
               'usage': {'input_tokens': 10, 'output_tokens': 1}}}
      if ident == 'child':
        event['agentId'] = 'child'
      events.append(event)
    report = self.mod.audit([('claude', self.write('children.jsonl', events))],
                            0, 9999999999)
    self.assertEqual({r['thread'] for r in report['groups']},
                      {'parent', 'child'})

  def test_mixed_epoch_and_unsupported_activity_are_incomplete(self):
    events = [
      {'timestamp': '2026-01-01T11:00:00Z', 'type': 'event_msg',
       'payload': {'type': 'token_count', 'info': {'total_token_usage':
         {'input_tokens': 20, 'output_tokens': 0, 'total_tokens': 20}}}},
      {'timestamp': '2026-01-01T12:00:00Z', 'type': 'token_usage_record',
       'payload': {'response_id': 'r', 'usage':
         {'input_tokens': 9, 'output_tokens': 1, 'total_tokens': 10}}}]
    report = self.mod.audit([('codex', self.write('mixed.jsonl', events))],
                            0, 9999999999)
    self.assertFalse(report['complete'])
    self.assertEqual(report['diagnostics']['uncovered_legacy_observations'], 1)
    unsupported = self.write('unsupported.jsonl', [
      {'timestamp': '2026-01-01T12:00:00Z', 'type': 'response_item',
       'payload': {'role': 'assistant', 'type': 'message'}}])
    report = self.mod.audit([('codex', unsupported)], 0, 9999999999)
    self.assertFalse(report['complete'])
    self.assertEqual(report['diagnostics']['assistant_files_without_usage'], 1)

  def test_legacy_after_request_cannot_claim_complete(self):
    path = self.write('later.jsonl', [
      {'timestamp': '2026-01-01T10:00:00Z', 'type': 'token_usage_record',
       'payload': {'response_id': 'r', 'usage':
         {'input_tokens': 9, 'output_tokens': 1, 'total_tokens': 10}}},
      {'timestamp': '2026-01-01T12:00:00Z', 'type': 'event_msg',
       'payload': {'type': 'token_count', 'info': {'total_token_usage':
         {'input_tokens': 20, 'output_tokens': 0, 'total_tokens': 20}}}}])
    report = self.mod.audit([('codex', path)],
                            self.mod.timestamp('2026-01-01T11:00:00Z'),
                            self.mod.timestamp('2026-01-01T13:00:00Z'))
    self.assertFalse(report['complete'])
    self.assertEqual(report['diagnostics'][
      'mixed_accounting_coverage_unverified'], 1)
