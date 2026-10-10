"""Fictional backend processes; no real assets, credentials or network."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

BIN = Path(__file__).resolve().parents[1] / 'bin' / 'asset-library'


class AssetLibraryTest(unittest.TestCase):
  def setUp(self):
    self.temp = tempfile.TemporaryDirectory()
    self.addCleanup(self.temp.cleanup)
    self.root = Path(self.temp.name)
    self.backend = self.root / 'fictional backend.py'
    self.backend.write_text('import os,sys,json\n'
      'print(json.dumps({"args":sys.argv[1:],"cwd":os.getcwd()}))\n'
      'sys.exit(int(os.environ.get("FIXTURE_EXIT", "0")))\n')
    self.config = self.root / 'client.json'
    self.data = {'backend': [sys.executable, str(self.backend)],
      'directory': str(self.root), 'config': str(self.root / 'owner.json')}
    self.save()

  def save(self):
    self.config.write_text(json.dumps(self.data))
    self.config.chmod(0o600)

  def run_client(self, *args, extra_env=None):
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1')
    env.update(extra_env or {})
    return subprocess.run([sys.executable, str(BIN), '--client-config',
      str(self.config), *args], capture_output=True, text=True, env=env,
      timeout=10)

  def test_status_delegates_without_shell(self):
    r = self.run_client('status')
    self.assertEqual(r.returncode, 0, r.stderr)
    self.assertEqual(json.loads(r.stdout), {'args': ['status', '--config',
      self.data['config']], 'cwd': str(self.root.resolve())})

  def test_tunnel_preserves_original_cwd_and_literal_command(self):
    result = self.run_client('tunnel', 'run', '--', 'fictional-tool',
      'literal ; $(not-a-command)', '--option=value')
    self.assertEqual(result.returncode, 0, result.stderr)
    self.assertEqual(json.loads(result.stdout)['args'], ['tunnel', 'run',
      '--config', self.data['config'], '--directory', str(Path.cwd()),
      '--', 'fictional-tool', 'literal ; $(not-a-command)', '--option=value'])

  def test_tunnel_without_command_is_refused_before_backend(self):
    result = self.run_client('tunnel', 'run', '--')
    self.assertEqual(result.returncode, 2)
    self.assertEqual(result.stdout, '')

  def test_doctor_delegates_exactly(self):
    result = self.run_client('doctor')
    self.assertEqual(result.returncode, 0, result.stderr)
    self.assertEqual(json.loads(result.stdout)['args'],
      ['doctor', '--config', self.data['config']])

  def test_fetch_passes_literal_paths_and_immutable_revision(self):
    revision = 'a' * 40
    paths = self.root / 'selected ; $(not-a-command).json'
    target = self.root / 'fresh materialization'
    r = self.run_client('fetch', '--revision', revision, '--paths-file',
      str(paths), '--into', str(target))
    self.assertEqual(r.returncode, 0, r.stderr)
    self.assertEqual(json.loads(r.stdout)['args'], ['fetch', '--config',
      self.data['config'], '--revision', revision, '--paths-file', str(paths),
      '--into', str(target)])

  def test_publish_flush_and_backend_failure_are_preserved(self):
    r = self.run_client('publish', '--flush', extra_env={'FIXTURE_EXIT': '23'})
    self.assertEqual(r.returncode, 23)
    self.assertEqual(json.loads(r.stdout)['args'], ['publish', '--config',
      self.data['config'], '--flush'])

  def test_nonimmutable_revision_refused_before_backend(self):
    for ref in ['main', 'HEAD', '-a', 'g' * 40, 'a' * 39]:
      r = self.run_client('fetch', '--revision=' + ref, '--paths-file', 'p',
        '--into', 'd')
      self.assertEqual(r.returncode, 2, ref)
      self.assertEqual(r.stdout, '')

  def test_missing_fetch_inputs_refused(self):
    r = self.run_client('fetch', '--revision', 'a' * 40)
    self.assertEqual(r.returncode, 2)
    self.assertEqual(r.stdout, '')

  def test_unknown_arguments_cannot_reach_backend(self):
    r = self.run_client('status', '--remote', 'https://elsewhere.invalid')
    self.assertEqual(r.returncode, 2)
    self.assertEqual(r.stdout, '')

  def test_config_symlink_and_untrusted_permissions_refused(self):
    original = self.config.read_bytes()
    target = self.root / 'original.json'
    self.config.rename(target)
    self.config.symlink_to(target)
    r = self.run_client('status')
    self.assertEqual(r.returncode, 2)
    self.assertEqual(r.stdout, '')
    self.config.unlink()
    self.config.write_bytes(original)
    self.config.chmod(0o666)
    r = self.run_client('status')
    self.assertEqual(r.returncode, 2)
    self.assertEqual(r.stdout, '')

  def test_owner_only_modes_accepted_and_shared_modes_refused(self):
    for mode, code in ((0o400, 0), (0o600, 0), (0o640, 2), (0o604, 2),
                       (0o200, 2), (0o700, 2), (0o500, 2), (0o4600, 2),
                       (0o2600, 2), (0o1600, 2)):
      with self.subTest(mode=oct(mode)):
        self.config.chmod(mode)
        self.assertEqual(self.run_client('status').returncode, code)
    self.config.chmod(0o600)

  def test_unknown_or_secret_configuration_refused_without_values(self):
    self.data['token'] = 'FICTIONAL-SECRET-NEVER-PRINT'
    self.save()
    r = self.run_client('status')
    self.assertEqual(r.returncode, 2)
    self.assertNotIn(self.data['token'], r.stdout + r.stderr)

  def test_command_string_relative_executable_and_missing_directory(self):
    for backend in ['python -m backend', ['python3'], []]:
      self.data['backend'] = backend
      self.save()
      r = self.run_client('status')
      self.assertEqual(r.returncode, 2)
      self.assertEqual(r.stdout, '')
    self.data['backend'] = [sys.executable, str(self.backend)]
    self.data['directory'] = str(self.root / 'absent')
    self.save()
    self.assertEqual(self.run_client('status').returncode, 2)

  def test_relative_fetch_paths_resolve_before_backend_chdir(self):
    r = self.run_client('fetch', '--revision', 'a' * 40,
      '--paths-file', 'selected.json', '--into', 'destination')
    self.assertEqual(r.returncode, 0, r.stderr)
    args = json.loads(r.stdout)['args']
    self.assertEqual(args[-3:], [str(Path.cwd() / 'selected.json'),
      '--into', str(Path.cwd() / 'destination')])

  def test_fifo_configuration_refused_without_hanging(self):
    self.config.unlink()
    os.mkfifo(self.config, 0o600)
    r = self.run_client('status')
    self.assertEqual(r.returncode, 2)
    self.assertEqual(r.stdout, '')

  def test_foreground_backend_signal_is_preserved(self):
    self.backend.write_text('import os,signal\n'
      'os.kill(os.getpid(), signal.SIGTERM)\n')
    self.assertEqual(self.run_client('status').returncode, -15)

  def test_help_needs_no_private_configuration(self):
    self.config.unlink()
    r = self.run_client('--help')
    self.assertEqual(r.returncode, 0)
    self.assertIn('fetch', r.stdout)


if __name__ == '__main__':
  unittest.main()
