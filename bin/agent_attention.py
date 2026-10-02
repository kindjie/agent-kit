"""Explicit, pane-local attention signals; no transcript or task inference."""
import argparse
import json
import os
import re
import subprocess
import sys

STATES = {'needs-you': ('YOU', 'Y'), 'blocked': ('BLOCKED', 'B'),
          'work': ('WORK', 'W'), 'check': ('CHECK', '?')}


def tmux(args):
  try:
    result = subprocess.run(['tmux', *args], capture_output=True, text=True,
                            timeout=.75)
    return result.stdout.strip() if result.returncode == 0 else None
  except (OSError, subprocess.TimeoutExpired):
    return None


def pane():
  value = os.environ.get('TMUX_PANE', '')
  return value if os.environ.get('TMUX') and re.fullmatch(r'%\d+', value) else ''


def ring():
  # Tool workers may have neither visible stdout nor a controlling terminal.
  # Resolve the exact pane's PTY; never inject terminal input or guess a pane.
  target = pane()
  tty = (tmux(['display-message', '-p', '-t', target, '#{pane_tty}'])
         if target else '/dev/tty')
  if not tty or (target and not re.fullmatch(r'/dev/(ttys?\d+|pts/\d+)', tty)):
    return
  try:
    fd = os.open(tty, os.O_WRONLY | os.O_NOCTTY)
    try:
      os.write(fd, b'\a')
    finally:
      os.close(fd)
  except OSError:
    pass


def publish(label, mark, kind=''):
  target = pane()
  if not target:
    return
  args = []
  for option, value in (('@agent_attention', label),
                        ('@agent_attention_mark', mark),
                        ('@agent_attention_kind', kind)):
    if args:
      args.append(';')
    args += ['set-option', '-p', '-t', target]
    args += [option, value] if value else ['-u', option]
  return tmux(args) is not None


def set_attention(state, bell=False):
  if state not in STATES:
    return
  target = pane()
  if not target:
    if bell and not os.environ.get('TMUX'):
      ring()
    return
  previous = tmux(['show-options', '-pqv', '-t', target,
                   '@agent_attention_kind'])
  if previous is None:
    return
  label, mark = STATES[state]
  published = publish(label, mark, state)
  if published and bell and previous != state:
    ring()


def clear_attention():
  publish('', '')


def update_badge(previous, badge):
  if badge is not None and badge != previous and publish(*badge):
    return badge
  return previous


def summary():
  rows = tmux(['list-panes', '-a', '-F',
    '#{pane_id}\t#{pane_dead}\t#{@agent_attention_dashboard}\t'
    '#{@agent_attention_kind}'])
  if rows is None:
    return None
  counts = dict.fromkeys(STATES, 0)
  for row in (rows or '').splitlines():
    parts = row.split('\t')
    if len(parts) != 4:
      continue
    _, dead, dashboard, state = parts
    if dead == '0' and not dashboard and state in counts:
      counts[state] += 1
  label = ' · '.join(f'{STATES[state][0]} {count}'
                     for state, count in counts.items() if count)
  total = sum(counts.values())
  return label, f'!{total}' if total else ''


def hook(payload):
  if not isinstance(payload, dict) or payload.get('agent_id'):
    return
  event = payload.get('hook_event_name')
  if event == 'UserPromptSubmit' or (event == 'SessionStart' and
      payload.get('source') != 'compact'):
    clear_attention()


def main():
  parser = argparse.ArgumentParser(description=__doc__)
  sub = parser.add_subparsers(dest='mode', required=True)
  setter = sub.add_parser('set')
  setter.add_argument('state', choices=STATES)
  setter.add_argument('--bell', action='store_true')
  sub.add_parser('clear')
  sub.add_parser('hook')
  args = parser.parse_args()
  if args.mode == 'set':
    set_attention(args.state, args.bell)
  elif args.mode == 'clear':
    clear_attention()
  else:
    try:
      hook(json.load(sys.stdin))
    except (ValueError, OSError):
      pass
  return 0


if __name__ == '__main__':
  sys.exit(main())
