#!/usr/bin/env python3
"""Reproduce Moss & Mugs using production renderers and isolated inputs."""
import argparse
import copy
from datetime import datetime, timezone
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
BIN = ROOT / 'bin'


def fixture():
  return json.loads(Path(__file__).with_name('moss-and-mugs.json').read_text())


def environment(stage):
  # Allowlist instead of retaining provider secrets or agent configuration.
  git = shutil.which('git')
  git_path = str(Path(git).parent) if git else ''
  return {'PATH': os.pathsep.join(filter(None,
      (str(stage / 'bin'), git_path, os.defpath))),
    'HOME': str(stage / 'home'), 'XDG_CONFIG_HOME': str(stage / 'config'),
    'XDG_CACHE_HOME': str(stage / 'cache'),
    'XDG_DATA_HOME': str(stage / 'data'), 'TERM': 'xterm-256color',
    'TZ': 'UTC', 'LANG': 'en_US.UTF-8', 'PYTHONDONTWRITEBYTECODE': '1',
    'AGENT_TASK_ROOT': str(stage / 'tasks'),
    'AGENT_CHANGELOG_ROOT': str(stage / 'changes'),
    'DEMO_STAGE': str(stage), 'DEMO_PYTHON': sys.executable}


def quota_module():
  sys.path.insert(0, str(BIN))
  loader = importlib.machinery.SourceFileLoader('demo_quota', str(BIN / 'agent-quota'))
  module = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
  loader.exec_module(module)
  return module


def inputs(quota):
  data = fixture()
  moment = datetime.fromisoformat(data['clock'].replace('Z', '+00:00'))
  document = {'schema_version': 3, 'generated_at': data['clock'], 'services': {}}
  for provider in data['quotas']:
    limits = []
    for item in provider['limits']:
      reset = datetime.fromisoformat(item['reset'].replace('Z', '+00:00'))
      observation = quota.make_observation(item['used'], moment, reset,
        3600, 'synthetic fixture', 'documented', moment)
      limits.append({'limit_id': item['id'], 'bucket': {
        'id': 'account', 'name': 'All models', 'scope_kind': 'account'},
        'window': {'label': item['id'], 'duration_seconds': 604800},
        'last_observation': observation,
        'pace': {'reset_in_seconds': (reset - moment).total_seconds()},
        'burn': item.get('burn', {})})
    document['services'][provider['id']] = {
      'service_id': provider['id'], 'display_name': provider['name'],
      'provider_id': provider['provider'], 'account': {
        'label': 'woodland-demo', 'email': 'demo@example.invalid'},
      'limits': limits, 'checked_at': data['clock'], 'freshness': 'fresh'}
  return data, moment, document


def validate_quota_arguments(quota, argv):
  """Use the production parser/gate, stop before any data collection."""
  class Validated(Exception):
    pass
  original = quota.validate_flags
  def gate(parser, args, arguments):
    original(parser, args, arguments)
    raise Validated()
  quota.validate_flags = gate
  try:
    quota.main(argv)
  except Validated:
    return
  finally:
    quota.validate_flags = original
  raise RuntimeError('demo quota command bypassed argument validation')


def materialize_records(stage):
  """Create schema-valid disposable repositories for the real read path."""
  git = shutil.which('git')
  if not git:
    raise RuntimeError('Git is required to create isolated demo records')
  sys.path.insert(0, str(BIN))
  from agent_records_core import render_document
  from agent_records_tasks import ORDER, CLOSED, lint_tasks, close_ready
  for name, kind in (('tasks', 'tasks'), ('changes', 'changelog')):
    root = stage / name
    root.mkdir(exist_ok=True)
    subprocess.run([git, 'init', '-q', str(root)],
      check=True, env=environment(stage), capture_output=True)
    (root / '.agent-records').write_text(json.dumps({'kind': kind}))
    (root / '.records.lock').touch(mode=0o600)
  tasks = stage / 'tasks'
  (tasks / 'archive').mkdir()
  (tasks / '.next-id').write_text('8\n')
  for ident, row in fixture()['tasks'].items():
    root = tasks / 'archive' if row['fields']['status'] in CLOSED else tasks
    (root / (ident + '-demo.md')).write_bytes(
      render_document(row['fields'], ORDER, row['body']))
    if row['fields']['status'] == 'done':
      close_ready(row['fields'], row['body'], stage / 'changes', ident, 'done')
  errors = lint_tasks(tasks, stage / 'changes')
  if errors:
    raise RuntimeError('Invalid demo records: ' + '; '.join(errors))


