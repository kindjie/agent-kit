"""The steamos device lease: its device script, holder identity and command.

Imported by steamos, which passes itself as `api` to lease_command.
"""
import getpass
import json
import os
import shutil
import socket
import subprocess
import sys
import time

from agent_records_core import RecordsError, agent_env_id

# Runs on the device with HOME as its root. POSIX sh; Linux-only sleep
# inhibition is optional so the lease also works on the macOS test host.
# Prints key=value lines; exit 0 on success, 1 when refused.
#
# Every change to the lease happens while holding a mutex directory, so
# take, renew, release, reclaim and break never interleave. The mutex
# holds an owner token: a stale one (older than two minutes) is renamed
# aside and removed only if it is still the one judged stale. Functions
# return explicit statuses because `set -e` does not apply inside `if`.
LEASE_SCRIPT = r'''
set -eu
action=$1 holder=$2 purpose=$3 seconds=$4 grace=$5 reason=$6 prevent_sleep=$7
shift 7
legacy_paths=$*
lock=$HOME/.agent-kit-steamos-lease
info=$lock/info
mutex=$lock.mutex
log=$HOME/.agent-kit-steamos-lease.log
now=$(date +%s)
token=$$.$now
fresh='' owned='' pending_unit='' inhibit_failed=''
unit=agent-kit-steamos-lease-inhibit
cleanup() {
  if [ -n "$pending_unit" ]; then
    systemctl --user stop "$unit" >/dev/null 2>&1 || true
  fi
  if [ -n "$fresh" ]; then rm -rf -- "$lock"; fi
  if [ -n "$owned" ] && [ "$(cat "$mutex/owner" 2>/dev/null)" = "$token" ]
  then rm -rf -- "$mutex"
  fi
}
trap cleanup EXIT
trap 'exit 1' HUP INT TERM
field() { sed -n "s/^$1=//p" "$info" 2>/dev/null | head -n 1; }
user_manager_available() {
  command -v systemctl >/dev/null 2>&1 &&
    systemctl --user show-environment >/dev/null 2>&1
}
inhibitor_active() {
  [ "$(systemctl --user is-active "$unit" 2>/dev/null)" = active ]
}
inhibitor_holds() {
  inhibitor_active && systemd-inhibit --list 2>/dev/null |
    awk '$1 == "agent-kit-steamos" { found=1 } END { exit !found }'
}
stop_inhibitor() {
  user_manager_available || return 0
  # A missing collected unit also returns nonzero from stop. Refuse only
  # if the unit is still active; never signal a PID from a lease record.
  if ! systemctl --user stop "$unit" >/dev/null 2>&1; then
    inhibitor_active && return 1
  fi
  return 0
}
inhibit_available() {
  command -v systemd-run >/dev/null 2>&1 &&
    command -v systemd-inhibit >/dev/null 2>&1 &&
    user_manager_available
}
start_inhibitor() {
  stop_inhibitor || return 1
  [ "$prevent_sleep" = true ] || return 0
  inhibit_available || return 0
  left=$(( $(field expires) - $(date +%s) ))
  [ "$left" -gt 0 ] || return 0
  # The user manager owns this process outside sshd's session scope, so
  # logind session cleanup cannot kill it. sleep bounds its lifetime.
  pending_unit=1
  systemd-run --user --unit="$unit" --collect --quiet \
    systemd-inhibit --what=sleep:idle --who=agent-kit-steamos \
    --why="steamos lease: $holder" --mode=block sleep "$left" \
    >/dev/null 2>&1 || return 1
  sleep 0.5
  inhibitor_holds || return 1
  pending_unit=''
  return 0
}
# state: free, broken (a lock without info: being taken, or left behind),
# active, expired (within grace) or reclaimable.
state() {
  if [ ! -d "$lock" ]; then echo free
  elif [ ! -f "$info" ]; then
    if [ -n "$(find "$lock" -maxdepth 0 -mmin +"$grace" 2>/dev/null)" ]; then
      echo reclaimable
    else
      echo broken
    fi
  else
    expires=$(field expires)
    case $expires in
      ''|*[!0-9]*) echo broken; return ;;
    esac
    if [ "$now" -lt "$expires" ]; then echo active
    elif [ "$now" -lt $((expires + grace * 60)) ]; then echo expired
    else echo reclaimable
    fi
  fi
}
legacy=''
for path in $legacy_paths; do
  if [ -e "$HOME/$path" ]; then legacy=$path; fi
done
emit() {
  printf 'state=%s\nnow=%s\n' "$(state)" "$now"
  # Reads without the mutex may race a removal; a vanished file is free.
  sed 's/^/lease_/' "$info" 2>/dev/null || true
  if [ -n "$inhibit_failed" ]; then echo inhibit=failed
  elif inhibit_available && inhibitor_holds; then echo inhibit=active
  elif [ ! -d "$lock" ]; then echo inhibit=inactive
  elif [ "$prevent_sleep" != true ]; then echo inhibit=disabled
  elif ! inhibit_available; then echo inhibit=unavailable
  else echo inhibit=inactive
  fi
  for path in $legacy_paths; do
    if [ -e "$HOME/$path" ]; then
      printf 'legacy=%s\n' "$path"
      if [ -f "$HOME/$path/info" ]; then
        printf 'legacy_info=%s: %s\n' "$path" \
          "$(tr '\n' ' ' <"$HOME/$path/info" 2>/dev/null || true)"
      fi
    fi
  done
}
finish() { printf 'result=%s\n' "$1"; emit; exit "$2"; }
mine() { [ -f "$info" ] && [ "$(field holder)" = "$holder" ]; }
record() { printf '%s %s by=%s %s\n' "$now" "$1" "$holder" "$2" >>"$log"; }
lock_mutex() {
  if [ -n "$(find "$mutex" -maxdepth 0 -mmin +2 2>/dev/null)" ]; then
    stale=$(cat "$mutex/owner" 2>/dev/null || echo none)
    aside=$mutex.stale.$$
    if mv "$mutex" "$aside" 2>/dev/null; then
      if [ "$(cat "$aside/owner" 2>/dev/null || echo none)" = "$stale" ]
      then
        rm -rf -- "$aside"
      elif [ ! -e "$mutex" ]; then
        # Someone else's live mutex: put it back. If that fails it stays
        # aside for the sweep, never deleted while it may be in use.
        mv "$aside" "$mutex" 2>/dev/null || true
      fi
    fi
  fi
  mkdir "$mutex" 2>/dev/null || return 1
  owned=1
  printf '%s\n' "$token" >"$mutex/owner" || return 1
  # Leftovers of interrupted runs, once they are older than the grace.
  find "$HOME" -maxdepth 1 \( -name '.agent-kit-steamos-lease.removed.*' \
    -o -name '.agent-kit-steamos-lease.mutex.stale.*' \) \
    -mmin +"$grace" -exec rm -rf {} + 2>/dev/null || true
}
# remove_lock EVENT DETAIL: moves the lock aside in one rename, logs its
# info, then deletes it.
remove_lock() {
  stop_inhibitor || return 1
  aside=$lock.removed.$now.$$
  mv "$lock" "$aside" 2>/dev/null || return 1
  record "$1" "$2 $(tr '\n' ' ' <"$aside/info" 2>/dev/null || true)" ||
    true
  rm -rf -- "$aside"
}
write() {
  new=$lock/info.new.$$
  printf 'holder=%s\npurpose=%s\nstart=%s\nexpires=%s\n' \
    "$holder" "$purpose" "$1" "$((now + seconds))" >"$new" || return 1
  mv -f "$new" "$info" || return 1
  if ! start_inhibitor; then
    inhibit_failed=1
    if [ -n "$pending_unit" ]; then
      systemctl --user stop "$unit" >/dev/null 2>&1 || true
    fi
  fi
  return 0
}
take_new() {
  mkdir "$lock" 2>/dev/null || return 1
  fresh=1
  write "$now" || return 1
  fresh=''
}
case $action in
  take)
    if [ -n "$legacy" ]; then finish legacy 1; fi
    lock_mutex || finish busy 1
    current=$(state)
    if [ "$current" = free ]; then
      take_new && finish took 0 || finish failed 1
    elif mine; then
      write "$(field start)" && finish refreshed 0 || finish failed 1
    elif [ "$current" = reclaimable ]; then
      remove_lock reclaimed '' || finish gone 1
      take_new && finish reclaimed 0 || finish failed 1
    fi
    finish held 1 ;;
  renew)
    lock_mutex || finish busy 1
    if mine; then
      write "$(field start)" && finish refreshed 0 || finish failed 1
    fi
    finish notmine 1 ;;
  release)
    lock_mutex || finish busy 1
    if [ ! -d "$lock" ]; then finish free 0; fi
    if mine; then
      remove_lock released '' && finish released 0
      finish gone 1
    fi
    finish notmine 1 ;;
  break)
    lock_mutex || finish busy 1
    if [ ! -d "$lock" ]; then finish free 0; fi
    remove_lock broken "reason=$reason" && finish broke 0
    finish gone 1 ;;
  check)
    if mine; then
      if [ "$(state)" = active ]; then finish ok 0; fi
      finish expired 1
    fi
    finish notmine 1 ;;
  show) finish shown 0 ;;
esac
'''


