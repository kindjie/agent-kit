"""Installation diagnostics against isolated files; no live tool calls."""
import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
DOCTOR = ROOT / 'bin/agent-doctor'


class DoctorTest(unittest.TestCase):
  def setUp(self):
    self.temp = tempfile.TemporaryDirectory()
    self.addCleanup(self.temp.cleanup)
    self.root = Path(self.temp.name)
    self.bin = self.root / 'bin'
    self.bin.mkdir()
    (self.bin / 'python3').symlink_to(sys.executable)
    for name in ('git', 'tmux', 'uv'):
      self.file(self.bin / name, '#!/bin/sh\nexit 99\n', True)
    self.env = dict(os.environ, HOME=str(self.root),
                    PYTHONDONTWRITEBYTECODE='1',
                    PATH=str(self.bin))
    for name in ('AGENT_TASKS_DIR', 'AGENT_CHANGELOG_DIR'):
      self.env.pop(name, None)

  def file(self, path, text='', executable=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    if executable:
      path.chmod(0o755)
    return path

  def command(self, name='agent-task', text='#!/usr/bin/env python3\n'):
    return self.file(self.bin / name, text, True)

  def run_doctor(self, *args):
    result = subprocess.run(
      [sys.executable, str(DOCTOR), '--bin-dir', str(self.bin),
       '--json', *args], env=self.env, text=True, capture_output=True,
      timeout=8)
    self.assertNotIn('Traceback', result.stderr)
    return result, json.loads(result.stdout)

  def codes(self, report, status=None):
    return [c['code'] for c in report['checks']
            if status is None or c['status'] == status]

  def records(self, kind='tasks'):
    root = self.root / kind
    (root / '.git').mkdir(parents=True)
    self.file(root / '.git/HEAD', 'ref: refs/heads/main\n')
    self.file(root / '.agent-records', json.dumps({'kind': kind}))
    self.file(root / '.records.lock')
    (root / ('tasks' if kind == 'tasks' else 'entries')).mkdir()
    return root

  def test_unconfigured_is_skipped_not_error(self):
    self.command()
    proc, report = self.run_doctor()
    self.assertEqual(proc.returncode, 0)
    self.assertIn('records.unconfigured', self.codes(report, 'skip'))
    self.assertIn('skills.unconfigured', self.codes(report, 'skip'))
    self.assertFalse(report['network_used'])
    self.assertFalse(report['signing_verified'])

  def test_selected_missing_command_fails(self):
    proc, report = self.run_doctor('--command', 'agent-task')
    self.assertEqual(proc.returncode, 1)
    issue = next(c for c in report['checks'] if c['status'] == 'error')
    self.assertEqual(issue['code'], 'command.missing')
    self.assertEqual(issue['location'], str(self.bin / 'agent-task'))
    self.assertTrue(issue['remedy'])

  def test_missing_local_import_and_transitive_dependency_fail(self):
    self.command(text='#!/usr/bin/env python3\n'
                 'from agent_records_core import x\n')
    self.file(self.bin / 'agent_records_core.py',
              'from agent_records_tasks import y\n')
    proc, report = self.run_doctor('--command', 'agent-task')
    self.assertEqual(proc.returncode, 1)
    self.assertIn('companion.missing', self.codes(report, 'error'))

  def test_command_is_not_executed_or_imported(self):
    sentinel = self.root / 'executed'
    self.command(text=f'#!/usr/bin/env python3\n'
                 f'open({str(sentinel)!r}, "w").write("bad")\n')
    proc, _ = self.run_doctor('--command', 'agent-task')
    self.assertEqual(proc.returncode, 0)
    self.assertFalse(sentinel.exists())

  def test_no_write_or_subprocess_audit(self):
    self.command()
    store = self.records()
    instruction = self.file(self.root / 'AGENTS.md', 'Review fixture.')
    skills = self.root / 'skills'
    self.file(skills / 'demo/SKILL.md',
              '---\nname: demo\ndescription: Demo\n---\n')
    program = '''import runpy, sys
path = sys.argv.pop(1)
def audit(event, args):
  if event == 'subprocess.Popen' or event.startswith(('socket.', 'os.mkdir')):
    raise RuntimeError('forbidden effect')
  if event == 'open' and isinstance(args[2], int):
    import os
    if args[2] & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC):
      raise RuntimeError('forbidden write')
sys.addaudithook(audit)
runpy.run_path(path, run_name='__main__')
'''
    proc = subprocess.run([sys.executable, '-c', program, str(DOCTOR),
                           '--bin-dir', str(self.bin), '--json',
                           '--tasks-dir', str(store), '--instruction',
                           str(instruction), '--skill-root', str(skills),
                           '--review-prompt'],
                          env=self.env, capture_output=True, text=True)
    self.assertEqual(proc.returncode, 0, proc.stderr)
    json.loads(proc.stdout)

  def test_installed_symlink_uses_real_sibling_directory(self):
    source = self.root / 'source'
    self.file(source / 'agent-task',
              '#!/usr/bin/env python3\nimport agent_records_core\n', True)
    self.file(source / 'agent_records_core.py')
    (self.bin / 'agent-task').symlink_to(source / 'agent-task')
    proc, report = self.run_doctor('--installed', '--command', 'agent-task')
    self.assertEqual(proc.returncode, 0)
    self.assertIn('companion.readable', self.codes(report, 'ok'))

  def test_invocation_relative_shell_companion_is_required(self):
    source = self.root / 'source'
    self.file(source / 'md-preview', '#!/bin/sh\n', True)
    self.file(source / 'md-preview.py')
    (self.bin / 'md-preview').symlink_to(source / 'md-preview')
    proc, report = self.run_doctor('--installed', '--command', 'md-preview')
    self.assertEqual(proc.returncode, 1)
    self.assertIn('companion.missing', self.codes(report, 'error'))

  def test_non_executable_and_invalid_python_are_errors(self):
    self.file(self.bin / 'agent-task', '#!/usr/bin/env python3\nif !!!\n')
    proc, report = self.run_doctor('--command', 'agent-task')
    self.assertEqual(proc.returncode, 1)
    self.assertIn('command.executable', self.codes(report, 'error'))
    self.assertIn('python.syntax', self.codes(report, 'error'))

  def test_invalid_config_does_not_echo_content(self):
    path = self.file(self.root / 'records.json', '{"private": "secret-marker",')
    proc, report = self.run_doctor('--records-config', str(path))
    self.assertEqual(proc.returncode, 1)
    self.assertIn('config.invalid', self.codes(report, 'error'))
    self.assertNotIn('secret-marker', proc.stdout + proc.stderr)

  def test_config_types_and_precedence(self):
    wrong = self.root / 'missing'
    cfg = self.file(self.root / 'records.json', json.dumps({
      'tasks_dir': str(wrong), 'push': {'tasks': True},
      'estimate_policy': 'warn', 'repos': {str(self.root): 'demo'}}))
    root = self.records()
    proc, report = self.run_doctor('--records-config', str(cfg),
                                   '--tasks-dir', str(root))
    self.assertEqual(proc.returncode, 0)
    cfg.write_text('{"push": {"tasks": "yes"}}')
    proc, report = self.run_doctor('--records-config', str(cfg))
    self.assertEqual(proc.returncode, 1)
    self.assertIn('config.invalid', self.codes(report, 'error'))

  def test_pending_journal_is_not_read_or_recovered(self):
    root = self.records()
    journal = self.file(root / '.records-journal.json', 'private-marker')
    before = journal.read_bytes()
    proc, report = self.run_doctor('--tasks-dir', str(root))
    self.assertEqual(proc.returncode, 1)
    self.assertIn('records.pending', self.codes(report, 'error'))
    self.assertNotIn('private-marker', proc.stdout)
    self.assertEqual(journal.read_bytes(), before)

  def test_missing_lock_is_not_provisioned(self):
    root = self.records()
    lock = root / '.records.lock'
    lock.unlink()
    proc, report = self.run_doctor('--tasks-dir', str(root))
    self.assertEqual(proc.returncode, 1)
    self.assertIn('records.lock_missing', self.codes(report, 'error'))
    self.assertFalse(lock.exists())

  def test_busy_lock_is_a_warning_and_unchanged(self):
    root = self.records()
    with (root / '.records.lock').open('rb') as stream:
      fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
      proc, report = self.run_doctor('--tasks-dir', str(root))
    self.assertEqual(proc.returncode, 0)
    self.assertIn('records.lock_busy', self.codes(report, 'warning'))

  def test_wrong_marker_and_linked_records_checkout_fail(self):
    root = self.records()
    (root / '.agent-records').write_text('{"kind": "changelog"}')
    proc, report = self.run_doctor('--tasks-dir', str(root))
    self.assertEqual(proc.returncode, 1)
    self.assertIn('records.marker', self.codes(report, 'error'))

  def test_skill_missing_reference_and_dangling_link(self):
    skills = self.root / 'skills'
    self.file(skills / 'demo/SKILL.md',
              '---\nname: demo\ndescription: Demo\n---\n'
              '[details](references/missing.md)\n')
    (skills / 'broken').symlink_to(self.root / 'absent')
    proc, report = self.run_doctor('--skill-root', str(skills))
    self.assertEqual(proc.returncode, 1)
    self.assertIn('skill.reference_missing', self.codes(report, 'error'))
    self.assertIn('skill.broken_link', self.codes(report, 'error'))

  def test_skill_reference_outside_scope_is_not_followed(self):
    skills = self.root / 'skills'
    self.file(skills / 'demo/SKILL.md',
              '---\nname: demo\ndescription: Demo\n---\n'
              '[external](../../private.md)\n')
    proc, report = self.run_doctor('--skill-root', str(skills))
    self.assertEqual(proc.returncode, 0)
    self.assertIn('skill.reference_unchecked', self.codes(report, 'warning'))

  def test_skill_link_preserves_reference_directory(self):
    source = self.root / 'source/demo'
    self.file(source / 'SKILL.md',
              '---\nname: demo\ndescription: Demo\n---\n'
              '[details](references/guide.md)\n')
    self.file(source / 'references/guide.md')
    skills = self.root / 'skills'
    skills.mkdir()
    (skills / 'demo').symlink_to(source, target_is_directory=True)
    proc, _ = self.run_doctor('--skill-root', str(skills))
    self.assertEqual(proc.returncode, 0)

  def test_prompt_is_bounded_and_does_not_embed_private_content(self):
    path = self.file(self.root / 'AGENTS.md', 'private-instruction-marker')
    proc, report = self.run_doctor('--instruction', str(path),
                                   '--review-prompt')
    self.assertEqual(proc.returncode, 0)
    self.assertIn(str(path), report['review_prompt'])
    self.assertIn('contradictions', report['review_prompt'])
    self.assertIn('read-only', report['review_prompt'])
    self.assertNotIn('private-instruction-marker', proc.stdout)
    self.assertFalse(report['semantic_review_performed'])

  def test_large_or_nonregular_input_is_refused(self):
    path = self.file(self.root / 'AGENTS.md', 'x' * 300000)
    proc, report = self.run_doctor('--instruction', str(path))
    self.assertEqual(proc.returncode, 1)
    self.assertIn('input.size', self.codes(report, 'error'))
    fifo = self.root / 'pipe'
    os.mkfifo(fifo)
    proc, report = self.run_doctor('--records-config', str(fifo))
    self.assertEqual(proc.returncode, 1)
    self.assertIn('input.type', self.codes(report, 'error'))

  def test_pending_and_busy_stores_do_not_read_marker(self):
    root = self.records()
    (root / '.agent-records').write_text('invalid private marker')
    self.file(root / '.records-journal.json')
    _, report = self.run_doctor('--tasks-dir', str(root))
    self.assertNotIn('records.marker', self.codes(report))
    (root / '.records-journal.json').unlink()
    with (root / '.records.lock').open('rb') as stream:
      fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
      _, report = self.run_doctor('--tasks-dir', str(root))
    self.assertNotIn('records.marker', self.codes(report))

  def test_missing_explicit_bin_directory_is_error(self):
    proc, report = self.run_doctor('--bin-dir', str(self.root / 'absent'))
    self.assertEqual(proc.returncode, 1)
    self.assertIn('command.directory', self.codes(report, 'error'))

  def test_linked_records_checkout_is_not_accepted(self):
    root = self.records()
    (root / '.git/HEAD').unlink()
    (root / '.git').rmdir()
    self.file(root / '.git', 'gitdir: elsewhere')
    proc, report = self.run_doctor('--tasks-dir', str(root))
    self.assertEqual(proc.returncode, 1)
    self.assertIn('records.git', self.codes(report, 'error'))

  def test_malformed_external_skill_link_is_bounded(self):
    skills = self.root / 'skills'
    self.file(skills / 'demo/SKILL.md',
              '---\nname: demo\ndescription: Demo\n---\n'
              '[broken](https://[bad)\n')
    proc, report = self.run_doctor('--skill-root', str(skills))
    self.assertEqual(proc.returncode, 0)
    self.assertIn('skill.reference_unchecked', self.codes(report, 'warning'))

  def test_companion_command_needs_execution_permission(self):
    self.command(name='agent-dash', text='#!/bin/sh\n')
    self.file(self.bin / 'agent-quota', '#!/usr/bin/env python3\n')
    self.command()
    proc, report = self.run_doctor('--command', 'agent-dash')
    self.assertEqual(proc.returncode, 1)
    self.assertIn('companion.executable', self.codes(report, 'error'))

  def test_human_output_has_status_location_and_remedy(self):
    proc = subprocess.run([sys.executable, str(DOCTOR),
                           '--bin-dir', str(self.bin),
                           '--command', 'agent-task'], env=self.env,
                          capture_output=True, text=True)
    self.assertEqual(proc.returncode, 1)
    self.assertIn('ERROR', proc.stdout)
    self.assertIn(str(self.bin / 'agent-task'), proc.stdout)
    self.assertIn('Remedy:', proc.stdout)

  def test_python_env_entrypoint_needs_python_on_path(self):
    self.command()
    (self.bin / 'python3').unlink()
    proc, report = self.run_doctor('--command', 'agent-task')
    self.assertEqual(proc.returncode, 1)
    self.assertIn('command.prerequisite', self.codes(report, 'error'))

  def test_deep_config_returns_diagnostic_without_traceback(self):
    path = self.file(self.root / 'records.json', '[' * 2000 + ']' * 2000)
    proc, report = self.run_doctor('--records-config', str(path))
    self.assertEqual(proc.returncode, 1)
    self.assertIn('config.invalid', self.codes(report, 'error'))

  def test_skill_root_entry_limit_is_explicit(self):
    skills = self.root / 'skills'
    skills.mkdir()
    for number in range(257):
      (skills / str(number)).mkdir()
    proc, report = self.run_doctor('--skill-root', str(skills))
    self.assertEqual(proc.returncode, 1)
    self.assertIn('input.count', self.codes(report, 'error'))

  def test_wrapper_transitive_command_companion_is_required(self):
    self.command(name='claude-ctx.sh', text='#!/bin/sh\n')
    self.command(name='agent-status')
    proc, report = self.run_doctor('--command', 'claude-ctx.sh')
    self.assertEqual(proc.returncode, 1)
    self.assertIn('companion.missing', self.codes(report, 'error'))
    self.command(name='agent-quota')
    proc, _ = self.run_doctor('--command', 'claude-ctx.sh')
    self.assertEqual(proc.returncode, 0)

  def test_valid_markdown_destinations_with_spaces_and_parentheses(self):
    skills = self.root / 'skills'
    self.file(skills / 'demo/SKILL.md',
              '---\nname: demo\ndescription: Demo\n---\n'
              '[spaces](<references/two words.md>)\n'
              '[parens](references/guide(v2).md "Title")\n')
    target = self.file(skills / 'demo/references/two words.md')
    self.file(skills / 'demo/references/guide(v2).md')
    proc, _ = self.run_doctor('--skill-root', str(skills))
    self.assertEqual(proc.returncode, 0)
    target.unlink()
    proc, report = self.run_doctor('--skill-root', str(skills))
    self.assertEqual(proc.returncode, 1)
    self.assertIn('skill.reference_missing', self.codes(report, 'error'))

  def test_unsupported_markdown_destination_warns_without_false_error(self):
    skills = self.root / 'skills'
    self.file(skills / 'demo/SKILL.md',
              '---\nname: demo\ndescription: Demo\n---\n'
              '[ambiguous](references/two words.md)\n')
    proc, report = self.run_doctor('--skill-root', str(skills))
    self.assertEqual(proc.returncode, 0)
    self.assertIn('skill.reference_unchecked', self.codes(report, 'warning'))

  def test_copyable_prompt_instruction_paths_are_absolute(self):
    with tempfile.TemporaryDirectory(dir=ROOT) as raw:
      path = self.file(Path(raw) / 'AGENTS.md')
      relative = str(path.relative_to(Path.cwd()))
      proc, report = self.run_doctor('--instruction', relative,
                                     '--review-prompt')
      self.assertEqual(proc.returncode, 0)
      self.assertIn(str(path), report['review_prompt'])

  def test_same_source_different_invocations_keep_distinct_companions(self):
    source = self.root / 'source'
    self.file(source / 'agent-status', '#!/usr/bin/env python3\n', True)
    (self.bin / 'agent-status').symlink_to(source / 'agent-status')
    self.command(name='agent-quota')
    other = self.root / 'other'
    other.mkdir()
    (other / 'agent-status').symlink_to(source / 'agent-status')
    program = """import json, runpy, sys
module = runpy.run_path(sys.argv[1])
diagnostic = module['Diagnostic']()
Path = module['Path']
diagnostic.source(Path(sys.argv[2]))
diagnostic.source(Path(sys.argv[3]))
print(json.dumps(diagnostic.checks))
"""
    proc = subprocess.run([sys.executable, '-c', program, str(DOCTOR),
                           str(self.bin / 'agent-status'),
                           str(other / 'agent-status')], env=self.env,
                          capture_output=True, text=True, check=True)
    checks = json.loads(proc.stdout)
    self.assertTrue(any(c['code'] == 'companion.missing' and
                        c['location'] == str(other / 'agent-quota')
                        for c in checks))

  def test_companion_prerequisite_is_checked(self):
    self.command(name='agent-dash', text='#!/bin/sh\n')
    self.command(name='agent-quota')
    self.command(name='agent-task')
    (self.bin / 'git').unlink()
    proc, report = self.run_doctor('--command', 'agent-dash')
    self.assertEqual(proc.returncode, 1)
    self.assertTrue(any(c['code'] == 'command.prerequisite' and
                        c['location'] == 'git' and c['status'] == 'error'
                        for c in report['checks']))

  def test_skill_code_examples_are_not_required_references(self):
    skills = self.root / 'skills'
    path = self.file(skills / 'demo/SKILL.md',
                     '---\nname: demo\ndescription: Demo\n---\n'
                     '```markdown\n[guide](references/example.md)\n```\n'
                     '`[inline](references/inline.md)`\n'
                     '~~~markdown\n[tilde](references/tilde.md)\n~~~\n')
    proc, report = self.run_doctor('--skill-root', str(skills))
    self.assertEqual(proc.returncode, 0)
    self.assertNotIn('skill.reference_missing', self.codes(report))
    path.write_text(path.read_text() +
                    '[real](references/missing.md)\n')
    proc, report = self.run_doctor('--skill-root', str(skills))
    self.assertEqual(proc.returncode, 1)
    errors = [c for c in report['checks']
              if c['code'] == 'skill.reference_missing']
    self.assertEqual(len(errors), 1)
    self.assertTrue(errors[0]['location'].endswith(':12'))

  def test_invalid_cli_is_usage_error(self):
    proc = subprocess.run([sys.executable, str(DOCTOR), '--command', '../x'],
                          env=self.env, capture_output=True, text=True)
    self.assertEqual(proc.returncode, 2)


if __name__ == '__main__':
  unittest.main()
