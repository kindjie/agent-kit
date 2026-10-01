"""Atomic startup, renewal and read-only recovery boundaries via the CLI."""

import base64
import json
import subprocess

from tests import test_agent_records_estimate_policy as policy_tests
from tests.agent_records_support import RecordsFixture


class EfficiencyTest(RecordsFixture):
  task = policy_tests.EstimatePolicyTest.task
  fields = policy_tests.EstimatePolicyTest.fields
  command = policy_tests.EstimatePolicyTest.command
  snapshot = policy_tests.EstimatePolicyTest.snapshot
  policy = policy_tests.EstimatePolicyTest.policy

  def setUp(self):
    super().setUp()
    self.init()

  def git(self, *args):
    return subprocess.check_output(
      ['git', '-C', str(self.tasks), *args], env=self.env).decode().strip()

  def expire(self, task):
    path = next(self.tasks.glob(task + '-*.md'))
    text = path.read_text()
    fields = self.fields(task)
    path.write_text(text.replace('expires: ' + fields['expires'],
                                'expires: 2000-01-01 00:00:00 +0000'))
    self.git('add', '--', path.name)
    self.git('commit', '-qm', 'Expire fixture claim')

  def test_expired_owner_keeps_model_helpers_and_selections(self):
    task = self.task()
    self.command('estimate', 'set', task, 'model-a', '--tokens', '20',
                 '--wall-seconds', '10')
    self.command('claim', task, '--model', 'model-a')
    self.command('helper', 'add', task, 'agent-b', '--model', 'model-a')
    before = self.fields(task)
    self.expire(task)
    self.policy('require')
    self.command('claim', task)
    after = self.fields(task)
    for key in ('execution-model', 'helpers', 'helper-models'):
      self.assertEqual(after[key], before[key])
    self.assertNotIn('from agent-a', self.run_cmd('agent-task', 'show', task))
    self.command('log', task, 'Helper still authorized', agent='agent-b')

  def test_expired_takeover_still_requires_model_and_clears_helpers(self):
    task = self.task()
    self.command('claim', task, '--model-unknown', 'runtime hides model')
    self.command('helper', 'add', task, 'agent-b', '--model', 'model-a')
    self.expire(task)
    self.policy('require')
    before = self.snapshot()
    self.command('claim', task, agent='agent-c', code=1)
    self.assertEqual(before, self.snapshot())
    self.command('claim', task, '--model-unknown', 'new runtime',
                 agent='agent-c')
    fields = self.fields(task)
    self.assertEqual(fields['helpers'], '')
    self.assertNotIn('helper-models', fields)
    self.assertEqual(fields['estimate-model-unknown'], 'new runtime')

  def test_create_estimate_and_claim_are_one_commit(self):
    self.policy('require')
    before = int(self.git('rev-list', '--count', 'HEAD'))
    task = self.task('One operation', '--claim', '--model', 'model-a',
                     '--wall-seconds', '60', '--tokens', '1000', '--hours', '3')
    fields = self.fields(task)
    self.assertEqual(fields['status'], 'in-progress')
    self.assertEqual(fields['owner'], 'agent-a')
    self.assertEqual(fields['execution-model'], 'model-a')
    self.assertEqual(json.loads(fields['estimates']),
                     {'model-a': {'wall-seconds': 60, 'tokens': 1000}})
    self.assertEqual(int(self.git('rev-list', '--count', 'HEAD')), before + 1)
    self.run_cmd('agent-task', 'lint')

  def test_invalid_startup_does_not_allocate_task_or_commit(self):
    self.policy('require')
    for options, code in [
        (['--claim', '--model', 'model-a'], 1),
        (['--claim', '--model', 'model-a', '--tokens', '-1'], 2),
        (['--claim', '--model-unknown', 'unknown', '--tokens', '1'], 2),
        (['--model', 'model-a'], 2),
        (['--claim', '--model-unknown', 'unknown', '--hours', '25'], 2),
    ]:
      with self.subTest(options=options):
        before = self.snapshot()
        self.command('new', '--title', 'Invalid', *options, code=code)
        self.assertEqual(before, self.snapshot())
    task = self.task('Unknown', '--claim', '--model-unknown', 'hidden')
    self.assertEqual(task, 'T-0001')
    self.assertEqual(self.fields(task)['estimate-model-unknown'], 'hidden')

  def test_claim_can_record_estimates_in_same_commit(self):
    task = self.task()
    self.policy('require')
    self.command('claim', task, '--model', 'model-a', '--tokens', '100',
                 '--wall-unknown', 'external wait')
    self.command('claim', task, '--tokens', '200')
    self.assertEqual(json.loads(self.fields(task)['estimates']),
                     {'model-a': {'tokens': 200,
                                  'wall-seconds-unknown': 'external wait'}})

  def test_warn_startup_reports_real_created_task(self):
    self.policy('warn')
    task = self.task('Incomplete estimate', '--claim', '--model', 'model-a')
    self.assertEqual(self.fields(task)['owner'], 'agent-a')

  def test_renewal_can_change_model_without_losing_helpers(self):
    task = self.task('Unknown', '--claim', '--model-unknown', 'hidden')
    self.command('helper', 'add', task, 'agent-b', '--model', 'model-b')
    self.expire(task)
    self.policy('require')
    self.command('claim', task, '--model', 'model-c', '--tokens', '100',
                 '--wall-seconds', '60')
    fields = self.fields(task)
    self.assertEqual(fields['execution-model'], 'model-c')
    self.assertNotIn('estimate-model-unknown', fields)
    self.assertEqual(json.loads(fields['helper-models']),
                     {'agent-b': {'model': 'model-b'}})

  def pending_edit(self):
    task = self.task()
    path = next(self.tasks.glob(task + '-*.md'))
    pre = path.read_bytes()
    post = pre.replace(b'title: Example', b'title: Pending')
    journal = {'id': 'example', 'operation': 'Edit', 'agent': 'agent-a',
               'head': self.git('rev-parse', 'HEAD'), 'status': '',
               'paths': {path.name: {
                 'pre': base64.b64encode(pre).decode(),
                 'post': base64.b64encode(post).decode()}}}
    (self.tasks / '.records-journal.json').write_text(json.dumps(journal))
    path.write_bytes(post)
    return task

  def test_reads_refuse_pending_recovery_without_writes(self):
    task = self.pending_edit()
    commands = [('agent-task', 'list'), ('agent-task', 'show', task),
                ('agent-task', 'next'), ('agent-task', 'lint'),
                ('agent-task', 'dependency', 'show', task),
                ('agent-task', 'watch', task, '--timeout', '0.1'),
                ('agent-changelog', 'list'), ('agent-changelog', 'lint')]
    before = self.snapshot()
    for command in commands:
      with self.subTest(command=command):
        self.run_cmd(*command, code=5)
        self.assertEqual(before, self.snapshot())
    self.command('recover')
    self.assertEqual(self.fields(task)['title'], 'Example')

  def test_unlocked_pending_read_is_unverified_and_never_recovers(self):
    task = self.pending_edit()
    before = self.snapshot()
    data = json.loads(self.run_cmd('agent-task', '--unlocked', 'show', task,
                                   '--json'))
    self.assertFalse(data['authoritative'])
    self.assertEqual(before, self.snapshot())

  def test_read_only_lock_access_and_missing_lock(self):
    self.task()
    for root in (self.tasks, self.changes):
      (root / '.records.lock').chmod(0o400)
    try:
      self.run_cmd('agent-task', 'list')
      self.run_cmd('agent-changelog', 'list')
    finally:
      for root in (self.tasks, self.changes):
        (root / '.records.lock').chmod(0o600)
    (self.tasks / '.records.lock').unlink()
    self.run_cmd('agent-task', 'list', code=1)
    self.assertFalse((self.tasks / '.records.lock').exists())
    self.run_cmd('agent-task', 'doctor')
    self.run_cmd('agent-task', 'list')