def pane(kind, argv):
  quota = quota_module()
  if kind == 'agent-quota':
    validate_quota_arguments(quota, argv)
  data, moment, document = inputs(quota)
  stage = Path(os.environ['DEMO_STAGE'])
  # All collectors are forbidden even if a renderer changes its fallback.
  def forbidden(*args, **kwargs):
    raise RuntimeError('demo attempted a live collector')
  quota.live_document = forbidden
  quota.live_consumers = forbidden
  if kind == 'agent-task':
    if '--help' in argv:
      print('Synthetic adapter: list --live | list --json')
      return
    if '--json' in argv:
      print(json.dumps(data['tasks']))
      return
    if argv != ['list', '--live']:
      raise SystemExit('Demo permits only agent-task list --live')
    import agent_records_live_terminal as terminal
    terminal.now = lambda: moment
    args = SimpleNamespace(here=False, wait=.1, interval=2, repo=None,
      status=None, owner=None, unowned=False, archived=False, all=False,
      agent_cache_file=stage / 'cache/agents.json')
    terminal.run_live(stage / 'tasks', stage / 'changes', args)
  elif '--agents' in argv:
    import agent_activity_live as activity
    # Override this module's wall clock only; leave scheduling/host clock alone.
    activity.time = SimpleNamespace(time=lambda: moment.timestamp())
    args = SimpleNamespace(interval=2, color_on=True, verbose=False, notify=False)
    frame = {'agents': data['agents'], 'cache': {'sessions': {}}, 'command': None}
    activity.run_agent_live(args, stage / 'cache/quota.json', quota,
      loader=lambda: (copy.deepcopy(frame), copy.deepcopy(document)))
  elif '--timeline' in argv:
    if '--live' not in argv:
      print(quota.render_timeline(document, timezone.utc, color=True))
      return
    cache = stage / 'cache/quota.json'
    cache.write_text(json.dumps(document))
    os.utime(cache, (moment.timestamp(), moment.timestamp()))
    namespace = quota.run_live.__globals__
    namespace['live_document'] = lambda *a, **k: copy.deepcopy(document)
    namespace['live_consumers'] = lambda *a, **k: {}
    args = SimpleNamespace(agents=False, timeline=True, interval=5,
      color_on=True, notify=False, verbose=False)
    quota.run_live(args, stage / 'cache/quota.json', clock=lambda: moment.timestamp())
  else:
    raise SystemExit('Unsupported demo adapter command')


def stage_files(stage, tmux):
  for name in ('bin', 'home', 'cache', 'config', 'data', 'tasks', 'changes'):
    (stage / name).mkdir()
  materialize_records(stage)
  shutil.copy2(BIN / 'agent-dash', stage / 'bin/agent-dash')
  for command in ('agent-task', 'agent-quota'):
    adapter = stage / 'bin' / command
    adapter.write_text('#!/bin/sh\nexec "$DEMO_PYTHON" ' +
      __import__('shlex').quote(str(Path(__file__).resolve())) +
      ' --pane ' + command + ' "$@"\n')
    adapter.chmod(0o755)
  adapter = stage / 'bin/tmux'
  adapter.write_text('#!/bin/sh\nexec ' + __import__('shlex').quote(tmux) +
    ' -S "$DEMO_STAGE/tmux.sock" -f /dev/null "$@"\n')
  adapter.chmod(0o755)


