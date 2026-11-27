"""Watchers wait out a pending journal that another writer recovers."""

import base64
import json
from pathlib import Path
import subprocess
import sys
import time

from tests import test_agent_records_planning as planning
from tests.agent_records_support import BIN, RecordsFixture


class WatchPendingTest(RecordsFixture):
  task = planning.PlanningTest.task
  fields = planning.PlanningTest.fields
  snapshot = planning.PlanningTest.snapshot

  def setUp(self):
    super().setUp()
    self.init()

  def git(self, *args):
    return subprocess.check_output(
      ["git", "-C", str(self.tasks), *args], env=self.env).decode().strip()

  def cursor(self, output):
    return next(line.split("cursor: ", 1)[1] for line in output.splitlines()
                if line.startswith("cursor: "))

  def pending_edit(self, task):
    """Leave the journal and file a writer abandons mid-mutation."""
    path = next(self.tasks.glob(task + "-*.md"))
    pre = path.read_bytes()
    post = pre.replace(b"title: Example", b"title: Pending")
    journal = {"id": "example", "operation": "Edit", "agent": "agent-a",
               "head": self.git("rev-parse", "HEAD"), "status": "",
               "paths": {path.name: {
                 "pre": base64.b64encode(pre).decode(),
                 "post": base64.b64encode(post).decode()}}}
    (self.tasks / ".records-journal.json").write_text(json.dumps(journal))
    path.write_bytes(post)

  def start(self, name, *args):
    process = subprocess.Popen(
      [sys.executable, str(BIN / name), *args], cwd=self.base, env=self.env,
      text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    self.addCleanup(self.stop, process)
    return process

  def stop(self, process):
    if process.poll() is None:
      process.kill()
    process.communicate()

  def finish(self, process, code):
    try:
      stdout, stderr = process.communicate(timeout=15)
    except subprocess.TimeoutExpired:
      process.kill()
      stdout, stderr = process.communicate()
    self.assertEqual(process.returncode, code, (stdout, stderr))
    return stdout, stderr

  def still_waiting(self, process):
    time.sleep(1.5)
    if process.poll() is not None:
      self.fail(("exited early", process.returncode, process.communicate()))

  def entry(self):
    return self.run_cmd(
      "agent-changelog", "--agent", "agent-a", "new", "--scope", "example",
      "--slug", "scratch", "--kind", "scratch", "--location", "scratch",
      "--why", "example", "--cleanup-when", "later", "--cleanup-how",
      "remove").strip()

  def test_task_watch_resumes_after_next_writer_recovers(self):
    task = self.task()
    after = self.cursor(self.run_cmd("agent-task", "show", task))
    self.pending_edit(task)
    watch = self.start("agent-task", "watch", task, "--for", "agent-b",
                       "--after", after, "--until", "message",
                       "--timeout", "1", "--interval", "1")
    self.still_waiting(watch)
    # The next writer auto-recovers the abandoned journal before writing.
    self.run_cmd("agent-task", "--agent", "agent-c", "log", task,
                 "after recovery", "--to", "agent-b")
    stdout, stderr = self.finish(watch, 0)
    self.assertIn("after recovery", stdout)
    self.assertIn("cursor: 2:", stdout)
    self.assertNotIn("log rewritten", stdout)
    self.assertIn("pending journal", stderr)
    self.assertEqual(self.fields(task)["title"], "Example")

  def test_task_watch_until_closed_survives_transient_journal(self):
    task = self.task()
    self.run_cmd("agent-task", "--agent", "agent-a", "claim", task)
    self.pending_edit(task)
    watch = self.start("agent-task", "watch", task, "--until", "closed",
                       "--timeout", "20", "--interval", "1")
    self.still_waiting(watch)
    self.run_cmd("agent-task", "--agent", "agent-a", "close", task,
                 "cancelled", "--reason", "example")
    stdout, _ = self.finish(watch, 0)
    self.assertIn("cursor: ", stdout)

  def test_persistent_journal_exits_5_without_recovery(self):
    task = self.task()
    entry = self.entry()
    self.pending_edit(task)
    before = self.snapshot()
    commands = [
      ("agent-task", "watch", task, "--until", "closed", "--timeout", "0.1"),
      ("agent-task", "events", task, "--timeout", "0.1"),
      ("agent-changelog", "watch", entry, "--until", "closed",
       "--timeout", "0.1"),
    ]
    for command in commands:
      with self.subTest(command=command[:2]):
        started = time.monotonic()
        process = self.start(*command, "--pending-wait", "0.6")
        _, stderr = self.finish(process, 5)
        self.assertGreaterEqual(time.monotonic() - started, 0.6)
        self.assertIn("recover", stderr)
        self.assertEqual(before, self.snapshot())
    self.assertTrue((self.tasks / ".records-journal.json").exists())

  def test_unrecoverable_state_fails_without_waiting(self):
    task = self.task()
    self.pending_edit(task)
    lock = Path(self.git("rev-parse", "--absolute-git-dir")) / "index.lock"
    lock.write_text("")
    self.addCleanup(lock.unlink, missing_ok=True)
    started = time.monotonic()
    process = self.start("agent-task", "watch", task, "--until", "closed",
                         "--timeout", "30", "--pending-wait", "30")
    _, stderr = self.finish(process, 5)
    self.assertLess(time.monotonic() - started, 10)
    self.assertIn("index.lock", stderr)

  def test_events_resume_after_next_writer_recovers(self):
    task = self.task()
    first = json.loads(self.run_cmd("agent-task", "events", task))
    self.pending_edit(task)
    events = self.start("agent-task", "events", task, "--after",
                        json.dumps(first["cursor"]), "--timeout", "20",
                        "--interval", "1")
    self.still_waiting(events)
    self.run_cmd("agent-task", "--agent", "agent-c", "log", task,
                 "after recovery")
    stdout, _ = self.finish(events, 0)
    batch = json.loads(stdout)
    self.assertEqual(batch["rewritten"], [])
    self.assertIn("after recovery", batch["events"][0]["log"][-1])

  def test_changelog_watch_resumes_after_next_writer_recovers(self):
    task = self.task()
    entry = self.entry()
    shown = self.run_cmd("agent-changelog", "show", entry)
    self.pending_edit(task)
    watch = self.start("agent-changelog", "watch", entry, "--after",
                       self.cursor(shown), "--until", "closed",
                       "--timeout", "20", "--interval", "1")
    self.still_waiting(watch)
    self.run_cmd("agent-changelog", "--agent", "agent-a", "close", entry,
                 "--what", "removed")
    stdout, _ = self.finish(watch, 0)
    self.assertIn("status: closed", stdout)

  def test_pending_wait_must_not_be_negative(self):
    task = self.task()
    self.run_cmd("agent-task", "watch", task, "--until", "closed",
                 "--pending-wait", "-1", code=2)


if __name__ == "__main__":
  import unittest
  unittest.main()
