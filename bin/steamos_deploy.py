"""Project bundles and device publication; transported verbatim over ssh.

The same verifier runs locally and on the device. This module uses only
Python's standard library and has no device effects when imported.
"""
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import signal
import stat
import sys
import time

VERSION = re.compile(r'[0-9a-f]{12}')
TITLE = re.compile(r'[A-Za-z0-9]+')


def pairs(items):
  result = {}
  for key, value in items:
    if key in result:
      raise ValueError('Duplicate JSON key: ' + key)
    result[key] = value
  return result


def read_json(path):
  return json.loads(path.read_text(), object_pairs_hook=pairs)


def relative(value):
  if not isinstance(value, str) or not value or '\\' in value or any(
      ord(c) < 32 or ord(c) == 127 for c in value):
    raise ValueError('Expected a safe relative path')
  path = PurePosixPath(value)
  if path.is_absolute() or any(p in ('', '.', '..') for p in value.split('/')):
    raise ValueError('Unsafe relative path: ' + repr(value))
  return value


def no_links(root, relative_name):
  path = root
  if path.is_symlink():
    raise ValueError('Symlink refused: ' + str(path))
  for part in relative(relative_name).split('/'):
    path = path / part
    if path.is_symlink():
      raise ValueError('Symlink refused: ' + str(path))
  return path


def project_config(location=None, optional=False):
  base = Path(location).expanduser().absolute() if location else Path.cwd()
  if not base.is_dir():
    raise ValueError('--project must name a directory')
  # Stop at the nearest checkout boundary, including linked worktrees.
  root = config_root = None
  for candidate in (base, *base.parents):
    if config_root is None and (candidate / 'steamos.json').exists():
      config_root = candidate
    if (candidate / '.git').exists():
      root = candidate
      break
  if root is not None and config_root is not None and config_root != root:
    raise ValueError('steamos.json must live at the repository root')
  if root is None:
    root = config_root  # Also support exported source trees.
  if root is None or not (root / 'steamos.json').exists():
    if optional and location is None:
      return None
    raise ValueError('No project steamos.json found')
  config = read_json(no_links(root, 'steamos.json'))
  allowed = {'title', 'bundle', 'inventory', 'start', 'args', 'runtime',
             'keep_versions'}
  if not isinstance(config, dict) or set(config) - allowed:
    raise ValueError('Unknown project configuration fields')
  if not isinstance(config.get('title'), str) or not TITLE.fullmatch(
      config['title']):
    raise ValueError('Project title must contain only letters and digits')
  for field in ('bundle', 'start'):
    relative(config.get(field))
  config.setdefault('inventory', 'bundle.json')
  relative(config['inventory'])
  config.setdefault('args', [])
  if not isinstance(config['args'], list) or any(
      not isinstance(a, str) or '\0' in a for a in config['args']):
    raise ValueError('args must be an array of strings without NUL')
  config.setdefault('runtime', 'slr4')
  if config['runtime'] not in ('slr4', 'none'):
    raise ValueError('runtime must be slr4 or none')
  config.setdefault('keep_versions', 3)
  if type(config['keep_versions']) is not int or config['keep_versions'] < 1:
    raise ValueError('keep_versions must be a positive integer')
  return root, config


def canonical(manifest):
  return json.dumps(manifest, sort_keys=True, separators=(',', ':'),
                    ensure_ascii=True).encode('utf-8')


def version_id(manifest):
  return hashlib.sha256(canonical(manifest)).hexdigest()[:12]


def digest(path):
  result = hashlib.sha256()
  with path.open('rb') as stream:
    for chunk in iter(lambda: stream.read(1024 * 1024), b''):
      result.update(chunk)
  return result.hexdigest()


def verify(bundle, inventory, start):
  if bundle.is_symlink() or not bundle.is_dir():
    raise ValueError('Bundle must be a directory without symlinks')
  manifest = read_json(no_links(bundle, inventory))
  if (not isinstance(manifest, dict) or
      set(manifest) - {'version', 'files', 'required'} or
      type(manifest.get('version')) is not int or manifest['version'] != 1 or
      not isinstance(manifest.get('files'), dict) or not manifest['files']):
    raise ValueError('Invalid or empty version-1 inventory')
  files = manifest['files']
  for name, expected in files.items():
    relative(name)
    if name == inventory or not isinstance(expected, str) or not re.fullmatch(
        r'[0-9a-f]{64}', expected):
      raise ValueError('Invalid inventory member or SHA256: ' + name)
    path = no_links(bundle, name)
    if not path.is_file() or digest(path) != expected:
      raise ValueError('Missing or changed bundle member: ' + name)
  if 'required' in manifest:
    required = manifest['required']
    if not isinstance(required, list) or not required:
      raise ValueError('required must be a nonempty array of member paths')
    for name in required:
      relative(name)
      if name not in files:
        raise ValueError('Missing required bundle member: ' + name)
  actual = set()
  for folder, directories, names in os.walk(bundle, followlinks=False):
    for name in directories + names:
      path = Path(folder) / name
      mode = path.lstat().st_mode
      if stat.S_ISLNK(mode) or not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
        raise ValueError('Symlink or special bundle member: ' + str(path))
      if stat.S_ISREG(mode):
        actual.add(str(path.relative_to(bundle)))
  if actual != set(files) | {inventory}:
    raise ValueError('Unlisted or missing bundle files')
  if start not in files or not os.access(no_links(bundle, start), os.X_OK):
    raise ValueError('start must be listed and executable')
  return manifest


