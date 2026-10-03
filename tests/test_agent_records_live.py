"""Read-only live collection, navigation and display contracts."""
import copy
from datetime import datetime, timedelta, timezone
import importlib
import os
import select
import signal
import struct
import subprocess
import sys
import time
import unittest

from .agent_records_support import BIN, RecordsFixture

sys.path.insert(0, str(BIN))
NOW = datetime(2026, 1, 1, 12, tzinfo=timezone.utc)


def record(ident, status='open', **fields):
  return {'fields': dict(id=ident, title='Example task ' + ident,
    status=status, owner='none', priority='P2', repos='example',
    created='2026-01-01 11:00:00 +0000 by reader', closed='',
    expires='', review='n/a', **fields),
    'body': '## Done when\n\n- [ ] Test\n\n## Log\n'}


class LiveModelTest(unittest.TestCase):
  def setUp(self):
    self.live = importlib.import_module('agent_records_live')
    self.state = self.live.LiveState()

  def test_recent_updates_sort_across_status_with_stable_ties(self):
    rows = {i: record(i, status) for i, status in (
      ('T-0004', 'in-progress'), ('T-0003', 'blocked'),
      ('T-0002', 'open'), ('T-0001', 'open'))}
    rows['T-0003']['body'] += '- 2026-01-01 11:59:00 +0000 a -- changed\n'
    rows['T-0002']['body'] += '- 2026-01-01 11:59:00 +0000 a -- changed\n'
    rows['T-0001']['fields']['created'] = ''
    self.state.update(rows, NOW)
    view = self.live.LiveView(self.state)
    self.assertEqual(view.visible(), ['T-0002', 'T-0003', 'T-0004', 'T-0001'])
    self.state.update(dict(reversed(list(rows.items()))), NOW)
    self.assertEqual(view.visible(), ['T-0002', 'T-0003', 'T-0004', 'T-0001'])

  def test_update_sort_uses_absolute_time_and_bad_log_falls_back(self):
    rows = {i: record(i) for i in ('T-0001', 'T-0002', 'T-0003')}
    rows['T-0001']['body'] += '- 2026-01-01 12:30:00 +0100 a -- changed\n'
    rows['T-0002']['body'] += '- 2026-01-01 11:45:00 +0000 a -- changed\n'
    rows['T-0003']['body'] += '- unknown-time a -- legacy record\n'
    self.assertEqual(self.live.last_update(rows['T-0003']),
                     NOW - timedelta(hours=1))
    self.state.update(rows, NOW)
    self.assertEqual(self.live.LiveView(self.state).visible(),
                     ['T-0002', 'T-0001', 'T-0003'])

  def test_sort_control_and_refresh_preserve_identity_details_and_mouse(self):
    rows = {i: record(i, status) for i, status in (
      ('T-0001', 'blocked'), ('T-0002', 'in-progress'), ('T-0003', 'open'))}
    rows['T-0003']['body'] += '- 2026-01-01 11:59:00 +0000 a -- changed\n'
    self.state.update(rows, NOW)
    view = self.live.LiveView(self.state)
    view.selected, view.expanded, view.detail_offset = 'T-0003', True, 2
    view.key('s', 10)
    self.assertEqual(view.visible(), ['T-0002', 'T-0003', 'T-0001'])
    self.assertEqual((view.selected, view.detail_offset), ('T-0003', 2))
    view.key('s', 10)
    self.assertEqual(view.visible(), ['T-0001', 'T-0002', 'T-0003'])
    view.key('s', 10)
    rows = copy.deepcopy(rows)
    rows['T-0001']['body'] += '- 2026-01-01 12:00:01 +0000 a -- changed\n'
    self.state.update(rows, NOW + timedelta(seconds=1))
    self.assertEqual(view.sync()[0], 'T-0001')
    self.assertEqual(view.selected, 'T-0003')
    view.frame(100, 30, NOW + timedelta(seconds=1))
    y = next(y for y, ident in view.mouse_rows.items() if ident == 'T-0001')
    view.mouse('click', 5, y, 10)
    self.assertEqual(view.selected, 'T-0001')
    view.key('/', 10)
    view.key('s', 10)
    self.assertEqual(view.editor, 's')
    view.key('\x1b', 10)
    self.assertIn('updated', str(view.frame(120, 20, NOW)))

  def test_creation_closure_archive_reopen_and_removal(self):
    rows = {'T-0001': record('T-0001')}
    self.state.update(rows, NOW)
    self.assertFalse(self.state.events)
    rows = copy.deepcopy(rows)
    rows['T-0002'] = record('T-0002')
    rows['T-0002']['fields']['created'] = (
      '2026-01-01 12:00:01 +0000 by reader')
    rows['T-0001']['fields'].update(status='done',
      closed='2026-01-01 12:00:01 +0000 by reader -- done: verified')
    self.state.update(rows, NOW + timedelta(seconds=2))
    self.assertEqual([e[1] for e in self.state.events], ['DONE', 'NEW'])
    self.assertEqual(self.state.counts['DONE'], 1)
    self.state.update(rows, NOW + timedelta(seconds=3))
    self.assertEqual(len(self.state.events), 2)
    rows = copy.deepcopy(rows)
    rows['T-0001']['fields'].update(status='open', closed='')
    del rows['T-0002']
    self.state.update(rows, NOW + timedelta(seconds=4))
    self.assertIn('REOPENED', [e[1] for e in self.state.events])
    self.assertIn('REMOVED', [e[1] for e in self.state.events])
    self.assertEqual(self.state.counts['DONE'], 1)

  def test_dependencies_use_archive_and_distinguish_cancelled(self):
    rows = {'T-0001': record('T-0001', 'done'),
            'T-0002': record('T-0002', **{'depends-on': 'T-0001'}),
            'T-0003': record('T-0003', **{'depends-on': 'T-0002'})}
    self.state.update(rows, NOW)
    self.assertEqual(self.live.unmet(rows, 'T-0002'), [])
    self.assertEqual(self.live.downstream(rows, 'T-0001'), {'T-0002', 'T-0003'})
    rows = copy.deepcopy(rows)
    rows['T-0001']['fields']['status'] = 'cancelled'
    self.assertEqual(self.live.unmet(rows, 'T-0002'), ['T-0001'])
    self.state.update(rows, NOW)
    rows = copy.deepcopy(rows)
    rows['T-0001']['fields']['status'] = 'done'
    self.state.update(rows, NOW)
    self.assertIn('UNBLOCKED', [e[1] for e in self.state.events])

  def test_estimate_is_not_eta_and_zero_is_known(self):
    row = record('T-0001', **{'execution-model': 'example-model',
      'estimates': '{"example-model":{"wall-seconds":0}}'})
    self.assertIn('est. 0m', self.live.estimate_label(row['fields']))
    self.assertIn('ETA unknown', self.live.estimate_label(row['fields']))
    row['fields'].pop('execution-model')
    self.assertEqual(self.live.estimate_label(row['fields']), 'ETA unknown')

  def test_created_and_closed_between_polls_is_both_events(self):
    self.state.update({}, NOW)
    row = record('T-0001', 'done')
    row['fields'].update(created='2026-01-01 12:00:01 +0000 by reader',
                          closed='2026-01-01 12:00:02 +0000 by reader')
    self.state.update({'T-0001': row}, NOW + timedelta(seconds=3))
    self.assertEqual([e[1] for e in self.state.events], ['NEW', 'DONE'])

  def test_recent_expiry_and_foreign_dependency_in_filtered_view(self):
    import argparse
    rows = {'T-0001': record('T-0001', 'done'),
            'T-0002': record('T-0002', **{'depends-on': 'T-0001'})}
    rows['T-0001']['fields'].update(repos='outside',
                                    closed='2026-01-01 11:30:00 +0000 by reader')
    args = argparse.Namespace(repo=['example'], status=None, owner=None,
                              unowned=False, archived=False, all=False)
    self.state.update(rows, NOW)
    view = self.live.LiveView(self.state, args)
    self.assertEqual(view.visible(), ['T-0002'])
    view.key('\t', 20)
    self.assertIn('T-0001 [done]', '\n'.join(view.detail(NOW)))
    view.args.repo = []
    self.assertIn('T-0001', view.visible())
    self.state.update(rows, NOW + timedelta(hours=1))
    self.assertNotIn('T-0001', view.visible())

  def test_observed_wait_tab_reports_condition_age_and_unknown_coverage(self):
    from unittest.mock import patch
    self.state.update({'T-0001': record('T-0001')}, NOW)
    view = self.live.LiveView(self.state)
    view.sync()
    for _ in range(3):
      view.key('\t', 20)
    with patch('agent_activity.cached_task_waits', return_value=(
        'Cached local observations; coverage not guaranteed',
        [{'agent': 'codex:example', 'state': 'WAITING', 'condition': 'closed',
          'at': NOW.timestamp() - 30}])):
      detail = '\n'.join(view.detail(NOW))
    self.assertIn('Observed waits', detail)
    self.assertIn('coverage not guaranteed', detail)
    self.assertIn('codex:example [WAITING] until closed', detail)
    self.assertIn('observed 30s ago', detail)

  def test_header_uses_two_rows_and_moves_secondary_health_to_help(self):
    self.state.update({'T-0001': record('T-0001')}, NOW)
    view = self.live.LiveView(self.state)
    frame = view.frame(160, 20, NOW)
    self.assertTrue(frame[0][0].startswith('Agent Tasks · 1 tasks ·'))
    self.assertIn('verified ', frame[0][0])
    self.assertIn('0 working · 0 review · 1 open · 0 blocked', frame[1][0])
    self.assertIn('0 expired · 0 holds', frame[1][0])
    self.assertIn('Est tokens', frame[2][0])
    self.assertTrue(frame[3][0].startswith('> T-0001'))
    self.assertFalse(any('read-only' in t or 'session(all)' in t
                         or 'next-eligible' in t for t, _ in frame))
    view.key('?', 30)
    help_text = '\n'.join(t for t, _ in view.frame(160, 60, NOW))
    self.assertIn('Read-only', help_text)
    self.assertIn('next-eligible', help_text)
    self.assertIn('session(all)', help_text)
    self.assertIn('1 next-eligible', '\n'.join(view.detail(NOW)))

  def test_help_groups_wraps_and_scrolls_without_moving_selection(self):
    from agent_activity_live import AgentView
    self.state.update({'T-0001': record('T-0001')}, NOW)
    for view, now in ((self.live.LiveView(self.state), NOW), (AgentView(), 1000)):
      view.key('?', 10)
      selected = view.selected
      frame = view.frame(50, 10, now)
      self.assertIn('Help', frame[0][0])
      self.assertTrue(any(t == 'Navigation' and s == 'bold' for t, s in frame))
      self.assertIn('scroll', frame[-1][0])
      for _ in range(80):
        view.key('j', 10)
      end = view.frame(50, 10, now)
      self.assertNotEqual(frame, end)
      self.assertEqual(view.selected, selected)
      self.assertTrue(all(self.live.cells(t) <= 49 for t, _ in end))
      view.key('g', 10)
      view.key('g', 10)
      self.assertEqual(view.frame(50, 10, now), frame)
      for width, height in ((20, 6), (3, 3), (1, 1)):
        narrow = view.frame(width, height, now)
        self.assertLessEqual(len(narrow), height)
        self.assertTrue(all(self.live.cells(t) <= width - 1
                            for t, _ in narrow))
      view.key('?', 10)
      self.assertEqual(view.mode, 'normal')

  def test_selected_block_scrolls_into_view_with_update_and_colours(self):
    rows = {f'T-{i:04d}': record(f'T-{i:04d}') for i in range(1, 31)}
    rows['T-0011']['fields']['status'] = 'blocked'
    self.state.update(rows, NOW)
    view = self.live.LiveView(self.state)
    view.selected = view.visible()[20]
    frame = view.frame(120, 17, NOW)
    selected = [text for text, style in frame if 'selected' in style]
    self.assertEqual(len(selected), 1)
    self.assertFalse(any('ETA' in line for line, _ in frame))
    self.assertIn('1h ago', selected[-1])
    self.assertTrue(any('open' in style for _, style in frame))
    view.selected = 'T-0011'
    frame = view.frame(120, 17, NOW)
    self.assertTrue(any(style == 'selected' for _, style in frame))
    rows['T-0011']['fields']['title'] = 'Long title ' * 30
    self.state.update(rows, NOW)
    selected = [text for text, style in view.frame(55, 20, NOW)
                if 'selected' in style]
    self.assertEqual(len(selected), 3)
    self.assertNotIn('Updated', selected[-1])

  def test_updated_column_uses_age_and_fullscreen_details(self):
    self.assertEqual(self.live.updated_label(NOW, NOW), '0s ago')
    self.assertEqual(self.live.updated_label(NOW - timedelta(minutes=5), NOW),
                     '5m ago')
    self.assertEqual(self.live.updated_label(NOW - timedelta(hours=13), NOW),
                     '13h ago')
    yesterday = NOW - timedelta(days=1)
    self.assertEqual(self.live.updated_label(yesterday, NOW),
                     '1d ago')
    self.assertEqual(self.live.updated_label(None, NOW), 'unknown')
    self.state.update({'T-0001': record('T-0001')}, NOW)
    view = self.live.LiveView(self.state)
    wide = '\n'.join(t for t, _ in view.frame(120, 24, NOW))
    self.assertTrue(any(line.endswith('Updated') for line in wide.splitlines()))
    self.state.rows['T-0001']['fields']['title'] = '猫 title'
    frame = view.frame(120, 24, NOW)
    first = next(t for t, _ in frame if t.startswith('> '))
    stamp = self.live.updated_label(last_update := NOW - timedelta(hours=1), NOW)
    self.assertEqual(self.live.cells(first[:first.rfind(stamp)]), 109)
    view.key('f', 20)
    self.assertTrue(view.full_details)
    frame = view.frame(120, 24, NOW)
    self.assertIn('full-screen', frame[0][0])
    self.assertIn('Updated:', '\n'.join(t for t, _ in frame))
    self.assertNotIn('tasks ·', frame[-1][0])
    self.assertIn('2026-01-01', '\n'.join(t for t, _ in frame))
    view.key('\n', 20)
    self.assertEqual(view.detail_offset, 1)
    view.key('\x0b', 20)
    self.assertEqual(view.detail_offset, 0)
    view.key(']', 20)
    view.frame(40, 8, NOW)
    view.key('\x1b', 20)
    self.assertFalse(view.full_details)
    self.assertFalse(view.expanded)

  def test_estimate_columns_use_selected_model_and_preserve_zero(self):
    fields = {'storypoints': '3', 'execution-model': 'chosen',
      'estimates': '{"chosen":{"tokens":120000,"wall-seconds":4800},'
                   '"other":{"tokens":999999,"wall-seconds":1}}'}
    self.assertEqual(self.live.estimate_cells(fields), ('3', '120k', '1h20m'))
    fields['execution-model'] = 'missing'
    self.assertEqual(self.live.estimate_cells(fields), ('3', '—', '—'))
    fields['estimates'] = '{"missing":{"tokens":0,"wall-seconds":0}}'
    self.assertEqual(self.live.estimate_cells(fields), ('3', '0', '0m'))
    self.state.update({'T-0001': record('T-0001', **fields)}, NOW)
    view = self.live.LiveView(self.state)
    frame = view.frame(140, 24, NOW)
    header = next(t for t, _ in frame if 'Est tokens' in t)
    row = next(t for t, _ in frame if t.startswith('> T-0001'))
    self.assertLess(header.index('SP'), header.index('Est tokens'))
    self.assertLess(header.index('Est tokens'), header.index('Est time'))
    self.assertLess(row.index('0m'), row.index('[open]'))

  def test_mouse_targets_rows_and_details_and_ignores_help(self):
    self.state.update({f'T-{i:04d}': record(f'T-{i:04d}')
                       for i in range(1, 5)}, NOW)
    view = self.live.LiveView(self.state)
    view.frame(120, 30, NOW)
    frame = view.frame(120, 30, NOW)
    self.assertTrue(frame[0][0].startswith('Agent Tasks ·'))
    self.assertTrue(any(text.startswith('> ') and style == 'selected'
                        for text, style in frame))
    y = next(y for y, ident in view.mouse_rows.items() if ident == 'T-0003')
    view.mouse('click', 5, y, 20)
    self.assertEqual(view.selected, 'T-0003')
    view.mouse('up', 5, y, 20)
    self.assertEqual(view.selected, 'T-0002')
    view.key('f', 20)
    view.frame(80, 8, NOW)
    view.mouse('down', 5, 3, 20)
    self.assertEqual(view.detail_offset, 1)
    view.mode = 'help'
    view.mouse('down', 5, 3, 20)
    self.assertEqual(view.detail_offset, 1)

  def test_selection_follows_id_filter_and_vim_keys(self):
    rows = {f'T-{i:04d}': record(f'T-{i:04d}') for i in range(1, 31)}
    self.state.update(rows, NOW)
    view = self.live.LiveView(self.state)
    view.key('G', 10)
    self.assertEqual(view.selected, 'T-0030')
    view.key('g', 10)
    view.key('g', 10)
    self.assertEqual(view.selected, 'T-0001')
    view.key('\x04', 10)
    self.assertEqual(view.selected, 'T-0006')
    view.key('/', 10)
    for char in 'T-0030':
      view.key(char, 10)
    view.key('\r', 10)
    self.assertEqual(view.selected, 'T-0030')
    view.key('/', 10)
    view.key('\x1b', 10)
    self.assertEqual(view.filter, '')
    self.assertEqual(view.selected, 'T-0030')
    view.key('?', 10)
    self.assertEqual(view.mode, 'help')
    view.key('\x1b', 10)
    self.assertEqual(view.mode, 'normal')
    view.key('Z', 10)
    self.assertTrue(view.key('Z', 10))

  def test_unrelated_changes_do_not_steal_selection_or_fake_progress(self):
    rows = {'T-0001': record('T-0001'), 'T-0002': record('T-0002')}
    self.state.update(rows, NOW)
    view = self.live.LiveView(self.state)
    view.key('j', 10)
    rows = copy.deepcopy(rows)
    rows['T-0001']['body'] += '- 2026-01-01 12:00:01 +0000 a -- update\n'
    self.state.update(rows, NOW + timedelta(seconds=1))
    view.sync()
    self.assertEqual(view.selected, 'T-0002')
    self.assertEqual(self.state.counts['DONE'], 0)
    self.assertIn('UPDATED', [e[1] for e in self.state.events])

  def test_rewritten_log_and_missing_dependency_are_explicit(self):
    row = record('T-0001', **{'depends-on': 'T-9999'})
    row['body'] += '- 2026-01-01 11:00:00 +0000 a -- first\n'
    self.state.update({'T-0001': row}, NOW)
    changed = copy.deepcopy(row)
    changed['body'] = changed['body'].replace('first', 'replacement')
    self.state.update({'T-0001': changed}, NOW)
    self.assertIn('REWRITTEN', [e[1] for e in self.state.events])
    view = self.live.LiveView(self.state)
    view.key('\r', 20)
    view.key('\t', 20)
    text = '\n'.join(line for line, _ in view.frame(90, 40, NOW))
    self.assertIn('T-9999 [missing]', text)

  def test_width_sanitization_wrapping_and_stale_retention(self):
    row = record('T-0001')
    row['fields']['title'] = 'Wide 界 text ' * 30 + '\x1b]52;c;bad\x07'
    self.state.update({'T-0001': row}, NOW)
    self.state.error = 'locked\x1b[2J'
    view = self.live.LiveView(self.state)
    for width, height in ((80, 24), (40, 12), (8, 3), (1, 1)):
      lines = view.frame(width, height, NOW)
      self.assertLessEqual(len(lines), height)
      for text, _ in lines:
        self.assertLessEqual(self.live.cells(text), max(0, width - 1))
        self.assertNotIn('\x1b', text)
    text = '\n'.join(line for line, _ in view.frame(90, 24, NOW))
    self.assertIn('STALE', text)
    self.assertIn('T-0001', text)


