"""Portable dashboard launch, targeting and resource ownership regressions."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


class DashboardTest(unittest.TestCase):
  def setUp(self):
    self.tmp = tempfile.TemporaryDirectory(prefix='dashboard space ')
    self.addCleanup(self.tmp.cleanup)
    self.base = Path(self.tmp.name).resolve()
    self.bin = self.base / 'kit bin'
    self.bin.mkdir()
    shutil.copy2(ROOT / 'bin/agent-dash', self.bin / 'agent-dash')
    self.log = self.base / 'calls.jsonl'
    self.env = dict(os.environ, PATH=str(self.bin) + ':' + os.environ['PATH'],
                    TMUX='isolated-fixture', DASH_LOG=str(self.log),
                    DASH_WIDTH='160', DASH_HEIGHT='48')
    self.tool('tmux', '''#!/usr/bin/env python3
import json, os, sys
args = sys.argv[1:]
with open(os.environ['DASH_LOG'], 'a') as stream:
  stream.write(json.dumps(args) + '\\n')
if args[0] == os.environ.get('DASH_FAIL'):
  sys.exit(1)
if args[0] == 'has-session':
  sys.exit(1)
if args[0] == 'display-message':
  print('$9' if args[-1] == '#{session_id}' else
        '@' + args[3].lstrip('%') if args[-1] == '#{window_id}' else
        os.environ['DASH_WIDTH' if args[-1] == '#{window_width}'
                   else 'DASH_HEIGHT'])
elif args[0] in ('new-window', 'new-session', 'split-window'):
  with open(os.environ['DASH_LOG']) as stream:
    count = sum(json.loads(line)[0] in
                ('new-window', 'new-session', 'split-window') for line in stream)
  print('%' + str(count))
''')
    self.tool('agent-quota', '#!/bin/sh\nprintf "one\\ntwo\\n"\n')
    self.tool('taskglance', '#!/bin/sh\nexit 0\n')
    self.tool('agent-task',
              '#!/bin/sh\nprintf "list --live\\n"\n')

  def tool(self, name, content):
    path = self.bin / name
    path.write_text(content)
    path.chmod(0o755)

  def run_dash(self, *args, success=True, executable=None):
    result = subprocess.run([str(executable or self.bin / 'agent-dash'), *args],
                            env=self.env, capture_output=True, text=True,
                            timeout=8)
    self.assertEqual(result.returncode == 0, success, result.stderr)
    calls = ([json.loads(line) for line in self.log.read_text().splitlines()]
             if self.log.exists() else [])
    return result, calls

  def commands(self, calls):
    commands = []
    for args in calls:
      if args[0] != 'respawn-pane':
        continue
      command = args[4:]
      if command[0] == '/usr/bin/env':
        while command[1] == '-u':
          command = [command[0], *command[3:]]
        while '=' in command[1]:
          command = [command[0], *command[2:]]
        command = command[1:]
      commands.append(command)
    return commands

  def test_core_default_needs_no_personal_tool_or_summaries(self):
    (self.bin / 'taskglance').unlink()
    _, calls = self.run_dash()
    commands = self.commands(calls)
    self.assertEqual(len(commands), 3)
    self.assertIn([str(self.bin / 'agent-task'), 'list', '--live'], commands)
    for command in commands:
      if '--agents' in command:
        self.assertIn('--no-summaries', command)
      elif '--timeline' in command:
        self.assertNotIn('--no-summaries', command)
    self.assertEqual({a[-1] for a in calls if a[0] == 'select-pane'
                     and '-T' in a}, {'Agents', 'Agent Tasks', 'Timeline'})

  def test_personal_and_summaries_are_explicit(self):
    _, calls = self.run_dash('--personal', '--summaries', 'my dashboard')
    commands = self.commands(calls)
    self.assertEqual(len(commands), 4)
    self.assertIn([str(self.bin / 'taskglance'), 'watch', '-i', '--all'], commands)
    self.assertFalse(any('--no-summaries' in c for c in commands))
    self.assertIn('my dashboard', next(a for a in calls if a[0] == 'new-window'))

  def test_generated_quota_argv_pass_production_parser_without_providers(self):
    from .test_agent_quota import AGENT_QUOTA
    class Parsed(Exception):
      pass
    validate = AGENT_QUOTA.validate_flags
    def validate_and_stop(*args):
      validate(*args)
      raise Parsed()
    quota_log = self.base / 'quota argv.jsonl'
    self.env['DASH_QUOTA_LOG'] = str(quota_log)
    self.tool('agent-quota', '''#!/usr/bin/env python3
import json, os, sys
with open(os.environ['DASH_QUOTA_LOG'], 'a') as stream:
  stream.write(json.dumps(sys.argv[1:]) + '\\n')
print('timeline')
''')
    for flags in ((), ('--personal',), ('--summaries',),
                  ('--personal', '--summaries')):
      with self.subTest(flags=flags):
        self.log.unlink(missing_ok=True)
        quota_log.unlink(missing_ok=True)
        _, calls = self.run_dash(*flags)
        generated = [c[1:] for c in self.commands(calls)
                     if c[0].endswith('agent-quota')]
        generated.extend(json.loads(line) for line in
                         quota_log.read_text().splitlines())
        self.assertEqual(len(generated), 3)
        for argv in generated:
          with patch.object(AGENT_QUOTA, 'validate_flags', validate_and_stop):
            # Parsing and real semantic validation happen; execution stops
            # at their boundary before caches, transcripts or providers.
            with self.assertRaises(Parsed, msg=str(argv)):
              AGENT_QUOTA.main(argv)

  def test_missing_optional_personal_tool_reduces_view(self):
    (self.bin / 'taskglance').unlink()
    self.env['PATH'] = str(self.bin) + ':/usr/bin:/bin'
    result, calls = self.run_dash('--personal')
    self.assertIn('taskglance', result.stderr)
    self.assertEqual(len(self.commands(calls)), 3)

  def test_checkout_and_individually_linked_launcher(self):
    link = self.base / 'linked dash'
    link.symlink_to(self.bin / 'agent-dash')
    _, calls = self.run_dash(executable=link)
    self.assertTrue(any(c[0] == str(self.bin / 'agent-quota')
                        for c in self.commands(calls)))
    for command in self.commands(calls):
      proc = subprocess.run(command, env=self.env, capture_output=True,
                            timeout=3)
      self.assertEqual(proc.returncode, 0, proc.stderr)

  def test_small_windows_keep_separate_usable_views(self):
    for width, height, splits, windows in [('100', '30', 1, 2),
                                           ('30', '8', 0, 3)]:
      self.env.update(DASH_WIDTH=width, DASH_HEIGHT=height)
      self.log.unlink(missing_ok=True)
      _, calls = self.run_dash()
      self.assertEqual(sum(a[0] == 'split-window' for a in calls), splits)
      self.assertEqual(sum(a[0] == 'new-window' for a in calls), windows)
      for a in calls:
        if a[0] == 'new-window' and '-d' in a:
          self.assertIn('$9:', a)
      self.assertIn(['set-option', '-p', '-t', '%1',
                     '@agent_attention_dashboard', '1'], calls)

  def test_unconfigured_records_show_setup_without_initialization(self):
    self.tool('agent-task', '''#!/bin/sh
case "$*" in
  'list --help') echo 'list --live';;
  *) echo 'records not configured' >&2; exit 1;;
esac
''')
    _, calls = self.run_dash()
    commands = self.commands(calls)
    self.assertTrue(any('agent-task init --help' in ' '.join(c)
                        for c in commands))
    self.assertFalse(any(c[0].endswith('agent-task') for c in commands))

  def test_actual_unconfigured_records_leave_config_and_store_absent(self):
    original = ROOT / 'bin/agent-task'
    self.tool('agent-task', '#!/bin/sh\nexec "' + str(original) + '" "$@"\n')
    config = self.base / 'isolated config'
    self.env.update(HOME=str(self.base), XDG_CONFIG_HOME=str(config),
                    AGENT_TASKS_DIR='')
    _, calls = self.run_dash()
    self.assertTrue(any('Agent Tasks unavailable' in ' '.join(c)
                        for c in self.commands(calls)))
    self.assertFalse(config.exists())

  def test_missing_required_tool_and_unknown_flag_fail_before_mutation(self):
    result, calls = self.run_dash('--bad', success=False)
    self.assertIn('Usage', result.stderr)
    self.assertEqual(calls, [])
    (self.bin / 'agent-quota').unlink()
    self.env['PATH'] = '/usr/bin:/bin'
    result, calls = self.run_dash(success=False)
    self.assertIn('agent-quota', result.stderr)
    self.assertEqual(calls, [])

  def test_failed_split_cleans_only_created_window(self):
    self.env['DASH_FAIL'] = 'split-window'
    _, calls = self.run_dash(success=False)
    self.assertIn(['kill-window', '-t', '@1'], calls)
    self.assertFalse(any(a[0] in ('kill-server', 'kill-session') for a in calls))

  def test_outside_failure_cleans_only_created_session(self):
    self.env.update(TMUX='', DASH_FAIL='split-window')
    _, calls = self.run_dash(success=False)
    created = next(a[a.index('-s') + 1] for a in calls
                   if a[0] == 'new-session')
    self.assertIn(['kill-session', '-t', '=' + created], calls)
    self.assertFalse(any(a[0] == 'kill-server' for a in calls))


@unittest.skipUnless(shutil.which('tmux'), 'real tmux unavailable')
class RealDashboardTest(unittest.TestCase):
  def test_real_old_server_space_paths_and_scoped_failure(self):
    with tempfile.TemporaryDirectory(prefix='real dashboard ') as temporary:
      base = Path(temporary)
      socket = base / 'server.sock'
      tools = base / 'kit commands'
      tools.mkdir()
      real = shutil.which('tmux')
      def tmux(*args):
        return subprocess.run([real, '-S', str(socket), *args],
                              capture_output=True, text=True, check=True)
      tmux('-f', '/dev/null', 'new-session', '-d', '-s', 'sentinel',
           '-n', 'sentinel', '-x', '110', '-y', '42', 'sleep', '600')
      try:
        tmux('set-environment', '-g', 'PATH', '/usr/bin:/bin')
        shutil.copy2(ROOT / 'bin/agent-dash', tools / 'agent-dash')
        wrapper = tools / 'tmux'
        wrapper.write_text('#!/bin/sh\n'
                           'if [ "${DASH_FAIL:-}" = "$1" ]; then exit 1; fi\n'
                           'if [ "${DASH_NO_ATTACH:-}" = 1 ] && '
                           '[ "$1" = attach-session ]; then exit 0; fi\n'
                           'exec ' +
                           "'" + real + "' -S '" + str(socket) + "' \"$@\"\n")
        wrapper.chmod(0o755)
        (tools / 'dashboard-python').symlink_to(sys.executable)
        for name in ('agent-quota', 'agent-task'):
          tool = tools / name
          tool.write_text('''#!/usr/bin/env dashboard-python
import sys, time
if '--help' in sys.argv:
  print('--live')
elif '--json' in sys.argv:
  print('[]')
elif '--cached' in sys.argv:
  print('timeline')
else:
  print(' '.join(sys.argv[1:]), flush=True)
  time.sleep(600)
''')
          tool.chmod(0o755)
        env = dict(os.environ, PATH=str(tools) + ':' + os.environ['PATH'],
                   TMUX=tmux('display-message', '-p', '-t', 'sentinel:',
                             '#{socket_path},#{pid},0').stdout.strip())
        subprocess.run([str(tools / 'agent-dash'), 'new dashboard'], env=env,
                       capture_output=True, text=True, check=True, timeout=8)
        panes = tmux('list-panes', '-t', 'sentinel:new dashboard', '-F',
                     '#{pane_title}|#{pane_dead}').stdout.splitlines()
        self.assertEqual(set(panes), {'Agents|0', 'Agent Tasks|0', 'Timeline|0'})
        self.assertEqual(tmux('list-windows', '-t', 'sentinel', '-F',
                              '#{window_name}').stdout.splitlines(),
                         ['sentinel', 'new dashboard'])
        # A real split failure removes its own window and leaves both
        # existing windows alive on the same server.
        failed = subprocess.run([str(tools / 'agent-dash'), 'failed'],
                                env=dict(env, DASH_FAIL='split-window'),
                                capture_output=True, text=True, timeout=8)
        self.assertNotEqual(failed.returncode, 0)
        self.assertEqual(tmux('list-windows', '-t', 'sentinel', '-F',
                              '#{window_name}').stdout.splitlines(),
                         ['sentinel', 'new dashboard'])
        tmux('set-option', '-t', 'sentinel', 'default-size', '60x18')
        subprocess.run([str(tools / 'agent-dash'), 'narrow'], env=env,
                       capture_output=True, text=True, check=True, timeout=8)
        for name, title in [('narrow', 'Agents'), ('narrow-tasks', 'Agent Tasks'),
                            ('narrow-timeline', 'Timeline')]:
          self.assertEqual(tmux('list-panes', '-t', 'sentinel:' + name,
                                '-F', '#{pane_title}|#{pane_dead}').stdout.strip(),
                           title + '|0')
        # Outside tmux uses a new isolated session, without attaching this
        # test process. Only the adapter suppresses the terminal attach.
        subprocess.run([str(tools / 'agent-dash'), 'outside'],
                       env=dict(env, TMUX='', DASH_NO_ATTACH='1'),
                       capture_output=True, text=True, check=True, timeout=8)
        self.assertEqual(tmux('list-panes', '-s', '-t', 'outside', '-F',
                              '#{pane_title}').stdout.splitlines(),
                         ['Agents', 'Agent Tasks', 'Timeline'])
        self.assertEqual(tmux('list-windows', '-t', 'sentinel', '-F',
                              '#{window_name}').stdout.splitlines(),
                         ['sentinel', 'new dashboard', 'narrow',
                          'narrow-tasks', 'narrow-timeline'])
      finally:
        tmux('kill-server')
