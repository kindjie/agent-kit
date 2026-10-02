"""Resumable, read-only multi-task feed contract."""
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch
from .agent_records_support import RecordsFixture


class EventsTest(RecordsFixture):
  def setUp(self):
    super().setUp()
    self.init()
    for title in ('First', 'Second'):
      self.run_cmd('agent-task', '--agent', 'owner', 'new', '--title', title)

  def events(self, *args, code=0):
    return json.loads(self.run_cmd('agent-task', 'events', 'T-0001',
                      'T-0002', *args, code=code))

  def test_resume_coalesces_and_filters(self):
    initial = self.events()
    self.assertEqual(initial['events'], [])
    for ident in ('T-0001', 'T-0002'):
      self.run_cmd('agent-task', '--agent', 'owner', 'log', ident,
                   'act on this', '--to', 'reader')
    result = self.events('--after', json.dumps(initial['cursor']),
                         '--for', 'reader')
    self.assertEqual(len(result['events']), 2)
    self.assertTrue(all('act on this' in e['log'][0]
                        for e in result['events']))
    self.events('--after', json.dumps(result['cursor']),
                '--timeout', '.01', code=4)

  def test_unaddressed_advances_cursor(self):
    initial = self.events()
    self.run_cmd('agent-task', '--agent', 'owner', 'log', 'T-0001', 'noise')
    result = self.events('--after', json.dumps(initial['cursor']),
                         '--for', 'reader', '--timeout', '.01', code=4)
    self.assertNotEqual(initial['cursor'], result['cursor'])
    self.assertEqual(result['events'], [])

  def test_bad_cursor_and_unlocked_refused(self):
    self.run_cmd('agent-task', 'events', 'T-0001', '--after', '{}', code=2)
    self.run_cmd('agent-task', '--unlocked', 'events', 'T-0001', code=2)

  def test_rewritten_log_refused(self):
    initial = self.events()
    path = next(self.tasks.rglob('T-0001*.md'))
    text = path.read_text()
    path.write_text(text.replace(' -- ', ' -- rewritten ', 1))
    self.events('--after', json.dumps(initial['cursor']), code=5)

  def set_expiry(self, value):
    path = next(self.tasks.rglob('T-0001*.md'))
    path.write_text(re.sub(r'^expires:.*$', 'expires: ' + value,
                          path.read_text(), flags=re.MULTILINE))

  def test_actionable_renewal_suppression_and_expiry(self):
    self.run_cmd('agent-task', '--agent', 'owner', 'claim', 'T-0001')
    self.set_expiry('2098-01-01 00:00:00 +0000')
    initial = self.events('--actionable')
    legacy = self.events()
    self.set_expiry('2099-01-01 00:00:00 +0000')
    result = self.events('--actionable', '--after',
                         json.dumps(initial['cursor']), '--timeout', '.01',
                         code=4)
    self.assertEqual(result['events'], [])
    self.assertEqual(len(self.events('--after',
                     json.dumps(legacy['cursor']))['events']), 1)
    self.set_expiry('2000-01-01 00:00:00 +0000')
    expired = self.events('--actionable', '--after',
                          json.dumps(result['cursor']))
    self.assertEqual(len(expired['events']), 1)

  def test_actionable_keeps_messages_and_owner_changes(self):
    initial = self.events('--actionable')
    self.run_cmd('agent-task', '--agent', 'owner', 'log', 'T-0001',
                 'relevant', '--to', 'reader')
    message = self.events('--actionable', '--for', 'reader', '--after',
                          json.dumps(initial['cursor']))
    self.assertIn('relevant', message['events'][0]['log'][0])
    self.run_cmd('agent-task', '--agent', 'owner', 'claim', 'T-0001')
    claimed = self.events('--actionable', '--after',
                          json.dumps(message['cursor']))
    self.assertEqual(claimed['events'][0]['fields']['owner'], 'owner')

  def test_agent_option_after_command_and_literal_separator(self):
    self.run_cmd('agent-task', 'log', 'T-0001', 'hello', '--agent=peer')
    self.run_cmd('agent-task', 'log', '--agent', 'peer', 'T-0001',
                 '--', '--agent literal')
    text = self.run_cmd('agent-task', 'show', 'T-0001')
    self.assertIn('peer -- hello', text)
    self.assertIn('peer -- --agent literal', text)

  def test_actionable_expiry_without_file_change(self):
    self.run_cmd('agent-task', '--agent', 'owner', 'claim', 'T-0001')
    self.set_expiry('2098-01-01 00:00:00 +0000')
    with patch.object(sys, 'path',
                      [str(Path(__file__).resolve().parents[1] / 'bin')]
                      + sys.path):
      from agent_records_events import snapshot
    path = next(self.tasks.rglob('T-0001*.md'))
    before = path.read_bytes()
    with patch('agent_records_tasks.now', return_value=datetime(
        2097, 1, 1, tzinfo=timezone.utc)):
      active = snapshot(self.tasks, ['T-0001'], True)['T-0001'][2]
      legacy = snapshot(self.tasks, ['T-0001'])['T-0001'][2]
    with patch('agent_records_tasks.now', return_value=datetime(
        2098, 1, 1, tzinfo=timezone.utc)):
      expired = snapshot(self.tasks, ['T-0001'], True)['T-0001'][2]
      self.assertEqual(legacy, snapshot(self.tasks, ['T-0001'])['T-0001'][2])
    self.assertNotEqual(active['header'], expired['header'])
    self.assertEqual(active['log'], expired['log'])
    self.assertEqual(path.read_bytes(), before)