def destination(config):
  home = Path.home()
  root = no_links(home, 'devkit-game/' + config['title'])
  for name in ('versions', 'deploys.log', '.steamos-deploy-lock'):
    no_links(root, name)
  if root.exists() and not root.is_dir():
    raise ValueError('Title destination is not a directory')
  return root


def current_version(root):
  current = root / 'current'
  if not current.is_symlink():
    if current.exists():
      raise ValueError('current must be a managed version symlink')
    return None
  target = os.readlink(current)
  if not re.fullmatch(r'versions/[0-9a-f]{12}', target):
    raise ValueError('current points outside managed versions')
  no_links(root, target)
  return target.split('/')[1]


def history(root):
  log = no_links(root, 'deploys.log')
  records = []
  if log.exists():
    for line in log.read_text().splitlines():
      record = json.loads(line, object_pairs_hook=pairs)
      if (not isinstance(record, dict) or
          not isinstance(record.get('version'), str) or
          not VERSION.fullmatch(record['version'])):
        raise ValueError('Invalid deploys.log; inspect before deployment')
      relative(record.get('inventory'))
      relative(record.get('start'))
      records.append(record)
  return records


def versions_list(root):
  directory = no_links(root, 'versions')
  if directory.exists() and not directory.is_dir():
    raise ValueError('versions must be a directory')
  if not directory.exists():
    return []
  return sorted(p.name for p in directory.iterdir()
                if VERSION.fullmatch(p.name) and
                not p.is_symlink() and p.is_dir())


def running_versions(root):
  running = {}
  prefix = str(root / 'versions') + '/'
  for executable in Path('/proc').glob('[0-9]*/exe'):
    try:
      path = os.readlink(executable)
    except OSError:
      continue  # Another user, a vanished process, or a kernel thread.
    if path.startswith(prefix):
      version = path[len(prefix):].split('/')[0]
      if VERSION.fullmatch(version):
        running.setdefault(version, []).append(int(executable.parent.name))
  return running


def list_result(root):
  return {'current': current_version(root), 'versions': versions_list(root),
          'running': running_versions(root),
          'deploy_order': [r['version'] for r in history(root)]}


def check_authority(request):
  home = Path.home()
  info = home / '.agent-kit-steamos-lease/info'
  values = dict(line.split('=', 1) for line in info.read_text().splitlines()
                if '=' in line)
  if values.get('holder') != request['holder'] or not values.get(
      'expires', '').isdigit() or int(values['expires']) <= time.time():
    raise ValueError('Deployment needs your active device lease')
  for name in request['legacy']:
    if (home / relative(name)).exists():
      raise ValueError('Older project lease blocks deployment')
  utils = no_links(home, 'devkit-utils/.agent-kit-pin')
  if utils.read_text().strip() != request['pin']:
    raise ValueError('Deployment needs pinned devkit-utils')


def locked(root, request):
  lock = no_links(root, '.steamos-deploy-lock')
  if no_links(lock, 'owner').read_text() != request['token']:
    raise ValueError('Deployment lock changed; refusing mutation')
  return lock


def previous_version(root):
  current = current_version(root)
  available = set(versions_list(root))
  records = history(root)
  seen_current = False
  for record in reversed(records):
    version = record['version']
    if version == current:
      seen_current = True
    elif seen_current and version in available:
      manifest = verify(root / 'versions' / version,
                        record['inventory'], record['start'])
      if version_id(manifest) != version:
        raise ValueError('Rollback inventory does not match its version')
      return record
  raise ValueError('No previous retained version for rollback')


def prepare(root, request):
  config = request['config']
  root.mkdir(exist_ok=True)
  current_version(root)
  history(root)
  versions = no_links(root, 'versions')
  versions.mkdir(exist_ok=True)
  if (root / 'current.new').exists() or (root / 'current.new').is_symlink():
    raise ValueError('current.new already exists; inspect before deployment')
  lock = no_links(root, '.steamos-deploy-lock')
  lock.mkdir()  # Same-holder deployments cannot interleave either.
  try:
    (lock / 'owner').write_text(request['token'])
    if request['rollback']:
      return previous_version(root)
    version = request['version']
    final = no_links(versions, version)
    partial = no_links(versions, version + '.partial')
    if partial.exists():
      raise ValueError('Partial directory already exists; inspect before retry')
    if final.exists():
      manifest = verify(final, config['inventory'], config['start'])
      if canonical(manifest) != canonical(request['manifest']):
        raise ValueError('Existing version has a different inventory')
      return {'skipped_copy': True}
    partial.mkdir()
    return {'skipped_copy': False}
  except BaseException:
    # An existing partial is never adopted or removed here.
    (lock / 'owner').unlink(missing_ok=True)
    lock.rmdir()
    raise


