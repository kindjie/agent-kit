"""Read-only batches with resumable per-task log and header cursors."""
import hashlib
import json
import re
import time

from agent_records_core import RecordsError, duration, record_locks
from agent_records_tasks import addressed, cursor, log_lines, read_task, task_path


def snapshot(root, tasks):
  result = {}
  for ident in tasks:
    fields, _, body = read_task(task_path(root, ident))
    lines = log_lines(body)
    header = hashlib.sha256(json.dumps(fields, sort_keys=True).encode()).hexdigest()
    result[ident] = (lines, fields, {'log': cursor(lines), 'header': header})
  return result


def events(root, changes, args):
  if args.unlocked:
    raise RecordsError('events requires verified reads', 2)
  tasks = sorted(set(args.tasks))
  timeout = duration(args.timeout, 'timeout')
  interval = duration(args.interval, 'interval')
  if interval < 1:
    raise RecordsError('interval must be at least 1 second', 2)
  previous = None
  if args.after is not None:
    try:
      previous = json.loads(args.after)
      if not isinstance(previous, dict) or set(previous) != set(tasks):
        raise ValueError()
      for item in previous.values():
        if (not isinstance(item, dict) or set(item) != {'log', 'header'} or
            not isinstance(item['log'], str) or
            not re.fullmatch(r'\d+:[0-9a-f]{16}', item['log']) or
            not isinstance(item['header'], str) or
            not re.fullmatch(r'[0-9a-f]{64}', item['header'])):
          raise ValueError()
    except (ValueError, TypeError):
      raise RecordsError('invalid cursor or different task set', 2)
  deadline = time.monotonic() + timeout
  while True:
    batch, rewritten, current = [], [], {}
    with record_locks(root, changes, wait=args.wait):
      for ident, (lines, fields, mark) in snapshot(root, tasks).items():
        current[ident] = mark
        if previous is None:
          continue
        old = previous[ident]
        count = int(old['log'].split(':', 1)[0])
        if cursor(lines[:count]) != old['log']:
          rewritten.append(ident)
          continue
        new = [line for line in lines[count:]
               if not args.for_id or addressed(line, args.for_id)]
        if new or mark['header'] != old['header']:
          batch.append({'task': ident, 'log': new,
                        'fields': fields if mark['header'] != old['header']
                        else {}})
    expired = time.monotonic() >= deadline
    if previous is None or batch or rewritten or expired:
      print(json.dumps({'schema_version': 1, 'cursor': current,
                        'events': batch, 'rewritten': rewritten}, sort_keys=True))
      return 5 if rewritten else (4 if expired and not batch else 0)
    previous = current
    time.sleep(min(interval, max(0, deadline - time.monotonic())))
