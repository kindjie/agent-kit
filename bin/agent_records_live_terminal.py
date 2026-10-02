"""Curses lifecycle for the read-only task view; no persistent UI state."""
import os
import re
import shutil
import signal
import sys
import time

from agent_records_core import RecordsError, now, repo_key
from agent_records_live import LiveState, LiveView, read_snapshot


def enable_mouse(curses):
  mask = (curses.BUTTON1_PRESSED | curses.BUTTON4_PRESSED |
          getattr(curses, 'BUTTON5_PRESSED', 0))
  try:
    curses.mousemask(mask)
    curses.mouseinterval(0)
    # Old macOS curses supports only four mouse buttons; use SGR reports
    # and decode them below when its terminfo still expects legacy X10.
    sys.stdout.write('\x1b[?1006h')
    sys.stdout.flush()
  except curses.error:
    pass


def read_mouse(curses):
  try:
    _, x, y, _, buttons = curses.getmouse()
  except curses.error:
    return None
  if buttons & curses.BUTTON4_PRESSED:
    action = 'up'
  elif buttons & getattr(curses, 'BUTTON5_PRESSED', 0):
    action = 'down'
  elif buttons & curses.BUTTON1_PRESSED:
    action = 'click'
  else:
    return None
  return action, x, y


def read_input(stdscr, curses):
  key = stdscr.get_wch()
  if key == curses.KEY_MOUSE:
    return None, read_mouse(curses)
  if key != '\x1b':
    return key, None
  sequence = ''
  stdscr.timeout(30)
  try:
    # Keypad mode already decodes ordinary arrows; this handles SGR only.
    first = stdscr.get_wch()
    if first != '[':
      curses.unget_wch(first)
      return key, None
    second = stdscr.get_wch()
    if second != '<':
      curses.unget_wch(second)
      curses.unget_wch(first)
      return key, None
    for _ in range(64):
      char = stdscr.get_wch()
      if not isinstance(char, str):
        return None, None
      sequence += char
      if char in 'Mm':
        break
    match = re.fullmatch(r'(\d+);(\d+);(\d+)M', sequence)
    if match:
      button, x, y = map(int, match.groups())
      action = {0: 'click', 64: 'up', 65: 'down'}.get(button & ~28)
      if action and x > 0 and y > 0:
        return None, (action, x - 1, y - 1)
    return None, None
  except curses.error:
    return key, None
  finally:
    stdscr.timeout(100)


def mouse_screen(curses, screen):
  def wrapped(stdscr):
    try:
      return screen(stdscr)
    finally:
      sys.stdout.write('\x1b[?1006l')
      sys.stdout.flush()
  return curses.wrapper(wrapped)


def run_live(root, changes, args):
  if args.here:
    here = repo_key()
    if not here:
      raise RecordsError('--here requires a git checkout', 2)
    args.repo = list(args.repo or []) + [here]
  state = LiveState()
  # A short lock wait keeps navigation and termination responsive.
  wait = min(args.wait, 0.1)
  terminal = (sys.stdin.isatty() and sys.stdout.isatty() and
              os.environ.get('TERM', '') not in ('', 'dumb'))
  if not terminal:
    state.update(read_snapshot(root, changes, wait), now())
    view = LiveView(state, args)
    width = shutil.get_terminal_size((120, 24)).columns
    for text, _ in view.frame(width, max(24, len(view.visible()) * 4 + 6), now()):
      print(text)
    return 0
  import curses
  import termios
  saved_terminal = termios.tcgetattr(sys.stdin.fileno())
  old_handlers = {}

  def interrupted(signum, frame):
    raise KeyboardInterrupt

  def screen(stdscr):
    try:
      curses.curs_set(0)
    except curses.error:
      pass
    curses.set_escdelay(30)
    colors = {}
    if 'NO_COLOR' not in os.environ and curses.has_colors():
      try:
        curses.start_color()
        curses.use_default_colors()
        tones = {'open': curses.COLOR_CYAN,
          'in-progress': curses.COLOR_GREEN, 'in-review': curses.COLOR_MAGENTA,
          'blocked': curses.COLOR_RED, 'warning': curses.COLOR_YELLOW,
          'done': curses.COLOR_GREEN, 'cancelled': curses.COLOR_WHITE}
        for number, (name, foreground) in enumerate(tones.items(), 1):
          curses.init_pair(number, foreground, -1)
          colors[name] = curses.color_pair(number)
      except curses.error:
        colors = {}
    curses.nonl()  # Preserve CR (Enter) versus LF (Ctrl-j).
    stdscr.timeout(100)
    stdscr.keypad(True)
    enable_mouse(curses)
    view = LiveView(state, args)
    deadline, previous = 0, None
    keys = {curses.KEY_DOWN: 'DOWN', curses.KEY_UP: 'UP',
            curses.KEY_LEFT: 'LEFT', curses.KEY_RIGHT: 'RIGHT',
            curses.KEY_HOME: 'HOME', curses.KEY_END: 'END',
            curses.KEY_BACKSPACE: 'BACKSPACE', curses.KEY_ENTER: '\r'}
    while True:
      if time.monotonic() >= deadline:
        try:
          state.update(read_snapshot(root, changes, wait), now())
        except (RecordsError, OSError, ValueError) as exc:
          state.error = str(exc)
        deadline = time.monotonic() + args.interval
      height, width = stdscr.getmaxyx()
      frame = view.frame(width, height, now())
      if (frame, height, width) != previous:
        stdscr.erase()
        for y, (text, style) in enumerate(frame):
          attr = curses.A_NORMAL
          for token in style.split():
            attr |= {'selected': curses.A_REVERSE, 'bold': curses.A_BOLD,
                     'cancelled': curses.A_DIM}.get(token, 0)
            attr |= colors.get(token, 0)
          try:
            stdscr.addstr(y, 0, text, attr)
          except curses.error:
            # A concurrent resize can invalidate the dimensions just read.
            pass
        stdscr.refresh()
        previous = (frame, height, width)
      try:
        key, event = read_input(stdscr, curses)
      except curses.error:
        continue
      if event:
        view.mouse(*event, max(1, height - 6))
      if key is None:
        continue
      if isinstance(key, int):
        key = keys.get(key, '')
      if view.key(key, max(1, height - 6)):
        return 0

  try:
    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
      old_handlers[sig] = signal.signal(sig, interrupted)
    return mouse_screen(curses, screen)
  except KeyboardInterrupt:
    return 0
  finally:
    termios.tcsetattr(sys.stdin.fileno(), termios.TCSANOW, saved_terminal)
    for sig, handler in old_handlers.items():
      signal.signal(sig, handler)
