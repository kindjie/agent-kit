"""End-to-end tests for the agent records commands."""

from __future__ import annotations

import base64
import hashlib
import os
import json
import subprocess
import sys
import tempfile
import unittest
import shutil
import time
from pathlib import Path

from tests.agent_records_support import RecordsFixture


ROOT = Path(__file__).resolve().parents[1]
BIN = ROOT / "bin"


class LifecycleTest(RecordsFixture):

  def test_done_checklist_and_transfer(self):
    self.init()
    first = self.run_cmd("agent-task", "--agent", "agent-a", "new",
                         "--title", "First", "--no-changes").strip()
    second = self.run_cmd("agent-task", "--agent", "agent-a", "new",
                          "--title", "Second").strip()
    self.run_cmd("agent-task", "--agent", "agent-a", "claim", first)
    for key in ("merged", "cleanup", "docs", "work-reviewed"):
      self.run_cmd("agent-task", "--agent", "agent-a", "check", first,
                   key, "--na", "No repository changes")
    self.run_cmd("agent-task", "--agent", "agent-a", "close", first, "done")
    self.assertIn("done", self.run_cmd("agent-task", "show", first))
    self.run_cmd("agent-task", "--agent", "agent-a", "claim", second)
    third = self.run_cmd("agent-task", "--agent", "agent-a", "new",
                         "--title", "Third").strip()
    entry = self.run_cmd(
      "agent-changelog", "--agent", "agent-a", "new", "--scope",
      "example", "--slug", "scratch", "--kind", "scratch",
      "--location", "scratch", "--why", "example",
      "--cleanup-when", "later", "--cleanup-how", "remove",
      "--task", second,
    ).strip()
    self.run_cmd("agent-task", "--agent", "agent-a", "close", second,
                 "superseded", "--by", third, "--reason", "moved")
    self.assertIn("tasks: " + third,
                  self.run_cmd("agent-changelog", "show", entry))
    self.assertIn("Transferred from " + second,
                  self.run_cmd("agent-task", "show", third))

  def test_refusal_preserves_head_and_task(self):
    self.init()
    task = self.run_cmd("agent-task", "--agent", "agent-a", "new",
                        "--title", "Owned").strip()
    self.run_cmd("agent-task", "--agent", "agent-a", "claim", task)
    path = next(self.tasks.glob("T-0001-*.md"))
    before = path.read_bytes()
    head = subprocess.check_output(
      ["git", "-C", str(self.tasks), "rev-parse", "HEAD"],
      env=self.env).strip()
    self.run_cmd("agent-task", "--agent", "agent-b", "release", task,
                 "--note", "no", code=1)
    self.assertEqual(path.read_bytes(), before)
    self.assertEqual(subprocess.check_output(
      ["git", "-C", str(self.tasks), "rev-parse", "HEAD"],
      env=self.env).strip(), head)

  def test_related_tasks_remain_symmetric(self):
    self.init()
    first = self.run_cmd("agent-task", "--agent", "agent-a", "new",
                         "--title", "First").strip()
    second = self.run_cmd("agent-task", "--agent", "agent-a", "new",
                          "--title", "Second").strip()
    self.run_cmd("agent-task", "--agent", "agent-a", "link", first,
                 "--related", second)
    self.assertIn("related: " + second,
                  self.run_cmd("agent-task", "show", first))
    self.assertIn("related: " + first,
                  self.run_cmd("agent-task", "show", second))
    self.run_cmd("agent-task", "--agent", "agent-a", "link", first,
                 "--remove", second, "--reason", "no longer related")
    self.assertIn("related: \n", self.run_cmd("agent-task", "show", first))
    self.assertIn("related: \n", self.run_cmd("agent-task", "show", second))

  def test_pr_link_sets_change_gate_and_ref_does_not(self):
    self.init()
    task = self.run_cmd("agent-task", "--agent", "agent-a", "new",
                        "--title", "Links", "--no-changes").strip()
    self.run_cmd("agent-task", "--agent", "agent-a", "link", task,
                 "--ref", "context-pr")
    self.assertIn("produces-changes: no",
                  self.run_cmd("agent-task", "show", task))
    self.run_cmd("agent-task", "--agent", "agent-a", "link", task,
                 "--pr", "work-pr")
    self.assertIn("produces-changes: yes",
                  self.run_cmd("agent-task", "show", task))
    self.run_cmd("agent-task", "--agent", "agent-a", "claim", task)
    self.run_cmd("agent-task", "--agent", "agent-a", "check", task,
                 "merged", "--na", "No merge", code=1)
    self.run_cmd("agent-task", "--agent", "agent-a", "set", task,
                 "--produces-changes", "no", "--reason", "mistake", code=1)
    self.run_cmd("agent-task", "--agent", "agent-a", "link", task,
                 "--demote", "work-pr", "--reason", "not this work")
    self.run_cmd("agent-task", "--agent", "agent-a", "set", task,
                 "--produces-changes", "no", "--reason", "research only")
    self.run_cmd("agent-task", "--agent", "agent-a", "check", task,
                 "merged", "--na", "No change")
    self.run_cmd("agent-task", "--agent", "agent-a", "set", task,
                 "--produces-changes", "yes")
    self.run_cmd("agent-task", "--agent", "agent-a", "check", task,
                 "merged", "--evidence", "commit reference")
    self.assertNotIn("merged: n/a",
                     self.run_cmd("agent-task", "show", task))

  def test_forced_close_still_checks_associations(self):
    self.init()
    task = self.run_cmd("agent-task", "--agent", "agent-a", "new",
                        "--title", "Forced close").strip()
    self.run_cmd("agent-task", "--agent", "agent-a", "claim", task)
    entry = self.run_cmd(
      "agent-changelog", "--agent", "agent-a", "new", "--scope",
      "example", "--slug", "state", "--kind", "scratch",
      "--location", "scratch", "--why", "example",
      "--cleanup-when", "later", "--cleanup-how", "remove",
      "--task", task,
    ).strip()
    self.run_cmd("agent-task", "--agent", "agent-b", "close", task,
                 "cancelled", "--reason", "stopped", "--force",
                 "owner requested", code=1)
    self.run_cmd("agent-changelog", "--agent", "agent-a", "close",
                 entry, "--what", "removed")
    self.run_cmd("agent-task", "--agent", "agent-b", "close", task,
                 "cancelled", "--reason", "stopped", "--force",
                 "owner requested")
    self.assertIn("FORCED by agent-b: owner requested",
                  self.run_cmd("agent-task", "show", task))

  def test_force_scope_and_expiry(self):
    self.init()
    task = self.run_cmd("agent-task", "--agent", "agent-a", "new",
                        "--title", "Claimed").strip()
    self.run_cmd("agent-task", "--agent", "agent-a", "claim", task)
    self.run_cmd("agent-task", "--agent", "agent-b", "set", task,
                 "--priority", "P1", code=1)
    self.run_cmd("agent-task", "--agent", "agent-b", "set", task,
                 "--priority", "P1", "--force", "owner instruction")
    self.assertIn("FORCED by agent-b: owner instruction",
                  self.run_cmd("agent-task", "show", task))
    self.run_cmd("agent-task", "--agent", "agent-a", "close", task,
                 "done", "--force", "owner instruction", code=2)
    self.run_cmd("agent-task", "--agent", "agent-a", "release", task,
                 "--note", "pause")
    self.run_cmd("agent-task", "--agent", "agent-b", "release", task,
                 "--note", "not claimed", "--force", "owner requested",
                 code=1)
    self.run_cmd("agent-task", "--agent", "agent-b", "set", task,
                 "--review", "required", code=1)
    self.run_cmd("agent-task", "--agent", "agent-b", "set", task,
                 "--review", "required", "--force", "owner instruction")
    self.assertIn("review: required", self.run_cmd("agent-task", "show", task))