def main():
  if len(sys.argv) >= 3 and sys.argv[1] == '--pane':
    pane(sys.argv[2], sys.argv[3:])
    return
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument('--pane', help=argparse.SUPPRESS)
  parser.add_argument('--capture', type=Path, help='write ANSI captures to directory')
  args, rest = parser.parse_known_args()
  if args.pane:
    pane(args.pane, rest)
    return
  if rest:
    parser.error('unknown arguments: ' + ' '.join(rest))
  tmux = shutil.which('tmux')
  if not tmux:
    parser.error('tmux is required')
  with tempfile.TemporaryDirectory(prefix='agent-kit-demo-') as temporary:
    stage = Path(temporary).resolve()
    stage_files(stage, tmux)
    env = environment(stage)
    def run(*argv):
      return subprocess.run([str(stage / 'bin/tmux'), *argv], env=env,
        check=True, text=True, capture_output=True).stdout
    try:
      run('new-session', '-d', '-s', 'demo', '-x', '110', '-y', '42')
      run('set-option', '-g', 'status', 'off')
      run('set-option', '-g', 'pane-border-status', 'top')
      run('set-option', '-g', 'pane-border-format', ' #{pane_title} ')
      env['TMUX'] = str(stage / 'tmux.sock') + ',0,0'
      subprocess.run([str(stage / 'bin/agent-dash'), 'moss-and-mugs'],
        env=env, check=True)
      if not args.capture:
        subprocess.run([str(stage / 'bin/tmux'), 'attach-session', '-t', 'demo'], env=env)
      else:
        capture(run, args.capture)
    finally:
      subprocess.run([tmux, '-S', str(stage / 'tmux.sock'), 'kill-server'],
        env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def capture(run, output):
  output.mkdir(parents=True, exist_ok=True)
  panes = run('list-panes', '-t', 'demo:moss-and-mugs', '-F',
    '#{pane_id}\t#{pane_title}').splitlines()
  mapping = dict(line.split('\t', 1)[::-1] for line in panes)
  time.sleep(1)
  agents = mapping['Agents']
  tasks = mapping['Agent Tasks']
  timeline = mapping['Timeline']
  run('send-keys', '-t', agents, 'Right')
  time.sleep(.3)
  def save(name, target):
    text = run('capture-pane', '-p', '-e', '-t', target).rstrip('\n') + '\n'
    (output / (name + '.ansi')).write_text(text)
    return text
  # Capture production pane geometry and text; no independently drawn UI.
  chunks = []
  for title in ('Agents', 'Agent Tasks', 'Timeline'):
    chunks.append('\x1b[1m' + title + '\x1b[0m\n' +
      save({'Agents':'agent-activity', 'Agent Tasks':'tasks',
            'Timeline':'quota-timeline'}[title], mapping[title]))
  (output / 'dashboard.ansi').write_text(''.join(chunks).rstrip('\n') + '\n')
  # Real keys drive filtering, selection, and details.
  run('send-keys', '-t', tasks, '/', 'Save the shop overnight', 'Enter')
  time.sleep(.2)
  filtered = run('capture-pane', '-p', '-t', tasks)
  assert 'Save the shop overnight' in filtered and 'Add teapot brewing' not in filtered
  run('send-keys', '-t', tasks, '/', 'Escape', 'g', 'g', 'j', 'j', 'j', 'Enter', 'Tab', 'Tab')
  run('resize-window', '-t', 'demo:moss-and-mugs', '-x', '110', '-y', '58')
  run('resize-pane', '-t', tasks, '-y', '28')
  time.sleep(.3)
  run('send-keys', '-t', tasks, 'g', 'g', 'j', 'j', 'j')
  time.sleep(.2)
  detail = save('task-details', tasks)
  assert 'Evidence · T-0004' in detail and 'work-reviewed: the work was reviewed' in detail
  run('send-keys', '-t', tasks, 'Escape', '/', 'Escape')
  run('send-keys', '-t', agents, 'Left')
  time.sleep(.2)
  folded = run('capture-pane', '-p', '-t', agents)
  assert 'Implement steeping timer' not in folded
  run('send-keys', '-t', agents, 'Right', 'Enter')
  run('resize-pane', '-t', agents, '-y', '24')
  time.sleep(.2)
  expanded = save('agent-activity', agents)
  assert 'Implement steeping timer' in expanded
  print('Captured real isolated dashboard panes in ' + str(output))


if __name__ == '__main__':
  main()
