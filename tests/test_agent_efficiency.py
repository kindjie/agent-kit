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

  def codex_tool(self, ident, command, result, wrapper=False):
    name = 'functions.exec' if wrapper else 'functions.exec_command'
    args = json.dumps({'cmd': command})
    if wrapper:
      args = 'text(await tools.exec_command(' + args + '));'
    return [
      {'timestamp': '2026-01-01T12:00:00Z', 'type': 'response_item',
       'payload': {'type': 'function_call', 'call_id': ident,
                   'name': name, 'arguments': args}},
      {'timestamp': '2026-01-01T12:01:00Z', 'type': 'response_item',
       'payload': {'type': 'function_call_output', 'call_id': ident,
                   'output': result}}]

  def coordination(self, events):
    path = self.write('coordination.jsonl', events)
    return self.mod.audit([('codex', path)], 0, 9999999999)

  def test_empty_events_and_failed_attempts_are_result_based(self):
    empty = {'schema_version': 1, 'cursor': {}, 'events': [],
             'rewritten': []}
    events = self.codex_tool('empty', 'agent-task events T-0001',
      json.dumps({'exit_code': 0, 'output': json.dumps(empty)}))
    events += self.codex_tool('timeout',
      "agent-task events T-0001 --after '{}'", {'exit_code': 4,
        'output': json.dumps(empty)})
    events += self.codex_tool('failed', 'agent-task claim T-0001',
                             {'exit_code': 2, 'output': 'refused'})
    events += self.codex_tool('pending', 'agent-task claim T-0001',
                             {'session_id': 12, 'output': ''})
    report = self.coordination(events + events)
    counters = report['coordination']
    self.assertEqual(counters['empty_events_returns'], 2)
    self.assertEqual(counters['empty_events_bootstrap_returns'], 1)
    self.assertEqual(counters['empty_events_timeout_returns'], 1)
    self.assertEqual(counters['failed_cli_attempts'], 1)
    self.assertEqual(counters['completed_cli_attempts'], 3)

  def test_nested_results_require_correlated_literal_shell_calls(self):
    empty = {'schema_version': 1, 'cursor': {}, 'events': [],
             'rewritten': []}
    result = {'content': [{'type': 'text', 'text': json.dumps({
      'status': 'fulfilled', 'value': {'exit_code': 0,
      'output': json.dumps(empty)}})}]}
    events = self.codex_tool('wrapped', 'agent-task events T-0001',
                             result, wrapper=True)
    events += self.codex_tool('echo',
      "echo 'agent-task events T-0001'", {'exit_code': 0,
        'output': json.dumps(empty)})
    # Arbitrary tool output and free prose are not CLI evidence.
    events.append({'timestamp': '2026-01-01T12:02:00Z',
      'type': 'response_item', 'payload': {'type': 'function_call_output',
        'call_id': 'unknown', 'output': {'exit_code': 2}}})
    report = self.coordination(events)
    self.assertEqual(report['coordination']['empty_events_returns'], 1)
    self.assertEqual(report['coordination']['failed_cli_attempts'], 0)
    self.assertIsNone(report['coordination'][
      'release_to_confirmation_seconds'])

  def test_common_exec_js_keys_and_input_text_outputs(self):
    batch = {'schema_version': 1, 'cursor': {}, 'events': [], 'rewritten': []}
    output = [{'type': 'input_text', 'text':
               'Script completed\nWall time 0.3 seconds\nOutput:\n'},
              {'type': 'input_text', 'text': json.dumps({
                'exit_code': 0, 'output': json.dumps(batch)})}]
    events = self.codex_tool('common', 'agent-task events T-0001', output,
                             wrapper=True)
    events[0]['payload']['arguments'] = (
      'const r = await tools.exec_command({cmd: "agent-task events T-0001",'
      ' max_output_tokens: 1000}); text(r);')
    report = self.coordination(events)
    self.assertEqual(report['coordination']['empty_events_returns'], 1)
    self.assertEqual(report['coordination']['recognized_cli_calls'], 1)
    self.assertEqual(report['coordination']['unsupported_cli_results'], 0)

  def test_batch_wrapper_and_textual_shell_result(self):
    result = ('Chunk ID: example\nWall time: 0.1 seconds\n'
              'Process exited with code 2\nFinal output:\nrefused')
    events = self.codex_tool('textual', 'agent-task claim T-0001', result)
    more = self.codex_tool('batch', 'unused', [
      {'type': 'input_text', 'text': json.dumps({'status': 'fulfilled',
        'value': {'exit_code': 0, 'output': 'ok'}})},
      {'type': 'input_text', 'text': json.dumps({'status': 'fulfilled',
        'value': {'exit_code': 1, 'output': 'no'}})}], wrapper=True)
    more[0]['payload']['arguments'] = (
      'const results = await Promise.allSettled(['
      'tools.exec_command({cmd: "true"}), '
      'tools.exec_command({cmd: "false"})]); results.forEach(text);')
    report = self.coordination(events + more)
    self.assertEqual(report['coordination']['completed_cli_attempts'], 3)
    self.assertEqual(report['coordination']['failed_cli_attempts'], 2)

  def test_malformed_coordination_does_not_crash_or_count(self):
    batch = {'schema_version': 1, 'cursor': {}, 'rewritten': [],
             'events': [{'task': [], 'log': [], 'fields': {'expires': []}}]}
    events = self.codex_tool('malformed', 'agent-task events T-0001',
      {'exit_code': 0, 'output': json.dumps(batch)})
    events += [{'timestamp': '2026-01-01T12:00:00Z',
                'type': 'response_item', 'payload': []}]
    events += [{'timestamp': '2026-01-01T12:00:00Z',
                'type': 'response_item', 'payload': {
                  'type': 'function_call', 'call_id': 'bad-name',
                  'name': None, 'arguments': '{}'}}]
    report = self.coordination(events)
    self.assertEqual(report['coordination']['renewal_only_wakeups'], 0)

  def test_global_options_keep_timeout_semantics(self):
    batch = {'schema_version': 1, 'cursor': {}, 'events': [], 'rewritten': []}
    events = self.codex_tool('global',
      "agent-task --dir /example --agent reader events T-0001 --after='{}'",
      {'exit_code': 4, 'output': json.dumps(batch)})
    report = self.coordination(events)
    self.assertEqual(report['coordination']['empty_events_timeout_returns'], 1)
    self.assertEqual(report['coordination']['failed_cli_attempts'], 0)

  def test_async_sessions_correlate_terminal_results_once(self):
    batch = {'schema_version': 1, 'cursor': {}, 'events': [], 'rewritten': []}
    events = self.codex_tool('start',
      "agent-task events T-0001 --after '{}'",
      {'session_id': 12, 'exit_code': None, 'output': ''}, wrapper=True)
    for ident, session, output in [
      ('wrong', 99, {'exit_code': 2, 'output': ''}),
      ('poll', 12, {'session_id': 12, 'output': ''}),
      ('done', 12, {'exit_code': 4, 'output': json.dumps(batch)}),
      ('duplicate', 12, {'exit_code': 4, 'output': json.dumps(batch)})]:
      more = self.codex_tool(ident, 'unused', output, wrapper=True)
      more[0]['payload']['arguments'] = (
        'text(await tools.write_stdin({session_id: ' + str(session) + '}));')
      events += more
    report = self.coordination(events)
    counters = report['coordination']
    self.assertEqual(counters['completed_cli_attempts'], 1)
    self.assertEqual(counters['recognized_cli_calls'], 1)
    self.assertEqual(counters['empty_events_timeout_returns'], 1)
    self.assertEqual(counters['failed_cli_attempts'], 0)
    self.assertEqual(counters['unmatched_cli_resumptions'], 1)
    self.assertEqual(counters['unsupported_cli_results'], 0)

  def test_same_task_in_different_roots_is_not_a_renewal(self):
    mark = {'T-0001': {'log': '0:' + 'a' * 16, 'header': 'a' * 64}}
    events = []
    for index, expires in enumerate(['12:00', '13:00']):
      batch = {'schema_version': 1, 'cursor': mark, 'rewritten': [],
        'events': [{'task': 'T-0001', 'log': [], 'fields': {'owner': 'example',
          'status': 'in-progress', 'expires': f'2026-01-01T{expires}:00Z'}}]}
      command = ('agent-task --dir /example/' + str(index) +
                 ' events T-0001 --after ' + repr(json.dumps(mark)))
      events += self.codex_tool(str(index), command,
                                {'exit_code': 0, 'output': json.dumps(batch)})
    self.assertEqual(self.coordination(events)['coordination'][
      'renewal_only_wakeups'], 0)

  def test_renewal_classification_requires_structured_proof(self):
    cursor = {'T-0001': {'log': '0:' + 'a' * 16, 'header': 'a' * 64}}
    base = {'schema_version': 1, 'cursor': cursor, 'rewritten': []}
    initial = dict(base, events=[{'task': 'T-0001', 'log': [],
      'fields': {'expires': '2026-01-01T12:00:00Z', 'owner': 'example',
                 'status': 'in-progress'}}])
    renewal = dict(base, events=[{'task': 'T-0001', 'log': [],
      'fields': {'expires': '2026-01-01T13:00:00Z', 'owner': 'example',
                 'status': 'in-progress'}}])
    prose = dict(base, events=[{'task': 'T-0001',
      'log': ['Renewed claim'], 'fields': {}}])
    mixed = dict(base, events=renewal['events'] + prose['events'])
    events = []
    for ident, batch in [('initial', initial), ('renewal', renewal),
                         ('prose', prose),
                         ('mixed', mixed)]:
      command = 'agent-task events T-0001 --after ' + repr(json.dumps(cursor))
      events += self.codex_tool(ident, command,
        {'exit_code': 0, 'output': json.dumps(batch)})
    report = self.coordination(events)
    self.assertEqual(report['coordination']['renewal_only_wakeups'], 1)

  def test_claude_tool_results_count_explicit_failures(self):
    events = [
      {'timestamp': '2026-01-01T12:00:00Z', 'type': 'assistant',
       'sessionId': 'c', 'message': {'id': 'm', 'model': 'example',
         'usage': {'input_tokens': 1, 'output_tokens': 1},
         'content': [{'type': 'tool_use', 'id': 't', 'name': 'Bash',
                     'input': {'command': 'agent-task claim T-0001'}}]}},
      {'timestamp': '2026-01-01T12:01:00Z', 'type': 'user',
       'sessionId': 'c', 'message': {'content': [
         {'type': 'tool_result', 'tool_use_id': 't', 'is_error': True,
          'content': 'Exit code 2\nrefused'}]}}]
    report = self.mod.audit([('claude', self.write('claude.jsonl', events))],
                            0, 9999999999)
    self.assertEqual(report['coordination']['failed_cli_attempts'], 1)

  def test_compaction_counts_unknown_duration_and_per_thread_totals(self):
    events = []
    for ident, begin, end in [('one', 0, 3000), ('two', 3000, 8000),
                             ('three', None, None)]:
      events.append({'timestamp': '2026-01-01T12:00:00Z',
        'type': 'event_msg', 'payload': {'type': 'item_completed',
          'item': {'type': 'ContextCompaction', 'id': ident},
          'started_at_ms': begin, 'completed_at_ms': end}})
    report = self.coordination(events + events)
    self.assertEqual(report['compaction_count'], 3)
    self.assertEqual(report['compaction_unknown_duration_count'], 1)
    self.assertEqual(report['compaction_agent_seconds'], 8)
    self.assertEqual(report['compactions_by_thread'][0]['count'], 3)
    self.assertEqual(report['compactions_by_thread'][0]['seconds'], 8)

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
