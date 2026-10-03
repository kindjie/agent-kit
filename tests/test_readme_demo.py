"""Synthetic documentation inputs must remain coherent and isolated."""
import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).parents[1]
SPEC = importlib.util.spec_from_file_location('readme_demo', ROOT / 'docs/demo/run.py')


class DemoTest(unittest.TestCase):
  @classmethod
  def setUpClass(cls):
    cls.demo = importlib.util.module_from_spec(SPEC)
    SPEC.loader.exec_module(cls.demo)

  def test_dashboard_apps_have_full_width_labelled_boundaries(self):
    text = self.demo.dashboard_capture([
      ('Agents', 'agent body\n'),
      ('Agent Tasks', 'task body\n'),
      ('Timeline', 'timeline body\n')], 30)
    lines = text.splitlines()
    for title, index in [('Agents', 0), ('Agent Tasks', 3), ('Timeline', 6)]:
      self.assertIn(title, lines[index])
      plain = lines[index].replace('\x1b[1m', '').replace('\x1b[0m', '')
      self.assertEqual(len(plain), 30)
      self.assertTrue(plain.startswith('─'))
      self.assertTrue(plain.endswith('─'))
    self.assertEqual([lines[i] for i in (1, 4, 7)],
                     ['agent body', 'task body', 'timeline body'])
    self.assertEqual(lines[2], '')
    self.assertEqual(lines[5], '')

  def test_growing_scenario_is_authored_and_keeps_families(self):
    from unittest.mock import patch
    import os
    with patch.dict(os.environ, {'DEMO_SCENARIO': 'growing'}):
      data = self.demo.fixture()
    self.assertEqual(len(data['agents']), 13)
    self.assertEqual(len(data['quotas']), 4)
    ids = {a['id'] for a in data['agents']}
    self.assertTrue(all(not a.get('parent_id') or a['parent_id'] in ids
                        for a in data['agents']))
    self.assertTrue(all(a['cwd'].startswith('/demo/') for a in data['agents']))
    self.assertEqual({p['account_label'] for p in data['quotas']},
                     {'woodland-demo', 'studio-demo'})

  def test_growing_timeline_includes_real_archived_account_schema(self):
    from unittest.mock import patch
    import os
    quota = self.demo.quota_module()
    with patch.dict(os.environ, {'DEMO_SCENARIO': 'growing'}):
      _, moment, document = self.demo.inputs(quota)
    events = quota.timeline_events(document, moment)
    self.assertEqual({e['account'] for e in events},
                     {'woodland-demo', 'studio-demo'})
    self.assertTrue(all(e['active'] for e in events
                       if e['account'] == 'woodland-demo'))
    self.assertTrue(all(not e['active'] for e in events
                       if e['account'] == 'studio-demo'))

  def test_fixture_relationships_and_real_dependency_semantics(self):
    data = self.demo.fixture()
    self.assertEqual(len(data['tasks']), 7)
    self.assertEqual(len(data['agents']), 5)
    self.assertEqual(sum(not a.get('parent_id') for a in data['agents']), 3)
    self.assertEqual(data['tasks']['T-0003']['fields']['depends-on'], 'T-0002')
    self.assertEqual(data['tasks']['T-0005']['fields']['status'], 'done')
    self.assertIn('synthetic', data['tasks']['T-0004']['body'].lower())
    self.assertEqual(data['tasks']['T-0001']['fields']['helpers'], 'demo-timer,demo-recipes')

  def test_agent_task_associations_use_production_identity_and_roles(self):
    import sys
    sys.path.insert(0, str(ROOT / 'bin'))
    import agent_quota_agents as agents
    from unittest.mock import patch
    import json
    from types import SimpleNamespace
    data = self.demo.fixture()
    reply = SimpleNamespace(returncode=0, stdout=json.dumps({
      'tasks': [row['fields'] for row in data['tasks'].values()]}))
    with patch.object(agents.shutil, 'which', return_value='demo-agent-task'), \
         patch.object(agents.subprocess, 'run', return_value=reply):
      claims = agents.claimed_tasks()
    rows = self.demo.associated_agents(data['agents'], claims)
    parent = next(a for a in rows if a['id'] == 'demo-brewing')
    helper = next(a for a in rows if a['id'] == 'demo-timer')
    self.assertEqual(parent['tasks'][0]['id'], 'T-0001')
    self.assertEqual(parent['tasks'][0]['role'], 'owner')
    self.assertEqual(helper['tasks'][0]['role'], 'helper')
    self.assertEqual(helper['tasks'][0]['records_id'], 'demo-timer')
    self.assertNotIn('records_ids', data['agents'][0])

  def test_production_dependency_and_quota_renderers(self):
    import sys
    sys.path.insert(0, str(ROOT / 'bin'))
    import agent_records_live as live
    from agent_records_tasks import checklist, log_lines
    data = self.demo.fixture()
    self.assertEqual(live.unmet(data['tasks'], 'T-0003'), ['T-0002'])
    self.assertEqual(checklist(data['tasks']['T-0004']['body'])[1][3],
                     '- [ ] work-reviewed: the work was reviewed')
    for task in data['tasks'].values():
      self.assertTrue(log_lines(task['body']))
    quota = self.demo.quota_module()
    _, moment, document = self.demo.inputs(quota)
    text = quota.render_timeline(document, moment.tzinfo)
    self.assertIn('BURN', text)
    self.assertIn('RESET', text)
    self.assertIn('20h', text)

  def test_records_lint_read_and_close_use_production_schema(self):
    import tempfile
    import sys
    sys.path.insert(0, str(ROOT / 'bin'))
    from agent_records_tasks import lint_tasks, close_ready
    from agent_records_live import read_snapshot
    with tempfile.TemporaryDirectory() as directory:
      stage = Path(directory).resolve()
      self.demo.materialize_records(stage)
      self.assertEqual(lint_tasks(stage / 'tasks', stage / 'changes'), [])
      rows = read_snapshot(stage / 'tasks', stage / 'changes', .1)
      self.assertEqual(rows, self.demo.fixture()['tasks'])
      row = rows['T-0005']
      close_ready(row['fields'], row['body'], stage / 'changes', 'T-0005', 'done')
      from agent_records_core import RecordsError
      pending = rows['T-0004']
      with self.assertRaises(RecordsError):
        close_ready(pending['fields'], pending['body'],
                    stage / 'changes', 'T-0004', 'done')

  def test_adapter_obeys_production_quota_argument_gate(self):
    import contextlib
    import io
    quota = self.demo.quota_module()
    self.demo.validate_quota_arguments(quota, ['--timeline', '--cached'])
    self.demo.validate_quota_arguments(quota,
      ['--agents', '--live', '--no-summaries'])
    with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
      self.demo.validate_quota_arguments(quota,
        ['--timeline', '--live', '--no-summaries'])

  def test_missing_git_is_a_clear_error(self):
    from unittest.mock import patch
    import tempfile
    with tempfile.TemporaryDirectory() as directory:
      with patch.object(self.demo.shutil, 'which', return_value=None):
        with self.assertRaisesRegex(RuntimeError, 'Git is required'):
          self.demo.materialize_records(Path(directory))

  def test_stage_does_not_inherit_user_configuration(self):
    import tempfile
    with tempfile.TemporaryDirectory() as directory:
      stage = Path(directory).resolve()
      env = self.demo.environment(stage)
      self.assertEqual(env['HOME'], str(stage / 'home'))
      self.assertEqual(env['XDG_CONFIG_HOME'], str(stage / 'config'))
      self.assertNotIn('TMUX', env)
      self.assertNotIn('OPENAI_API_KEY', env)
      self.assertEqual(env['TZ'], 'UTC')
      self.assertEqual(env['AGENT_TASK_ROOT'], str(stage / 'tasks'))
      self.assertEqual(env['AGENT_CHANGELOG_ROOT'], str(stage / 'changes'))

  def test_no_private_or_machine_derived_data(self):
    import json
    text = json.dumps(self.demo.fixture())
    for private in ('/' + 'Users' + '/', '@gmail', 'snapshot_at', 'project-01'):
      self.assertNotIn(private, text)
    self.assertEqual(self.demo.fixture()['clock'], '2026-10-02T12:00:00Z')
