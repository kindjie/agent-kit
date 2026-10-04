"""Failure diagnostics in isolated repos; never invokes records CLIs."""

import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bin"))
import agent_records_core as core


class FailureDiagnosticsTest(unittest.TestCase):
  def setUp(self):
    temporary = tempfile.TemporaryDirectory()
    self.addCleanup(temporary.cleanup)
    self.base = Path(temporary.name)
    fixture_environment = {key: value for key, value in os.environ.items()
                           if not key.startswith("GIT_")}
    fixture_environment.update(GIT_CONFIG_GLOBAL=os.devnull,
                               GIT_CONFIG_NOSYSTEM="1")
    environment = patch.dict(os.environ, fixture_environment, clear=True)
    environment.start()
    self.addCleanup(environment.stop)
    self.tasks, self.changes = self.base / "tasks", self.base / "changes"
    for root in (self.tasks, self.changes):
      root.mkdir()
      core.git(root, "init", "-q")
      core.git(root, "config", "user.name", "Fixture")
      core.git(root, "config", "user.email", "fixture@example.invalid")
      core.git(root, "config", "commit.gpgsign", "false")
      core.git(root, "commit", "--allow-empty", "-m", "Fixture")
    self.heads = {root: core.git(root, "rev-parse", "HEAD")[1]
                  for root in (self.tasks, self.changes)}

  def inject_failure(self, failing_side, cross):
    real_git = core.git
    attempts = []

    def fail_commit(root, *args, **kwargs):
      if args[0] == "commit":
        attempts.append(root)
        if root == failing_side:
          raise core.RecordsError("original signing failure", 1)
      return real_git(root, *args, **kwargs)

    recovery = "cross_recover" if cross else "recover_single"
    with patch.object(core, "git", fail_commit):
      with patch.object(core, recovery, side_effect=core.RecordsError(
          "recovery signing failure", 5)) as recover:
        with self.assertRaises(core.RecordsError) as error:
          if cross:
            core.mutate_cross(self.tasks, self.changes,
                              {"task.md": b"task"}, {"entry.md": b"entry"},
                              "Fixture mutation", "fixture")
          else:
            core.mutate(self.changes, {"entry.md": b"entry"},
                        "Fixture mutation", "fixture")
    self.assertEqual(error.exception.code, 5)
    self.assertIn("original signing failure", str(error.exception))
    self.assertIn("recovery signing failure", str(error.exception))
    self.assertNotIn("Safe to retry", str(error.exception))
    recover.assert_called_once()
    return attempts

  def test_single_dual_failure_preserves_journal_and_head(self):
    self.assertEqual(self.inject_failure(self.changes, False), [self.changes])
    self.assertEqual(core.git(self.changes, "rev-parse", "HEAD")[1],
                     self.heads[self.changes])
    self.assertTrue(core.journal_path(self.changes).exists())
    self.assertEqual((self.changes / "entry.md").read_bytes(), b"entry")

  def check_cross_journals(self):
    task_journal = core.read_journal(self.tasks)
    change_journal = core.read_journal(self.changes)
    self.assertEqual(task_journal["id"], change_journal["id"])
    self.assertTrue(task_journal["cross"])
    self.assertTrue(change_journal["cross_stub"])
    self.assertEqual(core.git(self.tasks, "rev-parse", "HEAD")[1],
                     self.heads[self.tasks])

  def test_cross_first_side_dual_failure_preserves_both_journals(self):
    self.assertEqual(self.inject_failure(self.changes, True), [self.changes])
    self.check_cross_journals()
    self.assertEqual(core.git(self.changes, "rev-parse", "HEAD")[1],
                     self.heads[self.changes])
    self.assertFalse((self.tasks / "task.md").exists())
    self.assertEqual((self.changes / "entry.md").read_bytes(), b"entry")

  def test_cross_second_side_dual_failure_preserves_committed_first_side(self):
    self.assertEqual(self.inject_failure(self.tasks, True),
                     [self.changes, self.tasks])
    self.check_cross_journals()
    self.assertNotEqual(core.git(self.changes, "rev-parse", "HEAD")[1],
                        self.heads[self.changes])
    self.assertEqual(core.head_bytes(self.changes, "entry.md"), b"entry")
    self.assertEqual((self.tasks / "task.md").read_bytes(), b"task")
    self.assertEqual(core.read_journal(self.tasks)["changelog"]["commit"],
                     core.git(self.changes, "rev-parse", "HEAD")[1].strip())
