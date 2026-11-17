"""Argument parsing that tells an agent how to fix a mistyped command.

The records commands are mostly called by agents, which guess flags from
other tools. A bare argparse error costs a --help call and a retry, so
unknown commands get a suggestion and unknown flags get the flags the
subcommand accepts.
"""

from __future__ import annotations

import argparse
import difflib
import re

# Commands agents reach for -> what the records tools call them, in order
# of preference; the first one the parser offers is suggested.
COMMAND_HINTS = {
  "cat": ("show",), "get": ("show",), "message": ("show",),
  "messages": ("show",), "read": ("show",), "view": ("show",),
  "edit": ("set", "update"), "modify": ("set", "update"), "update": ("set",),
}

# Unknown flag -> (accepted flag, why), offered when the subcommand has it.
OPTION_HINTS = {
  "--machine": ("--json", "machine-readable output; agent-changelog's "
                "--machine filters entries by host"),
  "--format": ("--json", "machine-readable output; the plain view is already "
               "compact"),
  "--output": ("--json", "machine-readable output"),
  "--limit": ("--tail", "how many log entries to print"),
  "--since": ("--after", "the cursor printed by the previous read"),
  "--cursor": ("--after", "the cursor printed by the previous read"),
  "--here": ("--repo", "the repository name"),
}

CHOICE_RE = re.compile(
  r"argument (?P<arg>\S+): invalid choice: '(?P<value>[^']*)' "
  r"\(choose from (?P<choices>.*)\)$")


class Parser(argparse.ArgumentParser):
  """ArgumentParser whose invalid-choice errors suggest a likely choice."""

  def error(self, message):
    match = CHOICE_RE.match(message)
    if match:
      choices = re.findall(r"'([^']*)'", match["choices"])
      value = match["value"]
      guess = next((hint for hint in COMMAND_HINTS.get(value, ())
                    if hint in choices), None)
      if guess is None:
        close = difflib.get_close_matches(value, choices, 1, 0.6)
        guess = close[0] if close else None
      if guess:
        message += f"; did you mean '{guess}'?"
    super().error(message)


def _subcommand(parser, args):
  """Return the innermost subparser that args selected."""
  while True:
    actions = [action for action in parser._actions
               if isinstance(action, argparse._SubParsersAction)]
    chosen = getattr(args, actions[0].dest, None) if actions else None
    if chosen not in (actions[0].choices if actions else {}):
      return parser
    parser = actions[0].choices[chosen]


def _accepted(parser):
  return sorted({name for action in parser._actions
                 if action.help is not argparse.SUPPRESS
                 for name in action.option_strings
                 if name.startswith("--") and name != "--help"})


def unknown_message(parser, extras, root=None):
  """Describe unknown arguments with the fix for each."""
  accepted = _accepted(parser)
  main = _accepted(root) if root is not None and root is not parser else []
  hints = []
  for token in extras:
    flag = token.split("=", 1)[0]
    if not flag.startswith("--") or flag in accepted:
      continue
    if flag in main:
      # Copying main options onto every subcommand would make existing
      # abbreviations such as watch --un (--until) ambiguous.
      command = parser.prog.removeprefix(root.prog).strip()
      hints.append(f"Put {flag} before the subcommand: {root.prog} {flag} "
                   f"... {command} ...")
      continue
    target, why = OPTION_HINTS.get(flag, (None, None))
    if target in accepted:
      hints.append(f"Use {target} instead of {flag} ({why}).")
      continue
    close = difflib.get_close_matches(flag, accepted, 1, 0.7)
    if close:
      hints.append(f"Did you mean {close[0]} instead of {flag}?")
  message = " ".join(["unrecognized arguments: " + " ".join(extras) + ".",
                       *hints])
  if accepted:
    message += f" {parser.prog} accepts " + ", ".join(accepted) + "."
  return message


def parse(parser, argv=None):
  """Parse like parse_args, explaining unknown arguments."""
  args, extras = parser.parse_known_args(argv)
  if extras:
    command = _subcommand(parser, args)
    command.error(unknown_message(command, extras, parser))
  return args
