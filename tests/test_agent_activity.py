"""Observed waits and parent/child UI evidence, without model calls."""
import importlib
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest

from tests import test_agent_quota_agents as fixtures

AGENTS = fixtures.AGENTS

sys.path.insert(0, str(Path(__file__).parents[1] / 'bin'))


def agent(ident, parent=None, phase='ended', pending=None, seen=1000):
  return dict(key='codex:' + ident, provider='codex', id=ident,
              parent_id=parent, work='Example work', label=ident,
              last_seen=seen, status={}, observation={
                'phase': phase, 'at': seen, 'pending': pending or []})


class ActivityTest(unittest.TestCase):
  def setUp(self):
    self.mod = importlib.import_module('agent_activity')

  def test_waits_require_executed_commands_not_mentions(self):
    wait = self.mod.wait_spec('exec_command', {
      'cmd': 'agent-task watch T-0012 --until closed --timeout 540'})
    self.assertEqual(wait['tasks'], ['T-0012'])
    self.assertEqual(wait['condition'], 'closed')
    for cmd in ('echo agent-task watch T-0012',
                'printf "agent-task watch T-0012"',
                'agent-task watch T-0012 &',
                'agent-task watch T-0012; echo done',
                'agent-task show T-0012'):
      self.assertIsNone(self.mod.wait_spec('exec_command', {'cmd': cmd}))
    self.assertIsNone(self.mod.wait_spec('Bash', {
      'command': 'agent-task watch T-0012', 'run_in_background': True}))

  def test_typed_agent_wait_and_unknown_target(self):
    wait = self.mod.wait_spec('collaboration.wait_agent', {'timeout_ms': 1000})
    self.assertEqual(wait['kind'], 'agent')
    self.assertEqual(wait['targets'], [])
    wait = self.mod.wait_spec('wait', {'ids': ['reviewer'], 'timeout_ms': 1000})
    self.assertEqual(wait['targets'], ['reviewer'])
    self.assertIsNone(self.mod.wait_spec('functions.wait', {'cell_id': '42'}))
    self.assertIsNone(self.mod.wait_spec('Agent', {'run_in_background': True}))

  def test_snapshot_and_background_poll_are_not_subscriptions(self):
    self.assertIsNone(self.mod.wait_spec('exec_command', {
      'cmd': 'agent-task events T-0001'}))
    wait = self.mod.wait_spec('exec_command', {
      'cmd': "agent-task events T-0001 T-0002 --after '{}' --for reviewer"})
    self.assertEqual(wait['tasks'], ['T-0001', 'T-0002'])
    self.assertEqual(wait['condition'], 'changes/messages for reviewer')

  def test_idle_thinking_active_and_stale_are_distinct(self):
    for phase, expected in (('ended', 'IDLE'), ('thinking', 'THINKING'),
                            ('active', 'ACTIVE'), ('done', 'DONE')):
      self.assertEqual(self.mod.observe(agent('a', phase=phase), 1001)['state'],
                       expected)
    self.assertEqual(self.mod.observe(agent('a', phase='thinking'), 2500)[
      'state'], 'THINKING')
    self.assertTrue(self.mod.observe(agent('a', phase='thinking'), 2500)[
      'uncertain'])
    self.assertEqual(self.mod.observe({'status': {'state': 'waiting'}}, 1001)[
      'state'], 'UNKNOWN')

  def test_wait_target_ready_and_timeout_do_not_fake_running(self):
    wait = self.mod.wait_spec('wait', {'ids': ['child'], 'timeout_ms': 10000})
    pending = [{'id': 'call', 'at': 1000, 'tool': 'wait', 'wait': wait}]
    parent = agent('root', phase='active', pending=pending)
    rows = [parent, agent('child', 'root', 'done')]
    tree = self.mod.AgentTree(rows, 1001)
    self.assertEqual(tree.observed['codex:root']['state'], 'RESULT READY')
    tree = self.mod.AgentTree(rows, 1011)
    self.assertEqual(tree.observed['codex:root']['state'], 'WAITING')
    early = agent('child', 'root', 'done', seen=999)
    self.assertEqual(self.mod.AgentTree([parent, early], 1001).observed[
      'codex:root']['state'], 'WAITING')

  def test_tree_summaries_include_grandchildren_and_unknown_coverage(self):
    rows = [agent('root'), agent('a', 'root'),
            agent('b', 'a', 'thinking'), agent('c', 'root', 'done')]
    tree = self.mod.AgentTree(rows, 1001, incomplete=True)
    self.assertEqual(tree.totals('codex:root')['working'], 1)
    self.assertEqual(tree.totals('codex:root')['idle'], 2)
    self.assertNotIn('coverage unknown', tree.summary('codex:root'))
    self.assertNotIn('+', tree.summary('codex:root'))
    self.assertEqual(len(tree.order), 4)
    rows[0]['parent_id'] = 'b'
    self.assertEqual(len(self.mod.AgentTree(rows, 1001).order), 4)

  def test_terminal_history_and_unknown_timestamps(self):
    for phase, label in [('ended', 'Idle'), ('done', 'Idle'),
                         ('aborted', 'Stopped')]:
      obs = self.mod.observe(agent('a', phase=phase), 8200)
      self.assertFalse(obs['uncertain'])
      self.assertEqual(self.mod.activity_label(obs, 8200), label + ' · 2h ago')
    for at in (None, float('nan'), float('inf'), True, 9000):
      obs = self.mod.observe(agent('a', seen=at), 8200)
      self.assertEqual(self.mod.activity_label(obs, 8200), 'Unknown')

  def test_expired_wait_is_uncertain_and_result_clears_it(self):
    tracker = self.mod.Tracker()
    tracker.call('wait', {'ids': ['child'], 'timeout_ms': 10000}, 'w', 1000)
    row = {'observation': tracker.value()}
    obs = self.mod.observe(row, 1011)
    self.assertEqual(self.mod.activity_label(obs, 1011), 'Waiting? · 11s ago')
    self.assertIn('deadline passed', obs['reason'])
    tracker.result('w', 1012)
    row['observation'] = tracker.value()
    self.assertEqual(self.mod.activity_label(self.mod.observe(row, 1013),
                                           1013), 'Working')

  def test_five_labels_and_summary_preserve_uncertainty(self):
    for phase in ('active', 'thinking', 'tool'):
      obs = self.mod.observe(agent('a', phase=phase), 1001)
      self.assertEqual(self.mod.activity_label(obs, 1001), 'Working')
    rows = [agent('root'), agent('a', 'root', 'thinking'),
            agent('b', 'root', 'thinking', seen=0),
            agent('c', 'root', 'aborted'), agent('d', 'root', 'unknown')]
    tree = self.mod.AgentTree(rows, 1300)
    self.assertEqual(tree.summary('codex:root'),
      '1 working · 1 working? · 1 stopped · 1 unknown')

  def test_incomplete_inventory_marks_only_existing_total(self):
    ui = importlib.import_module('agent_activity_live').AgentView()
    rows = [agent('root'), agent('child', 'root'), agent('other')]
    ui.update(rows, 1001)
    complete = ui.frame(160, 12, 1001)
    ui.update(rows, 1001, incomplete=True)
    partial = ui.frame(160, 12, 1001)
    self.assertEqual(len(complete), len(partial))
    self.assertIn('3+ agents', partial[-1][0])
    self.assertNotIn('partial list', partial[0][0])
    self.assertNotIn('coverage unknown', str(partial))
    self.assertNotIn('+', ui.tree.summary('codex:root'))
    self.assertIn('incomplete', ' '.join(ui.detail(1001)))

  def test_missing_parent_explained_without_marking_unrelated_family(self):
    ui = importlib.import_module('agent_activity_live').AgentView()
    ui.update([agent('root'), agent('child', 'root'),
               agent('orphan', 'absent')], 1001)
    self.assertIn('3+ agents', ui.frame(160, 12, 1001)[-1][0])
    self.assertNotIn('+', ui.tree.summary('codex:root'))
    ui.selected = 'codex:orphan'
    self.assertIn('parent', ' '.join(ui.detail(1001)))

  def test_plain_table_uses_same_status_conventions(self):
    row = agent('a', phase='thinking')
    row['observed_activity'] = self.mod.observe(row, 2500)
    self.assertEqual(AGENTS.state_text(row, 'stalled', False,
                                     fixtures.AGENT_QUOTA),
                     'Working? · 25m ago')

  def test_grouped_internal_sessions_keep_all_discovery_evidence(self):
    rows = [agent('lead'), agent('member')]
    for row in rows:
      row.update(internal=True, label='reviewer', state='done')
    rows[1].update(coverage_incomplete=True,
                  coverage_reasons=['Some transcript evidence could not be read'])
    grouped = AGENTS.group_internal(rows)
    ui = importlib.import_module('agent_activity_live').AgentView()
    ui.update(grouped, 1001)
    self.assertTrue(ui.tree.incomplete)
    self.assertIn('1+ agents', ui.frame(160, 12, 1001)[-1][0])
    self.assertIn('Some transcript evidence could not be read',
                  ' '.join(ui.detail(1001)))

  def test_expired_wait_details_keep_tool_and_timeout(self):
    tracker = self.mod.Tracker()
    tracker.call('collaboration.wait_agent', {'timeout_ms': 10000}, 'w', 1000)
    ui = importlib.import_module('agent_activity_live').AgentView()
    row = agent('root')
    row['observation'] = tracker.value()
    ui.update([row], 1011)
    details = ' '.join(ui.detail(1011))
    self.assertIn('Waiting? · 11s ago', details)
    self.assertIn('collaboration.wait_agent', details)
    self.assertIn('timeout 10s', details)
    self.assertIn('1970-01-01T00:16:40+00:00', details)

  def test_observed_status_compaction_fits_narrow_plain_table(self):
    from unittest.mock import patch
    rows = fixtures.AgentViewTest().ladder_agents()
    for row, phase in zip(rows, ('thinking', 'ended', 'aborted')):
      row['observed_activity'] = self.mod.observe(agent('a', phase=phase), 5000)
    for columns in range(60, 201):
      with patch.object(AGENTS, 'display_width', return_value=columns):
        output = AGENTS.render(rows, {'sessions': {}},
                               fixtures.AGENT_QUOTA, 5000)
      self.assertTrue(all(len(line) <= columns for line in output.splitlines()),
                      (columns, output))
    self.assertIn('▸? 1h', AGENTS.state_text(rows[0], 'stalled', True,
                                          fixtures.AGENT_QUOTA))

  def test_live_default_limit_is_100_and_explicit_limit_wins(self):
    from unittest.mock import patch
    quota = fixtures.AGENT_QUOTA
    for extra, expected in (([], 100), (['--agent-limit', '3'], 3)):
      with patch.object(quota.sys.stdout, 'isatty', return_value=True), \
           patch.object(quota.sys.stdin, 'isatty', return_value=True), \
           patch.object(quota, 'run_live', return_value=0) as run:
        self.assertEqual(quota.main(['--agents', '--live'] + extra), 0)
        self.assertEqual(run.call_args.args[0].agent_limit, expected)

  def test_session_titles_are_recorded_and_shown_with_ids(self):
    with tempfile.TemporaryDirectory() as directory:
      root = Path(directory)
      index = root / 'session_index.jsonl'
      index.write_text('{"id":"root","thread_name":"Old"}\n'
                       'invalid\n'
                       '{"id":"root","thread_name":"Build overview"}\n')
      self.assertEqual(AGENTS.session_titles(root / 'sessions'),
                       {'root': 'Build overview'})
      path = root / 'example.jsonl'
      path.write_text(json.dumps({'type': 'custom-title',
        'sessionId': 'example', 'customTitle': 'Review overview'}) + '\n' +
        json.dumps({'type': 'ai-title', 'sessionId': 'example',
                    'aiTitle': 'Lower priority title'}) + '\n' +
        json.dumps({'type': 'custom-title', 'sessionId': 'other',
                    'customTitle': 'Unrelated title'}) + '\n')
      parsed = AGENTS.parse_session(path, 'claude')
      self.assertEqual(parsed['session_title'], 'Review overview')
    row = agent('root')
    row['session_title'] = 'Build overview'
    ui = importlib.import_module('agent_activity_live').AgentView()
    ui.update([row, agent('child', 'root', 'thinking')], 1001)
    text = str(ui.frame(120, 15, 1001))
    self.assertIn('Build overview', text)
    self.assertIn('co:root', text)
    self.assertIn('1 working', text)

  def test_title_first_columns_keep_tree_and_unicode_alignment(self):
    ui = importlib.import_module('agent_activity_live').AgentView()
    root = agent('root')
    root['session_title'] = '猫 Build overview'
    child = agent('child', 'root', 'thinking')
    child['session_title'] = 'Review work'
    ui.update([root, child], 1001)
    ui.key('RIGHT', 10)
    rows = [t for t, _ in ui.frame(120, 15, 1001) if ' │ ' in t]
    self.assertEqual(len(rows), 2)
    from agent_records_live import cells
    self.assertEqual(cells(rows[0].split(' │ ')[0]),
                     cells(rows[1].split(' │ ')[0]))
    self.assertIn('猫 Build overview', rows[0].split(' │ ')[0])
    self.assertIn('co:root', rows[0].split(' │ ')[1])
    self.assertIn('Idle', rows[0].split(' │ ')[1])
    self.assertIn('1 working', rows[0].split(' │ ')[1])
    self.assertIn('└─', rows[1].split(' │ ')[0])
    for width in (1, 25, 60, 120):
      self.assertTrue(all(cells(t) < width for t, _ in ui.frame(width, 15, 1001)))

  def test_mouse_selection_group_folding_and_detail_scrolling(self):
    ui = importlib.import_module('agent_activity_live').AgentView()
    ui.update([agent('root'), agent('child', 'root', 'thinking')], 1001)
    ui.frame(120, 15, 1001)
    self.assertTrue(ui.frame(120, 15, 1001)[0][0].startswith('Agents ·'))
    self.assertTrue(ui.frame(120, 15, 1001)[1][0].startswith('> ▸ '))
    ui.mouse('click', 2, 1, 10)
    self.assertEqual(len(ui.visible()), 2)
    ui.frame(120, 15, 1001)
    ui.mouse('click', 10, 2, 10)
    self.assertEqual(ui.selected, 'codex:child')
    ui.mouse('up', 10, 2, 10)
    self.assertEqual(ui.selected, 'codex:root')
    ui.key('\r', 10)
    ui.frame(120, 15, 1001)
    ui.mouse('down', 10, ui.mouse_details[0], 10)
    self.assertEqual(ui.offset, 1)
    ui.mode = 'filter'
    ui.mouse('click', 0, 1, 10)
    self.assertEqual(ui.selected, 'codex:root')

  def test_live_limit_preserves_families_and_other_parents(self):
    from types import SimpleNamespace
    rows = [agent('root')] + [agent(str(i), 'root', seen=1100+i)
                             for i in range(25)] + [agent('other', seen=900)]
    for row in rows:
      row['messages'] = []
    cache = {'sessions': {r['key']: {'agent': r} for r in rows},
             'summaries': {}}
    args = SimpleNamespace(cached=True, provider='all', agent_days=1,
                           agent_limit=2, live=True)
    shown = AGENTS.view_agents(cache, args, 1200)
    self.assertEqual(len(shown), 27)
    self.assertIn('codex:other', [a['key'] for a in shown])
    args.live = False
    self.assertEqual(len(AGENTS.view_agents(cache, args, 1200)), 2)

  def test_overview_starts_folded_and_footer_distinguishes_screen(self):
    ui = importlib.import_module('agent_activity_live').AgentView()
    rows = [agent('root'), agent('child', 'root', 'thinking')]
    rows += [agent('root' + str(i)) for i in range(20)]
    ui.update(rows, 1001)
    self.assertEqual(len(ui.visible()), 21)
    frame = str(ui.frame(120, 10, 1001))
    self.assertIn('Rows 1-8/21', frame)
    self.assertIn('21 groups', frame)
    self.assertIn('22 agents', frame)
    ui.key('G', 7)
    self.assertIn('Rows 14-21/21', str(ui.frame(120, 10, 1001)))

  def test_sort_controls_keep_families_selection_folds_and_filter(self):
    ui = importlib.import_module('agent_activity_live').AgentView()
    rows = [agent('z-root', seen=900), agent('z-child', 'z-root', seen=999),
            agent('a-child', 'z-root', seen=998), agent('a-root', seen=990)]
    rows[0]['session_title'] = 'Zulu'
    rows[1]['session_title'] = 'Zulu child'
    rows[2]['session_title'] = 'Alpha child'
    rows[3]['session_title'] = 'Alpha'
    ui.update(rows, 1001)
    ui.key('RIGHT', 10)
    ui.selected, ui.details, ui.offset = 'codex:z-child', True, 2
    ui.key('s', 10)
    self.assertEqual(ui.visible(), ['codex:z-root', 'codex:z-child',
                                  'codex:a-child', 'codex:a-root'])
    ui.key('s', 10)
    self.assertEqual(ui.visible(), ['codex:a-root', 'codex:z-root',
                                  'codex:a-child', 'codex:z-child'])
    self.assertEqual((ui.selected, ui.offset), ('codex:z-child', 2))
    ui.update(list(reversed(rows)), 1002)
    self.assertEqual(ui.visible(), ['codex:a-root', 'codex:z-root',
                                  'codex:a-child', 'codex:z-child'])
    self.assertFalse(ui.collapsed['codex:z-root'])
    ui.frame(120, 24, 1002)
    y = next(y for y, (key, _) in ui.mouse_rows.items()
             if key == 'codex:a-child')
    ui.mouse('click', 10, y, 10)
    self.assertEqual(ui.selected, 'codex:a-child')
    ui.key('/', 10)
    for c in 'Zulu child':
      ui.key(c, 10)
    ui.key('\r', 10)
    self.assertEqual(ui.visible(), ['codex:z-root', 'codex:z-child'])
    ui.key('s', 10)
    self.assertEqual(ui.visible(), ['codex:z-root', 'codex:z-child'])

  def test_updated_sort_ties_missing_times_and_recent_descendant(self):
    ui = importlib.import_module('agent_activity_live').AgentView()
    rows = [agent('z', seen=None), agent('b', seen=999),
            agent('a', seen=999), agent('parent', seen=800),
            agent('child', 'parent', seen=1000)]
    ui.update(rows, 1001)
    ui.key('s', 10)
    self.assertEqual(ui.visible(), ['codex:parent', 'codex:a', 'codex:b',
                                  'codex:z'])
    ui.update(list(reversed(rows)), 1002)
    self.assertEqual(ui.visible(), ['codex:parent', 'codex:a', 'codex:b',
                                  'codex:z'])
    self.assertIn('updated', str(ui.frame(120, 24, 1002)))

  def test_collapsing_preserves_selection_and_explicit_choices(self):
    ui_mod = importlib.import_module('agent_activity_live')
    ui = ui_mod.AgentView()
    rows = [agent('root'), agent('a', 'root', 'thinking'), agent('b', 'root')]
    ui.update(rows, 1001)
    self.assertEqual(len(ui.visible()), 1)
    ui.key(' ', 10)
    self.assertEqual(len(ui.visible()), 3)
    ui.key(' ', 10)
    self.assertEqual(ui.visible(), ['codex:root'])
    ui.update(rows, 1002)
    self.assertEqual(ui.visible(), ['codex:root'])
    ui.key('RIGHT', 10)
    ui.key('RIGHT', 10)
    self.assertEqual(ui.selected, 'codex:a')
    ui.key('LEFT', 10)
    self.assertEqual(ui.selected, 'codex:root')
    ui.key('G', 10)
    self.assertEqual(ui.selected, 'codex:b')
    ui.update(rows + [agent('unrelated', phase='thinking')], 1003)
    self.assertEqual(ui.selected, 'codex:b')

  def test_connectors_width_and_details(self):
    ui_mod = importlib.import_module('agent_activity_live')
    ui = ui_mod.AgentView()
    ui.update([agent('root'), agent('a', 'root', 'thinking'),
               agent('b', 'root', 'tool')], 1001)
    ui.key('RIGHT', 10)
    text = '\n'.join(t for t, _ in ui.frame(100, 24, 1001))
    self.assertIn('├─', text)
    self.assertIn('└─', text)
    self.assertIn('2 working', text)
    ui.key('\n', 10)
    text = '\n'.join(t for t, _ in ui.frame(100, 24, 1001))
    self.assertIn('Tasks', text)
    self.assertIn('Turn ended', '\n'.join(ui.detail(1001)))
    for width in (1, 20, 40, 80):
      frame = ui.frame(width, 12, 1001)
      self.assertLessEqual(len(frame), 12)
      self.assertTrue(all(len(t) < width for t, _ in frame))

  def test_cached_task_waits_are_read_only_and_stale_is_not_zero(self):
    wait = self.mod.wait_spec('exec_command', {
      'cmd': 'agent-task watch T-0012 --until closed'})
    row = agent('root', phase='active', pending=[
      {'id': 'c', 'at': 1000, 'tool': 'exec_command', 'wait': wait}])
    with tempfile.TemporaryDirectory() as directory:
      path = Path(directory) / 'agents.json'
      cache = {'version': 8, 'observed_at': 1000,
               'sessions': {'one': {'agent': row}}}
      path.write_text(json.dumps(cache))
      before = path.read_bytes(), path.stat().st_mtime_ns
      label, waits = self.mod.cached_task_waits('T-0012', 1001, path)
      self.assertEqual(waits[0]['agent'], 'codex:root')
      self.assertEqual(waits[0]['state'], 'WAITING')
      self.assertIn('not guaranteed', label)
      self.assertEqual((path.read_bytes(), path.stat().st_mtime_ns), before)
      label, waits = self.mod.cached_task_waits('T-0012', 3000, path)
      self.assertIn('stale', label)
      self.assertEqual(waits, [])

  def test_frame_ages_observations_without_new_snapshot(self):
    ui = importlib.import_module('agent_activity_live').AgentView()
    ui.update([agent('root', phase='thinking')], 1001)
    self.assertIn('Working', str(ui.frame(100, 20, 1001)))
    self.assertIn('Working? · 33m ago', str(ui.frame(100, 20, 3000)))

  def test_filter_keeps_ancestors_and_idle_defaults_collapse(self):
    ui = importlib.import_module('agent_activity_live').AgentView()
    ui.update([agent('root'), agent('child', 'root')], 1001)
    self.assertEqual(ui.visible(), ['codex:root'])
    ui.key('/', 10)
    for c in 'child':
      ui.key(c, 10)
    self.assertEqual(ui.visible(), ['codex:root', 'codex:child'])
    ui.key('\x1b', 10)
    self.assertEqual(ui.visible(), ['codex:root'])


