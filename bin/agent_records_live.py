"""Read-only task snapshots, session changes and terminal-independent view."""
from collections import Counter, deque
from datetime import datetime, timedelta
import unicodedata

from agent_records_core import RecordsError, comma, parse_time, record_locks
from agent_records_tasks import (
  CLOSED, LIVE, checklist, dependency_ids, live_owner, log_lines, read_estimates,
  read_task, task_available, task_files,
)


def timestamp(value):
  return parse_time(value.split(' by ', 1)[0]) if value else None


def safe(value):
  return ''.join(c for c in str(value)
                 if not unicodedata.category(c).startswith('C'))


def cells(text):
  return sum(0 if unicodedata.combining(c) else
             2 if unicodedata.east_asian_width(c) in ('W', 'F') else 1
             for c in text)


def clip(text, width):
  text = safe(text)
  if cells(text) <= width:
    return text
  out, used = '', 0
  for c in text:
    size = cells(c)
    if used + size > max(0, width - 1):
      break
    out += c
    used += size
  return out + ('…' if width else '')


def wrap(text, width):
  text = safe(text)
  if width < 1:
    return ['']
  out = []
  while cells(text) > width:
    used, end = 0, 0
    for char in text:
      if used + cells(char) > width:
        break
      used += cells(char)
      end += 1
    if not end:
      out.append('?')
      text = text[1:]
      continue
    space = text.rfind(' ', 0, end + 1)
    if space > 0:
      out.append(text[:space])
      text = text[space + 1:]
    else:
      out.append(text[:end])
      text = text[end:]
  return out + [text]


def age(when, now):
  if when is None:
    return 'unknown'
  seconds = max(0, int((now - when).total_seconds()))
  if seconds < 60:
    return f'{seconds}s'
  if seconds < 3600:
    return f'{seconds // 60}m'
  if seconds < 86400:
    return f'{seconds // 3600}h'
  return f'{seconds // 86400}d'


def updated_label(when, now):
  if when is None:
    return 'unknown'
  local = when.astimezone()
  return local.strftime('%H:%M' if local.date() == now.astimezone().date()
                        else '%Y-%m-%d')


def last_update(row):
  logs = log_lines(row['body'])
  if logs:
    # Log entries begin with the fixed records timestamp, then the actor.
    try:
      return parse_time(logs[-1][2:27])
    except RecordsError:
      pass
  return timestamp(row['fields'].get('created', ''))


def read_snapshot(root, changes, wait):
  result = {}
  with record_locks(root, changes, wait=wait):
    for path in task_files(root, True):
      fields, _, body = read_task(path)
      ident = fields['id']
      if ident in result:
        raise RecordsError('duplicate task ID: ' + ident, 5)
      if fields.get('status') not in LIVE + CLOSED:
        raise RecordsError('invalid task status: ' + ident, 5)
      for key in ('title', 'owner', 'created'):
        if not fields.get(key):
          raise RecordsError('missing ' + key + ': ' + ident, 5)
      for key in ('created', 'closed', 'expires'):
        timestamp(fields.get(key, ''))
      dependency_ids(fields.get('depends-on', ''))
      read_estimates(fields.get('estimates', '{}'))
      log_lines(body)
      checklist(body)
      result[ident] = {'fields': fields, 'body': body}
  return result


def unmet(rows, ident):
  return [dep for dep in dependency_ids(
    rows[ident]['fields'].get('depends-on', ''))
    if rows.get(dep, {}).get('fields', {}).get('status') != 'done']


def downstream(rows, ident):
  reverse = {}
  for key, row in rows.items():
    if row['fields']['status'] in LIVE:
      for dep in dependency_ids(row['fields'].get('depends-on', '')):
        reverse.setdefault(dep, set()).add(key)
  found, pending = set(), list(reverse.get(ident, ()))
  while pending:
    key = pending.pop()
    if key != ident and key not in found:
      found.add(key)
      pending.extend(reverse.get(key, ()))
  return found


