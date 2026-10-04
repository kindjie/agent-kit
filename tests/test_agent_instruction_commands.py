"""Keep delegation quota examples compatible with the real CLI parser."""

from __future__ import annotations

import argparse
import io
import re
import shlex
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest.mock import patch

from tests.test_agent_quota import AGENT_QUOTA

ROOT = Path(__file__).resolve().parent.parent


class ParsedCommand(Exception):
  """Stop after argument parsing, before account queries or cache writes."""


class DelegationQuotaCommandsTest(unittest.TestCase):
  def check_examples(self, text):
    commands = [code for code in re.findall(r"`([^`\n]+)`", text)
                if code.startswith("agent-quota ")]
    self.assertTrue(commands, "delegation has no quota command examples")
    original = argparse.ArgumentParser.parse_args

    def parse_only(parser, *args, **kwargs):
      original(parser, *args, **kwargs)
      raise ParsedCommand()

    for command in commands:
      with patch.object(argparse.ArgumentParser, "parse_args", parse_only):
        with self.assertRaises(ParsedCommand, msg=command):
          AGENT_QUOTA.main(shlex.split(command)[1:])

  def test_delegation_examples_parse_without_running_commands(self):
    self.check_examples(
      (ROOT / "skills/delegation/SKILL.md").read_text())

  def test_retired_model_filter_is_rejected(self):
    with redirect_stderr(io.StringIO()):
      with self.assertRaises(SystemExit) as error:
        self.check_examples("Run `agent-quota --brief --for <model>`.")
    self.assertEqual(error.exception.code, 2)

  def test_missing_examples_cannot_pass(self):
    with self.assertRaisesRegex(AssertionError, "no quota command examples"):
      self.check_examples("No executable examples.")


if __name__ == "__main__":
  unittest.main()
