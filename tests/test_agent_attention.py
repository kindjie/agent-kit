import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import time
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
  'agent_attention', ROOT / 'bin' / 'agent_attention.py')
attention = importlib.util.module_from_spec(spec)
spec.loader.exec_module(attention)


class AttentionTest(unittest.TestCase):
  def test_explicit_states_and_clear_do_not_change_titles(self):
    with patch.dict(os.environ, TMUX='socket,1,0', TMUX_PANE='%7'), \
         patch.object(attention, 'tmux', return_value='') as command:
      attention.set_attention('blocked')
      calls = [call.args[0] for call in command.call_args_list]
      self.assertTrue(any('@agent_attention' in c and 'BLOCKED' in c
                          for c in calls))
      self.assertFalse(any('select-pane' in c or 'rename-window' in c
                           for c in calls))
      command.reset_mock()
      attention.clear_attention()
      self.assertTrue(any('@agent_attention' in c.args[0] and '-u' in c.args[0]
                          for c in command.call_args_list))

  def test_no_tmux_and_invalid_targets_are_noops(self):
    for pane in ('', 'other:1', '%1;kill-server'):
      with patch.dict(os.environ, TMUX_PANE=pane), \
           patch.object(attention, 'tmux') as command:
        attention.set_attention('needs-you')
        command.assert_not_called()

  def test_dashboard_ignores_dead_panes_and_other_dashboards(self):
    rows = '%1\t0\t\tneeds-you\n%2\t0\t\twork\n%3\t1\t\tblocked\n'
    rows += '%4\t0\t1\tneeds-you\n%5\t0\t\tgarbage\n'
    with patch.object(attention, 'tmux', return_value=rows):
      self.assertEqual(attention.summary(), ('YOU 1 · WORK 1', '!2'))
    with patch.object(attention, 'tmux', return_value=None):
      self.assertIsNone(attention.summary())

  def test_hooks_clear_only_main_session_resumption(self):
    with patch.object(attention, 'clear_attention') as clear:
      attention.hook({'hook_event_name': 'Stop'})
      attention.hook({'hook_event_name': 'UserPromptSubmit', 'agent_id': 'child'})
      attention.hook({'hook_event_name': 'SessionStart', 'source': 'compact'})
      clear.assert_not_called()
      attention.hook({'hook_event_name': 'UserPromptSubmit'})
      clear.assert_called_once()

  def test_failed_dashboard_publication_retries_same_badge(self):
    old, new = ('YOU 1', '!1'), ('YOU 2', '!2')
    with patch.object(attention, 'publish', side_effect=[False, True]) as publish:
      previous = attention.update_badge(old, new)
      self.assertEqual(previous, old)
      self.assertEqual(attention.update_badge(previous, None), old)
      self.assertEqual(attention.update_badge(previous, new), new)
      self.assertEqual(publish.call_count, 2)

  def test_bell_is_deduplicated_until_cleared(self):
    with patch.dict(os.environ, TMUX='socket,1,0', TMUX_PANE='%7'), \
         patch.object(attention, 'tmux', return_value='needs-you'), \
         patch.object(attention, 'ring') as ring:
      attention.set_attention('needs-you', bell=True)
      ring.assert_not_called()

  def test_bell_never_opens_an_unexpected_device(self):
    with patch.dict(os.environ, TMUX='socket,1,0', TMUX_PANE='%7'), \
         patch.object(attention, 'tmux', return_value='/tmp/unsafe'), \
         patch.object(os, 'open') as opened:
      attention.ring()
      opened.assert_not_called()


@unittest.skipUnless(shutil.which('tmux'), 'tmux unavailable')
class RealTmuxTest(unittest.TestCase):
  def test_bell_reaches_pane_with_tool_stdout_captured_and_muted_badge_stays(self):
    with tempfile.TemporaryDirectory() as temp:
      socket = str(Path(temp) / 'tmux.sock')
      env = dict(os.environ, XDG_CONFIG_HOME=temp)
      def tmux(*args):
        return subprocess.check_output(['tmux', '-S', socket, *args],
          env=env, text=True, stderr=subprocess.DEVNULL).strip()
      tmux('-f', '/dev/null', 'new-session', '-d', '-s', 'test', 'sleep 60')
      try:
        tmux('set-option', '-g', 'monitor-bell', 'on')
        command = ('"' + str(ROOT / 'bin' / 'agent-speak.sh') +
                   '" --bell --attention check "check work" >/dev/null; sleep 60')
        pane = tmux('new-window', '-d', '-P', '-F', '#{pane_id}', command)
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
          value = tmux('display-message', '-p', '-t', pane,
                       '#{@agent_attention}|#{window_bell_flag}')
          if value == 'CHECK|1':
            break
          time.sleep(.05)
        self.assertEqual(value, 'CHECK|1')
        Path(temp, 'agent-speak').mkdir(exist_ok=True)
        Path(temp, 'agent-speak', 'mute').touch()
        command = command.replace('check "check work"', 'blocked "blocked work"')
        pane = tmux('new-window', '-d', '-P', '-F', '#{pane_id}', command)
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
          value = tmux('display-message', '-p', '-t', pane,
                       '#{@agent_attention}|#{window_bell_flag}')
          if value == 'BLOCKED|0':
            break
          time.sleep(.05)
        self.assertEqual(value, 'BLOCKED|0')
      finally:
        tmux('kill-server')

  def test_publish_summary_clear_and_title_preservation(self):
    with tempfile.TemporaryDirectory() as temp:
      socket = str(Path(temp) / 'tmux.sock')
      env = dict(os.environ, TMUX=f'{socket},1,0')
      def tmux(*args):
        return subprocess.check_output(['tmux', '-S', socket, *args],
          env=env, text=True, stderr=subprocess.DEVNULL).strip()
      pane = tmux('-f', '/dev/null', 'new-session', '-d', '-P', '-F',
                   '#{pane_id}', '-s', 'test', 'sleep 60')
      env['TMUX_PANE'] = pane
      try:
        tmux('select-pane', '-t', pane, '-T', 'Original title')
        with patch.dict(os.environ, env):
          attention.set_attention('needs-you')
          self.assertEqual(tmux('display-message', '-p', '-t', pane,
            '#{@agent_attention}|#{pane_title}'), 'YOU|Original title')
          self.assertEqual(attention.summary(), ('YOU 1', '!1'))
          attention.clear_attention()
          self.assertEqual(attention.summary(), ('', ''))
        # A detached worker has no controlling TTY and piped stdout; its
        # explicit pane binding must still ring the correct tmux window.
        tmux('set-option', '-g', 'monitor-bell', 'on')
        subprocess.run([str(ROOT / 'bin' / 'agent-attention'), 'set',
                        'blocked', '--bell'], env=env, start_new_session=True,
                       capture_output=True, check=True)
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
          value = tmux('display-message', '-p', '-t', pane,
                       '#{@agent_attention}|#{window_bell_flag}')
          if value == 'BLOCKED|1':
            break
          time.sleep(.05)
        self.assertEqual(value, 'BLOCKED|1')
      finally:
        tmux('kill-server')


if __name__ == '__main__':
  unittest.main()