def estimate_cells(fields):
  selected = read_estimates(fields.get('estimates', '{}')).get(
    fields.get('execution-model'), {})
  tokens, seconds = selected.get('tokens'), selected.get('wall-seconds')
  token_text = '—' if tokens is None else str(tokens)
  if tokens is not None and tokens >= 1000:
    divisor, suffix = (1000000, 'M') if tokens >= 1000000 else (1000, 'k')
    token_text = f'{tokens / divisor:.3g}' + suffix
  return (fields.get('storypoints') or '—', token_text,
          estimate_duration(seconds) if seconds is not None else '—')


def estimate_duration(seconds):
  minutes = round(seconds / 60)
  if 0 < seconds < 60:
    return '<1m'
  return (f'{minutes}m' if minutes < 60 else
          f'{minutes // 60}h' + (f'{minutes % 60}m' if minutes % 60 else ''))


def estimate_label(fields):
  if fields.get('blocked-on-owner', '').startswith('yes'):
    return 'owner hold · ETA unknown'
  estimates = read_estimates(fields.get('estimates', '{}'))
  selected = estimates.get(fields.get('execution-model'), {})
  seconds = selected.get('wall-seconds')
  if seconds is not None:
    duration = estimate_duration(seconds)
    return f'est. {duration} · ETA unknown'
  return 'ETA unknown'


class LiveState:
  def __init__(self):
    self.rows = {}
    self.started = None
    self.verified = None
    self.error = ''
    self.events = deque(maxlen=500)
    self.counts = Counter()
    self.highlight = {}
    self.badges = {}
    self.departed = {}

  def update(self, rows, now):
    if self.started is None:
      self.started = now
    else:
      for ident, row in rows.items():
        previous = self.rows.get(ident)
        kind = None
        if previous is None:
          created = timestamp(row['fields'].get('created', ''))
          kind = 'NEW' if created and created >= self.started else 'APPEARED'
        elif previous != row:
          old, new = previous['fields']['status'], row['fields']['status']
          before, after = log_lines(previous['body']), log_lines(row['body'])
          if old != new and new in CLOSED:
            kind = new.upper()
          elif old in CLOSED and new in LIVE:
            kind = 'REOPENED'
          elif after[:len(before)] != before:
            kind = 'REWRITTEN'
          else:
            kind = 'UPDATED'
        if kind:
          self.add_event(now, kind, ident, row['fields']['title'])
          if previous is None and kind == 'NEW' and (
              row['fields']['status'] in CLOSED):
            self.add_event(now, row['fields']['status'].upper(), ident,
                           row['fields']['title'])
        if (previous and row['fields']['status'] in LIVE and
            unmet(self.rows, ident) and not unmet(rows, ident) and
            row['fields'].get('depends-on') ==
            previous['fields'].get('depends-on')):
          self.add_event(now, 'UNBLOCKED', ident, row['fields']['title'])
      for ident in self.rows.keys() - rows.keys():
        self.departed[ident] = self.rows[ident]
        self.add_event(now, 'REMOVED', ident, self.rows[ident]['fields']['title'])
    self.rows, self.verified, self.error = rows, now, ''

  def add_event(self, now, kind, ident, title):
    self.events.append((now, kind, ident, title))
    self.counts[kind] += 1
    self.highlight[ident] = now
    if kind != 'UPDATED':
      self.badges[ident] = (now, kind)


