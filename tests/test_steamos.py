"""steamos CLI tests: device-side scripts run locally behind a fake ssh."""
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys
import tempfile
import textwrap
from concurrent.futures import ThreadPoolExecutor
import time
import unittest
from unittest import mock

BIN = Path(__file__).resolve().parents[1] / 'bin' / 'steamos'

# Stands in for ssh: runs the remote command with sh, with HOME set to a
# per-host directory, and records each host it was asked to reach. Hosts
# named in FAKE_SSH_DOWN exit 255, as ssh does when it cannot connect;
# hosts in FAKE_SSH_DROP run the command and then exit 255, as when the
# connection drops part way.
FAKE_SSH = textwrap.dedent('''\
  #!/usr/bin/env python3
  import os, subprocess, sys
  args = sys.argv[1:]
  while args and args[0].startswith('-'):
    flag = args.pop(0)
    if flag in ('-o', '-i', '-p', '-l', '-F'):
      args.pop(0)
  host, command = args[0], ' '.join(args[1:])
  root = os.environ['FAKE_SSH_ROOT']
  with open(os.path.join(root, 'hosts.log'), 'a') as log:
    log.write(host + '\\n')
  if host.split('@')[-1] in os.environ.get('FAKE_SSH_DOWN', '').split():
    sys.stderr.write('ssh: connect: no route to host\\n')
    sys.exit(255)
  home = os.path.join(root, 'devices', host.split('@')[-1])
  os.makedirs(home, exist_ok=True)
  env = dict(os.environ, HOME=home)
  code = subprocess.run(['sh', '-c', command], env=env, cwd=home).returncode
  if host.split('@')[-1] in os.environ.get('FAKE_SSH_DROP', '').split():
    sys.exit(255)
  sys.exit(code)
''')


