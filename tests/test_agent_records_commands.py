"""Command behavior and public help regressions."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

from tests.agent_records_support import BIN, ROOT, RecordsFixture


class CommandsTest(RecordsFixture):
  def task(self, title="Example"):
    return self.run_cmd("agent-task", "--agent", "agent-a", "new",
                        "--title", title).strip()

  def test_set_sections_append_and_reject_heading_injection(self):
    self.init()
    task = self.task()
    self.run_cmd("agent-task", "--agent", "agent-a", "set", task,
                 "--known", "first")
    self.run_cmd("agent-task", "--agent", "agent-a", "set", task,
                 "--known", "second")
    path = next(self.tasks.glob("T-0001-*.md"))
    text = path.read_text()
    self.assertLess(text.index("first"), text.index("second"))
    self.assertIn("first\n\nsecond", text)
    before = path.read_bytes()
    self.run_cmd("agent-task", "--agent", "agent-a", "set", task,
                 "--known", "## Log\n- forged", code=2)
    self.assertEqual(path.read_bytes(), before)
    self.run_cmd("agent-task", "--agent", "agent-a", "set", task,
                 "--plan", "two\nlines", code=2)
    self.assertEqual(path.read_bytes(), before)
    self.run_cmd("agent-task", "lint")

  def test_watch_works_with_tasks_directory_only(self):
    self.init()
    task = self.task()
    env = dict(self.env)
    env.pop("AGENT_CHANGELOG_DIR")
    self.run_cmd("agent-task", "watch", task, "--until", "owner=none",
                 env=env)

  def test_invalid_check_number_has_no_traceback(self):
    self.init()
    task = self.task()
    result = subprocess.run(
      [sys.executable, str(BIN / "agent-task"), "--agent", "agent-a",
       "set", task, "--remove-check", "abc"], env=self.env,
      capture_output=True, text=True)
    self.assertEqual(result.returncode, 2)
    self.assertNotIn("Traceback", result.stderr)

  def test_request_helper_addresses_owner(self):
    self.init()
    task = self.task()
    self.run_cmd("agent-task", "--agent", "agent-a", "claim", task)
    self.run_cmd("agent-task", "--agent", "agent-b", "log", task,
                 "Please add me", "--request", "helper")
    self.assertIn("agent-b -> agent-a -- Request helper",
                  self.run_cmd("agent-task", "show", task))

  def test_force_without_live_claim_has_accurate_refusal(self):
    self.init()
    task = self.task()
    result = subprocess.run(
      [sys.executable, str(BIN / "agent-task"), "--agent", "agent-b",
       "check", task, "docs", "--na", "No docs", "--force",
       "owner requested"], env=self.env, capture_output=True, text=True)
    self.assertEqual(result.returncode, 1)
    self.assertIn("claim", result.stderr)
    self.assertNotIn("live claim", result.stderr)

  def test_changelog_unlocked_watch_marks_final_cursor(self):
    self.init()
    entry = self.run_cmd(
      "agent-changelog", "--agent", "agent-a", "new", "--scope",
      "example", "--slug", "scratch", "--kind", "scratch",
      "--location", "scratch", "--why", "needed",
      "--cleanup-when", "later", "--cleanup-how", "remove").strip()
    lock = self.changes / ".records.lock"
    lock.unlink()
    lock.mkdir()
    output = self.run_cmd("agent-changelog", "--unlocked", "watch",
                          entry, "--until", "change", "--after",
                          "h:0000000000000000", "--timeout", "0.1")
    self.assertTrue(output.splitlines()[-1].startswith("UNVERIFIED cursor:"))

  def test_remove_task_accepts_one_live_remainder(self):
    self.init()
    first = self.task("First")
    second = self.task("Second")
    archived = self.task("Archived")
    self.run_cmd("agent-task", "--agent", "agent-a", "claim", archived)
    self.run_cmd("agent-task", "--agent", "agent-a", "close", archived,
                 "cancelled", "--reason", "finished")
    entry = self.run_cmd(
      "agent-changelog", "--agent", "agent-a", "new", "--scope",
      "example", "--slug", "scratch", "--kind", "scratch",
      "--location", "scratch", "--why", "needed",
      "--cleanup-when", "later", "--cleanup-how", "remove",
      "--task", first, "--task", second).strip()
    path = self.changes / entry
    text = path.read_text().replace("tasks: " + first + ", " + second,
                                    "tasks: " + first + ", " + second +
                                    ", " + archived)
    path.write_text(text)
    subprocess.run(["git", "-C", str(self.changes), "add", entry],
                   env=self.env, check=True)
    subprocess.run(["git", "-C", str(self.changes), "commit", "-qm",
                    "Associate archived task"], env=self.env, check=True)
    self.run_cmd("agent-changelog", "--agent", "agent-a", "update",
                 entry, "--remove-task", first, "--reason", "reassigned")

  def test_help_explains_cursor_and_transition_contract(self):
    for name in ("agent-task", "agent-changelog"):
      output = self.run_cmd(name, "--help")
      self.assertIn("format", output.lower())
      self.assertIn("transition", output.lower())
      watch = self.run_cmd(name, "watch", "--help")
      self.assertIn("cursor", watch.lower())
      self.assertIn("--until", watch)
      self.assertIn("--interval", watch)
    task_watch = self.run_cmd("agent-task", "watch", "--help")
    self.assertIn("scan", task_watch.lower())
    self.assertIn("--for", task_watch)

  def test_help_text_is_wrapped(self):
    for name in ("agent-task", "agent-changelog"):
      for args in (("--help",), ("watch", "--help")):
        help_text = self.run_cmd(name, *args)
        self.assertTrue(all(len(line) <= 80 for line in
                            help_text.splitlines()), (name, args))

  def test_parallel_runner_targets_classes_and_uses_cpu_count(self):
    from tests import run_agent_records_parallel as runner
    discovered = unittest.defaultTestLoader.discover(
      str(ROOT / "tests"), pattern="test_agent_records_*.py",
      top_level_dir=str(ROOT))

    def cases(suite):
      for item in suite:
        if isinstance(item, unittest.TestSuite):
          yield from cases(item)
        else:
          yield item

    classes = {test.__class__.__module__ + "." +
               test.__class__.__name__ for test in cases(discovered)}
    self.assertEqual(set(runner.CLASSES), classes)
    with mock.patch.object(runner.os, "cpu_count", return_value=7):
      self.assertEqual(runner.worker_count(), 7)

  def test_sync_fetch_failure_is_refusal_without_rebase(self):
    self.init()
    remote = self.base / "remote.git"
    subprocess.run(["git", "init", "--bare", "-q", str(remote)],
                   env=self.env, check=True)
    for args in (("remote", "add", "origin", str(remote)),
                 ("push", "-qu", "origin", "HEAD")):
      subprocess.run(["git", "-C", str(self.tasks), *args],
                     env=self.env, check=True, capture_output=True)
    head = subprocess.check_output(
      ["git", "-C", str(self.tasks), "rev-parse", "HEAD"],
      env=self.env)
    shutil.rmtree(remote)
    result = subprocess.run(
      [sys.executable, str(BIN / "agent-task"), "--agent", "agent-a",
       "sync"], env=self.env, capture_output=True, text=True)
    self.assertEqual(result.returncode, 1)
    self.assertIn("not appear to be a git repository", result.stderr)
    self.assertEqual(subprocess.check_output(
      ["git", "-C", str(self.tasks), "rev-parse", "HEAD"],
      env=self.env), head)

  def test_repo_rebind_old_path_inside_other_repo_is_gone(self):
    self.init()
    former = self.base / "former"
    destination = self.base / "destination"
    for root in (former, destination):
      root.mkdir()
      subprocess.run(["git", "-C", str(root), "init", "-q"],
                     env=self.env, check=True)
    old = former / "missing-child"
    old.mkdir()
    registry = self.base / "agent-kit" / "repo-keys.json"
    registry.parent.mkdir(exist_ok=True)
    registry.write_text(json.dumps({"keys": {"example": str(old)},
                                    "events": []}))
    self.run_cmd("agent-id", "--agent", "agent-a", "repo-rebind",
                 "example", destination)
    self.assertEqual(json.loads(registry.read_text())["keys"]["example"],
                     str(destination.resolve()))

  def test_rebind_refuses_registered_destination_and_config_conflict(self):
    self.init()
    first = self.base / "first"
    second = self.base / "second"
    for root in (first, second):
      root.mkdir()
      subprocess.run(["git", "-C", str(root), "init", "-q"],
                     env=self.env, check=True)
      (root / "README.md").write_text("example\n")
      subprocess.run(["git", "-C", str(root), "add", "README.md"],
                     env=self.env, check=True)
      subprocess.run(["git", "-C", str(root), "commit", "-qm", "Initial"],
                     env=self.env, check=True)
    self.run_cmd("agent-task", "--agent", "agent-a", "new",
                 "--title", "First", cwd=first)
    self.run_cmd("agent-task", "--agent", "agent-a", "new",
                 "--title", "Second", cwd=second)
    self.run_cmd("agent-id", "--agent", "agent-a", "repo-rebind",
                 "first", second, "--force", "owner instruction", code=2)
    cfg = self.base / "agent-kit" / "records.json"
    cfg.parent.mkdir(exist_ok=True)
    cfg.write_text(json.dumps({"repos": {str(second): "first"}}))
    self.run_cmd("agent-task", "--agent", "agent-a", "new",
                 "--title", "Conflict", cwd=second, code=2)

  def test_doctor_preserves_recursive_record_bytes(self):
    self.init()
    task = self.task()
    self.run_cmd("agent-task", "--agent", "agent-a", "claim", task)
    self.run_cmd("agent-task", "--agent", "agent-a", "close", task,
                 "cancelled", "--reason", "finished")
    def snapshot(root):
      return {str(path.relative_to(root)): path.read_bytes()
              for path in root.rglob("*") if path.is_file() and
              ".git" not in path.parts and path.name != ".records.lock"}
    before = snapshot(self.tasks)
    head = subprocess.check_output(
      ["git", "-C", str(self.tasks), "rev-parse", "HEAD"], env=self.env)
    self.run_cmd("agent-task", "doctor")
    self.assertEqual(snapshot(self.tasks), before)
    self.assertEqual(subprocess.check_output(
      ["git", "-C", str(self.tasks), "rev-parse", "HEAD"],
      env=self.env), head)