class ParserObservationTest(unittest.TestCase):
  setUp = fixtures.AgentViewTest.setUp
  transcript = fixtures.AgentViewTest.transcript
  codex_rows = fixtures.AgentViewTest.codex_rows
  claude_rows = fixtures.AgentViewTest.claude_rows
  def test_pending_wait_clears_on_result_and_never_infers_from_prose(self):
    rows = self.codex_rows(None)[:2]
    rows.append({'type': 'response_item', 'timestamp': '2026-08-19T21:01:00Z',
      'payload': {'type': 'function_call', 'name': 'collaboration.wait_agent',
                  'call_id': 'w', 'arguments': '{"timeout_ms":30000}'}})
    parsed = AGENTS.parse_session(self.transcript(rows), 'codex')
    self.assertEqual(parsed['observation']['pending'][0]['wait']['kind'],
                     'agent')
    rows.append({'type': 'response_item', 'timestamp': '2026-08-19T21:01:01Z',
      'payload': {'type': 'function_call_output', 'call_id': 'w',
                  'output': 'timeout'}})
    parsed = AGENTS.parse_session(self.transcript(rows), 'codex')
    self.assertEqual(parsed['observation']['pending'], [])
    self.assertEqual(parsed['observation']['phase'], 'active')

  def test_claude_end_turn_and_reasoning_are_observations(self):
    parsed = AGENTS.parse_session(self.transcript(self.claude_rows(True)),
                                  'claude')
    self.assertEqual(parsed['observation']['phase'], 'ended')
    rows = self.codex_rows(None)[:2]
    rows.append({'type': 'response_item', 'timestamp': '2026-08-19T21:01:00Z',
                 'payload': {'type': 'reasoning', 'summary': []}})
    parsed = AGENTS.parse_session(self.transcript(rows), 'codex')
    self.assertEqual(parsed['observation']['phase'], 'thinking')

  def test_fork_lineage_is_not_a_spawn_and_inherited_waits_are_ignored(self):
    rows = self.codex_rows(None)[:2]
    rows[0]['payload']['forked_from_id'] = 'original'
    rows[0]['payload']['timestamp'] = '2026-08-19T21:02:00Z'
    rows.append({'type': 'response_item', 'timestamp': '2026-08-19T21:01:00Z',
      'payload': {'type': 'function_call', 'name': 'wait', 'call_id': 'w',
                  'arguments': '{"ids":["reviewer"]}'}})
    parsed = AGENTS.parse_session(self.transcript(rows), 'codex')
    self.assertIsNone(parsed['parent_id'])
    self.assertEqual(parsed['observation']['pending'], [])


