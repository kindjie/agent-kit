"""Offline observed usage ledger. No network, model calls, or transcript text output."""
import argparse
from collections import Counter
from datetime import datetime
import hashlib
import json
from pathlib import Path
import sys
import time

KEYS = ('input_tokens', 'cached_input_tokens', 'cache_write_input_tokens',
        'output_tokens', 'total_tokens')


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
          relevant = (kind in ('token_usage_record', 'assistant',
                               'response_item', 'event_msg'))
          if not relevant:
            continue
          at = timestamp(event.get('timestamp'))
          if since <= at < until and (kind == 'assistant' or
                                      payload.get('role') == 'assistant'):
            window_activity = True
          if start is not None and at < start:
            continue  # Inherited Codex history belongs to its original thread.
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
              if (isinstance(begin, (int, float)) and
                  isinstance(end, (int, float)) and end >= begin):
                compactions[(thread, begin, end)] = (at, (end - begin) / 1000)
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
  return {'schema_version': 1, 'since': since, 'until': until,
          'complete': not any(diagnostics[k] for k in fatal),
          'files_scanned': files, 'duplicate_records': duplicate,
          'diagnostics': dict(diagnostics), 'totals': dict(totals),
          'groups': sorted(groups.values(), key=lambda r: -r['total_tokens']),
          'tool_calls': dict(Counter(name for at, name in tools.values()
                                     if since <= at < until)),
          'compaction_agent_seconds': sum(seconds for at, seconds in
                                          compactions.values()
                                          if since <= at < until),
          'limitations': [
            'Observed local activity, not billing or productivity.',
            'Internal guardian groups are included in totals; separate by internal.',
            'Tool counts indicate activity, not causal overhead token attribution.',
            'Compaction seconds sum across agents and can overlap wall time.',
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
