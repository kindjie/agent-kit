"""Reported association, transfer, history and rollback regressions."""

from __future__ import annotations

import subprocess
import sys
import unittest
from unittest.mock import patch

from tests.agent_records_support import BIN, RecordsFixture
from tests import test_agent_quota as quota_tests

AGENT_QUOTA = quota_tests.AGENT_QUOTA

sys.path.insert(0, str(BIN))
import agent_records_core as core


class RecordToolRegressionTest(RecordsFixture):
  def task(self, title):
    return self.run_cmd("agent-task", "--agent", "agent-a", "new",
                        "--title", title, "--no-changes").strip()

  def entry(self, *tasks):
    args = ["--scope", "example", "--slug", "state-" + str(len(tasks)),
            "--kind", "scratch", "--location", "scratch", "--why",
            "needed", "--cleanup-when", "later", "--cleanup-how", "remove"]
    for task in tasks:
      args.extend(("--task", task))
    return self.run_cmd("agent-changelog", "--agent", "agent-a", "new",
                        *args).strip()

  def invoke(self, name, *args):
    return subprocess.run([sys.executable, str(BIN / name), "--agent",
                           "agent-a", *args], env=self.env,
                          capture_output=True, text=True)

  def test_repeated_association_edits_apply_every_value(self):
    self.init()
    first, second, third = [self.task(x) for x in ("First", "Second", "Third")]
    entry = self.entry(first, second, third)
    self.run_cmd("agent-changelog", "--agent", "agent-a", "update", entry,
                 "--remove-task", first, "--remove-task", second,
                 "--reason", "move")
    self.assertIn("tasks: " + third + "\n",
                  self.run_cmd("agent-changelog", "show", entry))
    self.run_cmd("agent-changelog", "--agent", "agent-a", "update", entry,
                 "--add-task", first, "--add-task", second,
                 "--add-repo", "one", "--add-repo", "two")
    shown = self.run_cmd("agent-changelog", "show", entry)
    self.assertIn("tasks: " + ", ".join((third, first, second)), shown)
    self.assertIn("repos: one, two", shown)
    self.run_cmd("agent-changelog", "--agent", "agent-a", "update", entry,
                 "--remove-repo", "one", "--remove-repo", "two")
    self.assertIn("repos: \n", self.run_cmd("agent-changelog", "show", entry))

  def test_removing_all_tasks_or_unknown_task_is_atomic(self):
    self.init()
    first, second, other = [self.task(x) for x in ("First", "Second", "Other")]
    entry = self.entry(first, second)
    before = (self.changes / entry).read_bytes()
    for removed in ((first, second), (first, other)):
      args = ["update", entry, "--reason", "move"]
      for task in removed:
        args.extend(("--remove-task", task))
      result = self.invoke("agent-changelog", *args)
      self.assertEqual(result.returncode, 1, result.stderr)
      self.assertEqual((self.changes / entry).read_bytes(), before)

  def test_done_transfer_to_claimed_task_moves_open_records(self):
    self.init()
    first, target = self.task("First"), self.task("Target")
    self.run_cmd("agent-task", "--agent", "agent-a", "claim", first)
    self.run_cmd("agent-task", "--agent", "agent-a", "claim", target)
    for key in ("merged", "cleanup", "docs", "work-reviewed"):
      self.run_cmd("agent-task", "--agent", "agent-a", "check", first,
                   key, "--na", "No repository changes")
    entries = (self.entry(first), self.entry(first, target))
    self.run_cmd("agent-task", "--agent", "agent-a", "close", first,
                 "done", "--transfer", target)
    for entry in entries:
      self.assertIn("tasks: " + target + "\n",
                    self.run_cmd("agent-changelog", "show", entry))
    self.assertIn("status: done", self.run_cmd("agent-task", "show", first))
    target_log = self.run_cmd("agent-task", "show", target)
    self.assertIn("Transferred from " + first, target_log)
    # A done close is not a supersession; the default reason says so.
    self.assertIn("reason: completed", target_log)
    self.assertNotIn("reason: superseded", target_log)

  def test_done_without_transfer_preserves_open_records(self):
    self.init()
    first = self.task("First")
    self.run_cmd("agent-task", "--agent", "agent-a", "claim", first)
    for key in ("merged", "cleanup", "docs", "work-reviewed"):
      self.run_cmd("agent-task", "--agent", "agent-a", "check", first,
                   key, "--na", "No repository changes")
    entry = self.entry(first)
    before = (self.changes / entry).read_bytes()
    result = self.invoke("agent-task", "close", first, "done")
    self.assertEqual(result.returncode, 1, result.stderr)
    self.assertIn("transfer or close the records first", result.stderr)
    self.assertEqual((self.changes / entry).read_bytes(), before)

  def test_cancelled_transfer_to_existing_association_deduplicates(self):
    self.init()
    first, target = self.task("First"), self.task("Target")
    self.run_cmd("agent-task", "--agent", "agent-a", "claim", first)
    entry = self.entry(first, target)
    self.run_cmd("agent-task", "--agent", "agent-a", "close", first,
                 "cancelled", "--reason", "moved", "--transfer", target)
    self.assertIn("tasks: " + target + "\n",
                  self.run_cmd("agent-changelog", "show", entry))

  def test_failed_commit_reports_verified_rollback_without_retry(self):
    self.init()
    real_git = core.git
    attempts = []

    def fail_commit(root, *args, **kwargs):
      if args[0] == "commit":
        attempts.append(args)
        raise core.RecordsError("git commit [exit 1]: fatal: failed to write "
                                "commit object", 1)
      return real_git(root, *args, **kwargs)

    with patch.object(core, "git", fail_commit):
      with self.assertRaisesRegex(core.RecordsError,
                                  "rollback verified") as err:
        core.mutate(self.changes, {"entries/example.md": b"example\n"},
                    "Example", "agent-a")
    self.assertIn("failed to write commit object", str(err.exception))
    self.assertIn("Safe to retry", str(err.exception))
    self.assertEqual(len(attempts), 1)
    self.assertFalse((self.changes / "entries/example.md").exists())
    self.assertFalse(core.journal_path(self.changes).exists())
    self.assertEqual(core.status(self.changes), "")

  def test_failed_recovery_preserves_commit_cause_without_retry_advice(self):
    self.init()
    real_git = core.git

    def fail_commit(root, *args, **kwargs):
      if args[0] == "commit":
        raise core.RecordsError("fatal: failed to write commit object", 1)
      return real_git(root, *args, **kwargs)

    with patch.object(core, "git", fail_commit):
      with patch.object(core, "recover_single", side_effect=core.RecordsError(
          "recovery denied", 5)):
        with self.assertRaises(core.RecordsError) as err:
          core.mutate(self.changes, {"entries/example.md": b"example\n"},
                      "Example", "agent-a")
    self.assertEqual(err.exception.code, 5)
    self.assertIn("failed to write commit object", str(err.exception))
    self.assertIn("recovery denied", str(err.exception))
    self.assertNotIn("Safe to retry", str(err.exception))
    self.assertTrue(core.journal_path(self.changes).exists())


class QuotaHistoryRegressionTest(unittest.TestCase):
  def test_archived_readings_follow_current_quota_and_credits(self):
    fixture = quota_tests.AgentQuotaTest()
    document = fixture.archived_codex_document()
    output = AGENT_QUOTA.render_brief(document)
    historical = output.index("Other accounts")
    self.assertLess(output.index("Quota"), historical)
    self.assertLess(output.index("Credits"), historical)
    self.assertLess(output.index("live@example.test"), historical)
    self.assertIn("historical; unverified", output)
