"""steamos CLI tests: device-side scripts run locally behind a fake ssh."""
import json
import base64
import os
from pathlib import Path
import runpy
import signal
import shutil
import subprocess
import sys
import tempfile
import textwrap
from concurrent.futures import ThreadPoolExecutor
import time
import unittest
import uuid
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
  child = subprocess.Popen(['sh', '-c', command], env=env, cwd=home,
                           start_new_session=True)
  code = child.wait()
  if os.environ.get('FAKE_SSH_HANGUP'):
    try:
      os.killpg(child.pid, 1)
    except ProcessLookupError:
      pass
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
    for name in ('AGENT_ID', 'CLAUDE_CODE_SESSION_ID', 'CODEX_THREAD_ID',
                 'CODEX_SESSION_ID', 'STEAMOS_NO_AGENT_ID'):
      self.env.pop(name, None)
    self.configure({'default': 'unit', 'devices': {
      'unit': {'address': '10.0.0.5', 'name': 'unit'}}})

  def configure(self, config):
    path = self.config_home / 'agent-kit' / 'steamos.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config))

  def device_home(self, host='10.0.0.5'):
    return self.root / 'devices' / host

  def run_cli(self, *args, holder=None, code=0, timeout=20):
    env = dict(self.env)
    if holder:
      env['STEAMOS_LEASE_HOLDER'] = holder
    proc = subprocess.run([sys.executable, str(BIN), *args], env=env,
                          capture_output=True, text=True, timeout=timeout)
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

  UNIT = 'agent-kit-steamos-lease-inhibit'

  def inhibitor_fixture(self):
    # Model the user manager boundary: no session process or PID signalling.
    self.env['FAKE_SSH_HANGUP'] = '1'
    self.fixture_tool('systemd-inhibit', """
      import json, os, pathlib, sys, time
      assert sys.argv[1:] == ['--list'], sys.argv
      unit = pathlib.Path.home() / 'unit'
      record = json.loads(unit.read_text()) if unit.exists() else {}
      if record.get('active') and record['expires'] > time.time() and not \
          os.environ.get('FAKE_NO_INHIBIT_LOCK'):
        print('agent-kit-steamos deck 1234 sleep:idle steamos lease block')
    """)
    self.fixture_tool('systemd-run', """
      import json, os, pathlib, sys, time
      home = pathlib.Path.home()
      args = sys.argv[1:]
      with (home / 'unit-events').open('a') as log:
        log.write('start\\n')
      if os.environ.get('FAKE_UNIT_START_FAIL'):
        sys.exit(1)
      (home / 'unit').write_text(json.dumps({
        'args': args, 'expires': time.time() + int(args[-1]),
        'active': not os.environ.get('FAKE_UNIT_INACTIVE'),
        'crash_at': time.time() + .2 if
          os.environ.get('FAKE_UNIT_EARLY_EXIT') else None}))
    """)
    self.fixture_tool('systemctl', """
      import json, os, pathlib, sys, time
      home = pathlib.Path.home()
      args = sys.argv[1:]
      if args == ['--user', 'show-environment']:
        sys.exit(1 if os.environ.get('FAKE_NO_USER_MANAGER') else 0)
      assert args[0] == '--user', args
      assert args[-1] == 'agent-kit-steamos-lease-inhibit', args
      unit = home / 'unit'
      if args[1] == 'stop':
        with (home / 'unit-events').open('a') as log:
          log.write('stop\\n')
        if os.environ.get('FAKE_UNIT_STOP_FAIL'):
          sys.exit(1)
        unit.unlink(missing_ok=True)
        sys.exit(0)
      assert args[1] == 'is-active', args
      record = json.loads(unit.read_text()) if unit.exists() else {}
      active = record.get('active') and record['expires'] > time.time()
      if record.get('crash_at') and time.time() >= record['crash_at']:
        active = False
      print('active' if active else 'inactive')
      sys.exit(0 if active else 3)
    """)

  def unit_record(self):
    return json.loads((self.device_home() / 'unit').read_text())

  def unit_events(self):
    return (self.device_home() / 'unit-events').read_text().splitlines()

  def test_inhibitor_take_refresh_renew_survive_session_hangup(self):
    self.inhibitor_fixture()
    proc = self.run_cli('lease', 'take', 'bench', '--hours', '1')
    self.assertIn('inhibit=active', proc.stdout)
    args = self.unit_record()['args']
    self.assertEqual(args[:-1], [
      '--user', f'--unit={self.UNIT}', '--collect', '--quiet',
      'systemd-inhibit', '--what=sleep:idle', '--who=agent-kit-steamos',
      '--why=steamos lease: agent-a', '--mode=block', 'sleep'])
    self.assertTrue(3590 <= int(args[-1]) <= 3600)
    self.assertIs(self.lease_json()['sleep_inhibited'], True)
    status = json.loads(self.run_cli('status', '--json').stdout)
    self.assertIs(status['lease']['sleep_inhibited'], True)
    events = self.unit_events()
    for command in (('take', 'theirs'), ('renew',), ('release',)):
      self.run_cli('lease', *command, holder='agent-b', code=1)
      self.assertEqual(self.unit_events(), events)
    for command in (('take', 'refresh'), ('renew', '--hours', '2')):
      self.run_cli('lease', *command)
      self.assertEqual(self.unit_events()[-2:], ['stop', 'start'])
    self.assertTrue(7190 <= int(self.unit_record()['args'][-1]) <= 7200)
    self.assertFalse((self.device_home() / '.agent-kit-steamos-lease' /
                      'inhibit').exists())
    self.run_cli('lease', 'release')
    self.assertFalse((self.device_home() / 'unit').exists())
    self.assertIs(self.lease_json()['sleep_inhibited'], False)

  def test_inhibitor_break_and_reclaim_stop_previous_unit(self):
    self.inhibitor_fixture()
    self.run_cli('lease', 'take', 'bench')
    self.run_cli('lease', 'break', '--reason', 'owner requested', holder='owner')
    self.assertFalse((self.device_home() / 'unit').exists())
    self.run_cli('lease', 'take', 'bench')
    self.age_lease(5 * 3600)
    self.run_cli('lease', 'take', 'new work', holder='agent-b')
    self.assertEqual(self.unit_events()[-3:], ['stop', 'stop', 'start'])
    self.assertIn('agent-b', self.unit_record()['args'][-4])
    lock = self.device_home() / '.agent-kit-steamos-lease'
    (lock / 'info').unlink()
    old = time.time() - 31 * 60
    os.utime(lock, (old, old))
    self.run_cli('lease', 'take', 'recover broken lease')
    self.assertEqual(self.unit_events()[-3:], ['stop', 'stop', 'start'])

  def test_inhibitor_disabled_and_unavailable(self):
    self.inhibitor_fixture()
    self.run_cli('lease', 'take', 'bench')
    self.configure({'devices': {'unit': {'address': '10.0.0.5'}},
                    'lease': {'prevent_sleep': False}})
    proc = self.run_cli('lease', 'renew')
    self.assertIn('inhibit=disabled', proc.stdout)
    self.assertIs(self.lease_json()['sleep_inhibited'], False)
    self.assertFalse((self.device_home() / 'unit').exists())
    self.configure({'devices': {'unit': {'address': '10.0.0.5'}}})
    self.env['FAKE_NO_USER_MANAGER'] = '1'
    proc = self.run_cli('lease', 'renew')
    self.assertIn('inhibit=unavailable', proc.stdout)
    self.assertEqual(proc.stderr, '')
    self.assertIsNone(self.lease_json()['sleep_inhibited'])
    del self.env['FAKE_NO_USER_MANAGER']
    # Force missing systemd-run even on a Linux test host.
    (self.root / 'bin' / 'systemd-run').unlink()
    tools = self.root / 'portable-tools'
    tools.mkdir()
    for name in ('python3', 'sh', 'cat', 'sed', 'head', 'tr', 'date',
                 'find', 'mkdir', 'mv', 'rm', 'sleep'):
      (tools / name).symlink_to(shutil.which(name))
    self.env['PATH'] = str(self.root / 'bin') + os.pathsep + str(tools)
    proc = self.run_cli('lease', 'renew')
    self.assertIn('inhibit=unavailable', proc.stdout)
    self.assertEqual(proc.stderr, '')
    self.assertIsNone(self.lease_json()['sleep_inhibited'])

  def test_inhibitor_start_failure_keeps_take_refresh_renew_and_reclaim(self):
    self.inhibitor_fixture()
    self.env['FAKE_UNIT_START_FAIL'] = '1'
    for command, result, who in (
        (('take', 'bench'), 'took', 'agent-a'),
        (('take', 'refresh'), 'refreshed', 'agent-a'),
        (('renew', '--hours', '6'), 'refreshed', 'agent-a'),
        (('take', 'reclaim'), 'reclaimed', 'agent-b')):
      with self.subTest(command=command):
        if result == 'reclaimed':
          self.age_lease(7 * 3600)
        proc = self.run_cli('lease', *command, '--json', holder=who)
        lease = json.loads(proc.stdout)
        self.assertEqual(lease['result'], result)
        self.assertEqual(lease['state'], 'active')
        self.assertEqual(lease['inhibit'], 'failed')
        self.assertIs(lease['sleep_inhibited'], False)
        self.assertIn('Warning:', proc.stderr)
        self.assertEqual(self.lease_json()['holder'], who)
        self.assertIs(self.lease_json()['sleep_inhibited'], False)
        self.assert_no_leftovers()
        self.assertFalse((self.device_home() / 'unit').exists())
    del self.env['FAKE_UNIT_START_FAIL']
    self.run_cli('lease', 'renew', holder='agent-b')
    self.assertIs(self.lease_json()['sleep_inhibited'], True)

  def test_inhibitor_verifies_delayed_unit_and_logind_lock(self):
    self.inhibitor_fixture()
    flags = ('FAKE_UNIT_INACTIVE', 'FAKE_UNIT_EARLY_EXIT',
             'FAKE_NO_INHIBIT_LOCK')
    for flag in flags:
      for other in flags:
        self.env.pop(other, None)
      with self.subTest(flag=flag):
        self.env[flag] = '1'
        proc = self.run_cli('lease', 'take', 'bench')
        self.assertIn('inhibit=failed', proc.stdout)
        self.assertIn('Warning:', proc.stderr)
        self.assertFalse((self.device_home() / 'unit').exists())
        self.assertIs(self.lease_json()['sleep_inhibited'], False)
        del self.env[flag]

  def test_inhibitor_stop_failure_preserves_lease(self):
    self.inhibitor_fixture()
    self.run_cli('lease', 'take', 'bench')
    self.env['FAKE_UNIT_STOP_FAIL'] = '1'
    self.run_cli('lease', 'release', code=1)
    self.assertEqual(self.lease_json()['state'], 'active')
    self.assertIs(self.lease_json()['sleep_inhibited'], True)

  def test_inhibitor_expires_without_lease_cleanup(self):
    self.inhibitor_fixture()
    self.run_cli('lease', 'take', 'short', '--hours', str(2 / 3600))
    time.sleep(2)
    self.assertIs(self.lease_json()['sleep_inhibited'], False)
    self.assertTrue((self.device_home() / '.agent-kit-steamos-lease').exists())

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
                  {'prevent_sleep': 'false'},
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
        # Valve's helper reports success with an empty string.
        print(os.environ.get('FAKE_SHORTCUT_REPLY', '{"success": ""}'))
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

  def fixture_tool(self, name, script):
    path = self.root / 'bin' / name
    path.write_text('#!/usr/bin/env python3\n' + textwrap.dedent(script))
    path.chmod(0o755)

  def launch_records(self, host='10.0.0.5'):
    home = self.device_home(host)
    home.mkdir(parents=True, exist_ok=True)
    (home / '.agent-kit-steamos-launches').write_text(
      '100 Demo1 agent-a\n120 Other2 agent-b\n130 Demo1 agent-a\n'
      'broken record\n999 bad-name agent-a\n')

  def journal_fixture(self):
    self.fixture_tool('journalctl', '''
      import json, os, pathlib, sys
      (pathlib.Path.home() / 'journal-args').write_text(
        json.dumps(sys.argv[1:]))
      if os.environ.get('FAKE_JOURNAL_FAIL'):
        sys.exit(1)
      print('1970-01-01T00:02:11+00:00 steam: first')
      print('1970-01-01T00:02:12+00:00 steam: connected=1')
      print('1970-01-01T00:02:13+00:00 steam: last')
    ''')

  def test_launch_appends_device_window_and_holder(self):
    self.title_helpers()
    self.run_cli('lease', 'take', 'launch')
    steam = self.device_home() / '.steam'
    steam.mkdir()
    (steam / 'steam.pid').write_text(str(os.getpid()))
    for fail in (False, True):
      if fail:
        self.env['FAKE_LAUNCH_FAIL'] = '1'
      proc = self.run_cli('title', 'launch', 'Demo1', '--json',
                          code=1 if fail else 0)
      epoch = json.loads(proc.stdout)['device_time']
      records = (self.device_home() / '.agent-kit-steamos-launches')
      self.assertEqual(records.read_text().splitlines()[-1].split(),
                       [str(epoch), 'Demo1', 'agent-a'])
    self.assertEqual(len(records.read_text().splitlines()), 2)

  def test_logs_title_window_journal_arguments_and_line_limit(self):
    self.launch_records()
    self.journal_fixture()
    result = json.loads(self.run_cli('logs', 'Demo1', '--until', '140',
                                     '--lines', '2', '--json').stdout)
    self.assertEqual(result, {
      'source': 'journalctl', 'since': 130, 'until': 140,
      'lines': ['1970-01-01T00:02:12+00:00 steam: connected=1',
                '1970-01-01T00:02:13+00:00 steam: last']})
    args = json.loads((self.device_home() / 'journal-args').read_text())
    self.assertEqual(args, ['--user', '--since', '@130', '--until', '@140',
                            '-o', 'short-iso', '--no-pager'])
    self.assertFalse((self.device_home() / '.agent-kit-steamos-lease')
                     .exists())
    result = json.loads(self.run_cli('logs', '--json').stdout)
    self.assertEqual(result['since'], 130)
    self.assertLess(abs(result['until'] - time.time()), 3)
    self.assertIn('first', self.run_cli('logs', '--since', '100').stdout)

  def test_logs_missing_launch_requires_explicit_since(self):
    self.journal_fixture()
    self.launch_records()
    proc = self.run_cli('logs', 'Missing3', code=1)
    self.assertIn('--since', proc.stderr)
    self.run_cli('logs', 'Missing3', '--since', '100')
    (self.device_home() / '.agent-kit-steamos-launches').unlink()
    self.run_cli('logs', code=1)

  def test_logs_console_fallback_filters_window_and_continuations(self):
    self.launch_records()
    self.journal_fixture()
    self.env['FAKE_JOURNAL_FAIL'] = '1'
    console = (self.device_home() / '.local/share/Steam/logs'
               / 'console-linux.txt')
    console.parent.mkdir(parents=True)
    console.write_text(
      'orphan\n[1970-01-01 00:02:09] before\nold continuation\n'
      '[1970-01-01 00:02:10] first\nstack frame\n'
      '[1970-01-01 00:02:20] last\n'
      '[1970-01-01 00:02:21] after\nafter continuation\n')
    # Steam's console timestamps use the device's local timezone.
    self.env['TZ'] = 'UTC'
    result = json.loads(self.run_cli('logs', '--until', '140',
                                     '--json').stdout)
    self.assertEqual(result['source'], 'console-linux.txt')
    self.assertEqual(result['lines'], [
      '[1970-01-01 00:02:10] first', 'stack frame',
      '[1970-01-01 00:02:20] last'])
    console.unlink()
    self.run_cli('logs', '--since', '100', code=1)

  def test_logs_invalid_values_fail_before_ssh(self):
    for args in (('bad-name',), ('--since', '-1'),
                 ('--until', 'nan'), ('--since', '2', '--until', '1'),
                 ('--lines', '0'), ('--lines', '1000001'),
                 ('--since', '999999999999999999999'),
                 ('--since', '1; touch injected')):
      self.run_cli('logs', *args, code=2)
    self.assertFalse((self.root / 'hosts.log').exists())

  def test_transfer_connection_values_and_holder_are_validated(self):
    for entry in ({'name': 'unit; touch bad'}, {'address': '-oProxyCommand=x'},
                  {'name': 'unit', 'user': 'deck; touch bad'}):
      self.configure({'devices': {'unit': entry}})
      self.run_cli('capture', code=2)
    self.assertFalse((self.root / 'hosts.log').exists())
    self.configure({'devices': {'unit': {'address': '10.0.0.5'}}})
    for identity in ('agent\nforged', 'agent\0bad'):
      module = runpy.run_path(str(BIN))
      with mock.patch.dict(os.environ, self.env), \
          mock.patch.dict(module['title_command'].__globals__,
                          holder=lambda config: identity):
        self.assertEqual(module['main'](['title', 'launch', 'Demo1']), 2)
    self.assertFalse((self.root / 'hosts.log').exists())

  def test_launch_ledger_failure_does_not_launch(self):
    self.title_helpers()
    self.run_cli('lease', 'take', 'launch')
    steam = self.device_home() / '.steam'
    steam.mkdir()
    (steam / 'steam.pid').write_text(str(os.getpid()))
    ledger = self.device_home() / '.agent-kit-steamos-launches'
    target = self.root / 'unrelated'
    target.write_text('preserve')
    ledger.symlink_to(target)
    proc = self.run_cli('title', 'launch', 'Demo1', '--json', code=1)
    self.assertFalse(json.loads(proc.stdout)['launched'])
    self.assertEqual(target.read_text(), 'preserve')
    self.assertFalse((self.device_home() / 'launch-time').exists())

  def capture_fixture(self):
    image = Path('/tmp') / f'gamescope-test-{uuid.uuid4().hex}.png'
    self.addCleanup(lambda: image.unlink(missing_ok=True))
    self.env['FAKE_CAPTURE_PATH'] = str(image)
    self.fixture_tool('pgrep', '''
      import os, re, sys
      # As on SteamOS: gamescope's process name is gamescope-wl.
      assert sys.argv[1] == '-x'
      running = not os.environ.get('FAKE_NO_GAMESCOPE')
      sys.exit(0 if running and re.fullmatch(sys.argv[2], 'gamescope-wl')
               else 1)
    ''')
    self.fixture_tool('xprop', '''
      import base64, json, os, pathlib, sys
      assert os.environ['DISPLAY'] == ':0'
      assert sys.argv[1:] == [
        '-root', '-f', 'GAMESCOPECTRL_DEBUG_REQUEST_SCREENSHOT', '32c',
        '-set', 'GAMESCOPECTRL_DEBUG_REQUEST_SCREENSHOT', '1']
      (pathlib.Path.home() / 'xprop-args').write_text(json.dumps(sys.argv))
      if os.environ.get('FAKE_XPROP_FAIL'):
        sys.exit(1)
      if not os.environ.get('FAKE_CAPTURE_TIMEOUT'):
        pathlib.Path(os.environ['FAKE_CAPTURE_PATH']).write_bytes(
          base64.b64decode(os.environ['FAKE_PNG']))
    ''')
    self.env['FAKE_PNG'] = base64.b64encode(
      b'\x89PNG\r\n\x1a\n' + b'fixture' +
      b'\x00\x00\x00\x00IEND\xaeB`\x82').decode()
    self.fixture_tool('scp', '''
      import json, os, pathlib, shutil, sys
      args = sys.argv[1:]
      with open(os.path.join(os.environ['FAKE_SSH_ROOT'], 'scp.log'), 'a') as f:
        f.write(json.dumps(args) + '\\n')
      remote, out = args[-2:]
      host, path = remote.split(':', 1)
      if host.split('@')[-1] in os.environ.get('FAKE_SCP_DOWN', '').split():
        sys.exit(255)
      if os.environ.get('FAKE_SCP_FAIL'):
        pathlib.Path(out).write_text('partial transfer')
        sys.exit(1)
      shutil.copyfile(path, out)
    ''')
    return image

  def test_capture_copies_new_png_and_cleans_only_its_temp_file(self):
    image = self.capture_fixture()
    stale = Path('/tmp') / f'gamescope-stale-{uuid.uuid4().hex}.png'
    stale.write_bytes(b'keep')
    self.addCleanup(lambda: stale.unlink(missing_ok=True))
    out = self.root / 'capture with spaces.png'
    proc = self.run_cli('capture', '--out', str(out))
    self.assertIn(str(out), proc.stdout)
    self.assertEqual(out.read_bytes(), base64.b64decode(self.env['FAKE_PNG']))
    self.assertFalse(image.exists())
    self.assertEqual(stale.read_bytes(), b'keep')
    args = json.loads((self.root / 'scp.log').read_text().splitlines()[0])
    self.assertIn('BatchMode=yes', args)
    remote = Path(args[-2].split(':', 1)[1])
    self.assertFalse(remote.exists())
    self.assertFalse(remote.parent.exists())
    self.assertFalse((self.device_home() / '.agent-kit-steamos-lease')
                     .exists())

  def test_capture_default_output_and_copy_address_fallback(self):
    self.capture_fixture()
    self.env['FAKE_SCP_DOWN'] = '10.0.0.5'
    module = runpy.run_path(str(BIN))
    with mock.patch.dict(os.environ, self.env), \
        mock.patch.object(module['os'], 'getcwd', return_value=str(self.root)):
      self.assertEqual(module['main'](['capture']), 0)
    outputs = list(self.root.glob('steamos-capture-unit-*.png'))
    self.assertEqual(len(outputs), 1)
    args = [json.loads(line) for line in
            (self.root / 'scp.log').read_text().splitlines()]
    self.assertEqual([a[-2].split(':')[0] for a in args],
                     ['deck@10.0.0.5', 'deck' + '@' + 'unit.local'])

  def test_capture_refuses_desktop_mode_and_failed_xprop(self):
    self.capture_fixture()
    self.env['FAKE_NO_GAMESCOPE'] = '1'
    proc = self.run_cli('capture', '--out', str(self.root / 'out'), code=1)
    self.assertIn('Game Mode', proc.stderr)
    self.assertFalse((self.device_home() / 'xprop-args').exists())
    del self.env['FAKE_NO_GAMESCOPE']
    self.env['FAKE_XPROP_FAIL'] = '1'
    self.run_cli('capture', '--out', str(self.root / 'out'), code=1)
    self.assertFalse((self.root / 'scp.log').exists())

  def test_capture_timeout_is_bounded_and_copy_failure_cleans_remote(self):
    image = self.capture_fixture()
    self.env['FAKE_CAPTURE_TIMEOUT'] = '1'
    proc = self.run_cli('capture', '--out', str(self.root / 'out'), code=1)
    self.assertIn('timed out', proc.stderr)
    del self.env['FAKE_CAPTURE_TIMEOUT']
    self.env['FAKE_SCP_FAIL'] = '1'
    (self.root / 'out').write_text('preserve existing output')
    self.run_cli('capture', '--out', str(self.root / 'out'), code=3)
    self.assertEqual((self.root / 'out').read_text(),
                     'preserve existing output')
    self.assertEqual(list(self.root.glob('.steamos-download-*')), [])
    self.assertFalse(image.exists())
    remote = Path(json.loads((self.root / 'scp.log').read_text())[-2]
                  .split(':', 1)[1])
    self.assertFalse(remote.parent.exists())

  def test_capture_signals_cleanup_lock_and_private_temp(self):
    self.capture_fixture()
    self.env['FAKE_CAPTURE_TIMEOUT'] = '1'
    home = self.device_home()
    home.mkdir(parents=True)
    module = runpy.run_path(str(BIN))
    script = module['CAPTURE_SCRIPT'].split("<<'PY'\n", 1)[1].rsplit(
      '\nPY', 1)[0]
    # Isolate gamescope files and temp folders so cleanup has its own oracle.
    script = script.replace("root = Path('/tmp')",
                            f'root = Path({str(self.root)!r})')
    for sig in (signal.SIGHUP, signal.SIGTERM):
      with self.subTest(signal=sig):
        marker = home / 'xprop-args'
        marker.unlink(missing_ok=True)
        proc = subprocess.Popen([sys.executable, '-c', script],
                                env=dict(self.env, HOME=str(home)),
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
          deadline = time.monotonic() + 5
          while not marker.exists() and time.monotonic() < deadline:
            time.sleep(.01)
          self.assertTrue(marker.exists(), 'capture did not reach PNG wait')
          lock = home / '.agent-kit-steamos-capture'
          self.assertTrue(lock.exists())
          records = list(lock.iterdir())
          metadata = records[0].read_text() if records else '{}'
          proc.send_signal(sig)
          proc.communicate(timeout=5)
          self.assertNotEqual(proc.returncode, 0)
          self.assertFalse(lock.exists())
          self.assertEqual(list(self.root.glob('steamos-capture-*')), [])
          record = json.loads(metadata)
          self.assertEqual(record['pid'], proc.pid)
          self.assertLess(abs(record['time'] - time.time()), 5)
        finally:
          if proc.poll() is None:
            proc.kill()
          proc.communicate(timeout=5)
          shutil.rmtree(home / '.agent-kit-steamos-capture', ignore_errors=True)
          for folder in self.root.glob('steamos-capture-*'):
            shutil.rmtree(folder)

  def test_capture_stale_lock_replaced_but_live_lock_refused(self):
    self.capture_fixture()
    home = self.device_home()
    home.mkdir(parents=True)
    lock = home / '.agent-kit-steamos-capture'
    # Obtain a real exited PID, rather than assuming an unused PID number.
    child = subprocess.Popen([sys.executable, '-c', 'pass'])
    child.wait()
    for pid, stamp, stale in ((os.getpid(), time.time(), False),
                              (child.pid, time.time(), True),
                              (os.getpid(), time.time() - 61, True)):
      with self.subTest(pid=pid, stale=stale):
        lock.mkdir(exist_ok=True)
        (lock / 'owner').write_text(json.dumps({'pid': pid, 'time': stamp}))
        proc = self.run_cli('capture', '--out', str(self.root / 'out'),
                            code=0 if stale else 1)
        if stale:
          self.assertFalse(lock.exists())
        else:
          self.assertIn('another capture', proc.stderr)
          self.assertTrue(lock.exists())
          (lock / 'owner').unlink()
          lock.rmdir()

  def test_capture_download_error_survives_cleanup_connection_failure(self):
    module = runpy.run_path(str(BIN))
    remote = '/tmp/steamos-capture-fixture/capture.png'
    reply = subprocess.CompletedProcess([], 0,
      'connected=1\n' + json.dumps({'path': remote}), '')
    for cleanup in (module['Failure'](3, 'cleanup ssh dropped'),
                    subprocess.CompletedProcess([], 1, '', 'cleanup denied')):
      with self.subTest(cleanup=cleanup):
        ssh = mock.Mock(side_effect=[reply, cleanup])
        with mock.patch.dict(os.environ, self.env), \
            mock.patch.dict(module['capture_command'].__globals__, {
              'ssh': ssh, 'pull_file': mock.Mock(side_effect=
                module['Failure'](3, 'download failed sentinel'))}), \
            mock.patch('sys.stderr') as stderr:
          self.assertEqual(module['main'](['capture']), 3)
        messages = ' '.join(str(c) for c in stderr.write.call_args_list)
        self.assertIn('download failed sentinel', messages)
        self.assertIn('Warning:', messages)
        self.assertIn('cleanup', messages)

  def test_fake_pgrep_returns_one_for_no_matching_process(self):
    self.capture_fixture()
    for pattern, expected in (('gamescope|gamescope-wl', 0),
                              ('nonexistent-process', 1)):
      proc = subprocess.run([str(self.root / 'bin' / 'pgrep'), '-x', pattern],
                            env=self.env)
      self.assertEqual(proc.returncode, expected)
    self.env['FAKE_NO_GAMESCOPE'] = '1'
    proc = subprocess.run([str(self.root / 'bin' / 'pgrep'), '-x',
                           'gamescope|gamescope-wl'], env=self.env)
    self.assertEqual(proc.returncode, 1)

  def test_frametimes_start_stop_need_lease_and_use_exact_control(self):
    self.fixture_tool('mangohudctl', '''
      import os, pathlib, sys
      with (pathlib.Path.home() / 'mangohud-args').open('a') as f:
        f.write(' '.join(sys.argv[1:]) + '\\n')
      sys.exit(1 if os.environ.get('FAKE_MANGOHUD_FAIL') else 0)
    ''')
    for action in ('start', 'stop'):
      self.run_cli('frametimes', action, code=1)
    self.run_cli('lease', 'take', 'theirs', holder='agent-b')
    self.run_cli('frametimes', 'start', code=1)
    self.run_cli('lease', 'release', holder='agent-b')
    self.run_cli('lease', 'take', 'frametimes')
    self.run_cli('frametimes', 'start')
    self.run_cli('frametimes', 'stop')
    args = (self.device_home() / 'mangohud-args').read_text().splitlines()
    self.assertEqual(args, ['set log_session true', 'set log_session false'])
    self.env['FAKE_MANGOHUD_FAIL'] = '1'
    self.run_cli('frametimes', 'start', code=1)
    self.age_lease(5 * 3600)
    self.run_cli('frametimes', 'stop', code=1)

  def test_frametimes_pull_newest_home_csvs_without_lease(self):
    self.capture_fixture()  # the same fake scp handles downloads
    self.launch_records()
    home = self.device_home()
    for name, stamp in (('mangoapp_old.csv', 100),
                        ('mangoapp_new.csv', 200),
                        ('mangoapp_new_summary.csv', 250),
                        ('other.csv', 300)):
      path = home / name
      path.write_text(name)
      os.utime(path, (stamp, stamp))
    outside = self.root / 'outside.csv'
    outside.write_text('private')
    (home / 'mangoapp_link.csv').symlink_to(outside)
    (home / 'mangoapp_bad;name.csv').write_text('unsafe')
    out = self.root / 'frame times'
    self.run_cli('frametimes', 'pull', '--out', str(out))
    self.assertEqual(sorted(p.name for p in out.iterdir()),
                     ['mangoapp_new.csv', 'mangoapp_new_summary.csv'])
    self.assertEqual((out / 'mangoapp_new.csv').read_text(), 'mangoapp_new.csv')
    self.assertTrue((home / 'mangoapp_old.csv').exists())
    self.assertFalse((home / '.agent-kit-steamos-lease').exists())

  def test_frametimes_pull_base_only_and_summary_only(self):
    self.capture_fixture()
    self.launch_records()
    home = self.device_home()
    (home / 'mangoapp_old.csv').write_text('old')
    os.utime(home / 'mangoapp_old.csv', (100, 100))
    base = home / 'mangoapp_latest.csv'
    base.write_text('frames')
    out = self.root / 'frames'
    proc = self.run_cli('frametimes', 'pull', '--out', str(out), code=1)
    self.assertIn('logging may still be active; run frametimes stop', proc.stderr)
    self.assertFalse(out.exists())
    self.assertFalse((self.root / 'scp.log').exists())
    self.run_cli('frametimes', 'pull', '--partial', '--out', str(out))
    self.assertEqual([p.name for p in out.iterdir()], [base.name])
    base.unlink()
    (home / 'mangoapp_latest_summary.csv').write_text('summary')
    proc = self.run_cli('frametimes', 'pull', '--out', str(out), code=1)
    self.assertIn('base CSV', proc.stderr)
    self.run_cli('frametimes', 'pull', '--partial', '--out', str(out), code=1)
    self.assertFalse((out / 'mangoapp_latest_summary.csv').exists())

  def test_frametimes_pull_no_logs_is_refused(self):
    self.run_cli('frametimes', 'pull', '--out', str(self.root / 'out'), code=1)

  def test_capture_rejects_untrusted_remote_reply_before_copy_or_delete(self):
    module = runpy.run_path(str(BIN))
    for path in ('/tmp/other.png', '/tmp/steamos-capture-x/../capture.png',
                 '/tmp/steamos-capture-x/capture.png; touch bad'):
      reply = subprocess.CompletedProcess([], 0,
        'connected=1\n' + json.dumps({'path': path}), '')
      remote = mock.Mock(return_value=reply)
      with mock.patch.dict(os.environ, self.env), \
          mock.patch.dict(module['capture_command'].__globals__, ssh=remote):
        self.assertEqual(module['main'](['capture']), 3)
      self.assertEqual(remote.call_count, 1)

  def test_new_read_commands_unreachable_and_logs_mdns_fallback(self):
    self.launch_records('unit.local')
    self.journal_fixture()
    self.env['FAKE_SSH_DOWN'] = '10.0.0.5'
    self.run_cli('logs', '--json')
    self.env['FAKE_SSH_DOWN'] = '10.0.0.5 unit.local'
    for args in (('logs', '--since', '100'), ('capture',),
                 ('frametimes', 'pull')):
      self.run_cli(*args, code=3)

  def test_all_failures_strip_connection_marker(self):
    module = runpy.run_path(str(BIN))
    for message in ('connected=1\nrefused', 'failed: connected=1',
                    'refused\nconnected=1\nmore detail'):
      with self.subTest(message=message):
        failure = module['Failure'](1, message)
        self.assertNotIn('connected=1', str(failure))
        self.assertEqual(failure.code, 1)
    for code, stdout, stderr in (
        (1, 'connected=1\n{"error":"refused"}', 'diagnostic'),
        (1, 'connected=1', 'connected=1\nrefused'),
        (0, 'connected=1\n{"error":"connected=1 refused"}', '')):
      with self.subTest(code=code, stdout=stdout, stderr=stderr):
        proc = subprocess.CompletedProcess([], code, stdout, stderr)
        with self.assertRaises(module['Failure']) as caught:
          module['last_json'](proc, 'fixture')
        self.assertNotIn('connected=1', str(caught.exception))
        self.assertIn('refused', str(caught.exception))

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

  def holder_env(self, **extra):
    env = {k: v for k, v in self.env.items() if k != 'STEAMOS_LEASE_HOLDER'}
    env.pop('AGENT_ID', None)
    agent_id = self.root / 'bin' / 'agent-id'
    agent_id.write_text(
      f'#!/bin/sh\nexec {sys.executable} {BIN.parent / "agent-id"} "$@"\n')
    agent_id.chmod(0o755)
    env['XDG_STATE_HOME'] = str(self.root / 'state')
    env['CLAUDE_CODE_SESSION_ID'] = 'parent-session'
    env.update(extra)
    return env

  def steamos(self, env, *args, code=0):
    proc = subprocess.run([sys.executable, str(BIN), *args], env=env,
                          capture_output=True, text=True, timeout=30)
    self.assertEqual(proc.returncode, code, proc.stdout + proc.stderr)
    return proc

  def lease_holder(self, env, *args):
    self.steamos(env, 'lease', 'take', 'x', *args)
    return json.loads(
      self.steamos(env, 'lease', 'show', '--json').stdout)['holder']

  def release(self, env, holder):
    self.steamos(env, 'lease', 'release', '--holder', holder)

  def mint(self, env, label='helper'):
    return subprocess.run(
      [sys.executable, str(BIN.parent / 'agent-id'), 'new', label],
      env=env, capture_output=True, text=True, check=True).stdout.strip()

  def session_derived(self, env):
    return subprocess.run(
      [sys.executable, str(BIN.parent / 'agent-id'), 'show'], env=env,
      capture_output=True, text=True, timeout=20).stdout.strip()

  def test_holder_follows_session_without_override(self):
    env = self.holder_env()
    derived = self.session_derived(env)
    self.assertTrue(derived.startswith('claude-'), derived)
    self.assertEqual(self.lease_holder(env), derived)

  def test_agent_id_env_names_subagent_holder(self):
    env = self.holder_env()
    own = self.mint(env)
    env['AGENT_ID'] = own  # same session: a subagent of the minting agent
    self.assertEqual(self.lease_holder(env), own)

  def test_agent_id_needs_neither_agent_id_tool_nor_opt_in(self):
    env = self.holder_env(AGENT_ID='helper-0123456789abcdef',
                          STEAMOS_NO_AGENT_ID='1')
    (self.root / 'bin' / 'agent-id').unlink()
    env['PATH'] = str(self.root / 'bin') + os.pathsep + '/usr/bin:/bin'
    self.assertIsNone(shutil.which('agent-id', path=env['PATH']))
    env.pop('CLAUDE_CODE_SESSION_ID')
    self.assertEqual(self.lease_holder(env), 'helper-0123456789abcdef')

  def test_holder_precedence(self):
    self.configure({'default': 'unit', 'holder': 'cfg-holder', 'devices': {
      'unit': {'address': '10.0.0.5', 'name': 'unit'}}})
    env = self.holder_env(AGENT_ID='helper-0123456789abcdef',
                          STEAMOS_LEASE_HOLDER='env-holder')
    for flags, expected in (((), 'env-holder'),
                            (('--holder', 'flag-holder'), 'flag-holder')):
      self.assertEqual(self.lease_holder(env, *flags), expected)
      self.release(env, expected)
    env.pop('STEAMOS_LEASE_HOLDER')
    self.assertEqual(self.lease_holder(env), 'helper-0123456789abcdef')
    self.release(env, 'helper-0123456789abcdef')
    env.pop('AGENT_ID')
    self.assertEqual(self.lease_holder(env), 'cfg-holder')
    self.release(env, 'cfg-holder')
    self.configure({'default': 'unit', 'devices': {
      'unit': {'address': '10.0.0.5', 'name': 'unit'}}})
    self.assertEqual(self.lease_holder(env), self.session_derived(env))

  def test_invalid_agent_id_is_refused_and_empty_is_unset(self):
    for bad in ('bad id!', ' ', '\t', 'x' * 200):
      env = self.holder_env(AGENT_ID=bad)
      proc = self.steamos(env, 'lease', 'take', 'x', code=2)
      self.assertIn('AGENT_ID', proc.stderr)
      self.assertEqual(self.lease_json()['state'], 'free')
    env = self.holder_env(AGENT_ID='')
    self.assertEqual(self.lease_holder(env), self.session_derived(env))

  def test_empty_holder_flag_is_a_usage_error(self):
    for bad in ('', '  '):
      proc = self.steamos(self.holder_env(), 'lease', 'take', 'x',
                          '--holder', bad, code=2)
      self.assertIn('--holder', proc.stderr)
      self.steamos(self.holder_env(), '--holder', bad, 'lease', 'show',
                   code=2)

  def test_nested_session_ignores_inherited_agent_id(self):
    env = self.holder_env()
    own = self.mint(env)
    env['AGENT_ID'] = own
    self.assertEqual(self.lease_holder(env), own)
    self.release(env, own)
    # A nested claude -p / codex exec inherits AGENT_ID but has its own
    # session, so it must not take the lease as the spawner.
    nested = dict(env, CLAUDE_CODE_SESSION_ID='nested-session')
    holder = self.lease_holder(nested)
    self.assertNotEqual(holder, own)
    self.assertEqual(holder, self.session_derived(
      {k: v for k, v in nested.items() if k != 'AGENT_ID'}))
    self.release(nested, holder)
    # An ID the registry has never seen is trusted.
    nested['AGENT_ID'] = 'handmade-1'
    self.assertEqual(self.lease_holder(nested), 'handmade-1')

  def test_holder_flag_applies_to_each_lease_action(self):
    self.run_cli('lease', 'take', 'x', '--holder', 'sub-1')
    self.run_cli('--holder', 'sub-1', 'lease', 'renew')
    self.run_cli('lease', 'check', '--holder', 'sub-1')
    self.run_cli('lease', 'check', holder='agent-b', code=1)
    self.run_cli('lease', 'release', '--holder', 'sub-1')

  def test_holder_applies_to_commands_that_require_the_lease(self):
    self.fixture_tool('mangohudctl', '''
      import sys
    ''')
    sub = self.holder_env(AGENT_ID='helper-0123456789abcdef')
    # Default holder (agent-a) does not own the subagent's lease.
    self.steamos(sub, 'lease', 'take', 'frametimes')
    self.run_cli('frametimes', 'start', code=1)
    self.steamos(sub, 'frametimes', 'start')
    self.steamos(self.holder_env(), 'frametimes', 'stop',
                 '--holder', 'helper-0123456789abcdef')
    self.steamos(self.holder_env(), 'frametimes', 'stop', code=1)


if __name__ == '__main__':
  unittest.main()