HOLDER_OVERRIDE = None  # set from --holder; beats every other source


class HolderError(ValueError):
  pass


def holder(config):
  """Resolve the lease holder.

  Precedence: --holder, STEAMOS_LEASE_HOLDER, AGENT_ID, the configured
  holder, `agent-id show` (the session), then user@host. AGENT_ID is read
  here, not through agent-id, so it also works with STEAMOS_NO_AGENT_ID or
  agent-id absent from PATH.
  """
  if HOLDER_OVERRIDE:
    return HOLDER_OVERRIDE
  name = os.environ.get('STEAMOS_LEASE_HOLDER')
  if not name:
    try:
      name = agent_env_id()
    except RecordsError as error:
      raise HolderError(str(error))
  name = name or config.get('holder')
  if not name and not os.environ.get('STEAMOS_NO_AGENT_ID') and \
      shutil.which('agent-id'):
    proc = subprocess.run(['agent-id', 'show'], capture_output=True,
                          text=True, timeout=10)
    name = proc.stdout.strip() if proc.returncode == 0 else ''
  return name or f'{getpass.getuser()}@{stable_host()}'


def stable_host():
  """A host name that does not follow the network, as macOS's does."""
  if sys.platform == 'darwin' and shutil.which('scutil'):
    proc = subprocess.run(['scutil', '--get', 'LocalHostName'],
                          capture_output=True, text=True, timeout=10)
    if proc.returncode == 0 and proc.stdout.strip():
      return proc.stdout.strip()
  return socket.gethostname().split('.')[0]


