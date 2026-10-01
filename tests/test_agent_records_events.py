"""Resumable, read-only multi-task feed contract."""
import json
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
