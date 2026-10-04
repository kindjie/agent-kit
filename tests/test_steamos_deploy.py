"""Versioned publication behind fake ssh and Valve helpers, never devices."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time
import unittest

from tests import test_steamos


class SteamosDeployTest(unittest.TestCase):
  setUp = test_steamos.SteamosTest.setUp
  configure = test_steamos.SteamosTest.configure
  device_home = test_steamos.SteamosTest.device_home
  run_cli = test_steamos.SteamosTest.run_cli
  fixture_tool = test_steamos.SteamosTest.fixture_tool
  title_helpers = test_steamos.SteamosTest.title_helpers
  age_lease = test_steamos.SteamosTest.age_lease

  def project(self, **settings):
    self.repo = self.root / 'project'
    self.repo.mkdir(exist_ok=True)
    (self.repo / '.git').mkdir(exist_ok=True)
    self.bundle = self.repo / 'build/bundle'
    self.bundle.mkdir(parents=True, exist_ok=True)
    (self.bundle / 'run').write_text('#!/bin/sh\nexit 0\n')
    (self.bundle / 'run').chmod(0o755)
    self.settings = {'title': 'Demo1', 'bundle': 'build/bundle', 'start': 'run',
                     'args': ['--flag', 'space and $quote'], 'runtime': 'slr4'}
    self.settings.update(settings)
    self.write_config()
    return self.seal()

  def write_config(self):
    (self.repo / 'steamos.json').write_text(json.dumps(self.settings))

  def seal(self, **extra):
    inventory = self.bundle / self.settings.get('inventory', 'bundle.json')
    inventory.parent.mkdir(parents=True, exist_ok=True)
    files = {str(p.relative_to(self.bundle)):
             hashlib.sha256(p.read_bytes()).hexdigest()
             for p in self.bundle.rglob('*') if p.is_file() and p != inventory}
    self.manifest = {'version': 1, 'files': files, **extra}
    inventory.write_text(json.dumps(self.manifest, indent=2) + '\n')
    canonical = json.dumps(self.manifest, sort_keys=True,
                           separators=(',', ':'), ensure_ascii=True).encode()
    return hashlib.sha256(canonical).hexdigest()[:12]

  def stage(self, code=0):
    return self.run_cli('stage', '--project', str(self.repo), '--json',
                        code=code)

  def deploy(self, *args, code=0):
    return self.run_cli('deploy', '--project', str(self.repo), '--json',
                        *args, code=code)

  def fixture(self, **settings):
    version = self.project(**settings)
    self.title_helpers()
    self.fixture_tool('rsync', '''
      import json, os, pathlib, shutil, sys, time
      args = sys.argv[1:]
      assert '--delete' in args, args
      source, remote = args[-2:]
      destination = pathlib.Path(remote.split(':', 1)[1])
      assert destination.name.endswith('.partial'), destination
      home = destination.parents[3]
      with (home / 'copies.log').open('a') as log:
        log.write(json.dumps(args) + '\\n')
      assert destination.is_dir() and not destination.is_symlink()
      if os.environ.get('FAKE_COPY_WAIT'):
        (destination / 'half').write_text('partial')
        (home / 'copy-started').touch()
        time.sleep(30)
      if os.environ.get('FAKE_COPY_FAIL'):
        (destination / 'half').write_text('partial')
        sys.exit(23)
      shutil.copytree(source, destination, dirs_exist_ok=True)
      if os.environ.get('FAKE_COPY_CORRUPT'):
        (destination / 'run').write_text('corrupt')
    ''')
    self.run_cli('lease', 'take', 'fixture deploy')
    self.title = self.device_home() / 'devkit-game/Demo1'
    return version

  def change(self):
    with (self.bundle / 'run').open('a') as stream:
      stream.write('# changed\n')
    return self.seal()

  def test_stage_canonical_version_and_upward_discovery(self):
    version = self.project()
    self.assertEqual(json.loads(self.stage().stdout)['version'], version)
    self.assertFalse((self.root / 'hosts.log').exists())
    inventory = self.bundle / 'bundle.json'
    inventory.write_text(json.dumps(self.manifest, separators=(',', ':')))
    child = self.repo / 'src'
    child.mkdir()
    proc = subprocess.run([sys.executable, str(test_steamos.BIN),
                           'stage', '--json'], cwd=child, env=self.env,
                          capture_output=True, text=True, timeout=20)
    self.assertEqual(proc.returncode, 0, proc.stderr)
    self.assertEqual(json.loads(proc.stdout)['version'], version)

  def test_stage_missing_extra_changed_empty_and_nonexecutable(self):
    for defect in ('missing', 'extra', 'changed', 'empty', 'nonexecutable'):
      with self.subTest(defect=defect):
        self.project()
        if defect == 'missing':
          (self.bundle / 'run').unlink()
        elif defect == 'extra':
          (self.bundle / 'extra').write_text('extra')
        elif defect == 'changed':
          (self.bundle / 'run').write_text('changed')
        elif defect == 'empty':
          (self.bundle / 'bundle.json').write_text('{"version":1,"files":{}}')
        else:
          (self.bundle / 'run').chmod(0o644)
        self.stage(code=1)
        (self.bundle / 'extra').unlink(missing_ok=True)
    self.assertFalse((self.root / 'hosts.log').exists())

  def test_inventory_rejects_paths_duplicates_types_and_self_listing(self):
    self.project()
    inventory = self.bundle / 'bundle.json'
    for path in ('../escape', '/absolute', 'a/../run', './run', 'a//run',
                 'bundle.json', 'a\\b', 'a\nname'):
      with self.subTest(path=path):
        inventory.write_text(json.dumps({'version': 1, 'files': {
          path: '0' * 64}}))
        self.stage(code=1)
    for text in ('{"version":1,"version":1,"files":{}}',
                 '{"version":true,"files":{"run":"' + '0' * 64 + '"}}',
                 '{"version":1,"files":{"run":17}}',
                 '{"version":1,"files":[],"other":null}',
                 '{"version":1,"files":{"run":"' + '0' * 64 +
                 '"},"unknown":true}'):
      inventory.write_text(text)
      self.stage(code=1)

  def test_required_extension_accepted_without_format_guessing(self):
    self.project()
    (self.bundle / 'queue.sh').write_text('#!/bin/sh\n')
    version = self.seal(required=['run', 'queue.sh'])
    self.assertEqual(json.loads(self.stage().stdout)['version'], version)
    for required in ([], ['missing'], ['../escape'], [17], 'run'):
      self.seal(required=required)
      self.stage(code=1)

  def test_symlinks_rejected_including_directory_and_inventory(self):
    self.project()
    outside = self.root / 'outside'
    outside.mkdir()
    (outside / 'keep').write_text('keep')
    for name in ('link', 'directory', 'bundle.json'):
      with self.subTest(name=name):
        path = self.bundle / name
        if path.exists():
          path.unlink()
        path.symlink_to(outside if name == 'directory' else outside / 'keep')
        self.stage(code=1)
        path.unlink()
        self.seal()
    bundle = self.bundle
    bundle.rename(bundle.with_name('real'))
    bundle.symlink_to(bundle.with_name('real'), target_is_directory=True)
    self.stage(code=1)

  def test_strict_project_config_before_ssh(self):
    self.project()
    for settings in ({'title': 'bad-name'}, {'title': 'é'}, {'title': ''},
                     {'title': 1}, {'bundle': '../escape'},
                     {'bundle': '/tmp/build'}, {'start': '../run'},
                     {'inventory': '/tmp/inventory'}, {'runtime': 'proton'},
                     {'keep_versions': True}, {'keep_versions': 0},
                     {'keep_versions': 1.5}, {'args': 'one'}, {'args': [1]},
                     {'args': ['\0']}, {'inventory_format': 'guessed'},
                     {'unknown': True}):
      with self.subTest(settings=settings):
        original = dict(self.settings)
        self.settings.update(settings)
        self.write_config()
        self.stage(code=2)
        self.settings = original
    self.assertFalse((self.root / 'hosts.log').exists())

  def test_deploy_publish_shortcut_skip_and_status(self):
    version = self.fixture()
    result = json.loads(self.deploy().stdout)
    self.assertEqual(result['current'], version)
    self.assertFalse(result['skipped_copy'])
    self.assertEqual(os.readlink(self.title / 'current'), 'versions/' + version)
    self.assertFalse((self.title / f'versions/{version}.partial').exists())
    parms = json.loads((self.device_home() / 'parms.json').read_text())
    self.assertEqual(parms['argv'],
                     ['./current/run', '--flag', 'space and $quote'])
    self.assertEqual(parms['directory'], str(self.title))
    self.assertEqual(parms['settings']['compat_tool'], 'SteamLinuxRuntime_4')
    copies = (self.device_home() / 'copies.log').read_text()
    self.assertTrue(json.loads(self.deploy().stdout)['skipped_copy'])
    self.assertEqual((self.device_home() / 'copies.log').read_text(), copies)
    status = json.loads(self.run_cli('status', '--project', str(self.repo),
                                     '--json').stdout)['project']
    self.assertEqual(status['current'], version)
    self.assertEqual(status['local_version'], version)
    self.change()
    status = json.loads(self.run_cli('status', '--project', str(self.repo),
                                     '--json').stdout)['project']
    self.assertNotEqual(status['current'], status['local_version'])

  def test_copy_failure_and_device_hash_mismatch_preserve_current(self):
    old = self.fixture()
    self.deploy()
    new = self.change()
    for flag in ('FAKE_COPY_FAIL', 'FAKE_COPY_CORRUPT'):
      with self.subTest(flag=flag):
        self.env[flag] = '1'
        self.deploy(code=3 if flag == 'FAKE_COPY_FAIL' else 1)
        self.assertEqual(os.readlink(self.title / 'current'), 'versions/' + old)
        self.assertFalse((self.title / f'versions/{new}.partial').exists())
        self.assertFalse((self.title / f'versions/{new}').exists())
        del self.env[flag]

  def test_existing_corrupt_version_is_refused_without_copy(self):
    version = self.fixture()
    self.deploy()
    (self.title / f'versions/{version}/run').write_text('corrupt')
    copies = (self.device_home() / 'copies.log').read_text()
    self.deploy(code=1)
    self.assertEqual((self.device_home() / 'copies.log').read_text(), copies)

  def test_rollback_list_deploy_order_and_retention_guards(self):
    first = self.fixture(keep_versions=2)
    self.deploy()
    outside = self.device_home() / 'outside'
    outside.mkdir()
    (outside / 'keep').write_text('keep')
    versions = self.title / 'versions'
    (versions / ('f' * 12)).symlink_to(outside, target_is_directory=True)
    (versions / 'unrelated').mkdir()
    (self.title / 'keep').write_text('keep')
    second = self.change()
    self.deploy()
    # mtime must not override publication order.
    os.utime(versions / first, (9999999999, 9999999999))
    third = self.change()
    self.deploy()
    self.assertFalse((versions / first).exists())
    self.assertTrue((versions / second).is_dir())
    self.assertEqual(json.loads(self.deploy('--rollback').stdout)['current'],
                     second)
    self.assertEqual(json.loads(self.deploy('--list').stdout)['current'],
                     second)
    self.assertEqual(os.readlink(self.title / 'current'), 'versions/' + second)
    self.assertEqual((outside / 'keep').read_text(), 'keep')
    self.assertTrue((versions / ('f' * 12)).is_symlink())
    self.assertTrue((versions / 'unrelated').is_dir())
    self.assertEqual((self.title / 'keep').read_text(), 'keep')
    self.assertTrue((versions / third).is_dir())
    shutil.rmtree(self.bundle)  # rollback/list do not need a local build
    self.deploy('--list')
    self.deploy('--rollback')

  def test_rollback_refuses_missing_previous_or_corrupt_target(self):
    first = self.fixture()
    self.deploy()
    self.deploy('--rollback', code=1)
    second = self.change()
    self.deploy()
    (self.title / f'versions/{first}/run').write_text('corrupt')
    self.deploy('--rollback', code=1)
    self.assertEqual(os.readlink(self.title / 'current'), 'versions/' + second)

  def test_lease_pin_and_shortcut_reply_refusals(self):
    version = self.fixture()
    self.run_cli('lease', 'release')
    self.deploy(code=1)
    self.deploy('--rollback', code=1)
    self.deploy('--list')  # read only
    self.run_cli('lease', 'take', 'fixture deploy')
    pin = self.device_home() / 'devkit-utils/.agent-kit-pin'
    pin.write_text('unversioned')
    self.deploy(code=1)
    self.deploy('--rollback', code=1)
    self.assertFalse(self.title.exists())
    pin.write_text('a00ceb7d91ea44a0c3e714a91a06417d6e5cdb33\n')
    for reply, code in (('{"error":"RPC refused"}', 1), ('{}', 3),
                        ('{"success":false}', 1)):
      self.env['FAKE_SHORTCUT_REPLY'] = reply
      proc = self.deploy(code=code)
      self.assertIn('published version ' + version, proc.stderr)
      self.assertEqual(os.readlink(self.title / 'current'), 'versions/' + version)
      self.assertFalse((self.title / '.steamos-deploy-lock').exists())

  def test_changed_shortcut_not_registered_if_switch_refuses(self):
    old = self.fixture()
    self.deploy()
    parms = (self.device_home() / 'parms.json').read_text()
    shutil.copy2(self.bundle / 'run', self.bundle / 'run2')
    self.settings['start'] = 'run2'
    self.write_config()
    self.seal()
    ssh = test_steamos.FAKE_SSH.replace(
      'env = dict(os.environ, HOME=home)',
      '''env = dict(os.environ, HOME=home)
if '"action": "switch"' in command:
  with open(os.path.join(home, '.agent-kit-steamos-lease/info'), 'w') as f:
    f.write('holder=agent-a\\nexpires=1\\n')''')
    (self.root / 'bin/ssh').write_text(ssh)
    proc = self.deploy(code=1)
    self.assertIn('project switch', proc.stderr)
    self.assertEqual(os.readlink(self.title / 'current'), 'versions/' + old)
    self.assertEqual((self.device_home() / 'parms.json').read_text(), parms)

  def test_lost_prepare_reply_attempts_owned_cleanup(self):
    self.fixture()
    ssh = test_steamos.FAKE_SSH.replace(
      'sys.exit(code)',
      '''if '"action": "prepare"' in command:
  sys.exit(255)
sys.exit(code)''')
    (self.root / 'bin/ssh').write_text(ssh)
    self.deploy(code=3)
    self.assertFalse((self.title / '.steamos-deploy-lock').exists())
    self.assertEqual(list((self.title / 'versions').glob('*.partial')), [])

  def test_host_signals_during_copy_clean_only_owned_partial(self):
    old = self.fixture()
    self.deploy()
    self.change()
    self.env['FAKE_COPY_WAIT'] = '1'
    started = self.device_home() / 'copy-started'
    for signum in (signal.SIGHUP, signal.SIGINT, signal.SIGTERM):
      with self.subTest(signum=signum):
        started.unlink(missing_ok=True)
        proc = subprocess.Popen(
          [sys.executable, str(test_steamos.BIN), 'deploy', '--project',
           str(self.repo), '--json'], env=self.env, text=True,
          stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
          deadline = time.monotonic() + 10
          while not started.exists() and proc.poll() is None and \
              time.monotonic() < deadline:
            time.sleep(.02)
          self.assertTrue(started.exists(), 'copy did not reach partial write')
          proc.send_signal(signum)
          stdout, stderr = proc.communicate(timeout=10)
          self.assertEqual(proc.returncode, 1, stdout + stderr)
          self.assertIn('deployment interrupted', stderr)
        finally:
          if proc.poll() is None:
            proc.kill()
            proc.communicate()
        self.assertEqual(os.readlink(self.title / 'current'), 'versions/' + old)
        self.assertEqual(list((self.title / 'versions').glob('*.partial')), [])
        self.assertFalse((self.title / '.steamos-deploy-lock').exists())

  def test_remote_symlink_guards_preserve_outside(self):
    self.fixture()
    outside = self.device_home() / 'outside'
    outside.mkdir()
    (outside / 'keep').write_text('keep')
    self.title.mkdir(parents=True)
    for leaf in ('versions', 'deploys.log', 'current.new'):
      with self.subTest(leaf=leaf):
        path = self.title / leaf
        path.symlink_to(outside, target_is_directory=True)
        self.deploy(code=1)
        self.assertTrue(path.is_symlink())
        self.assertEqual((outside / 'keep').read_text(), 'keep')
        path.unlink()

  def test_atomic_switch_uses_replace_without_unlinking_current(self):
    old = self.fixture()
    self.deploy()
    new = self.change()
    # Instrument the actual remote interpreter, independently of CLI output.
    wrapper = self.root / 'atomic.py'
    wrapper.write_text('''import json, os, pathlib, runpy, sys
original_replace, original_unlink = os.replace, os.unlink
def replace(source, target, *args, **kwargs):
  target = pathlib.Path(target)
  if target.name == 'current':
    assert target.is_symlink(), 'current disappeared before switch'
    assert pathlib.Path(source).name == 'current.new'
    original_replace(source, target, *args, **kwargs)
    assert target.is_symlink(), 'current disappeared after switch'
    (target.parent / 'atomic-observed').write_text('yes')
  else:
    original_replace(source, target, *args, **kwargs)
def unlink(path, *args, **kwargs):
  assert pathlib.Path(path).name != 'current', 'current was unlinked'
  return original_unlink(path, *args, **kwargs)
os.replace, os.unlink = replace, unlink
sys.argv.pop(0)
if sys.argv[0] == '-':
  exec(compile(sys.stdin.read(), '<device>', 'exec'))
else:
  runpy.run_path(sys.argv[0], run_name='__main__')
''')
    python = self.root / 'bin/python3'
    python.write_text(f'#!/bin/sh\nexec {sys.executable} {wrapper} "$@"\n')
    python.chmod(0o755)
    self.deploy()
    self.assertEqual((self.title / 'atomic-observed').read_text(), 'yes')
    self.assertEqual(os.readlink(self.title / 'current'), 'versions/' + new)
    self.assertTrue((self.title / f'versions/{old}').is_dir())

  def test_list_running_version_and_retention_preserve_running(self):
    old = self.fixture(keep_versions=1)
    self.deploy()
    proc = self.device_home() / '.fake-proc/123'
    proc.mkdir(parents=True)
    (proc / 'exe').symlink_to(self.title / f'versions/{old}/run')
    wrapper = self.root / 'proc.py'
    wrapper.write_text('''import pathlib, runpy, sys
original = pathlib.Path.glob
def glob(path, pattern):
  if str(path) == '/proc':
    path = pathlib.Path.home() / '.fake-proc'
  return original(path, pattern)
pathlib.Path.glob = glob
sys.argv.pop(0)
if sys.argv[0] == '-':
  exec(compile(sys.stdin.read(), '<device>', 'exec'))
else:
  runpy.run_path(sys.argv[0], run_name='__main__')
''')
    python = self.root / 'bin/python3'
    python.write_text(f'#!/bin/sh\nexec {sys.executable} {wrapper} "$@"\n')
    python.chmod(0o755)
    new = self.change()
    self.deploy()
    result = json.loads(self.deploy('--list').stdout)
    self.assertEqual(result['current'], new)
    self.assertEqual(result['running'], {old: [123]})
    self.assertTrue((self.title / f'versions/{old}').is_dir())

  def test_preexisting_partial_and_lock_are_not_adopted(self):
    version = self.fixture()
    versions = self.title / 'versions'
    versions.mkdir(parents=True)
    partial = versions / (version + '.partial')
    partial.mkdir()
    (partial / 'keep').write_text('unknown operation')
    self.deploy(code=1)
    self.assertEqual((partial / 'keep').read_text(), 'unknown operation')
    self.assertFalse((self.title / '.steamos-deploy-lock').exists())
    lock = self.title / '.steamos-deploy-lock'
    lock.mkdir()
    (lock / 'owner').write_text('another invocation')
    self.deploy(code=1)
    self.assertEqual((lock / 'owner').read_text(), 'another invocation')
    self.assertEqual((partial / 'keep').read_text(), 'unknown operation')

  def test_lease_expiring_during_copy_aborts_before_switch(self):
    old = self.fixture()
    self.deploy()
    self.change()
    rsync = self.root / 'bin/rsync'
    rsync.write_text(rsync.read_text() + '''
info = home / '.agent-kit-steamos-lease/info'
info.write_text('holder=agent-a\\nexpires=1\\n')
''')
    self.deploy(code=1)
    self.assertEqual(os.readlink(self.title / 'current'), 'versions/' + old)
    self.assertEqual(list((self.title / 'versions').glob('*.partial')), [])
    self.assertFalse((self.title / '.steamos-deploy-lock').exists())

  def test_subdirectory_config_cannot_override_repository_root(self):
    self.project()
    nested = self.repo / 'src'
    nested.mkdir()
    (nested / 'steamos.json').write_text(
      (self.repo / 'steamos.json').read_text())
    proc = self.run_cli('stage', '--project', str(nested), code=2)
    self.assertIn('repository root', proc.stderr)

  def test_inventory_custom_path_and_runtime_none(self):
    version = self.fixture(inventory='meta/files.json', runtime='none')
    self.assertEqual(json.loads(self.stage().stdout)['version'], version)
    self.deploy()
    parms = json.loads((self.device_home() / 'parms.json').read_text())
    self.assertEqual(parms['settings']['compat_tool'], '')

  def test_ledger_io_and_replace_failure_preserve_current_and_history(self):
    old = self.fixture()
    self.deploy()
    new = self.change()
    old_history = (self.title / 'deploys.log').read_bytes()
    wrapper = self.root / 'ledger.py'
    wrapper.write_text('''import os, pathlib, runpy, sys
original_open = pathlib.Path.open
original_sync, original_replace = os.fsync, os.replace
fault = os.environ['FAKE_LEDGER_FAULT']
class Log:
  def __init__(self, stream):
    self.stream = stream
  def __getattr__(self, name):
    return getattr(self.stream, name)
  def __enter__(self):
    return self
  def __exit__(self, *args):
    self.stream.close()
  def write(self, data):
    if fault == 'write':
      raise OSError('injected ledger write ENOSPC')
    return self.stream.write(data)
  def flush(self):
    if fault == 'flush':
      raise OSError('injected ledger flush failure')
    return self.stream.flush()
def open(path, *args, **kwargs):
  stream = original_open(path, *args, **kwargs)
  if path.name == 'deploys.log' and args and args[0].startswith('a'):
    return Log(stream)
  return stream
def sync(fd):
  if fault == 'fsync':
    raise OSError('injected ledger fsync failure')
  if fault == 'expiry':
    (pathlib.Path.home() / '.agent-kit-steamos-lease/info').write_text(
      'holder=agent-a\\nexpires=1\\n')
  return original_sync(fd)
def replace(source, target, *args, **kwargs):
  if pathlib.Path(target).name == 'current' and fault == 'replace':
    raise OSError('injected replace failure')
  return original_replace(source, target, *args, **kwargs)
pathlib.Path.open, os.fsync, os.replace = open, sync, replace
sys.argv.pop(0)
if sys.argv[0] == '-':
  exec(compile(sys.stdin.read(), '<device>', 'exec'))
else:
  runpy.run_path(sys.argv[0], run_name='__main__')
''')
    python = self.root / 'bin/python3'
    python.write_text(f'#!/bin/sh\nexec {sys.executable} {wrapper} "$@"\n')
    python.chmod(0o755)
    for fault in ('write', 'flush', 'fsync', 'replace', 'expiry'):
      with self.subTest(fault=fault):
        self.env['FAKE_LEDGER_FAULT'] = fault
        self.run_cli('lease', 'take', 'fixture deploy')
        proc = self.deploy(code=1)
        self.assertEqual(os.readlink(self.title / 'current'), 'versions/' + old)
        self.assertEqual((self.title / 'deploys.log').read_bytes(), old_history)
        self.assertFalse((self.title / 'current.new').exists())
        self.assertFalse((self.title / '.steamos-deploy-lock').exists())
        if fault in ('flush', 'fsync'):
          self.assertIn('inspect deploys.log', proc.stderr)
    self.assertTrue((self.title / f'versions/{new}').is_dir())
