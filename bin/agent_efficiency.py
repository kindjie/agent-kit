"""Offline observed usage ledger. No network, model calls, or transcript text output."""
import argparse
from collections import Counter
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path
import re
import shlex
import sys
import time

KEYS = ('input_tokens', 'cached_input_tokens', 'cache_write_input_tokens',
        'output_tokens', 'total_tokens')


def literal_keys(value):
  # Permit unquoted JavaScript object keys in the JSON literal subset.
  # Matching complete JSON strings first leaves command text untouched.
  return re.sub(r'"(?:\\.|[^"\\])*"|([A-Za-z_]\w*)\s*:',
                lambda m: json.dumps(m[1]) + ':' if m[1] else m[0], value)


def shell_specs(name, value):
  """Recognize literal shell invocations; never evaluate JavaScript/shell."""
  if not isinstance(name, str):
    return []
  short = name.rsplit('.', 1)[-1]
  if short == 'exec' and isinstance(value, str):
    # Only small known wrappers have a reliable call/output correspondence.
    value = re.sub(r'^\s*// @exec:[^\n]*\n', '', value)
    one = re.fullmatch(
      r'\s*(?:text\()?await tools\.(exec_command|write_stdin)'
      r'\((\{.*\})\)\)?;?\s*',
      value, re.S)
    assigned = re.fullmatch(
      r'\s*(?:const|let|var) (\w+)\s*=\s*await tools\.'
      r'(exec_command|write_stdin)'
      r'\((\{.*\})\);\s*text\(\1\);?\s*', value, re.S)
    if one or assigned:
      return shell_specs(one[1] if one else assigned[2],
                         one[2] if one else assigned[3])
    batch = re.fullmatch(
      r'\s*(?:const|let|var) (\w+)\s*=\s*await Promise\.allSettled'
      r'\(\[(.*)\]\);\s*\1\.forEach\(text\);?\s*', value, re.S)
    if not batch:
      return []
    remaining, result = literal_keys(batch[2]).strip(), []
    while remaining:
      prefix = re.match(r'tools\.(exec_command|write_stdin)\(\s*', remaining)
      if not prefix:
        return []
      try:
        args, end = json.JSONDecoder().raw_decode(remaining[prefix.end():])
      except ValueError:
        return []
      tail = remaining[prefix.end() + end:].lstrip()
      if not tail.startswith(')'):
        return []
      specs = shell_specs(prefix[1], args)
      if len(specs) != 1:
        return []
      result.extend(specs)
      remaining = tail[1:].strip()
      if remaining.startswith(','):
        remaining = remaining[1:].strip()
      elif remaining:
        return []
    return result
  if short not in ('exec_command', 'shell_command', 'Bash', 'write_stdin'):
    return []
  if isinstance(value, str):
    try:
      value = json.loads(literal_keys(value))
    except ValueError:
      return []
  if not isinstance(value, dict):
    return []
  if short == 'write_stdin':
    session = value.get('session_id')
    if isinstance(session, (str, int)) and not isinstance(session, bool):
      return [{'resume_session': session}]
    return []
  command = value.get('cmd', value.get('command'))
  if not isinstance(command, str):
    return []
  result = {'events': False, 'bootstrap': False, 'after': None,
            'scope': (value.get('workdir', value.get('cwd')), '')}
  try:
    lexer = shlex.shlex(command, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    words = list(lexer)
  except ValueError:
    return [result]
  if (not words or Path(words[0]).name != 'agent-task' or
      '\n' in command or '$' in command or '`' in command or
      any(re.fullmatch(r'[;&|<>()]+', w) for w in words)):
    return [result]
  opts, index = {}, 1
  while index < len(words) and words[index].startswith('--'):
    word = words[index]
    option, sep, argument = word.partition('=')
    if option not in ('--dir', '--agent', '--wait'):
      return [result]
    if not sep:
      index += 1
      if index >= len(words):
        return [result]
      argument = words[index]
    opts[option] = argument
    index += 1
  if index >= len(words) or words[index] != 'events':
    return [result]
  index += 1
  tasks = []
  while index < len(words):
    word = words[index]
    if re.fullmatch(r'T-\d{4,}', word):
      tasks.append(word)
    elif word == '--actionable':
      opts[word] = True
    else:
      option, sep, argument = word.partition('=')
      if option not in ('--after', '--for', '--timeout', '--interval',
                        '--agent'):
        return [result]
      if not sep:
        index += 1
        if index >= len(words):
          return [result]
        argument = words[index]
      opts[option] = argument
    index += 1
  if not tasks:
    return [result]
  after = None
  try:
    after = json.loads(opts['--after']) if '--after' in opts else None
  except (ValueError, TypeError):
    pass
  scope = [value.get('workdir', value.get('cwd')), opts.get('--dir'),
           opts.get('--for'), opts.get('--actionable'), sorted(tasks)]
  result.update(events=True, bootstrap='--after' not in opts, after=after,
                scope=json.dumps(scope))
  return [result]


def result_objects(value, depth=0):
  """Unwrap known tool envelopes, with bounded depth and no prose search."""
  if depth > 8:
    return []
  if isinstance(value, str):
    try:
      value = json.loads(value)
    except ValueError:
      # Exact native shell envelope; arbitrary mentions of an exit code
      # in transcript prose are never interpreted as process results.
      match = re.fullmatch(
        r'Chunk ID: [^\n]+\nWall time: [^\n]+\n'
        r'Process exited with code (-?\d+)\nFinal output:\n(.*)',
        value, re.S)
      if match:
        return [{'exit_code': int(match[1]), 'output': match[2]}]
      return []
  if isinstance(value, list):
    return [obj for item in value
            for obj in result_objects(item, depth + 1)]
  if not isinstance(value, dict):
    return []
  if value.get('type') in ('text', 'input_text'):
    return result_objects(value.get('text'), depth + 1)
  if 'exit_code' in value or 'session_id' in value:
    return [value]
  if value.get('status') == 'fulfilled':
    return result_objects(value.get('value'), depth + 1)
  if isinstance(value.get('content'), list):
    return [obj for item in value['content'] if isinstance(item, dict)
            and item.get('type') == 'text'
            for obj in result_objects(item.get('text'), depth + 1)]
  return []


class Coordination:
  """Counters are observed results, never proof of successful mutations."""
  def __init__(self):
    self.calls, self.results = {}, {}

  def feed(self, event, provider, thread, at):
    payload = event.get('payload', {})
    if not isinstance(payload, dict):
      payload = {}
    if provider == 'codex' and event.get('type') == 'response_item':
      kind, ident = payload.get('type'), payload.get('call_id')
      if not isinstance(ident, str) or not ident:
        return
      key = (provider, ident)
      if kind in ('function_call', 'custom_tool_call'):
        specs = shell_specs(payload.get('name', ''),
          payload.get('arguments', payload.get('input')))
        if specs:
          self.calls.setdefault(key, (at, specs))
      elif kind in ('function_call_output', 'custom_tool_call_output'):
        self.results.setdefault(key, (at, thread,
          result_objects(payload.get('output'))))
    elif provider == 'claude':
      message = event.get('message') or {}
      if not isinstance(message, dict):
        return
      blocks = message.get('content', [])
      if not isinstance(blocks, list):
        return
      for block in blocks:
        if not isinstance(block, dict):
          continue
        ident = block.get('id', block.get('tool_use_id'))
        if not isinstance(ident, str) or not ident:
          continue
        key = (provider, ident)
        if block.get('type') == 'tool_use':
          specs = shell_specs(block.get('name', ''), block.get('input'))
          if specs:
            self.calls.setdefault(key, (at, specs))
        elif block.get('type') == 'tool_result':
          objects = result_objects(block.get('content'))
          # Claude supplies is_error, but not a portable process exit status.
          if not objects and block.get('is_error') is True:
            objects = [{'explicit_error': True}]
          self.results.setdefault(key, (at, thread, objects))

  def report(self, since, until):
    counters = Counter({key: 0 for key in (
      'completed_cli_attempts', 'failed_cli_attempts',
      'empty_events_returns', 'empty_events_bootstrap_returns',
      'empty_events_timeout_returns', 'renewal_only_wakeups',
      'unsupported_cli_results', 'recognized_cli_calls',
      'cli_calls_without_results', 'unmatched_cli_resumptions')})
    for key, (at, specs) in self.calls.items():
      if since <= at < until:
        original = sum('resume_session' not in s for s in specs)
        counters['recognized_cli_calls'] += original
        if key not in self.results:
          counters['cli_calls_without_results'] += original
    fields, sessions, finished = {}, {}, set()
    for key, (at, thread, objects) in sorted(self.results.items(),
                                           key=lambda item: item[1][0]):
      call = self.calls.get(key)
      if not call:
        continue
      specs = call[1]
      in_window = since <= at < until
      if len(objects) != len(specs):
        if in_window:
          counters['unsupported_cli_results'] += 1
        continue
      for index, (spec, obj) in enumerate(zip(specs, objects)):
        origin = (key, index)
        if 'resume_session' in spec:
          resumed = sessions.get((key[0], thread, spec['resume_session']))
          if resumed is None:
            if in_window:
              counters['unmatched_cli_resumptions'] += 1
            continue
          origin, spec = resumed
        if origin in finished:
          continue
        session = obj.get('session_id')
        code = obj.get('exit_code')
        explicit_error = obj.get('explicit_error') is True
        if (code is None and isinstance(session, (int, str)) and
            not isinstance(session, bool)):
          sessions[(key[0], thread, session)] = (origin, spec)
          continue
        if not explicit_error and (isinstance(code, bool) or
                                   not isinstance(code, int)):
          if in_window:
            counters['unsupported_cli_results'] += 1
          continue
        finished.add(origin)
        if in_window:
          counters['completed_cli_attempts'] += 1
          if explicit_error or (code != 0 and not
                                (spec['events'] and code == 4)):
            counters['failed_cli_attempts'] += 1
        if not spec['events'] or code not in (0, 4):
          continue
        try:
          batch = json.loads(obj.get('output', ''))
        except (ValueError, TypeError):
          continue
        if (not isinstance(batch, dict) or batch.get('schema_version') != 1
            or not isinstance(batch.get('cursor'), dict)
            or not isinstance(batch.get('events'), list)
            or not isinstance(batch.get('rewritten'), list)
            or batch.get('authoritative') is False):
          continue
        if batch['rewritten']:
          fields = {k: v for k, v in fields.items()
                    if k[:2] != (key[0], thread)}
          continue
        if not batch['events'] and in_window:
          counters['empty_events_returns'] += 1
          counters['empty_events_bootstrap_returns'] += spec['bootstrap']
          counters['empty_events_timeout_returns'] += code == 4
        renewals = []
        for event in batch['events']:
          if not isinstance(event, dict):
            renewals.append(False)
            continue
          task = event.get('task')
          if not isinstance(task, str):
            renewals.append(False)
            continue
          ident = (key[0], thread, spec['scope'], task)
          new = event.get('fields')
          baseline = fields.get(ident)
          old = baseline[0] if baseline else None
          renewal = False
          if isinstance(new, dict) and new:
            after = spec.get('after')
            if (old and isinstance(after, dict) and task in after and
                after[task] == baseline[1] and new.get('owner') and
                event.get('log') == []):
              changed = {k for k in old.keys() | new.keys()
                         if old.get(k) != new.get(k)}
              if changed == {'expires'}:
                try:
                  renewal = (timestamp(new['expires']) >
                             timestamp(old['expires']))
                except (ValueError, TypeError):
                  pass
            if task in batch['cursor']:
              fields[ident] = (new, batch['cursor'][task])
          elif (baseline and isinstance(spec.get('after'), dict) and
                spec['after'].get(task) == baseline[1] and
                task in batch['cursor']):
            fields[ident] = (old, batch['cursor'][task])
          renewals.append(renewal)
        if renewals and all(renewals) and in_window and not spec['bootstrap']:
          counters['renewal_only_wakeups'] += 1
    return dict(counters, release_to_confirmation_seconds=None,
      release_to_confirmation_status='unavailable: no structured receipts',
      coverage='recognized literal shell calls and supported result envelopes')


def timestamp(value):
  if not isinstance(value, str):
    raise ValueError('timestamp must be an ISO date with timezone')
  parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
  if parsed.tzinfo is None:
    raise ValueError('timestamp requires timezone')
  return parsed.timestamp()


def usage(raw, provider):
  if not isinstance(raw, dict):
    raise ValueError('missing usage')
  def number(key, required=False):
    n = raw[key] if required else raw.get(key, 0)
    if isinstance(n, bool) or not isinstance(n, int) or n < 0:
      raise ValueError('invalid usage')
    return n
  inp = number('input_tokens', True)
  out = number('output_tokens', True)
  if provider == 'claude':
    cached = number('cache_read_input_tokens')
    write = number('cache_creation_input_tokens')
    inp += cached + write
  else:
    cached = number('cached_input_tokens')
    write = number('cache_write_input_tokens')
    if number('total_tokens', True) != inp + out:
      raise ValueError('inconsistent total')
  if cached + write > inp:
    raise ValueError('cache exceeds input')
  return dict(zip(KEYS, (inp, cached, write, out, inp + out)))


def audit(paths, since, until):
  diagnostics = Counter()
  coordination = Coordination()
  requests, cumulative, tools, compactions = {}, {}, {}, {}
  modern_threads = {}
  duplicate = 0
  files = 0
  for provider, path in paths:
    files += 1
    # Fallback IDs are opaque and local; never print private source paths.
    thread = hashlib.sha256(str(path).encode()).hexdigest()[:20]
    model, internal = 'unknown', False
    start = None
    assistant_activity = False
    recognized_usage = False
    window_activity = False
    window_usage = False
    try:
      stream = path.open(encoding='utf-8')
    except OSError:
      diagnostics['unreadable_files'] += 1
      continue
    with stream:
      for index, line in enumerate(stream):
        try:
          event = json.loads(line)
          if not isinstance(event, dict):
            raise ValueError()
          kind = event.get('type')
          if kind == 'assistant':
            assistant_activity = True
          payload = event.get('payload', {})
          if not isinstance(payload, dict):
            payload = {}
          if kind == 'session_meta':
            thread = payload.get('id') or thread
            source = payload.get('source', {})
            internal = (isinstance(source, dict) and
                        isinstance(source.get('subagent'), dict) and
                        source['subagent'].get('other') == 'guardian')
            start = timestamp(payload.get('timestamp', event['timestamp']))
            continue
          if kind == 'turn_context':
            model = payload.get('model') or 'unknown'
          if payload.get('role') == 'assistant':
            assistant_activity = True
          relevant = (kind in ('token_usage_record', 'assistant', 'user',
                               'response_item', 'event_msg'))
          if not relevant:
            continue
          at = timestamp(event.get('timestamp'))
          if since <= at < until and (kind == 'assistant' or
                                      payload.get('role') == 'assistant'):
            window_activity = True
          if start is not None and at < start:
            continue  # Inherited Codex history belongs to its original thread.
          if provider == 'claude':
            thread = event.get('agentId') or event.get('sessionId') or thread
          coordination.feed(event, provider, thread, at)
          rid, raw, method = None, None, None
          if provider == 'codex' and kind == 'token_usage_record':
            thread = payload.get('thread_id') or thread
            modern_threads[thread] = min(at, modern_threads.get(thread, at))
            rid, raw = payload.get('response_id'), payload.get('usage')
            method = 'request'
          elif provider == 'claude' and kind == 'assistant':
            message = event.get('message', {})
            if not isinstance(message, dict):
              raise ValueError()
            rid, raw = message.get('id'), message.get('usage')
            model = message.get('model') or 'unknown'
            thread = event.get('agentId') or event.get('sessionId') or thread
            method = 'request'
            for block in message.get('content', []):
              if isinstance(block, dict) and block.get('type') == 'tool_use':
                tools[('claude', block.get('id') or f'{thread}:{index}') ] = (
                  at, block.get('name', 'unknown'))
          elif (provider == 'codex' and kind == 'event_msg' and
                payload.get('type') == 'token_count'):
            info = payload.get('info') or {}
            raw = info.get('total_token_usage')
            if raw is None:
              continue
            method = 'cumulative'
          if method:
            normalized = usage(raw, provider)
            recognized_usage = True
            if since <= at < until:
              window_usage = True
            row = {'provider': provider, 'thread': thread, 'model': model,
                   'internal': internal, 'at': at, 'usage': normalized,
                   'method': method}
            if method == 'cumulative':
              cumulative[(thread, at, tuple(normalized.values()))] = row
            else:
              if not isinstance(rid, str) or not rid:
                diagnostics['missing_request_ids'] += 1
                continue
              key = (provider, rid)
              if key in requests:
                duplicate += 1
                old = requests[key]
                # Streaming updates often fill output later; preserve earliest
                # origin/time and use the largest observed value per category.
                merged = {k: max(old['usage'][k], normalized[k]) for k in KEYS}
                if at < old['at']:
                  requests[key] = row
                merged['total_tokens'] = merged['input_tokens'] + merged['output_tokens']
                requests[key]['usage'] = merged
              else:
                requests[key] = row
          if kind == 'response_item' and payload.get('type') in (
              'function_call', 'custom_tool_call'):
            key = ('codex', payload.get('call_id') or f'{thread}:{index}')
            tools[key] = (at, payload.get('name', 'unknown'))
          if kind == 'event_msg' and payload.get('type') == 'item_completed':
            item = payload.get('item', {})
            if isinstance(item, dict) and item.get('type') == 'ContextCompaction':
              begin = payload.get('started_at_ms')
              end = payload.get('completed_at_ms')
              seconds = None
              if (not isinstance(begin, bool) and not isinstance(end, bool)
                  and isinstance(begin, (int, float))
                  and isinstance(end, (int, float))
                  and math.isfinite(begin) and math.isfinite(end)
                  and 0 <= begin <= end):
                seconds = (end - begin) / 1000
              ident = item.get('id') or (at, begin, end)
              compactions.setdefault(('codex', thread, str(ident)),
                                     (at, seconds))
        except (ValueError, KeyError, TypeError, OverflowError):
          diagnostics['malformed_lines'] += 1
    if window_activity and not window_usage:
      diagnostics['window_activity_without_usage'] += 1
    if assistant_activity and not recognized_usage:
      diagnostics['assistant_files_without_usage'] += 1
  rows = list(requests.values())
  previous = {}
  for row in sorted(cumulative.values(), key=lambda r: r['at']):
    ident = row['thread']
    if ident in modern_threads:
      if since <= row['at'] < until:
        diagnostics['mixed_accounting_coverage_unverified'] += 1
      if since <= row['at'] < min(until, modern_threads[ident]):
        diagnostics['uncovered_legacy_observations'] += 1
      continue
    current = row['usage']
    old = previous.get(ident)
    previous[ident] = current
    if old is None:
      if since <= row['at'] < until:
        diagnostics['missing_counter_baselines'] += 1
      continue
    reset = any(current[k] < old[k] for k in KEYS)
    if reset and since <= row['at'] < until:
      diagnostics['counter_resets'] += 1
    row = dict(row, usage=current if reset else
               {k: current[k] - old[k] for k in KEYS})
    rows.append(row)
  totals = Counter({key: 0 for key in KEYS})
  groups = {}
  for row in rows:
    if not since <= row['at'] < until:
      continue
    if row['model'] == 'unknown':
      diagnostics['unknown_model_requests'] += 1
    totals.update(row['usage'])
    key = (row['provider'], row['thread'], row['model'], row['internal'],
           row['method'])
    if key not in groups:
      groups[key] = dict(zip(('provider', 'thread', 'model', 'internal',
                             'method'), key), requests=0,
                         **{k: 0 for k in KEYS})
    group = groups[key]
    group['requests'] += 1
    for k in KEYS:
      group[k] += row['usage'][k]
  if not requests and not cumulative:
    diagnostics['no_supported_usage'] += 1
  if not files:
    diagnostics['no_files'] += 1
  fatal = ('unreadable_files', 'malformed_lines', 'missing_request_ids',
           'missing_counter_baselines', 'no_files', 'no_supported_usage',
           'assistant_files_without_usage', 'uncovered_legacy_observations',
           'mixed_accounting_coverage_unverified', 'window_activity_without_usage')
  compact_groups = {}
  for (provider, thread, _), (at, seconds) in compactions.items():
    if not since <= at < until:
      continue
    group = compact_groups.setdefault((provider, thread), {
      'provider': provider, 'thread': thread, 'count': 0, 'seconds': 0,
      'unknown_duration_count': 0})
    group['count'] += 1
    group['unknown_duration_count'] += seconds is None
    group['seconds'] += seconds if seconds is not None else 0
  return {'schema_version': 1, 'since': since, 'until': until,
          'complete': not any(diagnostics[k] for k in fatal),
          'files_scanned': files, 'duplicate_records': duplicate,
          'diagnostics': dict(diagnostics), 'totals': dict(totals),
          'groups': sorted(groups.values(), key=lambda r: -r['total_tokens']),
          'tool_calls': dict(Counter(name for at, name in tools.values()
                                     if since <= at < until)),
          'coordination': coordination.report(since, until),
          'compaction_count': sum(g['count'] for g in compact_groups.values()),
          'compaction_unknown_duration_count': sum(
            g['unknown_duration_count'] for g in compact_groups.values()),
          'compaction_agent_seconds': sum(g['seconds']
                                          for g in compact_groups.values()),
          'compactions_by_thread': sorted(compact_groups.values(),
            key=lambda r: (r['provider'], r['thread'])),
          'limitations': [
            'Observed local activity, not billing or productivity.',
            'Internal guardian groups are included in totals; separate by internal.',
            'Tool counts indicate activity, not causal overhead token attribution.',
            'Compaction seconds sum across agents and can overlap wall time.',
            'Compaction counts cover completed Codex ContextCompaction items.',
            'CLI counters cover observed results, not successful mutations.',
            'Events timeout exit 4 is a timeout, not a failed CLI attempt.',
            'Renewal-only requires a continuous cursor, expiry-only increase '
            'and no logs.',
            'Release-to-confirmation latency needs structured receipts; '
            'unavailable.',
            'Complete certifies supported usage coverage, '
            'not coordination coverage.',
            'Dynamic wrappers and shell commands can be absent from CLI counters.',
            'Resumed CLI results require observed same-thread session IDs.',
            'Missing files and unsupported record types cannot prove zero usage.',
            'Cumulative fallback omits the first counter without a baseline.',
            'Request records override cumulative counters for the whole thread.',
            'Unknown models remain unattributed; duplicate origins can be ambiguous.']}


def main():
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument('--since', type=timestamp,
                      default=time.time() - 6 * 3600)
  parser.add_argument('--until', type=timestamp, default=time.time())
  parser.add_argument('--codex-dir', type=Path,
                      default=Path.home() / '.codex' / 'sessions')
  parser.add_argument('--claude-dir', type=Path,
                      default=Path.home() / '.claude' / 'projects')
  parser.add_argument('--provider', choices=('codex', 'claude', 'both'),
                      default='both')
  args = parser.parse_args()
  if args.since >= args.until:
    parser.error('--since must precede --until')
  paths, missing = [], 0
  for provider, root in (('codex', args.codex_dir), ('claude', args.claude_dir)):
    if args.provider not in (provider, 'both'):
      continue
    if not root.is_dir():
      missing += 1
    paths.extend((provider, p) for p in sorted(root.rglob('*.jsonl')))
  report = audit(paths, args.since, args.until)
  if missing:
    report['diagnostics']['missing_source_roots'] = missing
    report['complete'] = False
  print(json.dumps(report, indent=2, sort_keys=True))
  return 0 if report['complete'] else 1