def iso(epoch):
  return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(int(epoch)))


def lease_summary(values):
  lease = {'state': values.get('state', 'unknown'),
           'inhibit': values.get('inhibit', 'unavailable'),
           'sleep_inhibited': {'active': True, 'inactive': False,
                              'disabled': False, 'failed': False}.get(
                                values.get('inhibit')),
           'legacy': values.get('legacy', []),
           'legacy_info': values.get('legacy_info', [])}
  for key in ('holder', 'purpose', 'start', 'expires'):
    if 'lease_' + key in values:
      lease[key] = values['lease_' + key]
  for key in ('start', 'expires'):
    if lease.get(key, '').isdigit():
      lease[key] = int(lease[key])
  return lease


def describe(name, lease):
  if lease['state'] == 'free':
    text = f'The {name} lease is free.'
  elif lease['state'] == 'broken':
    text = (f'The {name} lease lock exists without its record: it is being '
            'taken now, or was left behind and will be reclaimable after '
            'the grace period.')
  else:
    text = (f'The {name} lease is {lease["state"]}: held by '
            f'{lease.get("holder")} for "{lease.get("purpose")}" since '
            f'{iso(lease.get("start", 0))}, until '
            f'{iso(lease.get("expires", 0))}.')
  for path in lease['legacy']:
    text += (f'\nAn older project lock is present on the device: ~/{path}; '
             'remove it with that project\'s tool once it is not needed.')
  for info in lease.get('legacy_info', []):
    text += f'\n  {info.strip()}'
  text += f'\ninhibit={lease["inhibit"]}'
  return text


def lease_command(options, api):
  config = api.load_config()
  name, entry = api.device(config, options.device)
  policy, grace, legacy = api.lease_policy(config)
  hours = api.number(options.hours or policy.get('hours', 4), 'hours', 0, 72)
  who = api.one_line('holder', api.holder(config))
  purpose = api.one_line('purpose', getattr(options, 'purpose', '') or '')
  reason = api.one_line('reason', getattr(options, 'reason', '') or '')
  if options.action == 'break':
    if not reason:
      raise api.Failure(api.USAGE, 'break needs --reason')
    breakers = policy.get('breakers') or []
    if breakers and who not in breakers:
      raise api.Failure(
        api.REFUSED, f'{who} may not break a lease; configured breakers: '
        f'{", ".join(breakers)}')
  proc = api.ssh(entry, LEASE_SCRIPT, [
    options.action, who, purpose, int(hours * 3600), grace, reason,
    str(policy.get('prevent_sleep', True)).lower(),
    *legacy])
  if proc.returncode not in (0, 1):
    raise api.Failure(api.UNREACHABLE,
                      f'device script failed: {proc.stderr.strip()}')
  values = api.parse(proc.stdout)
  lease = lease_summary(values)
  result = values.get('result', 'unknown')
  if lease['inhibit'] == 'failed':
    print('Warning: sleep inhibition failed; the device may sleep.',
          file=sys.stderr)
  if options.json:
    print(json.dumps(dict(lease, result=result, device=name), indent=2))
  else:
    messages = {
      'took': f'Took the {name} lease.',
      'refreshed': f'Renewed the {name} lease.',
      'reclaimed': f'Took the {name} lease (reclaimed an expired one).',
      'released': f'Released the {name} lease.',
      'broke': f'Broke the {name} lease; the device log records why.',
      'free': f'The {name} lease is free.',
      'ok': f'The {name} lease is yours.',
      'legacy': f'An older project lock holds the {name}.',
      'busy': f'Another agent is reclaiming or breaking the {name} lease.',
      'notmine': f'The {name} lease is not yours.',
      'held': f'The {name} lease is held.',
      'expired': f'Your {name} lease has expired; renew it to keep it.',
      'gone': f'The {name} lease changed while this ran; check it again.',
      'failed': f'Could not write the {name} lease record on the device.',
    }
    stream = sys.stdout if proc.returncode == 0 else sys.stderr
    if result != 'shown':
      print(messages.get(result, result), file=stream)
    if result not in ('released', 'broke', 'free') or lease['legacy']:
      print(describe(name, lease), file=stream)
  return api.OK if proc.returncode == 0 else api.REFUSED
