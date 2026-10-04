"""Read-only local, project and SteamOS device diagnostics."""
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import time

DOCTOR_SCRIPT = r'''
echo "glibc=$(ldd --version 2>/dev/null | head -n 1 | awk '{print $NF}')"
if [ -f "$helper" ]; then echo doctor_helper=present
else echo doctor_helper=missing; fi
if sudo_rules=$(sudo -n -l 2>/dev/null); then
  echo doctor_sudo=listed
  printf '%s\n' "$sudo_rules" | sed 's/^/doctor_sudo_line=/'
else echo doctor_sudo=unavailable; fi
info=$HOME/.agent-kit-steamos-lease/info
if [ -r "$info" ]; then
  echo "doctor_lease_holder=$(sed -n 's/^holder=//p' "$info" | head -n 1)"
  echo "doctor_lease_purpose=$(sed -n 's/^purpose=//p' "$info" | head -n 1)"
  echo "doctor_lease_expires=$(sed -n 's/^expires=//p' "$info" | head -n 1)"
elif [ -d "$HOME/.agent-kit-steamos-lease" ]; then
  echo doctor_lease_holder=unknown
else echo doctor_lease_holder=free; fi
if command -v systemctl >/dev/null 2>&1 &&
   command -v systemd-inhibit >/dev/null 2>&1 &&
   systemctl --user show-environment >/dev/null 2>&1; then
  if [ "$(systemctl --user is-active agent-kit-steamos-lease-inhibit \
      2>/dev/null)" = active ] &&
     systemd-inhibit --list 2>/dev/null |
       awk '$1 == "agent-kit-steamos" { found=1 } END { exit !found }'; then
    echo doctor_inhibit=active
  else echo doctor_inhibit=inactive; fi
else echo doctor_inhibit=unavailable; fi
'''


def helper_sudo_rule(output, helper):
  """Find a root NOPASSWD command entry for the configured helper."""
  rule = re.compile(r'^\s*\((?:root|ALL)\)\s+NOPASSWD:\s*'
                    r'(?:[^,]+,\s*)*(?:ALL|' + re.escape(helper) +
                    r')(?:\s|$)')
  return any(rule.match(line.removeprefix('doctor_sudo_line='))
             for line in output.splitlines()
             if line.startswith('doctor_sudo_line='))


def glibc_tuple(value):
  match = re.fullmatch(r'(\d+)\.(\d+)(?:\.(\d+))?', value or '')
  return tuple(int(item or 0) for item in match.groups()) if match else None


def bundle_glibc(bundle, manifest):
  tool = shutil.which('readelf') or shutil.which('objdump')
  if not tool:
    return None, 'readelf/objdump unavailable locally'
  versions = []
  candidates = [name for name in manifest['files'] if name.endswith('.so') or
                '.so.' in name or os.access(bundle / name, os.X_OK)]
  for name in candidates:
    argv = [tool, '--dyn-syms', '--wide', str(bundle / name)] if \
      Path(tool).name == 'readelf' else [tool, '-T', str(bundle / name)]
    proc = subprocess.run(argv, capture_output=True, text=True)
    if proc.returncode:
      continue  # Scripts and non-ELF executable inputs have no GLIBC ABI.
    for line in proc.stdout.splitlines():
      if 'UND' not in line:
        continue
      for match in re.finditer(r'GLIBC_(\d+(?:\.\d+)+)', line):
        version = glibc_tuple(match[1])
        if version:
          versions.append(version)
  if not versions:
    return None, 'no GLIBC symbols found in bundle ELF files'
  return max(versions), None