class SteamosTest(unittest.TestCase):
  def setUp(self):
    tmp = tempfile.TemporaryDirectory()
    self.addCleanup(tmp.cleanup)
    self.root = Path(tmp.name)
    bindir = self.root / 'bin'
    bindir.mkdir()
    ssh = bindir / 'ssh'
    ssh.write_text(FAKE_SSH)
    ssh.chmod(0o755)
    self.config_home = self.root / 'config'
    self.env = dict(
      os.environ, PATH=f'{bindir}{os.pathsep}{os.environ["PATH"]}',
      FAKE_SSH_ROOT=str(self.root), XDG_CONFIG_HOME=str(self.config_home),
      HOME=str(self.root / 'home'), STEAMOS_LEASE_HOLDER='agent-a')
    self.configure({'default': 'unit', 'devices': {
      'unit': {'address': '10.0.0.5', 'name': 'unit'}}})

  def configure(self, config):
    path = self.config_home / 'agent-kit' / 'steamos.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config))

  def device_home(self, host='10.0.0.5'):
    return self.root / 'devices' / host

  def run_cli(self, *args, holder=None, code=0):
    env = dict(self.env)
    if holder:
      env['STEAMOS_LEASE_HOLDER'] = holder
    proc = subprocess.run([sys.executable, str(BIN), *args], env=env,
                          capture_output=True, text=True, timeout=20)
    self.assertEqual(proc.returncode, code, proc.stdout + proc.stderr)
    return proc

  def run_quiet(self, *args, holder=None):
    env = dict(self.env)
    if holder:
      env['STEAMOS_LEASE_HOLDER'] = holder
    return subprocess.run([sys.executable, str(BIN), *args], env=env,
                          capture_output=True, text=True, timeout=30)

  def assert_no_leftovers(self):
    names = sorted(p.name for p in self.device_home().iterdir())
    self.assertEqual([n for n in names if '.mutex' in n or '.removed.' in n
                      or 'info.new' in n], [])

  def lease_json(self):
    return json.loads(self.run_cli('lease', 'show', '--json').stdout)

  def age_lease(self, seconds):
    info = self.device_home() / '.agent-kit-steamos-lease' / 'info'
    lines = info.read_text().splitlines()
    expires = int(next(l for l in lines if l.startswith('expires='))[8:])
    info.write_text('\n'.join(
      f'expires={expires - seconds}' if l.startswith('expires=') else l
      for l in lines) + '\n')

  def test_take_show_check_release(self):
    self.assertEqual(self.lease_json()['state'], 'free')
    self.run_cli('lease', 'take', 'deploy build 12')
    lease = self.lease_json()
    self.assertEqual((lease['state'], lease['holder'], lease['purpose']),
                     ('active', 'agent-a', 'deploy build 12'))
    self.run_cli('lease', 'check')
    self.run_cli('lease', 'check', holder='agent-b', code=1)
    self.run_cli('lease', 'release', holder='agent-b', code=1)
    self.run_cli('lease', 'release')
    self.assertEqual(self.lease_json()['state'], 'free')
    self.assert_no_leftovers()

  def test_concurrent_takes_have_exactly_one_winner(self):
    holders = [f'agent-{n}' for n in range(8)]
    with ThreadPoolExecutor(len(holders)) as pool:
      results = list(pool.map(
        lambda h: self.run_quiet('lease', 'take', 'race', holder=h),
        holders))
    winners = [h for h, r in zip(holders, results) if r.returncode == 0]
    self.assertEqual(len(winners), 1, [r.stdout + r.stderr for r in results])
    self.assertEqual(self.lease_json()['holder'], winners[0])
    self.assert_no_leftovers()

  def test_concurrent_release_and_break_leave_it_free_and_clean(self):
    for _ in range(3):
      self.run_cli('lease', 'take', 'bench')
      with ThreadPoolExecutor(2) as pool:
        release = pool.submit(self.run_quiet, 'lease', 'release')
        broke = pool.submit(self.run_quiet, 'lease', 'break', '--reason',
                            'race', holder='owner')
        codes = (release.result().returncode, broke.result().returncode)
      self.assertNotIn(2, codes)
      self.assertNotIn(3, codes)
      self.assertEqual(self.lease_json()['state'], 'free')
      self.assert_no_leftovers()

  def test_held_mutex_reports_busy_and_stale_mutex_is_cleared(self):
    mutex = self.device_home() / '.agent-kit-steamos-lease.mutex'
    mutex.mkdir(parents=True)
    (mutex / 'owner').write_text('other\n')
    proc = self.run_cli('lease', 'take', 'bench', '--json', code=1)
    self.assertEqual(json.loads(proc.stdout)['result'], 'busy')
    old = time.time() - 3 * 60
    os.utime(mutex, (old, old))
    self.run_cli('lease', 'take', 'bench')
    self.assert_no_leftovers()

  def test_check_on_own_expired_lease_says_expired(self):
    self.run_cli('lease', 'take', 'bench', '--hours', '1')
    self.age_lease(3600 + 60)
    proc = self.run_cli('lease', 'check', '--json', code=1)
    self.assertEqual(json.loads(proc.stdout)['result'], 'expired')
    self.run_cli('lease', 'renew')
    self.run_cli('lease', 'check')

  def test_dropped_connection_is_not_retried_elsewhere(self):
    self.env['FAKE_SSH_DROP'] = '10.0.0.5'
    proc = self.run_cli('lease', 'take', 'bench', code=3)
    self.assertIn('dropped', proc.stderr)
    hosts = (self.root / 'hosts.log').read_text().split()
    self.assertEqual(len(hosts), 1)

  def test_unsafe_policy_values_exit_two(self):
    for lease in ({'legacy_locks': ['../outside']},
                  {'legacy_locks': ['/absolute']},
                  {'legacy_locks': ['a b']},
                  {'hours': 'four'},
                  {'grace_minutes': None},
                  {'grace_minutes': 0.5}):
      self.configure({'default': 'unit', 'devices': {'unit': {
        'address': '10.0.0.5'}}, 'lease': lease})
      self.run_cli('lease', 'take', 'bench', code=2)
      self.run_cli('status', code=2)

  def make_devkit_source(self):
    """A local stand-in for Valve's repository; returns (url, commit)."""
    repo = self.root / 'valve'
    utils = repo / 'client' / 'devkit-utils'
    (utils / 'devkit_utils').mkdir(parents=True)
    (utils / 'steamos-get-status').write_text('#!/usr/bin/env python3\n')
    (utils / 'devkit_utils' / '__init__.py').write_text('')
    git = ['git', '-C', str(repo), '-c', 'user.name=t', '-c',
           'user.email=t@example.invalid']
    subprocess.run(['git', 'init', '-q', str(repo)], check=True)
    subprocess.run([*git, 'add', '.'], check=True)
    subprocess.run([*git, 'commit', '-q', '-m', 'drop'], check=True)
    commit = subprocess.check_output(
      ['git', '-C', str(repo), 'rev-parse', 'HEAD'], text=True).strip()
    return repo.as_uri(), commit

  def configure_devkit(self, source, commit):
    self.configure({'default': 'unit', 'devices': {'unit': {
      'address': '10.0.0.5'}}, 'devkit': {'source': source,
                                          'commit': commit}})
    self.env['XDG_CACHE_HOME'] = str(self.root / 'cache')

  def test_devkit_install_copies_pinned_helpers_and_records_commit(self):
    source, commit = self.make_devkit_source()
    self.configure_devkit(source, commit)
    stale = self.device_home() / 'devkit-utils' / 'old-helper'
    stale.parent.mkdir(parents=True)
    stale.write_text('old')
    self.run_cli('lease', 'take', 'install helpers')
    self.run_cli('devkit', 'install')
    utils = self.device_home() / 'devkit-utils'
    self.assertTrue((utils / 'steamos-get-status').is_file())
    self.assertTrue((utils / 'devkit_utils' / '__init__.py').is_file())
    self.assertFalse(stale.exists())
    self.assertEqual((utils / '.agent-kit-pin').read_text().strip(), commit)
    status = json.loads(self.run_cli('status', '--json').stdout)
    self.assertEqual((status['devkit_utils'], status['devkit_commit'],
                      status['devkit_pinned']), ('yes', commit, True))

  def test_devkit_install_reuses_cache_and_rejects_bad_pins(self):
    source, commit = self.make_devkit_source()
    self.configure_devkit(source, commit)
    self.run_cli('lease', 'take', 'install helpers')
    self.run_cli('devkit', 'install')
    subprocess.run(['rm', '-rf', str(self.root / 'valve')], check=True)
    self.run_cli('devkit', 'install')
    for bad in ('main', 'a' * 39, '../' + 'a' * 40):
      self.configure_devkit(source, bad)
      self.run_cli('devkit', 'install', code=2)

  def test_devkit_install_fails_when_commit_is_missing(self):
    source, _ = self.make_devkit_source()
    self.configure_devkit(source, 'b' * 40)
    self.run_cli('lease', 'take', 'install helpers')
    proc = self.run_cli('devkit', 'install', code=3)
    self.assertIn('could not fetch', proc.stderr)
    self.assertFalse((self.device_home() / 'devkit-utils').exists())

  def test_devkit_install_needs_the_lease(self):
    source, commit = self.make_devkit_source()
    self.configure_devkit(source, commit)
    proc = self.run_cli('devkit', 'install', code=1)
    self.assertIn('lease', proc.stderr)
    self.run_cli('lease', 'take', 'theirs', holder='agent-b')
    self.run_cli('devkit', 'install', code=1)
    self.assertFalse((self.device_home() / 'devkit-utils').exists())

  def test_devkit_install_clears_old_pin_and_refuses_symlink(self):
    source, commit = self.make_devkit_source()
    self.configure_devkit(source, commit)
    self.run_cli('lease', 'take', 'install helpers')
    utils = self.device_home() / 'devkit-utils'
    utils.mkdir(parents=True)
    pin = utils / '.agent-kit-pin'
    pin.write_text('c' * 40 + '\n')
    self.env['PATH'] = self.env['PATH'].replace(
      str(self.root / 'bin') + os.pathsep,
      str(self.root / 'bin') + os.pathsep + str(self.no_rsync()) + os.pathsep)
    self.run_cli('devkit', 'install', code=3)
    self.assertFalse(pin.exists())
    self.env['PATH'] = self.env['PATH'].replace(
      str(self.root / 'failing') + os.pathsep, '')
    target = self.root / 'elsewhere'
    target.mkdir()
    (target / 'keep').write_text('keep')
    subprocess.run(['rm', '-rf', str(utils)], check=True)
    utils.symlink_to(target)
    proc = self.run_cli('devkit', 'install', code=1)
    self.assertIn('symlink', proc.stderr)
    self.assertTrue((target / 'keep').exists())

  def no_rsync(self):
    """A directory whose rsync fails part way, as on a dropped copy."""
    failing = self.root / 'failing'
    failing.mkdir(exist_ok=True)
    rsync = failing / 'rsync'
    rsync.write_text('#!/bin/sh\necho "partial transfer" >&2\nexit 23\n')
    rsync.chmod(0o755)
    return failing

  def test_bad_devkit_config_does_not_break_status(self):
    self.configure({'default': 'unit', 'devices': {'unit': {
      'address': '10.0.0.5'}}, 'devkit': {'commit': 'main'}})
    status = json.loads(self.run_cli('status', '--json').stdout)
    self.assertFalse(status['devkit_pinned'])

  def title_helpers(self, host='10.0.0.5'):
    """Small Valve stand-ins; none reaches Steam or a real device."""
    utils = self.device_home(host) / 'devkit-utils'
    utils.mkdir(parents=True, exist_ok=True)
    (utils / '.agent-kit-pin').write_text(
      'a00ceb7d91ea44a0c3e714a91a06417d6e5cdb33\n')
    scripts = {
      'steamos-prepare-upload': '''
        import json, pathlib, sys
        home = pathlib.Path.home()
        name = sys.argv[sys.argv.index('--gameid') + 1]
        directory = home / 'devkit-game' / name
        directory.mkdir(parents=True, exist_ok=True)
        print(json.dumps({'directory': str(directory)}))
      ''',
      'steam-client-create-shortcut': '''
        import json, os, pathlib, sys
        parms = json.loads(sys.argv[sys.argv.index('--parms') + 1])
        (pathlib.Path.home() / 'parms.json').write_text(json.dumps(parms))
        print('helper progress')
        print(os.environ.get('FAKE_SHORTCUT_REPLY', '{"success": true}'))
      ''',
      'steamos-list-games': '''
        import json, pathlib
        root = pathlib.Path.home() / 'devkit-game'
        print(json.dumps([{'gameid': p.name} for p in root.iterdir()
                          if p.is_dir()] if root.exists() else []))
      ''',
      'steamos-delete': '''
        import os, pathlib, shutil, sys
        assert sys.argv[1:] == ['--delete-title', 'Demo1']
        shutil.rmtree(pathlib.Path.home() / 'devkit-game' / 'Demo1')
        if os.environ.get('FAKE_SYNC_FAIL'):
          print('Steam client sync of devkit games failed', file=sys.stderr)
        if os.environ.get('FAKE_DELETE_EXIT'):
          print('RPC unavailable', file=sys.stderr)
          sys.exit(int(os.environ['FAKE_DELETE_EXIT']))
      ''',
      'steam-devkit-rpc': '''
        import os, pathlib, sys, time
        assert sys.argv[1:] == ['run-game', 'gameid=Demo1']
        (pathlib.Path.home() / 'launch-time').write_text(str(int(time.time())))
        print('launched')
        if os.environ.get('FAKE_LAUNCH_FAIL'):
          print('Steam RPC failed', file=sys.stderr)
          sys.exit(255)
      ''',
    }
    for name, script in scripts.items():
      (utils / name).write_text(textwrap.dedent(script))
    source = self.root / 'build'
    source.mkdir(exist_ok=True)
    (source / 'run.sh').write_text('#!/bin/sh\nexit 0\n')
    return source

  def register(self, source, *args, code=0, name='Demo1'):
    return self.run_cli('title', 'register', name, str(source),
                        '--start', './run.sh', *args, code=code)

  def test_title_names_rejected_before_device_calls(self):
    source = self.title_helpers()
    for name in ('bad-name', 'bad_name', '../Demo', 'é', '', 'bad name'):
      for action in ('register', 'launch', 'remove'):
        if action == 'register':
          proc = self.register(source, name=name, code=2)
        else:
          proc = self.run_cli('title', action, name, code=2)
        self.assertIn('letters and digits', proc.stderr)
    self.assertFalse((self.root / 'hosts.log').exists())

  def test_title_mutations_need_lease_and_pinned_helpers(self):
    source = self.title_helpers()
    for action in ('register', 'launch', 'remove'):
      args = [str(source), '--start', './run.sh'] if action == 'register' \
        else []
      proc = self.run_cli('title', action, 'Demo1', *args, code=1)
      self.assertIn('lease', proc.stderr)
    self.run_cli('lease', 'take', 'titles', holder='agent-b')
    self.register(source, code=1)
    self.run_cli('lease', 'release', holder='agent-b')
    self.run_cli('lease', 'take', 'titles')
    (self.device_home() / 'devkit-utils' / '.agent-kit-pin').unlink()
    proc = self.register(source, code=1)
    self.assertIn('steamos devkit install', proc.stderr)

  def test_register_copies_and_passes_exact_shortcut_parameters(self):
    source = self.title_helpers()
    self.run_cli('lease', 'take', 'titles')
    self.register(source, '--arg=--flag', '--arg', 'space and $quote',
                  '--json')
    home = self.device_home()
    directory = home / 'devkit-game' / 'Demo1'
    self.assertEqual((directory / 'run.sh').read_text(),
                     (source / 'run.sh').read_text())
    parms = json.loads((home / 'parms.json').read_text())
    self.assertEqual(parms, {
      'gameid': 'Demo1', 'directory': str(directory),
      'argv': ['./run.sh', '--flag', 'space and $quote'], 'env': {},
      'settings': {'steam_play': '0', 'compat_tool': 'SteamLinuxRuntime_4'},
      'clear_settings': True, 'force_appid': None, 'lepton_args': ''})
    self.register(source, '--runtime', 'none')
    parms = json.loads((home / 'parms.json').read_text())
    # Valve's helper reads compat_tool unconditionally; its own GUI sends
    # an empty string when no runtime is selected.
    self.assertEqual(parms['settings'],
                     {'steam_play': '0', 'compat_tool': ''})

  def test_register_zero_exit_json_error_and_malformed_reply_fail(self):
    source = self.title_helpers()
    self.run_cli('lease', 'take', 'titles')
    self.env['FAKE_SHORTCUT_REPLY'] = '{"error": "Steam is not running"}'
    proc = self.register(source, code=1)
    self.assertIn('Steam is not running', proc.stderr)
    self.env['FAKE_SHORTCUT_REPLY'] = 'not JSON'
    self.register(source, code=3)
    for reply in ('null', '[]', '"success"', '{}'):
      self.env['FAKE_SHORTCUT_REPLY'] = reply
      self.register(source, code=3)

  def test_register_uses_mdns_fallback_and_preserves_other_titles(self):
    self.env['FAKE_SSH_DOWN'] = '10.0.0.5'
    source = self.title_helpers('unit.local')
    other = self.device_home('unit.local') / 'devkit-game' / 'Other2'
    other.mkdir(parents=True)
    (other / 'keep').write_text('keep')
    self.run_cli('lease', 'take', 'titles')
    self.register(source)
    self.assertEqual((other / 'keep').read_text(), 'keep')
    self.assertTrue((other.parent / 'Demo1' / 'run.sh').is_file())

  def test_register_refuses_symlinked_destination(self):
    source = self.title_helpers()
    self.run_cli('lease', 'take', 'titles')
    root = self.device_home() / 'devkit-game'
    root.mkdir()
    (root / 'Demo1').symlink_to(source)
    proc = self.register(source, code=1)
    self.assertIn('symlink', proc.stderr)
    self.assertFalse((self.device_home() / 'parms.json').exists())

  def test_register_start_must_be_inside_source_and_exist(self):
    source = self.title_helpers()
    for start in ('/bin/sh', '../run.sh', 'missing'):
      self.run_cli('title', 'register', 'Demo1', str(source),
                   '--start', start, code=2)
    self.run_cli('title', 'register', 'Demo1', str(source), code=2)
    self.assertFalse((self.root / 'hosts.log').exists())

  def test_invalid_leftovers_listed_and_registration_refused(self):
    source = self.title_helpers()
    root = self.device_home() / 'devkit-game'
    root.mkdir()
    for name, suffix in (('bad-name', 'argv'), ('bad_name', 'settings')):
      (root / name).mkdir()
      (root / f'{name}-{suffix}.json').write_text('{}')
    (root / 'harmless-folder').mkdir()
    proc = self.run_cli('title', 'list', '--json')
    result = json.loads(proc.stdout)
    self.assertEqual(result['invalid_leftovers'], ['bad-name', 'bad_name'])
    self.assertIn('bad-name', self.run_cli('title', 'list').stdout)
    self.run_cli('lease', 'take', 'titles')
    proc = self.register(source, code=1)
    self.assertIn('bad-name', proc.stderr)
    self.assertIn('bad_name', proc.stderr)
    self.assertFalse((root / 'Demo1').exists())
    self.assertTrue((root / 'bad-name-argv.json').exists())

  def test_remove_only_own_title_and_reports_sync_failure(self):
    self.title_helpers()
    root = self.device_home() / 'devkit-game'
    for name in ('Demo1', 'Other2'):
      (root / name).mkdir(parents=True)
      for suffix in ('argv', 'env', 'settings'):
        (root / f'{name}-{suffix}.json').write_text('keep')
    self.run_cli('lease', 'take', 'titles')
    result = json.loads(self.run_cli('title', 'remove', 'Demo1',
                                     '--json').stdout)
    self.assertTrue(result['steam_sync'])
    self.assertEqual(sorted(p.name for p in root.iterdir()),
                     ['Other2', 'Other2-argv.json', 'Other2-env.json',
                      'Other2-settings.json'])
    (root / 'Demo1').mkdir()
    self.env['FAKE_SYNC_FAIL'] = '1'
    proc = self.run_cli('title', 'remove', 'Demo1', '--json', code=1)
    self.assertFalse(json.loads(proc.stdout)['steam_sync'])

  def test_remove_refuses_named_invalid_leftovers_before_deleting(self):
    self.title_helpers()
    root = self.device_home() / 'devkit-game'
    (root / 'Demo1').mkdir(parents=True)
    (root / 'bad-name').mkdir()
    (root / 'bad-name-argv.json').write_text('{}')
    self.run_cli('lease', 'take', 'titles')
    proc = self.run_cli('title', 'remove', 'Demo1', code=1)
    self.assertIn('bad-name', proc.stderr)
    self.assertTrue((root / 'Demo1').is_dir())
    self.assertTrue((root / 'bad-name-argv.json').is_file())

  def test_remove_cleans_configs_after_nonzero_helper_exit(self):
    self.title_helpers()
    root = self.device_home() / 'devkit-game'
    for name in ('Demo1', 'Other2'):
      (root / name).mkdir(parents=True)
      for suffix in ('argv', 'env', 'settings'):
        (root / f'{name}-{suffix}.json').write_text('keep')
    self.run_cli('lease', 'take', 'titles')
    self.env['FAKE_DELETE_EXIT'] = '7'
    proc = self.run_cli('title', 'remove', 'Demo1', '--json', code=1)
    self.assertEqual(sorted(p.name for p in root.iterdir()),
                     ['Other2', 'Other2-argv.json', 'Other2-env.json',
                      'Other2-settings.json'])
    result = json.loads(proc.stdout)
    self.assertFalse(result['steam_sync'])
    self.assertIn('RPC unavailable', result['stderr'])

  def test_register_again_mirrors_source_only_in_prepared_title(self):
    source = self.title_helpers()
    (source / 'obsolete').write_text('old')
    self.run_cli('lease', 'take', 'titles')
    self.register(source)
    root = self.device_home() / 'devkit-game'
    (root / 'Other2').mkdir()
    (root / 'Other2' / 'keep').write_text('keep')
    (root / 'Demo1-settings.json').write_text('keep config')
    (source / 'obsolete').unlink()
    (source / 'new').write_text('new')
    self.register(source)
    title = root / 'Demo1'
    self.assertEqual(sorted(p.name for p in title.iterdir()), ['new', 'run.sh'])
    self.assertEqual((root / 'Other2' / 'keep').read_text(), 'keep')
    self.assertEqual((root / 'Demo1-settings.json').read_text(), 'keep config')

  def test_register_refuses_unsafe_remote_path_before_rsync(self):
    source = self.title_helpers()
    self.run_cli('lease', 'take', 'titles')
    unsafe = self.root / 'unsafe home'
    unsafe.mkdir()
    (self.root / 'devices').rename(unsafe / 'devices')
    self.env['FAKE_SSH_ROOT'] = str(unsafe)
    proc = self.register(source, code=1)
    self.assertIn('unsafe remote path', proc.stderr)
    home = unsafe / 'devices' / '10.0.0.5'
    self.assertFalse((home / 'devkit-game' / 'Demo1' / 'run.sh').exists())

  def test_launch_reports_device_time_and_needs_running_steam(self):
    self.title_helpers()
    self.run_cli('lease', 'take', 'launch')
    proc = self.run_cli('title', 'launch', 'Demo1', code=1)
    self.assertIn('Game Mode', proc.stderr)
    steam = self.device_home() / '.steam'
    steam.mkdir()
    (steam / 'steam.pid').write_text(str(os.getpid()))
    result = json.loads(self.run_cli('--json', 'title', 'launch', 'Demo1',
                                     '--device', 'unit').stdout)
    launched = int((self.device_home() / 'launch-time').read_text())
    self.assertLessEqual(result['device_time'], launched)
    self.assertLess(launched - result['device_time'], 3)
    self.assertIn('device time',
                  self.run_cli('title', 'launch', 'Demo1').stdout.lower())
    self.env['FAKE_LAUNCH_FAIL'] = '1'
    result = json.loads(self.run_cli('title', 'launch', 'Demo1', '--json',
                                     code=1).stdout)
    self.assertFalse(result['launched'])
    self.assertIsInstance(result['device_time'], int)
    self.assertIn('Steam RPC failed', result['stderr'])

  def test_wake_packet_and_ssh_wait_are_offline(self):
    module = runpy.run_path(str(BIN))
    self.configure({'devices': {'unit': {
      'mac': '02:00:00:00:00:01', 'broadcast': '192.0.2.255'}}})
    sock = mock.MagicMock()
    options = ['wake', '--wait', '0', '--json']
    with mock.patch.dict(os.environ, self.env), \
        mock.patch.object(module['socket'], 'socket', return_value=sock), \
        mock.patch.dict(module['main'].__globals__, {
          'ssh': mock.Mock(return_value=subprocess.CompletedProcess(
            [], 0, 'connected=1\nawake=1\n', ''))}), \
        mock.patch.object(module['time'], 'sleep') as sleep, \
        mock.patch('sys.stdout'):
      self.assertEqual(module['main'](options), 0)
    packet = b'\xff' * 6 + bytes.fromhex('020000000001') * 16
    self.assertEqual(sock.__enter__.return_value.sendto.call_args_list,
                     [mock.call(packet, ('192.0.2.255', 9))] * 3)
    self.assertEqual(sleep.call_args_list, [mock.call(0.1)] * 2)
    sock.__enter__.return_value.setsockopt.assert_called_once_with(
      module['socket'].SOL_SOCKET, module['socket'].SO_BROADCAST, 1)
    with mock.patch.dict(os.environ, self.env), \
        mock.patch.object(module['socket'], 'socket', return_value=sock), \
        mock.patch.dict(module['main'].__globals__, {
          'ssh': mock.Mock(side_effect=module['Failure'](3, 'asleep'))}), \
        mock.patch('sys.stdout'):
      self.assertEqual(module['main'](options), 3)

  def test_wake_config_validation_precedes_socket_or_ssh(self):
    for entry in ({}, {'mac': 'garbage'}, {'mac': '00:00:00:00:00:GG'},
                  {'mac': '02:00:00:00:00:01', 'broadcast': 'not-an-ip'}):
      self.configure({'devices': {'unit': entry}})
      self.run_cli('wake', '--wait', '0', code=2)
    self.assertFalse((self.root / 'hosts.log').exists())

  def test_status_reports_unpinned_devkit_utils(self):
    (self.device_home() / 'devkit-utils').mkdir(parents=True)
    status = json.loads(self.run_cli('status', '--json').stdout)
    self.assertEqual((status['devkit_utils'], status['devkit_commit'],
                      status['devkit_pinned']), ('yes', '', False))

  def test_second_holder_is_refused_and_told_who_holds_it(self):
    self.run_cli('lease', 'take', 'bench')
    proc = self.run_cli('lease', 'take', 'deploy', holder='agent-b', code=1)
    self.assertIn('agent-a', proc.stderr)
    self.assertEqual(self.lease_json()['holder'], 'agent-a')

  def test_renew_keeps_start_and_extends_expiry(self):
    self.run_cli('lease', 'take', 'bench', '--hours', '1')
    before = self.lease_json()
    self.age_lease(600)
    self.run_cli('lease', 'renew', '--hours', '2')
    after = self.lease_json()
    self.assertEqual(after['start'], before['start'])
    self.assertGreater(after['expires'], before['expires'])
    self.run_cli('lease', 'renew', holder='agent-b', code=1)

  def test_expired_lease_is_reclaimed_only_after_grace(self):
    self.configure({'default': 'unit', 'devices': {'unit': {
      'address': '10.0.0.5'}}, 'lease': {'grace_minutes': 30}})
    self.run_cli('lease', 'take', 'bench', '--hours', '1')
    self.age_lease(3600 + 600)
    self.assertEqual(self.lease_json()['state'], 'expired')
    self.run_cli('lease', 'check', code=1)
    self.run_cli('lease', 'take', 'deploy', holder='agent-b', code=1)
    self.age_lease(3600)
    proc = self.run_cli('lease', 'take', 'deploy', holder='agent-b')
    self.assertIn('reclaimed', proc.stdout)
    self.assertEqual(self.lease_json()['holder'], 'agent-b')
    log = (self.device_home() / '.agent-kit-steamos-lease.log').read_text()
    self.assertIn('reclaimed', log)
    self.assertIn('holder=agent-a', log)

  def test_broken_lock_is_reclaimed_only_after_grace(self):
    lock = self.device_home() / '.agent-kit-steamos-lease'
    lock.mkdir(parents=True)
    self.assertEqual(self.lease_json()['state'], 'broken')
    self.run_cli('lease', 'take', 'deploy', code=1)
    old = time.time() - 31 * 60
    os.utime(lock, (old, old))
    self.run_cli('lease', 'take', 'deploy')
    self.assertEqual(self.lease_json()['holder'], 'agent-a')

  def test_break_follows_policy_and_logs_reason(self):
    self.configure({'default': 'unit', 'devices': {'unit': {
      'address': '10.0.0.5'}}, 'lease': {'breakers': ['owner']}})
    self.run_cli('lease', 'take', 'bench')
    self.run_cli('lease', 'break', '--reason', 'stuck', holder='agent-b',
                 code=1)
    self.assertEqual(self.lease_json()['holder'], 'agent-a')
    self.run_cli('lease', 'break', holder='owner', code=2)
    self.run_cli('lease', 'break', '--reason', 'owner cleared a stuck run',
                 holder='owner')
    self.assertEqual(self.lease_json()['state'], 'free')
    log = (self.device_home() / '.agent-kit-steamos-lease.log').read_text()
    self.assertIn('owner cleared a stuck run', log)

  def test_anyone_may_break_when_no_breakers_are_configured(self):
    self.run_cli('lease', 'take', 'bench')
    self.run_cli('lease', 'break', '--reason', 'abandoned', holder='agent-b')
    self.assertEqual(self.lease_json()['state'], 'free')

  def test_legacy_lock_blocks_take_and_is_reported(self):
    self.configure({'default': 'unit', 'devices': {'unit': {
      'address': '10.0.0.5'}}, 'lease': {
        'legacy_locks': ['.project-deck-lock']}})
    legacy = self.device_home() / '.project-deck-lock'
    legacy.mkdir(parents=True)
    (legacy / 'info').write_text('holder=someone\npurpose=play\n')
    lease = self.lease_json()
    self.assertEqual(lease['legacy'], ['.project-deck-lock'])
    self.assertIn('holder=someone', lease['legacy_info'][0])
    proc = self.run_cli('lease', 'take', 'deploy', code=1)
    self.assertIn('.project-deck-lock', proc.stderr)

  def test_unreachable_address_falls_back_to_mdns_name(self):
    self.env['FAKE_SSH_DOWN'] = '10.0.0.5'
    self.run_cli('lease', 'take', 'bench')
    hosts = (self.root / 'hosts.log').read_text().split()
    self.assertEqual([h.split('@')[-1] for h in hosts],
                     ['10.0.0.5', 'unit.local'])

  def test_unreachable_device_exits_three(self):
    self.env['FAKE_SSH_DOWN'] = '10.0.0.5 unit.local'
    proc = self.run_cli('status', code=3)
    self.assertIn('cannot reach', proc.stderr)

  def test_status_reports_lease_and_device_fields(self):
    self.run_cli('lease', 'take', 'bench')
    status = json.loads(self.run_cli('status', '--json').stdout)
    self.assertEqual(status['device'], 'unit')
    self.assertEqual(status['lease']['holder'], 'agent-a')
    for key in ('os_name', 'os_build', 'channel', 'model', 'free_bytes',
                'devkit_utils'):
      self.assertIn(key, status)
    self.assertIn('unit', self.run_cli('status').stdout)

  def test_configuration_errors_exit_two(self):
    self.run_cli('status', '--device', 'missing', code=2)
    self.run_cli('lease', 'take', 'a\nb', code=2)
    (self.config_home / 'agent-kit' / 'steamos.json').write_text('{')
    self.run_cli('status', code=2)

  def test_holder_falls_back_to_user_at_host(self):
    env = {k: v for k, v in self.env.items() if k != 'STEAMOS_LEASE_HOLDER'}
    env['PATH'] = str(self.root / 'bin') + os.pathsep + '/usr/bin:/bin'
    env['STEAMOS_NO_AGENT_ID'] = '1'
    proc = subprocess.run([sys.executable, str(BIN), 'lease', 'take', 'x'],
                          env=env, capture_output=True, text=True, timeout=20)
    self.assertEqual(proc.returncode, 0, proc.stderr)
    holder = json.loads(subprocess.run(
      [sys.executable, str(BIN), 'lease', 'show', '--json'], env=env,
      capture_output=True, text=True, timeout=20).stdout)['holder']
    self.assertIn('@', holder)


if __name__ == '__main__':
  unittest.main()
