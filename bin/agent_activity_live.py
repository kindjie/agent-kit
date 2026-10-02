"""Interactive agent tree. UI choices are process-local, never task state."""
import queue
import signal
import sys
import threading
import time
from pathlib import Path

from agent_activity import AgentTree
from agent_records_live import cells, clip, wrap


def elapsed(at, now):
  if not isinstance(at, (float, int)):
    return 'unknown'
  value = max(0, int(now - at))
  return f'{value}s' if value < 60 else f'{value // 60}m'


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
    self.mode, self.filter, self.editor, self.pending = 'normal', '', '', ''
    self.error = ''
    self.refreshed = None
    self.labels = {}
    self.mouse_rows, self.mouse_details = {}, (0, 0)

  def update(self, agents, now, incomplete=False):
    old = self.tree
    self.tree = AgentTree(agents, now, incomplete or any(
      a.get('coverage_incomplete') for a in agents))
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
      elif key == 'Z':
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
    lines = [a['key'], obs['reason'],
             'Observed ' + elapsed(obs['at'], now) + ' ago',
             'Subagents: ' + self.tree.summary(self.selected)]
    lines += ['Observed wait: ' + wait_label(w) + ' · ' +
              elapsed(w.get('at'), now) + ' ago' for w in obs['waits']]
    lines += ['Session title: ' + str(a.get('session_title') or 'not recorded'),
              'Work: ' + str(a.get('work', '')),
              'Model: ' + str(a.get('model', 'unknown')) + ' · ' +
              str(a.get('effort', 'unknown')),
              'Directory: ' + str(a.get('cwd') or 'unknown'),
              'Recorded plan: ' + str(a.get('now') or 'unknown'),
              'Tasks owned/helped: ' + (', '.join(t['id'] for t in
                a.get('tasks', [])) or 'none observed'),
              'Tokens: ' + str((a.get('tokens') or {}).get('total', 'unknown')),
              'Observed uncached tokens / 15m: ' +
              str(a.get('recent_tokens', 'unknown')),
              'Local observations only; no watcher count or liveness proof.']
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
      lines = ['AGENTS · tree controls',
        'j/k or arrows: select · gg/G: first/last · Ctrl-d/u: half page',
        'Space: collapse/expand · Left/h: collapse or parent',
        'Right/l: expand or first child · /: filter (keeps ancestors)',
        'Enter: details · [/]: scroll details · Esc: close/cancel',
        '? or Esc: close help · q/ZZ/Ctrl-C: quit',
        'Collapse choices and selection survive refreshes.',
        'IDLE = turn ended; not proof the agent is waiting for work.',
        'WAITING = outstanding observed wait; target may be unknown.',
        'THINKING = reasoning observed; TOOL = outstanding call.',
        'UNKNOWN = missing, old or incomplete execution evidence.',
        'Subagent summaries include descendants, even while collapsed.',
        'No watchers are counted. Observations are local and incomplete.']
      return [(clip(t, width), '') for t in lines[:height]]
    title = ('STALE · last refresh ' + elapsed(self.refreshed, now) +
             ' ago · ' + self.error if self.error else
             'Agents · observed activity · refreshed ' +
             elapsed(self.refreshed, now) + ' ago')
    title += ' · partial list' if self.tree.incomplete else ''
    lines = [(title, 'bold')]
    details = [part for line in self.detail(now) for part in wrap(line, width)]
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
        state = obs['state']
        if state == 'WAITING' and obs['waits']:
          wait = obs['waits'][0]
          state = 'WAIT ' + (wait['tasks'][0] if wait['kind'] == 'task'
                             else 'agent' if wait['targets'] else 'agent?')
        counts = self.tree.totals(key)
        summary = self.tree.summary(key) if group else ''
        session_title = str(a.get('session_title') or a.get('work') or
                            self.labels[key])
        gutter = '> ' if key == self.selected else '  '
        label = gutter + prefix + icon + ' ' + session_title
        metadata = self.labels[key] + ' · ' + state
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
          'attention' if state == 'IDLE' and counts['active'] else
          'dim' if state in ('IDLE', 'DONE') else
          'unknown' if state == 'UNKNOWN' else
          'waiting' if state.startswith('WAIT') else
          'working' if state in ('TOOL', 'THINKING') else
          'bold' if group else '')
        self.mouse_rows[len(lines)] = (key, 2 + cells(prefix))
        lines.append((text, style))
    elif slots:
      lines.append(('No matching agents observed', ''))
    if detail_slots:
      self.mouse_details = (len(lines), len(lines) + detail_slots)
      self.offset = min(self.offset, max(0, len(details) - detail_slots))
      lines.extend((t, '') for t in details[self.offset:self.offset + detail_slots])
    start = self.first + 1 if shown and slots else 0
    end = min(len(shown), self.first + slots) if start else 0
    groups = len(self.tree.rows) - len(self.tree.parents)
    footer = (f'Rows {start}-{end}/{len(shown)} · {groups} groups · '
              f'{len(self.tree.rows)} agents · ? help · q quit')
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
