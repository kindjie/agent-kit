"""Mistyped records arguments: accepted variants and corrective errors."""

from __future__ import annotations

import json
import subprocess
import sys
import unittest

from tests.agent_records_support import BIN, RecordsFixture


class ArgumentsTest(RecordsFixture):
  def run_full(self, name, *args, code=0):
    result = subprocess.run(
      [sys.executable, str(BIN / name), *map(str, args)], cwd=self.base,
      env=self.env, text=True, stdout=subprocess.PIPE,
      stderr=subprocess.PIPE, timeout=8)
    self.assertEqual(result.returncode, code, (result.stdout, result.stderr))
    return result

  def error(self, name, *args):
    return self.run_full(name, *args, code=2).stderr.splitlines()[-1]

  def task(self):
    return self.run_cmd("agent-task", "--agent", "agent-a", "new",
                        "--title", "Example").strip()

  def test_common_variants_are_accepted(self):
    self.init()
    task = self.task()
    for n in range(3):
      self.run_cmd("agent-task", "--agent", "agent-a", "log", task,
                   f"entry {n}")
    tail = self.run_cmd("agent-task", "show", task, "--tail", "1")
    self.assertEqual(self.run_cmd("agent-task", "show", task, "--last", "1"),
                     tail)
    self.assertIn("entry 2", tail)
    self.assertNotIn("entry 1", tail)
    self.run_cmd("agent-task", "log", task, "after", "--agent", "agent-a")
    # Abbreviations keep working: --un is still --until for watch.
    self.run_cmd("agent-task", "watch", task, "--un", "closed", "--timeout",
                 "0.1", code=4)
    batch = self.run_cmd("agent-task", "events", task, "--json",
                         "--timeout", "0.1")
    self.assertIn("cursor", json.loads(batch.splitlines()[-1]))

  def test_unknown_flags_name_the_fix_and_the_accepted_flags(self):
    self.init()
    message = self.error("agent-task", "list", "--machine")
    self.assertIn("unrecognized arguments: --machine.", message)
    self.assertIn("Use --json instead of --machine", message)
    self.assertIn("agent-changelog's --machine filters entries by host",
                  message)
    self.assertIn("agent-task list accepts ", message)
    self.assertIn("--with-changes.", message)
    self.assertNotIn("--help", message)
    message = self.error("agent-task", "show", "T-0001", "--format", "json")
    self.assertIn("unrecognized arguments: --format json.", message)
    self.assertIn("Use --json instead of --format", message)
    message = self.error("agent-task", "show", "T-0001", "--limit", "3")
    self.assertIn("Use --tail instead of --limit", message)
    self.assertIn("Did you mean --full instead of --fulll?",
                  self.error("agent-task", "show", "T-0001", "--fulll"))
    message = self.error("agent-task", "next", "--here")
    self.assertIn("Use --repo instead of --here", message)
    message = self.error("agent-task", "show", "T-0001", "--unlocked")
    self.assertIn("Put --unlocked before the subcommand: agent-task "
                  "--unlocked ... show ...", message)
    message = self.error("agent-changelog", "list", "--agent", "a")
    self.assertIn("Put --agent before the subcommand", message)
    # A hint is offered only when the subcommand has the replacement.
    message = self.error("agent-task", "log", "T-0001", "x", "--format", "j")
    self.assertNotIn("Use --json", message)

  def test_unknown_commands_suggest_one(self):
    for name, wrong, right in (("agent-task", "read", "show"),
                               ("agent-task", "messages", "show"),
                               ("agent-task", "update", "set"),
                               ("agent-task", "chek", "check"),
                               ("agent-changelog", "shwo", "show"),
                               ("agent-changelog", "edit", "update")):
      with self.subTest(wrong=wrong):
        message = self.error(name, wrong)
        self.assertIn(f"invalid choice: '{wrong}'", message)
        self.assertTrue(message.endswith(f"; did you mean '{right}'?"),
                        message)
    self.assertNotIn("did you mean", self.error("agent-changelog", "zzz"))
    message = self.error("agent-changelog", "mistake", "edit", "x")
    self.assertTrue(message.endswith("; did you mean 'update'?"), message)
    message = self.error("agent-task", "estimate", "shw", "T-0001", "m")
    self.assertNotIn("did you mean", message)


if __name__ == "__main__":
  unittest.main()
