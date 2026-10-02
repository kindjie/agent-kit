"""Conservative transcript observations; these never establish liveness."""
from collections import Counter
import json
import math
import os
from pathlib import Path
import re
import shlex

FRESH_FOR = 20 * 60


def arguments(value):
  if isinstance(value, str):
    try:
      value = json.loads(value)
    except ValueError:
      return {}
  return value if isinstance(value, dict) else {}


def seconds(value, divisor=1):
  try:
    result = float(value) / divisor
    return result if math.isfinite(result) and result >= 0 else None
  except (TypeError, ValueError):
    return None


def wait_spec(name, value):
  """Only recognized foreground calls/commands, never free-text mentions."""
  args = arguments(value)
  short = name.rsplit('.', 1)[-1]
  if args.get('run_in_background') or args.get('background'):
    return None
  if short == 'wait_agent' or (short == 'wait' and 'ids' in args):
    targets = args.get('ids', [])
    if not isinstance(targets, list):
      targets = []
    return {'kind': 'agent', 'targets': [str(i) for i in targets],
            'tasks': [], 'condition': 'agent update',
            'timeout': seconds(args.get('timeout_ms'), 1000)}
  if short in ('Agent', 'Task'):
    return {'kind': 'agent', 'targets': [], 'tasks': [],
            'condition': 'foreground delegate result', 'timeout': None}
  if short == 'exec' and isinstance(value, str):
    # A single literal wrapper is common. General JavaScript is not evaluated.
    match = re.fullmatch(
      r'\s*(?:text\()?await tools\.exec_command\((\{.*\})\)\)?;?\s*',
      value, re.S)
    return wait_spec('exec_command', match[1]) if match else None
  if short not in ('Bash', 'exec_command', 'shell_command'):
    return None
  command = args.get('cmd', args.get('command'))
  if not isinstance(command, str) or '\n' in command:
    return None
  try:
    lexer = shlex.shlex(command, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    words = list(lexer)
  except ValueError:
    return None
  if not words or Path(words[0]).name != 'agent-task':
    return None
  if any(re.fullmatch(r'[;&|<>()]+', w) for w in words):
    return None
  # Global options are deliberately not guessed; accepting only a simple
  # executable command avoids mistaking shell text for a real subscription.
  if len(words) < 3 or words[1] not in ('watch', 'events'):
    return None
  tasks, opts = [], {}
  index = 2
  while index < len(words):
    word = words[index]
    if re.fullmatch(r'T-\d{4,}', word):
      tasks.append(word)
      index += 1
    elif word in ('--until', '--for', '--after', '--timeout', '--interval'):
      if index + 1 >= len(words):
        return None
      opts[word] = words[index + 1]
      index += 2
    else:
      return None
  if not tasks or (words[1] == 'watch' and len(tasks) != 1):
    return None
  if words[1] == 'events' and '--after' not in opts:
    return None  # Initial cursor read returns immediately.
  condition = opts.get('--until', 'changes')
  if words[1] == 'events':
    condition = 'changes/messages'
  if opts.get('--for'):
    condition += ' for ' + opts['--for']
  return {'kind': 'task', 'targets': [], 'tasks': tasks,
          'condition': condition,
          'timeout': seconds(opts.get('--timeout', 540))}


class Tracker:
  def __init__(self):
    self.phase, self.at, self.pending = 'unknown', None, {}

  def phase_at(self, phase, at, clear=False):
    self.phase, self.at = phase, at
    if clear:
      self.pending.clear()

  def call(self, name, value, ident, at):
    self.phase_at('active', at)
    self.pending[ident] = {'id': ident, 'at': at, 'tool': name,
                          'wait': wait_spec(name, value)}

  def result(self, ident, at):
    self.pending.pop(ident, None)
    self.phase_at('active', at)

  def feed(self, row, provider, at):
    if provider == 'codex':
      payload = row.get('payload') or {}
      event = payload.get('type')
      if row.get('type') == 'event_msg':
        if event == 'task_started':
          self.phase_at('active', at, True)
        elif event in ('task_complete', 'turn_aborted'):
          self.phase_at('ended' if event == 'task_complete' else 'aborted',
                        at, True)
      elif row.get('type') == 'response_item':
        if event in ('function_call', 'custom_tool_call'):
          self.call(payload.get('name', 'tool'),
                    payload.get('arguments', payload.get('input')),
                    payload.get('call_id'), at)
        elif event in ('function_call_output', 'custom_tool_call_output'):
          self.result(payload.get('call_id'), at)
        elif event == 'reasoning':
          self.phase_at('thinking', at)
        elif event == 'message' and payload.get('role') == 'assistant':
          self.phase_at('active', at)
    else:
      message = row.get('message') or {}
      content = message.get('content')
      if row.get('type') == 'user' and not row.get('isMeta'):
        items = content if isinstance(content, list) else []
        results = [i for i in items if isinstance(i, dict) and
                   i.get('type') == 'tool_result']
        for item in results:
          self.result(item.get('tool_use_id'), at)
        if not results:
          self.phase_at('active', at, True)
      elif row.get('type') == 'assistant':
        for item in content if isinstance(content, list) else []:
          if not isinstance(item, dict):
            continue
          if item.get('type') == 'tool_use':
            self.call(item.get('name', 'tool'), item.get('input'),
                      item.get('id'), at)
          elif item.get('type') in ('thinking', 'text'):
            self.phase_at('thinking' if item['type'] == 'thinking' else
                          'active', at)
        if message.get('stop_reason') in ('end_turn', 'stop_sequence'):
          self.phase_at('ended', at, True)

  def value(self):
    return {'phase': self.phase, 'at': self.at,
            'pending': list(self.pending.values())}


def observe(agent, now):
  raw = agent.get('observation') or {}
  phase, at = raw.get('phase'), raw.get('at')
  result = {'state': 'UNKNOWN', 'at': at, 'waits': [],
            'reason': 'No current execution observation'}
  if not isinstance(at, (int, float)) or at > now + 5:
    return result
  pending = raw.get('pending') or []
  result['waits'] = [dict(p['wait'], at=p.get('at')) for p in pending
                     if p.get('wait')]
  if now - at > FRESH_FOR:
    result['reason'] = 'Observation older than 20m; current state unknown'
    return result
  for wait in result['waits']:
    if wait.get('timeout') is not None and (
        now - (wait.get('at') or at) > wait['timeout']):
      result['reason'] = 'Observed wait deadline passed; result not observed'
      return result
  if pending:
    all_waiting = all(p.get('wait') for p in pending)
    result.update(state='WAITING' if all_waiting else 'TOOL',
                  reason='Outstanding observed call: ' +
                  ', '.join(p['tool'] for p in pending))
  else:
    state = {'ended': 'IDLE', 'done': 'DONE', 'aborted': 'ABORTED',
             'thinking': 'THINKING', 'active': 'ACTIVE', 'tool': 'TOOL'}
    result.update(state=state.get(phase, 'UNKNOWN'), reason={
      'ended': 'Turn ended; no current work inferred',
      'done': 'Subagent turn ended', 'thinking': 'Reasoning event observed',
      'active': 'Turn open; current action unknown',
      'aborted': 'Turn aborted', 'tool': 'Tool operation observed',
    }.get(phase, 'No current execution observation'))
  return result


def cached_task_waits(task, now, path=None):
  """Read existing observations only; no discovery, cache writes or processes."""
  if path is None:
    root = Path(os.environ.get('XDG_CACHE_HOME', Path.home() / '.cache'))
    path = root / 'agent-quota' / 'agents-v1.json'
  try:
    path = Path(path).expanduser()
    if path.stat().st_size > 32 * 1024 * 1024:
      return 'Agent cache too large; observations unavailable', []
    cache = json.loads(path.read_text())
    if not isinstance(cache, dict) or cache.get('version') != 7:
      return 'Agent cache format unavailable; refresh the agent view', []
    at = cache.get('observed_at')
    if not isinstance(at, (int, float)) or not 0 <= now - at <= FRESH_FOR:
      return 'Agent cache stale or timestamp unknown', []
    records = cache.get('sessions')
    if not isinstance(records, dict):
      return 'Agent cache records unavailable', []
    result = {}
    for record in records.values():
      a = record.get('agent') if isinstance(record, dict) else None
      if not isinstance(a, dict):
        continue
      obs = observe(a, now)
      for wait in obs['waits']:
        if task in wait['tasks']:
          key = a.get('key') or a.get('id', 'unknown')
          result[key] = {'agent': key, 'state': obs['state'],
                         'condition': wait['condition'], 'at': wait['at']}
    label = 'Cached local observations; coverage not guaranteed'
    if cache.get('scan_truncated') or cache.get('scan_errors'):
      label += ' (incomplete scan)'
    return label, list(result.values())
  except (OSError, ValueError, TypeError, AttributeError):
    return 'Agent observations unavailable', []


class AgentTree:
  def __init__(self, agents, now, incomplete=False):
    self.rows = {a['key']: a for a in agents}
    self.children = {key: [] for key in self.rows}
    self.parents, self.order = {}, []
    self.incomplete = incomplete
    for key, row in self.rows.items():
      parent = row['provider'] + ':' + str(row.get('parent_id'))
      # Only keep an acyclic path; malformed links remain separate roots.
      cursor, visited = parent, {key}
      while cursor in self.rows and cursor not in visited:
        visited.add(cursor)
        other = self.rows[cursor]
        cursor = other['provider'] + ':' + str(other.get('parent_id'))
      if cursor in visited:
        self.incomplete = True
        continue
      if parent in self.rows:
        self.parents[key] = parent
        self.children[parent].append(key)
      elif row.get('parent_id'):
        self.incomplete = True
    def visit(key):
      self.order.append(key)
      for child in self.children[key]:
        visit(child)
    for key in self.rows:
      if key not in self.parents:
        visit(key)
    self.refresh(now)

  def refresh(self, now):
    self.observed = {k: observe(a, now) for k, a in self.rows.items()}
    for key, obs in self.observed.items():
      if obs['state'] != 'WAITING':
        continue
      waits = obs['waits']
      targets = [(self.rows[key]['provider'] + ':' + i, w['at'])
                 for w in waits for i in w['targets']]
      if targets and all(w['kind'] == 'agent' and w['targets']
                         for w in waits) and all(
          t in self.observed and self.observed[t]['state'] in ('DONE', 'IDLE')
          and (self.observed[t]['at'] or 0) >= started
          for t, started in targets):
        obs['state'] = 'RESULT READY'
        obs['reason'] = 'Awaited turn ended; parent wait result not observed'

  def descendants(self, key):
    found, pending = [], list(self.children[key])
    while pending:
      child = pending.pop(0)
      found.append(child)
      pending.extend(self.children[child])
    return found

  def totals(self, key):
    totals = Counter()
    for child in self.descendants(key):
      state = self.observed[child]['state']
      category = ('active' if state in ('ACTIVE', 'THINKING', 'TOOL') else
                  'idle' if state == 'IDLE' else 'done' if state == 'DONE' else
                  'waiting' if state in ('WAITING', 'RESULT READY') else 'unknown')
      totals[category] += 1
    return totals

  def summary(self, key):
    counts = self.totals(key)
    text = ' · '.join(f'{counts[k]} {k}' for k in
                       ('active', 'waiting', 'idle', 'done', 'unknown')
                       if counts[k]) or 'no active subagents observed'
    if self.incomplete:
      text += ' · coverage unknown'
    return text
