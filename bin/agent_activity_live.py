"""Interactive agent tree. UI choices are process-local, never task state."""
from datetime import datetime, timezone
import math
import queue
import signal
import sys
import threading
import time
from pathlib import Path

from agent_activity import AgentTree, activity_age, activity_label, activity_status
from agent_records_live import cells, clip, wrap, help_frame, help_scroll


def elapsed(at, now):
  if (isinstance(at, bool) or not isinstance(at, (float, int)) or
      not math.isfinite(at)):
    return 'unknown'
  return activity_age(max(0, now - at))


def wait_label(wait):
  if wait['kind'] == 'task':
    return 'task ' + ', '.join(wait['tasks']) + ' until ' + wait['condition']
  return ('subagent ' + ', '.join(wait['targets']) if wait['targets'] else
          'subagent update (target unknown)')


class AgentView:
  def __init__(self):
    self.tree = AgentTree([], 0)
    self.selected = None
    self.collapsed = {}
    self.first = 0
    self.details = False
    self.offset = 0
    self.help_offset = 0
    self.mode, self.filter, self.editor, self.pending = 'normal', '', '', ''
    self.error = ''
    self.refreshed = None
    self.labels = {}
    self.mouse_rows, self.mouse_details = {}, (0, 0)

  def update(self, agents, now, incomplete=False):
    old = self.tree
    self.tree = AgentTree(agents, now, incomplete)
    identifiers = [a['id'] for a in agents]
    suffix = 8
    while (suffix < max(map(len, identifiers), default=0) and
           len({i[-suffix:] for i in identifiers}) < len(set(identifiers))):
      suffix += 1
    self.labels = {}
    for key, a in self.tree.rows.items():
      name = a.get('label')
      duplicate = sum(other.get('label') == name for other in agents) > 1
      if not name or name == a['id'] or duplicate:
        name = a['provider'][:2] + ':' + a['id'][-suffix:]
      self.labels[key] = name
    for key in self.tree.rows:
      if key not in self.collapsed:
        self.collapsed[key] = True
    if self.selected not in self.tree.rows:
      self.selected = old.parents.get(self.selected)
    self.sync()
    self.refreshed, self.error = now, ''

  def visible(self):
    query = (self.editor if self.mode == 'filter' else self.filter).casefold()
    if query:
      keep = set()
      for key, a in self.tree.rows.items():
        text = ' '.join(str(a.get(k) or '') for k in
                        ('id', 'label', 'session_title', 'work', 'cwd', 'model'))
        if query in text.casefold():
          keep.add(key)
          parent = self.tree.parents.get(key)
          while parent:
            keep.add(parent)
            parent = self.tree.parents.get(parent)
      return [k for k in self.tree.order if k in keep]
    shown = []
    for key in self.tree.order:
      parent = self.tree.parents.get(key)
      if parent and (parent not in shown or self.collapsed.get(parent)):
        continue
      shown.append(key)
    return shown

  def sync(self):
    shown = self.visible()
    while self.selected not in shown and self.selected in self.tree.parents:
      self.selected = self.tree.parents[self.selected]
    if self.selected not in shown:
      self.selected = shown[0] if shown else None
    return shown

  def key(self, key, page):
    if key == '\x03':
      return True
    if self.mode == 'filter':
      if key in ('\r', '\n'):
        self.filter, self.mode = self.editor, 'normal'
      elif key == '\x1b':
        self.filter, self.mode = '', 'normal'
      elif key in ('\b', '\x7f', 'BACKSPACE'):
        self.editor = self.editor[:-1]
      elif key == '\x15':
        self.editor = ''
      elif key == '\x17':
        prefix = self.editor.rstrip()
        self.editor = prefix[:prefix.rfind(' ') + 1]
      elif len(key) == 1 and key.isprintable():
        self.editor += key
      self.sync()
      return False
    seq, self.pending = self.pending + key, ''
    if key in ('q', 'Q') or seq == 'ZZ':
      return True
    if self.mode == 'help':
      if key in ('?', '\x1b'):
        self.mode = 'normal'
      else:
        self.help_offset = help_scroll(key, seq, self.help_offset, page)
        if key in ('g', 'Z'):
          self.pending = key
      return False
    shown = self.sync()
    index = shown.index(self.selected) if self.selected else 0
    target = index
    if seq == 'gg':
      target = 0
    elif key in ('g', 'Z'):
      self.pending = key
    elif key == 'G':
      target = len(shown) - 1
    elif key in ('j', 'DOWN', 'k', 'UP', '\x04', '\x15'):
      step = max(1, page // 2) if key in ('\x04', '\x15') else 1
      target += step if key in ('j', 'DOWN', '\x04') else -step
    elif key in ('LEFT', 'h') and self.selected:
      if self.tree.children[self.selected] and not self.collapsed[self.selected]:
        self.collapsed[self.selected] = True
      else:
        self.selected = self.tree.parents.get(self.selected, self.selected)
      return False
    elif key in ('RIGHT', 'l') and self.selected:
      children = self.tree.children[self.selected]
      if children and self.collapsed[self.selected]:
        self.collapsed[self.selected] = False
      elif children:
        self.selected = children[0]
      return False
    elif key == ' ' and self.selected:
      self.collapsed[self.selected] = not self.collapsed[self.selected]
    elif key in ('\n', '\r'):
      self.details, self.offset = not self.details, 0
    elif key in ('[', ']'):
      self.offset = max(0, self.offset + (1 if key == ']' else -1))
    elif key == '/':
      self.mode, self.editor = 'filter', self.filter
    elif key == '?':
      self.mode = 'help'
    elif key == '\x1b':
      self.details = False
    if shown:
      selected = shown[max(0, min(len(shown) - 1, target))]
      if selected != self.selected:
        self.offset = 0
      self.selected = selected
    self.sync()
    return False

  def detail(self, now):
    if not self.selected:
      return []
    a = self.tree.rows[self.selected]
    obs = self.tree.observed[self.selected]
    stamp = 'unknown'
    if obs['age'] is not None:
      try:
        stamp = datetime.fromtimestamp(obs['at'], timezone.utc).isoformat()
      except (OverflowError, OSError, ValueError):
        pass
    def field(label, value):
      parts = str(value).splitlines() or ['unknown']
      return ['  ' + (label + ':').ljust(12) + parts[0]] + [
        ' ' * 14 + part for part in parts[1:]]

    def number(value):
      return f'{value:,}' if isinstance(value, (int, float)) else 'unknown'

    lines = ['Tasks']
    tasks = a.get('tasks') or []
    for task in tasks:
      lines.append('  ' + str(task.get('id') or '?') + ' · ' +
                   str(task.get('status') or 'unknown') + ' · ' +
                   str(task.get('role') or 'associated'))
      lines.append('    ' + str(task.get('title') or 'Untitled task'))
    if not tasks:
      lines.append('  ' + (a.get('tasks_status') or
                           'No matching owner/helper tasks found'))
    lines += ['', 'Activity']
    lines += field('Status', activity_label(obs, now))
    lines += field('Work', a.get('work') or 'unknown')
    lines += field('Current', a.get('now') or 'unknown')
    if self.tree.children[self.selected]:
      lines += field('Subagents', self.tree.summary(self.selected))
    lines += ['', 'Session']
    lines += field('Title', a.get('session_title') or 'not recorded')
    lines += field('Agent', a['key'])
    lines += field('Model', str(a.get('model') or 'unknown') + ' · ' +
                   str(a.get('effort') or 'unknown'))
    lines += field('Directory', a.get('cwd') or 'unknown')
    lines += field('Tokens', number((a.get('tokens') or {}).get('total')) +
                   ' total · ' + number(a.get('recent_tokens')) +
                   ' uncached / 15m')
    lines += ['', 'Evidence']
    lines += field('Reason', obs['reason'])
    lines += field('Last event', (a.get('observation') or {}).get('phase') or
                   'unknown')
    lines += field('Observed', stamp + ' · ' + elapsed(obs['at'], now) + ' ago')
    for task in tasks:
      if task.get('records_id'):
        lines += field('Task match', str(task['id']) + ' via ' + task['records_id'])
    for reason in sorted(self.tree.coverage_reasons):
      lines += field('Discovery', reason)
    for call in (a.get('observation') or {}).get('pending') or []:
      lines += field('Tool', str(call.get('tool') or 'unknown') +
                     ' · ' + elapsed(call.get('at'), now) + ' ago')
    for wait in obs['waits']:
      timeout = wait.get('timeout')
      limit = (' · timeout ' + activity_age(timeout) if timeout is not None
               else ' · timeout unknown')
      lines += field('Wait', wait_label(wait) + ' · ' +
                     elapsed(wait.get('at'), now) + ' ago' + limit)
    lines += ['  Local observations; not proof of process liveness.']
    return lines

  def mouse(self, action, x, y, page):
    if self.mode != 'normal':
      return
    if action in ('up', 'down'):
      in_details = self.mouse_details[0] <= y < self.mouse_details[1]
      self.key(('[' if action == 'up' else ']') if in_details else
               ('k' if action == 'up' else 'j'), page)
    elif action == 'click' and y in self.mouse_rows:
      key, fold_x = self.mouse_rows[y]
      self.selected, self.offset = key, 0
      if x == fold_x and self.tree.children[key]:
        self.collapsed[key] = not self.collapsed[key]

  def frame(self, width, height, now):
    self.mouse_rows, self.mouse_details = {}, (0, 0)
    width, height = max(0, width - 1), max(1, height)
    self.tree.refresh(now)
    shown = self.sync()
    if self.mode == 'help':
      sections = [
        ('Navigation', [('j/k, Up/Down', 'Select agent'),
          ('gg / G', 'First / last agent'),
          ('Ctrl-d / Ctrl-u', 'Move half a page'),
          ('Wheel / click', 'Scroll / select agent')]),
        ('Agent tree', [('Space', 'Collapse or expand group'),
          ('Left / h', 'Collapse group or select parent'),
          ('Right / l', 'Expand group or select first child'),
          ('/', 'Filter; matching ancestors stay visible')]),
        ('Details', [('Enter', 'Open or close details'),
          ('[/]', 'Scroll details'), ('Esc', 'Close details or cancel filter')]),
        ('Status', [('Working', 'Activity, reasoning or a tool call observed'),
          ('Waiting', 'A foreground wait was observed'),
          ('Idle', 'Turn ended; not proof the agent awaits work'),
          ('Stopped', 'Turn was aborted'),
          ('Unknown', 'No usable status observation'),
          ('?', 'Status uncertain; age and details explain why')]),
        ('Reading the view', [('Groups', 'Summaries include collapsed descendants'),
          ('Refresh', 'Selection and collapse choices survive updates'),
          ('N+ agents', 'At least N discovered; details explain missing evidence'),
          ('Evidence', 'Local observations, not proof of process liveness')]),
      ]
      frame, self.help_offset = help_frame(
        'Agents', sections, width, height, self.help_offset)
      return frame
    title = ('STALE · last refresh ' + elapsed(self.refreshed, now) +
             ' ago · ' + self.error if self.error else
             'Agents · observed activity · refreshed ' +
             elapsed(self.refreshed, now) + ' ago')
    lines = [(title, 'bold')]
    details = []
    for line in self.detail(now):
      indent = len(line) - len(line.lstrip())
      if line.startswith('  ') and line[2:14].strip().endswith(':'):
        indent = 14
      indent = min(indent, max(0, width - 2))
      parts = wrap(line[indent:], max(1, width - indent))
      style = 'bold' if line in ('Tasks', 'Activity', 'Session', 'Evidence') else ''
      details.extend((line[:indent] + part if index == 0 else
                      ' ' * indent + part, style)
                     for index, part in enumerate(parts))
    detail_slots = min(len(details), height // 2) if self.details else 0
    slots = max(0, height - len(lines) - detail_slots - 1)
    if shown and slots:
      at = shown.index(self.selected)
      self.first = max(0, min(self.first, at))
      self.first = max(self.first, at - slots + 1)
      for key in shown[self.first:self.first + slots]:
        a, obs = self.tree.rows[key], self.tree.observed[key]
        chain, cursor = [], key
        while cursor in self.tree.parents:
          parent = self.tree.parents[cursor]
          siblings = [s for s in self.tree.children[parent] if s in shown]
          chain.append((cursor == siblings[-1]))
          cursor = parent
        chain.reverse()
        prefix = ''.join('  ' if last else '│ ' for last in chain[:-1])
        if chain:
          prefix += '└─' if chain[-1] else '├─'
        group = bool(self.tree.children[key])
        icon = ('▸' if self.collapsed.get(key) and not self.filter else '▾'
                ) if group else ' '
        state = activity_status(obs)
        counts = self.tree.totals(key)
        summary = self.tree.summary(key) if group else ''
        session_title = str(a.get('session_title') or a.get('work') or
                            self.labels[key])
        gutter = '> ' if key == self.selected else '  '
        label = gutter + prefix + icon + ' ' + session_title
        metadata = self.labels[key] + ' · ' + activity_label(obs, now)
        if summary:
          metadata += ' · ' + summary
        if width >= 40:
          title_width = max(18, width * 55 // 100)
          title_cell = clip(label, title_width)
          text = (title_cell + ' ' * (title_width - cells(title_cell)) +
                  ' │ ' + metadata)
        else:
          text = label + ' · ' + metadata
        style = 'selected' if key == self.selected else (
          'attention' if state == 'Idle' and counts['working'] else
          'dim' if state in ('Idle', 'Stopped') else
          'unknown' if state == 'Unknown' or obs.get('uncertain') else
          'waiting' if state == 'Waiting' else
          'working' if state == 'Working' else
          'bold' if group else '')
        self.mouse_rows[len(lines)] = (key, 2 + cells(prefix))
        lines.append((text, style))
    elif slots:
      lines.append(('No matching agents observed', ''))
    if detail_slots:
      self.mouse_details = (len(lines), len(lines) + detail_slots)
      self.offset = min(self.offset, max(0, len(details) - detail_slots))
      lines.extend(details[self.offset:self.offset + detail_slots])
    start = self.first + 1 if shown and slots else 0
    end = min(len(shown), self.first + slots) if start else 0
    groups = len(self.tree.rows) - len(self.tree.parents)
    more = '+' if self.tree.incomplete else ''
    footer = (f'Rows {start}-{end}/{len(shown)} · {groups} groups · '
              f'{len(self.tree.rows)}{more} agents · ? help · q quit')
    if self.mode == 'filter':
      footer = '/' + self.editor + ' · Enter keep, Esc clear'
    elif self.filter:
      footer = '/' + self.filter + ' · ' + footer
    if height == 1 and self.error:
      return [(clip(title, width), 'bold')]
    return [(clip(t, width), s) for t, s in
            lines[:height - 1] + [(footer, 'dim' if self.mode == 'normal' else '')]]


def run_agent_live(args, cache_path, quota, loader=None):
  import curses
  import termios
  from agent_records_live_terminal import enable_mouse, read_input, mouse_screen
  from agent_attention import pane, tmux, summary, update_badge, clear_attention
  target = pane()
  dashboard = bool(target and tmux(['show-options', '-pqv', '-t', target,
                                    '@agent_attention_dashboard']) == '1')
  module = quota.agents_module()
  interval = args.interval or quota.LIVE_INTERVAL
  if loader is None:
    def loader():
      now = time.time()
      document = quota.live_document(args, cache_path, now)
      frame = module.agent_frame(args, quota,
                                 Path(module.__file__).with_name('agent-quota'),
                                 now)
      return frame, document
  view = AgentView()
  results = queue.Queue(maxsize=1)
  handlers = {}
  saved = termios.tcgetattr(sys.stdin.fileno())

  def refresh():
    try:
      value = loader()
      results.put((value, None, summary() if dashboard else None))
    except Exception as exc:
      results.put((None, str(exc), None))

  def stop(signum, frame):
    raise KeyboardInterrupt

  def screen(stdscr):
    try:
      curses.curs_set(0)
    except curses.error:
      pass
    curses.set_escdelay(30)
    colors = {}
    if args.color_on and curses.has_colors():
      curses.start_color()
      try:
        curses.use_default_colors()
        for number, foreground in enumerate(
            (curses.COLOR_YELLOW, curses.COLOR_CYAN, curses.COLOR_GREEN), 1):
          curses.init_pair(number, foreground, -1)
        colors = {'attention': curses.color_pair(1),
                  'unknown': curses.color_pair(1),
                  'waiting': curses.color_pair(2),
                  'working': curses.color_pair(3)}
      except curses.error:
        pass
    stdscr.keypad(True)
    enable_mouse(curses)
    stdscr.timeout(100)
    pending, deadline, last_summaries = False, 0, 0
    previous_states, sent = {}, set()
    previous, header, attention_badge = None, [], None
    mapping = {curses.KEY_UP: 'UP', curses.KEY_DOWN: 'DOWN',
      curses.KEY_LEFT: 'LEFT', curses.KEY_RIGHT: 'RIGHT',
      curses.KEY_BACKSPACE: 'BACKSPACE', curses.KEY_ENTER: '\n'}
    while True:
      now = time.time()
      if not pending and now >= deadline:
        threading.Thread(target=refresh, daemon=True).start()
        pending = True
      try:
        value, error, badge = results.get_nowait()
      except queue.Empty:
        pass
      else:
        pending, deadline = False, now + interval
        attention_badge = update_badge(attention_badge, badge)
        if error:
          view.error = error
        else:
          frame, document = value
          agents, cache = frame['agents'], frame['cache']
          if not args.verbose:
            agents = module.group_internal(agents)
          view.update(agents, now, bool(cache.get('scan_truncated') or
                                       cache.get('scan_errors')))
          header = module.live_header(document, agents, quota, now, False)
          if args.notify and previous_states:
            for alert in module.agent_alerts(previous_states, agents, now, sent):
              quota.notify(alert)
          previous_states = {a['key']: a.get('state') for a in agents}
          if frame['command'] and now - last_summaries >= 300:
            module.start_summaries(frame['command'])
            last_summaries = now
      height, width = stdscr.getmaxyx()
      # Keep the provider header short enough to leave navigation usable.
      top = header[:min(2, max(0, height - 8))]
      frame_lines = view.frame(width, max(1, height - len(top)), now)
      lines = frame_lines[:1]
      lines += [(clip(t, max(0, width - 1)), '') for t in top]
      lines += frame_lines[1:]
      if (lines, height, width) != previous:
        stdscr.erase()
        for y, (text, style) in enumerate(lines):
          attr = {'selected': curses.A_REVERSE, 'bold': curses.A_BOLD,
                  'attention': curses.A_BOLD, 'dim': curses.A_DIM}.get(
                    style, curses.A_NORMAL)
          attr |= colors.get(style, 0)
          try:
            stdscr.addstr(y, 0, text, attr)
          except curses.error:
            pass
        stdscr.refresh()
        previous = (lines, height, width)
      try:
        key, event = read_input(stdscr, curses)
      except curses.error:
        continue
      if event:
        action, x, y = event
        local_y = y - len(top) if y > len(top) else 0 if y == 0 else -1
        if local_y >= 0:
          view.mouse(action, x, local_y, max(1, height - len(top) - 3))
      if key is None:
        continue
      key = mapping.get(key, '') if isinstance(key, int) else key
      if view.key(key, max(1, height - len(top) - 3)):
        return 0

  try:
    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
      handlers[sig] = signal.signal(sig, stop)
    return mouse_screen(curses, screen)
  except KeyboardInterrupt:
    return 0
  finally:
    if dashboard:
      clear_attention()
    termios.tcsetattr(sys.stdin.fileno(), termios.TCSANOW, saved)
    for sig, handler in handlers.items():
      signal.signal(sig, handler)