class TaskDetailsTest(unittest.TestCase):
  setUp = fixtures.AgentViewTest.setUp
  transcript = fixtures.AgentViewTest.transcript
  codex_rows = fixtures.AgentViewTest.codex_rows

  def test_explicit_record_actor_from_calls_not_prose_or_quoted_commands(self):
    from agent_activity import record_actors
    cmd = "agent-task log T-0012 --agent helper-123 'checking'"
    self.assertEqual(record_actors('exec_command', {'cmd': cmd}), {'helper-123'})
    wrapped = ('text((await tools.exec_command({cmd:' + json.dumps(cmd) +
               ',max_output_tokens:100})).output);')
    self.assertEqual(record_actors('exec', wrapped), {'helper-123'})
    for text in ('echo "' + cmd + '"', 'printf x; echo ' + json.dumps(cmd),
                 "cat <<EOF\n" + cmd + "\nEOF", cmd + ' $(dynamic)',
                 "agent-task log T-0012 'example --agent other-123'",
                 'agent-task log T-0012 -- ' + '--agent other-123',
                 "echo ';' agent-task log T-0012 --agent other-123 checking",
                 "false && agent-task log T-0012 --agent other-123 checking"):
      self.assertEqual(record_actors('exec_command', {'cmd': text}), set(), text)
    self.assertEqual(record_actors('exec', 'if (false) { ' + wrapped + ' }'), set())
    self.assertEqual(record_actors('exec', 'const sample = ' + json.dumps(wrapped)),
                     set())
    self.assertEqual(record_actors('exec_command', {'cmd':
      'agent-task --agent parent-123 log T-0012 --agent helper-123 checking'}),
      {'helper-123'})

  def test_parsed_delegate_actor_matches_current_helper_task(self):
    from types import SimpleNamespace
    from unittest.mock import patch
    rows = self.codex_rows(None)[:2]
    rows.append({'type': 'response_item', 'timestamp': '2026-08-19T21:01:00Z',
      'payload': {'type': 'function_call', 'name': 'exec_command',
        'call_id': 'r', 'arguments': json.dumps({'cmd':
          "agent-task log T-0012 --agent helper-123 'checking'"})}})
    parsed = AGENTS.parse_session(self.transcript(rows), 'codex')
    self.assertEqual(parsed['records_ids'], ['helper-123'])
    task = {'id': 'T-0012', 'title': 'Validate the boundary',
            'status': 'in-review', 'role': 'helper'}
    args = SimpleNamespace(cached=False, provider='all', agent_days=1,
                           agent_limit=20, live=True)
    now = parsed['last_seen'] + 1
    with patch.object(AGENTS, 'claimed_tasks', return_value={'helper-123':[task]}):
      shown = AGENTS.view_agents({'sessions': {'s': {'agent': parsed}},
                                 'summaries': {}}, args, now)
    self.assertEqual(shown[0]['tasks'][0]['id'], 'T-0012')
    self.assertEqual(shown[0]['tasks'][0]['role'], 'helper')
    ui = importlib.import_module('agent_activity_live').AgentView()
    ui.update(shown, now)
    details = '\n'.join(ui.detail(now))
    self.assertIn('Validate the boundary', details)
    self.assertIn('helper', details)
    self.assertIn('in-review', details)
    self.assertLess(details.index('T-0012'), details.index('Activity'))
    args.cached = True
    with patch.object(AGENTS, 'claimed_tasks') as lookup:
      cached = AGENTS.view_agents({'sessions': {'s': {'agent': parsed}},
                                  'summaries': {}}, args, now)
    lookup.assert_not_called()
    self.assertIn('skipped', cached[0]['tasks_status'])

  def test_inherited_history_does_not_assign_parent_records_identity(self):
    rows = self.codex_rows(None)[:2]
    rows[0]['payload']['timestamp'] = '2026-08-19T21:02:00Z'
    rows[0]['payload']['forked_from_id'] = 'original'
    rows.append({'type': 'response_item', 'timestamp': '2026-08-19T21:01:00Z',
      'payload': {'type': 'function_call', 'name': 'exec_command',
        'call_id': 'r', 'arguments': json.dumps({'cmd':
          "agent-task log T-0012 --agent parent-123 'checking'"})}})
    parsed = AGENTS.parse_session(self.transcript(rows), 'codex')
    self.assertEqual(parsed['records_ids'], [])

  def test_task_association_deduplicates_and_preserves_session_status(self):
    row = agent('root')
    task = dict(id='T-0012', title='Example task', status='in-review', role='helper')
    row['records_ids'] = ['helper-123', 'helper-456']
    original = row['status']
    AGENTS.associate_tasks(row, {'helper-123': [task], 'helper-456': [task]})
    self.assertEqual(len(row['tasks']), 1)
    self.assertIs(row['status'], original)
    other = agent('child')
    other.update(internal=True, label='reviewers', tasks=[task])
    row.update(internal=True, label='reviewers')
    grouped = AGENTS.group_internal([row,other])
    self.assertIn('T-0012', [t['id'] for t in grouped[0]['tasks']])

  def test_details_group_fields_format_numbers_and_wrap_commands(self):
    from agent_records_live import cells
    ui = importlib.import_module('agent_activity_live').AgentView()
    row = agent('root', phase='active')
    row.update(tasks=[dict(id='T-0012', title='Validate the boundary',
                          status='in-progress', role='owner')],
               now='exec: first command\nsecond command ' + 'x' * 200,
               tokens={'total': 9717445}, recent_tokens=41948)
    ui.update([row], 1001)
    details = '\n'.join(ui.detail(1001))
    self.assertIn('9,717,445', details)
    self.assertIn('41,948', details)
    self.assertIn('Tasks', details)
    self.assertIn('Session', details)
    self.assertNotIn('Recorded plan:', details)
    ui.key('\n', 8)
    frame = ui.frame(70, 18, 1001)
    self.assertTrue(any(t == 'Tasks' and style == 'bold' for t,style in frame))
    for _ in range(150):
      ui.key(']', 8)
      self.assertTrue(all(cells(t) < 70 for t,_ in ui.frame(70,18,1001)))
    self.assertEqual(ui.selected, 'codex:root')

  def test_lookup_failure_is_not_reported_as_no_tasks(self):
    from types import SimpleNamespace
    from unittest.mock import patch
    row = agent('root'); row['messages'] = []
    args = SimpleNamespace(cached=False, provider='all', agent_days=1,
                           agent_limit=20, live=True)
    with patch.object(AGENTS, 'claimed_tasks', return_value=None):
      shown = AGENTS.view_agents({'sessions': {'s': {'agent': row}},
                                 'summaries': {}}, args, 1001)
    ui = importlib.import_module('agent_activity_live').AgentView()
    ui.update(shown, 1001)
    self.assertIn('unavailable', '\n'.join(ui.detail(1001)))



