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
      'state'], 'UNKNOWN')
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
    self.assertEqual(tree.observed['codex:root']['state'], 'UNKNOWN')
    early = agent('child', 'root', 'done', seen=999)
    self.assertEqual(self.mod.AgentTree([parent, early], 1001).observed[
      'codex:root']['state'], 'WAITING')

  def test_tree_summaries_include_grandchildren_and_unknown_coverage(self):
    rows = [agent('root'), agent('a', 'root'),
            agent('b', 'a', 'thinking'), agent('c', 'root', 'done')]
    tree = self.mod.AgentTree(rows, 1001, incomplete=True)
    self.assertEqual(tree.totals('codex:root')['active'], 1)
    self.assertEqual(tree.totals('codex:root')['idle'], 1)
    self.assertEqual(tree.totals('codex:root')['done'], 1)
    self.assertIn('coverage unknown', tree.summary('codex:root'))
    self.assertEqual(len(tree.order), 4)
    rows[0]['parent_id'] = 'b'
    self.assertEqual(len(self.mod.AgentTree(rows, 1001).order), 4)

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
    self.assertIn('1 active', text)

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
    self.assertIn('IDLE', rows[0].split(' │ ')[1])
    self.assertIn('1 active', rows[0].split(' │ ')[1])
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
    self.assertIn('2 active', text)
    ui.key('\n', 10)
    text = '\n'.join(t for t, _ in ui.frame(100, 24, 1001))
    self.assertIn('Turn ended', text)
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
    self.assertIn('THINKING', str(ui.frame(100, 20, 1001)))
    self.assertIn('UNKNOWN', str(ui.frame(100, 20, 3000)))

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
      self.assertIn(b'active', result.stdout)
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
            if b'1 active' in output or proc.poll() is not None:
              break
          self.assertIn(b'1 active', output)
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
            initial = until(b'1 active')
            self.assertLess(initial.index(b'Agents'), initial.index(b'Provider quota'))
            def mouse(button, x, y):
              return (f'\x1b[<{button};{x+1};{y+1}M'.encode()
                      if b'1006' in initial else
                      b'\x1b[M' + bytes((button+32, x+33, y+33)))
            os.write(master, mouse(0, 2, 2))
            until(b'THINKING')
            os.write(master, mouse(65, 9, 3) + b'\r')
            until(b'Reasoning event observed')
            os.write(master, b'\x1b')
            until(b'Rows')
            os.write(master, mouse(64, 9, 2))
            time.sleep(.2)  # Collector is now slow; keys must still respond.
            os.write(master, b'?')
            until(b'Agent tree')
            os.write(master, b'\x1b')
            until(b'THINKING')
            # Select the root explicitly after mouse/help activity; folding
            # must not depend on a wheel report's timing in the full suite.
            os.write(master, b'gg ')
            until(b'Rows 1-1/1')
            os.write(master, b'\x1bOC')
            until(b'THINKING')
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