def doctor_command(options, api):
  checks = []
  def add(scope, name, status, message):
    checks.append({'scope': scope, 'name': name, 'status': status,
                   'message': str(message)})

  add('local', 'python', 'ok' if sys.version_info >= (3, 9) else 'fail',
      f'Python {sys.version_info.major}.{sys.version_info.minor}')
  for name in ('ssh', 'rsync'):
    path = shutil.which(name)
    add('local', name, 'ok' if path else 'fail', path or f'{name} missing')
  if sys.platform == 'darwin':
    add('local', 'local-network', 'warn',
        'macOS may require Local Network permission for Python; '
        'ssh itself can have different permission')

  selected = None
  stage = None
  try:
    selected = api.project(options, optional=True)
    if selected is None:
      add('project', 'config', 'warn', 'no steamos.json; benchmark-only mode')
    else:
      root, config = selected
      add('project', 'config', 'ok', f'{root / "steamos.json"} valid')
      if 'build' in config:
        try:
          plan = api.build_plan(root, config)
          for name, path in plan['requires_missing']:
            add('project', 'build.requires', 'fail',
                f'{name}: path does not exist: '
                f'{"<redacted>" if api.secret_name(name) else path}')
          if not plan['requires_missing']:
            add('project', 'build.requires', 'ok', 'all paths present')
          for output in plan['outputs']:
            add('project', 'build.output',
                'ok' if (root / output).exists() else 'warn',
                f'{output}: ' + ('present' if (root / output).exists() else
                                'not built yet'))
          scripts = [root / arg for arg in plan['command']
                     if not arg.startswith('-') and
                     not Path(arg).is_absolute()]
          uses_docker = Path(plan['command'][0]).name == 'docker' or any(
            script.is_file() and script.stat().st_size < 1024 * 1024 and
            re.search(
              r'\bdocker\s+(?:build|run|exec)',
              script.read_text(errors='replace')) for script in scripts)
          if uses_docker:
            installed = shutil.which('docker')
            add('local', 'docker', 'ok' if installed else 'fail',
                installed or 'docker missing')
            if installed:
              proc = subprocess.run(['docker', 'info'], capture_output=True,
                                    text=True, timeout=10)
              add('local', 'docker-daemon',
                  'ok' if proc.returncode == 0 else 'fail',
                  'reachable' if proc.returncode == 0 else
                  'docker daemon unreachable')
        except (api.Failure, OSError, subprocess.TimeoutExpired) as error:
          add('project', 'build', 'fail', str(error))
      try:
        bundle, manifest, version = api.local_stage(root, config)
        size = sum((bundle / name).stat().st_size
                   for name in manifest['files'])
        stage = {'bundle': bundle, 'manifest': manifest, 'version': version,
                 'size': size}
        add('project', 'stage', 'ok', f'{version}, {size} bytes')
      except api.Failure as error:
        add('project', 'stage', 'warn', str(error))
  except api.Failure as error:
    add('project', 'config', 'fail', str(error))

  devices = []
  names = []
  if options.device or options.all:
    try:
      machine = api.load_config()
      names = sorted(machine['devices']) if options.all else [
        api.device(machine, options.device)[0]]
    except api.Failure as error:
      add('device', 'config', 'fail', str(error))
  for name in names:
    scope = f'device:{name}'
    _, entry = api.device(machine, name)
    try:
      helper_path = api.governor_helper(machine)
      script = 'helper=' + shlex.quote(helper_path) + '\n' + \
        api.STATUS_SCRIPT + DOCTOR_SCRIPT
      proc = api.ssh(entry, script, [], timeout=15)
      if proc.returncode:
        raise api.Failure(api.UNREACHABLE, proc.stderr.strip() or
                      f'device script exited {proc.returncode}')
      facts = api.parse(proc.stdout)
      facts.pop('doctor_sudo_line', None)
      if facts.get('connected') != '1' or not facts.get('hostname'):
        raise api.Failure(api.UNREACHABLE,
                          'device returned no usable status facts')
      devices.append({'name': name, 'facts': facts})
      version = facts.get('os_version', '')
      build = facts.get('os_build', '')
      channel = facts.get('channel', '')
      add(scope, 'reachable', 'ok', 'ssh connected')
      add(scope, 'steamos', 'ok' if version and build else 'warn',
          f'version {version or "unknown"}, build {build or "unknown"}, '
          f'channel {channel or "unknown"}')
      remote_glibc = glibc_tuple(facts.get('glibc'))
      if stage:
        needed, reason = bundle_glibc(stage['bundle'], stage['manifest'])
      else:
        needed, reason = None, 'no verified project bundle'
      if needed and remote_glibc:
        add(scope, 'glibc', 'ok' if remote_glibc >= needed else 'fail',
            f'device {facts["glibc"]}; bundle requires '
            f'{".".join(map(str, needed[:2]))}')
      else:
        add(scope, 'glibc', 'warn', reason if not needed else
            'device glibc unknown')
      free = facts.get('free_bytes', '')
      if stage and free.isdigit():
        add(scope, 'free-space',
            'ok' if int(free) >= stage['size'] else 'fail',
            f'{free} bytes free; bundle {stage["size"]} bytes')
      else:
        add(scope, 'free-space', 'warn',
            f'{free or "unknown"} bytes free; bundle size unknown')
      battery = facts.get('battery_percent', '')
      if not battery:
        power = 'mains (no battery reported)'
      elif not battery.isdigit():
        power = 'unknown'
      elif facts.get('external_power') == 'yes':
        power = f'external power, battery {battery}%'
      else:
        power = f'battery {battery}%'
      add(scope, 'power', 'ok' if power != 'unknown' else 'warn', power)
      try:
        pin = api.devkit_pin(machine)[1]
        installed = facts.get('devkit_commit', '')
        add(scope, 'devkit-utils', 'ok' if installed == pin else 'warn',
            'pinned' if installed == pin else
            f'installed {installed or "missing"}; expected {pin}')
      except api.Failure as error:
        add(scope, 'devkit-utils', 'fail', str(error))
      lease_holder = facts.get('doctor_lease_holder', 'unknown')
      expires = facts.get('doctor_lease_expires', '')
      active_lease = lease_holder not in ('free', 'unknown') and \
        (not expires.isdigit() or int(expires) > time.time())
      lease_message = ('free' if lease_holder == 'free' else
                       f'held by {lease_holder} for '
                       f'{facts.get("doctor_lease_purpose", "unknown")}; '
                       f'expires {expires or "unknown"}')
      add(scope, 'lease', 'ok' if lease_holder == 'free' or
          lease_holder == api.holder(machine) or
          (expires.isdigit() and not active_lease) else 'warn',
          lease_message)
      inhibit = facts.get('doctor_inhibit', 'unavailable')
      add(scope, 'sleep-inhibition',
          'ok' if not active_lease or inhibit == 'active' else 'warn',
          inhibit)
      if facts.get('doctor_helper') != 'present':
        helper_status, helper_message = 'warn', f'missing: {helper_path}'
      elif facts.get('doctor_sudo') != 'listed' or not \
          helper_sudo_rule(proc.stdout, helper_path):
        helper_status, helper_message = 'warn', \
          f'no-sudo-rule: {helper_path}'
      else:
        helper_status, helper_message = 'ok', f'present: {helper_path}'
      add(scope, 'governor-helper', helper_status, helper_message)
    except api.Failure as error:
      add(scope, 'reachable', 'fail', str(error))

  report = {'checks': checks, 'devices': devices}
  if options.json:
    print(json.dumps(report, indent=2))
  else:
    for check in checks:
      print(f'{check["status"]}: {check["scope"]} '
            f'{check["name"]}: {check["message"]}')
  return api.REFUSED if any(c['status'] == 'fail' for c in checks) else api.OK