class LiveCommandTest(RecordsFixture):
  def setUp(self):
    super().setUp()
    self.init()
    self.run_cmd('agent-task', '--agent', 'reader', 'new', '--title', 'Example')

  def test_pipe_one_plain_frame_and_no_record_changes(self):
    before = {str(p): p.read_bytes() for root in (self.tasks, self.changes)
              for p in root.rglob('*') if p.is_file()}
    text = self.run_cmd('agent-task', 'list', '--live')
    self.assertIn('T-0001', text)
    self.assertNotIn('ETA unknown', text)
    self.assertNotIn('\x1b', text)
    after = {str(p): p.read_bytes() for root in (self.tasks, self.changes)
             for p in root.rglob('*') if p.is_file()}
    self.assertEqual(before, after)

  def test_invalid_combinations_and_pending_journal(self):
    for args in (('list', '--live', '--json'),
                 ('--unlocked', 'list', '--live'),
                 ('list', '--interval', '2'),
                 ('list', '--live', '--interval', 'nan'),
                 ('list', '--live', '--interval', '0')):
      self.run_cmd('agent-task', *args, code=2)
    journal = self.tasks / '.records-journal.json'
    journal.write_text('{"invalid":true}')
    before = journal.read_bytes()
    self.run_cmd('agent-task', 'list', '--live', code=5)
    self.assertEqual(journal.read_bytes(), before)

  def test_collection_discovers_new_tasks_and_archive(self):
    live = importlib.import_module('agent_records_live')
    first = live.read_snapshot(self.tasks.resolve(), self.changes.resolve(), 0)
    self.run_cmd('agent-task', '--agent', 'reader', 'new', '--title', 'Second')
    self.run_cmd('agent-task', '--agent', 'reader', 'claim', 'T-0001')
    self.run_cmd('agent-task', '--agent', 'reader', 'close', 'T-0001',
                 'cancelled', '--reason', 'No longer needed')
    second = live.read_snapshot(self.tasks.resolve(), self.changes.resolve(), 0)
    self.assertEqual(set(first), {'T-0001'})
    self.assertEqual(set(second), {'T-0001', 'T-0002'})
    self.assertEqual(second['T-0001']['fields']['status'], 'cancelled')

  def test_lock_contention_is_read_only_and_bounded(self):
    import fcntl
    live = importlib.import_module('agent_records_live')
    with (self.tasks / '.records.lock').open('rb') as lock:
      fcntl.flock(lock, fcntl.LOCK_EX)
      started = time.monotonic()
      with self.assertRaisesRegex(Exception, 'lock held'):
        live.read_snapshot(self.tasks.resolve(), self.changes.resolve(), .01)
      self.assertLess(time.monotonic() - started, 1)

  def test_terminal_keys_resize_and_signal_restore(self):
    import fcntl
    import pty
    import termios
    self.run_cmd('agent-task', '--agent', 'reader', 'new',
                 '--title', 'Mouse second task')
    for exit_signal in (None, signal.SIGTERM, signal.SIGHUP, signal.SIGINT):
      with self.subTest(exit_signal=exit_signal):
        master, slave = pty.openpty()
        try:
          original = termios.tcgetattr(slave)
          fcntl.ioctl(slave, termios.TIOCSWINSZ,
                      struct.pack('HHHH', 24, 80, 0, 0))
          proc = subprocess.Popen([sys.executable, str(BIN / 'agent-task'),
            'list', '--live', '--interval', '.1'], stdin=slave, stdout=slave,
            stderr=slave, env=dict(self.env, TERM='xterm-256color'))
          def until(expected):
            output = b''
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
              if select.select([master], [], [], .1)[0]:
                output += os.read(master, 65536)
                if expected in output:
                  return output
              if proc.poll() is not None:
                break
            self.fail(repr(output))
          try:
            initial = until(b'Agent Tasks')
            def mouse(button, x, y):
              return (f'\x1b[<{button};{x+1};{y+1}M'.encode()
                      if b'1006' in initial else
                      b'\x1b[M' + bytes((button+32, x+33, y+33)))
            os.write(master, b's')
            until(b'status')
            os.write(master, mouse(65, 5, 4) + b'\r')
            until(b'Progress')
            os.write(master, b'\x1b')
            until(b'2 tasks')
            os.write(master, mouse(0, 5, 4) + b'\r')
            until(b'Progress')
            os.write(master, b'f')
            until(b'full-screen')
            os.write(master, b'\n\x0b')
            os.write(master, b'\x1b')
            until(b'Agent Tasks')
            os.write(master, b'?')
            until(b'Navigation')
            os.write(master, b'\x1b')
            until(b'verified')
            fcntl.ioctl(slave, termios.TIOCSWINSZ,
                        struct.pack('HHHH', 12, 40, 0, 0))
            proc.send_signal(signal.SIGWINCH)
            if exit_signal:
              proc.send_signal(exit_signal)
            else:
              os.write(master, b'ZZ')
            deadline = time.monotonic() + 5
            while proc.poll() is None and time.monotonic() < deadline:
              if select.select([master], [], [], .05)[0]:
                os.read(master, 65536)
            proc.wait(timeout=1)
            self.assertEqual(proc.returncode, 0)
            restored = termios.tcgetattr(slave)
            # Darwin sets this kernel-owned retype-pending bit when canonical
            # mode returns, even when tcsetattr receives the original flags.
            restored[3] &= ~getattr(termios, 'PENDIN', 0)
            original[3] &= ~getattr(termios, 'PENDIN', 0)
            self.assertEqual(restored, original)
          finally:
            if proc.poll() is None:
              proc.kill()
              proc.wait()
        finally:
          os.close(master)
          os.close(slave)
