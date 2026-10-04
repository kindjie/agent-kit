"""steamos CLI tests: device-side scripts run locally behind a fake ssh."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap
from concurrent.futures import ThreadPoolExecutor
import time
import unittest

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