class LiveView:
  def __init__(self, state, args=None):
    self.state, self.args = state, args
    self.selected = None
    self.filter, self.editor, self.mode, self.pending = '', '', 'normal', ''
    self.cursor = 0
    self.expanded = False
    self.full_details = False
    self.section = 0
    self.detail_offset = 0
    self.first = 0
    self.wait_observations = None
    self.sync()

  def scoped(self, row):
    f, args = row['fields'], self.args
    if args is None:
      return True
    repos = args.repo or []
    record_repos = comma(f.get('repos', ''))
    return (not repos or any(r in record_repos or
            (r == 'none' and not record_repos) for r in repos)) and (
      not args.status or f['status'] == args.status) and (
      not args.owner or f['owner'] == args.owner) and (
      not args.unowned or not live_owner(f))

  def visible(self):
    result = []
    query = (self.editor if self.mode == 'filter' else self.filter).casefold()
    for ident, row in self.state.rows.items():
      f = row['fields']
      if not self.scoped(row):
        continue
      closed = timestamp(f.get('closed', ''))
      recent = closed and timedelta(0) <= (
        self.state.verified - closed) <= timedelta(hours=1)
      if self.args and self.args.archived:
        if f['status'] not in CLOSED:
          continue
      elif not (self.args and self.args.all):
        if f['status'] not in LIVE and not recent:
          continue
      searchable = ' '.join(f.get(k, '') for k in
                            ('id', 'title', 'repos', 'owner', 'status'))
      if query in searchable.casefold():
        result.append(ident)
    def order(ident):
      f = self.state.rows[ident]['fields']
      state = ('in-progress', 'in-review', 'open', 'blocked', *CLOSED)
      priority = ('P0', 'P1', 'P2', 'P3', 'unset')
      return (state.index(f['status']),
              priority.index(f.get('priority', 'unset'))
              if f.get('priority', 'unset') in priority else 4, ident)
    return sorted(result, key=order)

  def sync(self):
    ids = self.visible()
    if self.selected not in ids:
      self.selected = ids[min(self.first, len(ids) - 1)] if ids else None
      self.detail_offset = 0
    return ids

  def key(self, key, page):
    if key == '\x03':
      return True
    if self.mode == 'filter':
      if key in ('\n', '\r'):
        self.filter, self.mode = self.editor, 'normal'
      elif key == '\x1b':
        self.filter, self.mode = '', 'normal'
      elif key in ('\x7f', '\b', 'BACKSPACE'):
        if self.cursor:
          self.editor = self.editor[:self.cursor - 1] + self.editor[self.cursor:]
          self.cursor -= 1
      elif key == '\x15':
        self.editor, self.cursor = '', 0
      elif key == '\x17':
        prefix = self.editor[:self.cursor].rstrip()
        cut = prefix.rfind(' ') + 1
        self.editor = self.editor[:cut] + self.editor[self.cursor:]
        self.cursor = cut
      elif key in ('LEFT', 'RIGHT', 'HOME', 'END', '\x01', '\x05'):
        if key == 'LEFT':
          self.cursor = max(0, self.cursor - 1)
        elif key == 'RIGHT':
          self.cursor = min(len(self.editor), self.cursor + 1)
        else:
          self.cursor = 0 if key in ('HOME', '\x01') else len(self.editor)
      elif len(key) == 1 and key.isprintable():
        self.editor = self.editor[:self.cursor] + key + self.editor[self.cursor:]
        self.cursor += 1
      self.sync()
      return False
    seq, self.pending = self.pending + key, ''
    if key == 'q' or seq == 'ZZ':
      return True
    if self.mode == 'help':
      if key in ('?', '\x1b'):
        self.mode = 'normal'
      elif key == 'Z':
        self.pending = key
      return False
    ids = self.sync()
    index = ids.index(self.selected) if self.selected else 0
    target = index
    if seq == 'gg':
      target = 0
    elif key in ('g', 'Z'):
      self.pending = key
    elif key == 'G':
      target = len(ids) - 1
    elif key in ('j', 'DOWN', 'k', 'UP', '\x04', '\x15'):
      step = max(1, page // 2) if key in ('\x04', '\x15') else 1
      target += step if key in ('j', 'DOWN', '\x04') else -step
    elif key == '/':
      self.mode, self.editor = 'filter', self.filter
      self.cursor = len(self.editor)
    elif key == '?':
      self.mode = 'help'
    elif key == 'f':
      self.full_details = not self.full_details
      self.expanded = True
      self.detail_offset = 0
    elif key == '\r':
      self.full_details = False
      self.expanded = not self.expanded
      self.detail_offset = 0
    elif key == '\t':
      self.expanded = True
      self.section = (self.section + 1) % 4
      self.detail_offset = 0
    elif key in ('[', ']', '\n', '\x0b'):
      self.detail_offset = max(0, self.detail_offset +
                               (1 if key in (']', '\n') else -1))
    elif key == '\x1b':
      self.expanded = False
      self.full_details = False
    if ids:
      selected = ids[max(0, min(len(ids) - 1, target))]
      if selected != self.selected:
        self.detail_offset = 0
      self.selected = selected
    return False

  def detail(self, now):
    if not self.selected:
      return []
    row = self.state.rows[self.selected]
    f, body = row['fields'], row['body']
    title = ('Progress', 'Dependencies', 'Evidence', 'Observed waits')[self.section]
    updated = last_update(row)
    stamp = (updated.astimezone().strftime('%Y-%m-%d %H:%M:%S %Z')
             if updated else 'unknown')
    lines = [title + ' · ' + self.selected + ' · Tab section, [/] scroll',
             'Updated: ' + stamp]
    if self.section == 0:
      lines += [f['title'], 'Owner: ' + f['owner'],
                'Priority: ' + f.get('priority', 'unset') + ' · points: ' +
                (f.get('storypoints') or 'unknown'),
                'Helpers: ' + (f.get('helpers') or 'none'),
                'Model: ' + (f.get('execution-model') or
                            f.get('estimate-model-unknown') or 'unknown'),
                'Claim expires: ' + (f.get('expires') or 'none'),
                'Estimate: ' + estimate_label(f),
                'Owner hold: ' + f.get('blocked-on-owner', 'no')]
      selected = read_estimates(f.get('estimates', '{}')).get(
        f.get('execution-model'), {})
      if selected.get('wall-seconds-unknown'):
        lines.append('Time unknown: ' + selected['wall-seconds-unknown'])
      logs = log_lines(body)
      lines += ['Last record: ' + (logs[-1][2:] if logs else 'none')]
    elif self.section == 1:
      def describe(ident):
        other = self.state.rows.get(ident, {}).get('fields', {})
        return ident + ' [' + other.get('status', 'missing') + '] ' + (
          other.get('title', ''))
      deps = dependency_ids(f.get('depends-on', ''))
      dependents = [ident for ident, row in self.state.rows.items()
                    if self.selected in dependency_ids(
                      row['fields'].get('depends-on', ''))]
      lines += ['Prerequisites:'] + ([describe(i) for i in deps] or ['none'])
      lines += ['Dependents:'] + ([describe(i) for i in dependents] or ['none'])
      lines += [f'{len(downstream(self.state.rows, self.selected))} live '
                'downstream tasks (not all necessarily blocked)']
    elif self.section == 2:
      checks = checklist(body)[1]
      done = sum(line.startswith('- [x]') for line in checks)
      lines += [f'{done}/{len(checks)} completion checks recorded',
                'Review: ' + f.get('review', 'n/a'),
                'Links: ' + (f.get('links') or 'none')] + checks
    else:
      from agent_activity import cached_task_waits
      cache_key = (self.selected, self.state.verified)
      if not self.wait_observations or self.wait_observations[0] != cache_key:
        self.wait_observations = (cache_key, cached_task_waits(
          self.selected, now.timestamp(),
          getattr(self.args, 'agent_cache_file', None)))
      label, waits = self.wait_observations[1]
      lines += [label]
      for wait in waits:
        lines.append(wait['agent'] + ' [' + wait['state'] + '] until ' +
                     wait['condition'] + ' · observed ' +
                     age(datetime.fromtimestamp(wait['at'], now.tzinfo), now)
                     + ' ago')
      if not waits:
        lines += ['No current waits observed; this is not a watcher count.']
    return lines

  def frame(self, width, height, now):
    width, height = max(0, width - 1), max(1, height)
    ids = self.sync()
    if self.mode == 'help':
      help_lines = [
        'TASKS · read-only controls',
        'j/k or arrows: select · gg/G: first/last',
        'Ctrl-d/u: half page · /: filter',
        'Filter: Enter keeps; Esc clears; arrows/Home/End edit',
        'Ctrl-a/e: start/end; Ctrl-w: word; Ctrl-u: clear',
        'Enter: details · Tab: progress/dependencies/evidence/waits',
        'f: full-screen details · [/], Ctrl-j/k: scroll · Esc: close',
        '? or Esc: close help · q/ZZ/Ctrl-C: quit',
        'Selection follows ID; selected titles wrap.',
        'Bold: changed in 10s · NEW badge: 5m · recent closed: 1h',
        'Expired claim is not proof of stopped work.',
        'Estimates are per model; ETA remains unknown.',
        'Counts follow CLI scope; / only filters the list.',
        'No editing, recovery, syncing or model calls.',
      ]
      return [(clip(line, width), '') for line in help_lines[:height]]
    if self.full_details:
      detail = [part for line in self.detail(now) for part in wrap(line, width)]
      slots = max(0, height - 2)
      self.detail_offset = min(self.detail_offset, max(0, len(detail) - slots))
      lines = [('DETAILS · full-screen · ' + str(self.selected or ''), 'bold')]
      lines += [(t, '') for t in detail[self.detail_offset:self.detail_offset + slots]]
      lines += [('Tab section · [/], Ctrl-j/k scroll · f split · Esc close', '')]
      return [(clip(t, width), style) for t, style in lines[:height]]
    rows = [row for row in self.state.rows.values() if self.scoped(row)]
    live = [r for r in rows if r['fields']['status'] in LIVE]
    states = Counter(r['fields']['status'] for r in live)
    fields = {i: r['fields'] for i, r in self.state.rows.items()}
    expired = sum(r['fields'].get('owner', 'none') != 'none' and
                  bool(r['fields'].get('expires')) and
                  timestamp(r['fields']['expires']) <= now for r in live)
    holds = sum(r['fields'].get('blocked-on-owner', '').startswith('yes')
                for r in live)
    ready = sum(not unmet(self.state.rows, r['fields']['id']) and
                task_available(fields, r['fields']) for r in live)
    scope = ','.join(self.args.repo or []) if self.args else ''
    header = f'TASKS · {scope or "all repositories"} · read-only'
    status = ('STALE · last verified ' + (
      self.state.verified.astimezone().strftime('%H:%M:%S')
      if self.state.verified else 'never') + ' · ' + self.state.error
      if self.state.error else 'Verified ' +
      self.state.verified.astimezone().strftime('%H:%M:%S'))
    recent = [r for r in rows if r['fields']['status'] == 'done' and
              timestamp(r['fields'].get('closed', '')) and
              timedelta(0) <= now - timestamp(r['fields']['closed']) <=
              timedelta(hours=1)]
    estimated = sum('wall-seconds' in read_estimates(
      r['fields'].get('estimates', '{}')).get(
        r['fields'].get('execution-model'), {}) for r in live)
    created = sum(timedelta(0) <= now - timestamp(r['fields']['created']) <=
                  timedelta(hours=1) for r in rows)
    lines = [(header, 'bold'), (status, 'bold' if self.state.error else ''),
      (f'{states["in-progress"]} working · {states["in-review"]} review · '
       f'{states["open"]} open · {states["blocked"]} blocked', ''),
      (f'{expired} expired claims · {holds} owner holds · {ready} next-eligible', ''),
      (f'1h: +{created} new / {len(recent)} done · session(all) '
       f'+{self.state.counts["NEW"]} new / '
       f'{self.state.counts["DONE"]} done / '
       f'{self.state.counts["REOPENED"]} reopened', '')]
    if height >= 20:
      lines.append((f'{estimated}/{len(live)} selected-model time estimates '
                    '· finish ETAs unknown', ''))
    detail = []
    if self.expanded:
      for line in self.detail(now):
        detail.extend(wrap(line, width))
    detail_slots = min(max(0, height // 2), len(detail))
    event_rows = self.state.departed | self.state.rows
    events = [e for e in self.state.events if e[1] != 'UPDATED' and
              e[2] in event_rows and self.scoped(event_rows[e[2]])
              and now - e[0] < timedelta(hours=1)]
    recent_lines = [f'{kind} {ident} · {age(at, now)} ago · {title}'
                    for at, kind, ident, title in events[-2:]]
    if not events:
      recent_lines = ['DONE ' + r['fields']['id'] + ' · ' + r['fields']['title']
                      for r in sorted(recent, key=lambda r:
                        timestamp(r['fields']['closed']))[-2:]]
    recent_slots = min(len(recent_lines), max(0, height - 12))
    column = width >= 100
    row_width = width - 12 if column else width
    if column:
      heading = f'  {"Task":7} {"SP":>2} {"Est tokens":>10} {"Est time":>8}  Task / state'
      lines.append((heading.ljust(row_width + 2) + 'Updated', 'bold'))
    available = max(0, height - len(lines) - detail_slots - recent_slots - 1)
    def describe(ident):
      row = self.state.rows[ident]
      f = row['fields']
      badge = self.state.badges.get(ident)
      marker = badge[1] + ' ' if badge and (
        now - badge[0] < timedelta(minutes=5)) else ''
      deps = unmet(self.state.rows, ident)
      info = 'needs ' + ','.join(deps) if deps else estimate_label(f)
      if f['status'] in CLOSED:
        info = 'closed ' + age(timestamp(f.get('closed')), now) + ' ago'
      flags = []
      if f.get('owner', 'none') != 'none' and f.get('expires') and (
          timestamp(f['expires']) <= now):
        flags.append('EXPIRED')
      if f.get('blocked-on-owner', '').startswith('yes'):
        flags.append('HOLD')
      if deps:
        flags.append('WAIT')
      flags = (' ' + '/'.join(flags)) if flags else ''
      prefix = ident
      if column:
        points, tokens, duration = estimate_cells(f)
        prefix = f'{ident:7} {points:>2} {tokens:>10} {duration:>8} '
      text = f'{prefix} {marker}[{f["status"]}{flags}] {f["title"]}'
      return text, info, f, bool(flags)

    if ids and available:
      index = ids.index(self.selected)
      self.first = min(self.first, index)
      selected_text = describe(self.selected)[0]
      selected_height = min(available, min(3, len(wrap(
        selected_text, max(1, row_width - 2)))) + 2)
      self.first = max(self.first, index - available + selected_height)
      used = 0
      for ident in ids[self.first:]:
        if used >= available:
          break
        text, info, f, warning = describe(ident)
        selected = ident == self.selected
        if selected:
          wrapped = wrap(text, max(1, row_width - 2))
          row_lines = [('> ' if n == 0 else '  ') + line
                       for n, line in enumerate(wrapped[
                         :max(1, min(3, available - used))])]
          if len(wrapped) > len(row_lines):
            row_lines[-1] = clip(row_lines[-1] + '…', width)
          if used + len(row_lines) < available:
            row_lines.append(clip('  ' + info, width))
          if used + len(row_lines) < available:
            updated = last_update(self.state.rows[ident])
            stamp = updated.strftime('%Y-%m-%d %H:%M %Z') if updated else 'unknown'
            row_lines.append(clip('  Updated ' + stamp + ' · ' +
              age(updated, now) + ' ago · ' + (f.get('repos') or 'no repo') +
              ' · ' + f.get('priority', 'unset') + ' · ' + f['owner'], width))
        else:
          row_lines = [clip(('  ' if column else '') + text, row_width)]
        if column:
          label = clip(row_lines[0], row_width)
          row_lines[0] = label + ' ' * (row_width + 2 - cells(label)) + \
            updated_label(last_update(self.state.rows[ident]), now)
        changed = self.state.highlight.get(ident)
        tone = 'warning' if warning else f['status']
        style = tone + (' selected' if selected else ' bold' if changed and (
          now - changed < timedelta(seconds=10)) else '')
        lines.extend((line, style) for line in row_lines)
        used += len(row_lines)
    elif available:
      lines.append(('No matching tasks', ''))
    if detail_slots:
      self.detail_offset = min(self.detail_offset,
                               max(0, len(detail) - detail_slots))
      lines.extend((line, '') for line in detail[
        self.detail_offset:self.detail_offset + detail_slots])
    lines.extend((line, 'bold') for line in recent_lines[-recent_slots:]
                 if recent_slots)
    footer = f'{len(ids)} tasks · j/k move / filter Enter details f full ? help q quit'
    if self.mode == 'filter':
      footer = '/' + self.editor[:self.cursor] + '│' + self.editor[self.cursor:]
    elif self.filter:
      footer = '/' + self.filter + ' · ' + footer
    # Keep errors visible even in a very short pane.
    if height < 4 and self.state.error:
      lines = [(status, 'bold')]
      if height == 1:
        return [(clip(status, width), 'bold')]
    lines = lines[:max(0, height - 1)] + [(footer, '')]
    return [(clip(text, width), style) for text, style in lines]