class TerminalTest(unittest.TestCase):
  def test_actual_cached_cli_tree_and_piped_fallback(self):
    import fcntl, pty, select, struct, subprocess, termios
    from datetime import datetime, timezone
    with tempfile.TemporaryDirectory() as directory:
      root = Path(directory)
      now = time.time()
      stamp = datetime.now(timezone.utc).isoformat()
      records = {}
      for ident, parent, kind in [('root', None, 'task_complete'),
                                  ('child', 'root', 'task_started')]:
        payload = {'id': ident, 'timestamp': stamp, 'cwd': '/example'}
        if parent:
          payload['source'] = {'subagent': {'thread_spawn': {
            'parent_thread_id': parent}}}
        rows = [{'type': 'session_meta', 'timestamp': stamp,
                 'payload': payload},
                {'type': 'event_msg', 'timestamp': stamp,
                 'payload': {'type': kind}}]
        path = root / (ident + '.jsonl')
        path.write_text(''.join(json.dumps(r) + '\n' for r in rows))
        records[str(path)] = {'agent': AGENTS.parse_session(path, 'codex')}
      cache = root / 'agents.json'
      cache.write_text(json.dumps({'version': 8, 'sessions': records,
        'summaries': {}, 'observed_at': now}))
      cmd = [str(Path(__file__).parents[1] / 'bin/agent-quota'),
        '--agents', '--live', '--cached', '--no-summaries',
        '--cache-file', str(root / 'quota.json'), '--agent-cache-file',
        str(cache)]
      env = dict(os.environ, TERM='xterm-256color',
                 PYTHONDONTWRITEBYTECODE='1')
      result = subprocess.run(cmd, capture_output=True, env=env, timeout=10)
      self.assertEqual(result.returncode, 0, result.stderr)
      self.assertIn(b'Working', result.stdout)
      master, slave = pty.openpty()
      try:
        fcntl.ioctl(slave, termios.TIOCSWINSZ,
                    struct.pack('HHHH', 24, 110, 0, 0))
        proc = subprocess.Popen(cmd, stdin=slave, stdout=slave, stderr=slave,
                                env=env)
        try:
          output, deadline = b'', time.monotonic() + 10
          while time.monotonic() < deadline:
            if select.select([master], [], [], .05)[0]:
              output += os.read(master, 65536)
            if b'1 working' in output or proc.poll() is not None:
              break
          self.assertIn(b'1 working', output)
          os.write(master, b'\r')
          time.sleep(.1)
          os.write(master, b'q')
          deadline = time.monotonic() + 3
          while proc.poll() is None and time.monotonic() < deadline:
            if select.select([master], [], [], .05)[0]:
              os.read(master, 65536)
          proc.wait(timeout=1)
          self.assertEqual(proc.returncode, 0)
        finally:
          if proc.poll() is None:
            proc.kill()
            proc.wait()
      finally:
        os.close(master)
        os.close(slave)

  def test_tree_keys_resize_signals_and_slow_refresh(self):
    import fcntl
    import pty
    import select
    import signal
    import struct
    import subprocess
    import termios
    binary_dir = str(Path(__file__).parents[1] / 'bin')
    script = '''
import sys, time
from pathlib import Path
from types import SimpleNamespace as NS
sys.path.insert(0, sys.argv[1])
from agent_activity_live import run_agent_live
calls = 0
def loader():
  global calls
  calls += 1
  if calls > 1:
    time.sleep(20)
  at = time.time()
  agents = [dict(key='codex:'+name, provider='codex', id=name,
    parent_id=parent, work='Example work', observation=dict(
      phase=phase, at=at, pending=[]))
    for name,parent,phase in [('root',None,'ended'),
                              ('child','root','thinking')]]
  return dict(agents=agents,cache={},command=None), {}
module = NS(group_internal=lambda a:a, live_header=lambda *a:['Provider quota'])
quota = NS(agents_module=lambda:module, LIVE_INTERVAL=.1)
args = NS(interval=.1, notify=False, verbose=False, color_on=False)
run_agent_live(args, Path('/unused'), quota, loader)
'''
    for sig in (None, signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
      with self.subTest(signal=sig):
        master, slave = pty.openpty()
        try:
          saved = termios.tcgetattr(slave)
          fcntl.ioctl(slave, termios.TIOCSWINSZ,
                      struct.pack('HHHH', 24, 100, 0, 0))
          proc = subprocess.Popen([sys.executable, '-c', script, binary_dir],
            stdin=slave, stdout=slave, stderr=slave,
            env=dict(os.environ, TERM='xterm-256color',
                     PYTHONDONTWRITEBYTECODE='1'))
          def until(text):
            output, deadline = b'', time.monotonic() + 5
            while time.monotonic() < deadline:
              if select.select([master], [], [], .05)[0]:
                output += os.read(master, 65536)
                if text in output:
                  return output
              if proc.poll() is not None:
                break
            self.fail(repr(output))
          try:
            initial = until(b'1 working')
            self.assertLess(initial.index(b'Agents'), initial.index(b'Provider quota'))
            def mouse(button, x, y):
              return (f'\x1b[<{button};{x+1};{y+1}M'.encode()
                      if b'1006' in initial else
                      b'\x1b[M' + bytes((button+32, x+33, y+33)))
            os.write(master, b's')
            until(b'updated')
            os.write(master, mouse(0, 2, 2))
            until(b'Working')
            os.write(master, mouse(65, 9, 3) + b'\r')
            until(b'Tasks')
            os.write(master, b']' * 12)
            until(b'Reasoning event observed')
            os.write(master, b'\x1b')
            until(b'Rows')
            os.write(master, mouse(64, 9, 2))
            time.sleep(.2)  # Collector is now slow; keys must still respond.
            os.write(master, b'?')
            until(b'Agent tree')
            os.write(master, b'\x1b')
            until(b'Working')
            # Select the root explicitly after mouse/help activity; folding
            # must not depend on a wheel report's timing in the full suite.
            os.write(master, b'gg ')
            until(b'Rows 1-1/1')
            os.write(master, b'\x1bOC')
            until(b'Working')
            fcntl.ioctl(slave, termios.TIOCSWINSZ,
                        struct.pack('HHHH', 12, 40, 0, 0))
            proc.send_signal(signal.SIGWINCH)
            started = time.monotonic()
            if sig:
              proc.send_signal(sig)
            else:
              os.write(master, b'ZZ')
            while proc.poll() is None and time.monotonic() - started < 3:
              if select.select([master], [], [], .05)[0]:
                os.read(master, 65536)
            proc.wait(timeout=1)
            self.assertEqual(proc.returncode, 0)
            restored = termios.tcgetattr(slave)
            for attrs in (saved, restored):
              attrs[3] &= ~getattr(termios, 'PENDIN', 0)
            self.assertEqual(restored, saved)
          finally:
            if proc.poll() is None:
              proc.kill()
              proc.wait()
        finally:
          os.close(master)
          os.close(slave)
