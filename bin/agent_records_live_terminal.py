"""Curses lifecycle for the read-only task view; no persistent UI state."""
import os
import shutil
import signal
import sys
import time

from agent_records_core import RecordsError, now, repo_key
from agent_records_live import LiveState, LiveView, read_snapshot


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
    stdscr.timeout(100)
    stdscr.keypad(True)
    view = LiveView(state, args)
    deadline, previous = 0, None
    keys = {curses.KEY_DOWN: 'DOWN', curses.KEY_UP: 'UP',
            curses.KEY_LEFT: 'LEFT', curses.KEY_RIGHT: 'RIGHT',
            curses.KEY_HOME: 'HOME', curses.KEY_END: 'END',
            curses.KEY_BACKSPACE: 'BACKSPACE', curses.KEY_ENTER: '\n'}
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
          attr = {'selected': curses.A_REVERSE, 'bold': curses.A_BOLD}.get(
            style, curses.A_NORMAL)
          try:
            stdscr.addstr(y, 0, text, attr)
          except curses.error:
            # A concurrent resize can invalidate the dimensions just read.
            pass
        stdscr.refresh()
        previous = (frame, height, width)
      try:
        key = stdscr.get_wch()
      except curses.error:
        continue
      if isinstance(key, int):
        key = keys.get(key, '')
      if view.key(key, max(1, height - 6)):
        return 0

  try:
    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
      old_handlers[sig] = signal.signal(sig, interrupted)
    return curses.wrapper(screen)
  except KeyboardInterrupt:
    return 0
  finally:
    termios.tcsetattr(sys.stdin.fileno(), termios.TCSANOW, saved_terminal)
    for sig, handler in old_handlers.items():
      signal.signal(sig, handler)