def finish_copy(root, request):
  config = request['config']
  version = request['version']
  versions = no_links(root, 'versions')
  partial = no_links(versions, version + '.partial')
  manifest = verify(partial, config['inventory'], config['start'])
  if canonical(manifest) != canonical(request['manifest']):
    raise ValueError('Uploaded inventory differs from local stage')
  final = no_links(versions, version)
  if final.exists():
    raise ValueError('Final version appeared during upload')
  check_authority(request)
  partial.rename(final)
  return {'version': version}


def switch(root, request):
  config = request['config']
  version = request['version']
  final = no_links(root, 'versions/' + version)
  manifest = verify(final, request['inventory'], request['start'])
  if version_id(manifest) != version:
    raise ValueError('Version inventory hash does not match directory name')
  current_version(root)
  records = history(root)
  new = root / 'current.new'
  if new.exists() or new.is_symlink():
    raise ValueError('current.new already exists; refusing to replace it')
  check_authority(request)
  # Write through an unbuffered stream so a partial write can be truncated
  # without flushing failed buffered data. Ledger I/O precedes publication.
  with no_links(root, 'deploys.log').open('ab', buffering=0) as log:
    offset = log.tell()
    record = {'version': version, 'inventory': request['inventory'],
              'start': request['start'], 'device_time': time.time()}
    # Finish this bounded commit section despite SSH hangup or Ctrl-C.
    previous = {s: signal.signal(s, signal.SIG_IGN)
                for s in (signal.SIGHUP, signal.SIGINT, signal.SIGTERM)}
    try:
      data = (json.dumps(record, separators=(',', ':')) + '\n').encode()
      while data:
        written = log.write(data)
        if not written:
          raise OSError('Short deploys.log write')
        data = data[written:]
      log.flush()
      os.fsync(log.fileno())
      # A slow ledger sync may outlive the lease. No shortcut or current
      # mutation happens until this final device-clock check succeeds.
      check_authority(request)
      new.symlink_to('versions/' + version)
      os.replace(new, root / 'current')
    except BaseException as error:
      try:
        log.truncate(offset)
        log.flush()
        os.fsync(log.fileno())
      except OSError as restore_error:
        raise ValueError(f'{error}; ledger restoration failed: '
                         f'{restore_error}; inspect deploys.log') from error
      raise
    finally:
      if new.is_symlink():
        new.unlink()
      for s, handler in previous.items():
        signal.signal(s, handler)
  records.append(record)
  # Retention failure after publication is reported without undoing current.
  recent = list(dict.fromkeys(r['version'] for r in reversed(records)))
  keep = set(recent[:config['keep_versions']]) | {version}
  keep.update(running_versions(root))
  removed, warnings = [], []
  for old in recent:
    path = root / 'versions' / old
    if old not in keep and not path.is_symlink() and path.is_dir():
      try:
        check_authority(request)
        no_links(root, 'versions/' + old)
        shutil.rmtree(path)  # Does not follow nested symlinks.
        removed.append(old)
      except (OSError, ValueError) as error:
        warnings.append(str(error))
  return dict(list_result(root), removed=removed, warnings=warnings)


def abort(root, request):
  lock = locked(root, request)
  # Only our partial, beneath the guarded versions directory. Never remove
  # a completed version, current, or an interrupted operation's lock.
  version = request.get('version')
  if version is not None:
    partial = no_links(root, 'versions/' + version + '.partial')
    if partial.is_dir():
      shutil.rmtree(partial)
  (lock / 'owner').unlink()
  lock.rmdir()
  return {}


def device_main():
  try:
    request = json.loads(sys.argv[1])
    action = request['action']
    config = request['config']
    if not TITLE.fullmatch(config['title']):
      raise ValueError('Invalid title')
    if 'version' in request and not VERSION.fullmatch(request['version']):
      raise ValueError('Invalid version')
    root = destination(config)
    if action == 'list':
      result = list_result(root)
    elif action == 'abort':
      result = abort(root, request)
    else:
      check_authority(request)
      if action == 'prepare':
        result = prepare(root, request)
      else:
        locked(root, request)
        if action == 'finish_copy':
          result = finish_copy(root, request)
        elif action == 'switch':
          result = switch(root, request)
        else:
          raise ValueError('Unknown deployment action')
    print(json.dumps(result))
  except (OSError, ValueError, KeyError, TypeError) as error:
    print(json.dumps({'error': str(error)}))
    sys.exit(1)


if __name__ == '__main__':
  device_main()
