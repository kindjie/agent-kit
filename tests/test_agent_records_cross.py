"""Cross-repository ordering, recovery and concurrency coverage."""

from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
import sys
import time

from tests.agent_records_support import BIN, RecordsFixture


class CrossTest(RecordsFixture):
  def git(self, root, *args):
    return subprocess.run(["git", "-C", str(root), *args], env=self.env,
                          capture_output=True, text=True, check=True).stdout

  def task(self, title="Example"):
    return self.run_cmd("agent-task", "--agent", "agent-a", "new",
                        "--title", title).strip()

  def entry(self, *tasks):
    arguments = ["agent-changelog", "--agent", "agent-a", "new",
                 "--scope", "example", "--slug", "scratch",
                 "--kind", "scratch", "--location", "scratch",
                 "--why", "needed", "--cleanup-when", "later",
                 "--cleanup-how", "remove"]
    for task in tasks:
      arguments.extend(("--task", task))
    return self.run_cmd(*arguments).strip()

  def test_skipped_task_side_is_not_committed_during_recovery(self):
    self.init()
    before = (self.changes / "README.md").read_bytes()
    after = before + b"change\n"
    task_head = self.git(self.tasks, "rev-parse", "HEAD").strip()
    change_head = self.git(self.changes, "rev-parse", "HEAD").strip()
    ident = "example-skipped-side"
    journal = {"id": ident, "cross": True, "operation": "Cross edit",
               "agent": "agent-a",
               "tasks": {"head": task_head, "status": "", "paths": {},
                         "skipped": True},
               "changelog": {"head": change_head, "status": "",
                             "paths": {"README.md": {
                               "pre": base64.b64encode(before).decode(),
                               "post": base64.b64encode(after).decode()}}}}
    (self.tasks / ".records-journal.json").write_text(json.dumps(journal))
    (self.changes / ".records-journal.json").write_text(json.dumps(
      {"id": ident, "cross_stub": True, "changelog": journal["changelog"]}))
    (self.changes / "README.md").write_bytes(after)
    self.git(self.changes, "add", "README.md")
    self.git(self.changes, "commit", "-qm", "Cross edit", "-m",
             "Records-Journal: " + ident)
    self.run_cmd("agent-changelog", "list", code=5)
    self.run_cmd("agent-changelog", "--agent", "agent-a", "recover")
    self.assertEqual(self.git(self.tasks, "rev-parse", "HEAD").strip(),
                     task_head)
    self.assertFalse((self.tasks / ".records-journal.json").exists())

  def test_journal_id_prefix_does_not_match_longer_commit_id(self):
    self.init()
    before = (self.tasks / "README.md").read_bytes()
    after = before + b"changed\n"
    old = self.git(self.tasks, "rev-parse", "HEAD").strip()
    (self.tasks / "README.md").write_bytes(after)
    self.git(self.tasks, "add", "README.md")
    self.git(self.tasks, "commit", "-qm", "Edit", "-m",
             "Records-Journal: example-longer")
    journal = {"id": "example", "operation": "Edit", "agent": "a",
               "head": old, "status": "", "paths": {"README.md": {
                 "pre": base64.b64encode(before).decode(),
                 "post": base64.b64encode(after).decode()}}}
    (self.tasks / ".records-journal.json").write_text(json.dumps(journal))
    self.run_cmd("agent-task", "list", code=5)
    self.assertTrue((self.tasks / ".records-journal.json").exists())

  def test_diverged_head_keeps_journal(self):
    self.init()
    other = self.base / "other"
    other.mkdir()
    self.git(other, "init", "-q")
    (other / "file").write_text("data\n")
    self.git(other, "add", "file")
    self.git(other, "commit", "-qm", "Unrelated")
    wrong_head = self.git(other, "rev-parse", "HEAD").strip()
    before = (self.tasks / "README.md").read_bytes()
    journal = {"id": "example", "operation": "Edit", "agent": "a",
               "head": wrong_head, "status": "", "paths": {"README.md": {
                 "pre": base64.b64encode(before).decode(),
                 "post": base64.b64encode(before + b"post\n").decode()}}}
    (self.tasks / ".records-journal.json").write_text(json.dumps(journal))
    self.run_cmd("agent-task", "list", code=5)
    self.assertTrue((self.tasks / ".records-journal.json").exists())
    self.assertEqual((self.tasks / "README.md").read_bytes(), before)

  def test_explicit_recovery_rolls_forward_failed_task_commit(self):
    self.init()
    source = self.task("Source")
    target = self.task("Target")
    self.run_cmd("agent-task", "--agent", "agent-a", "claim", source)
    entry = self.entry(source)
    self.git(self.tasks, "config", "commit.gpgsign", "true")
    self.git(self.tasks, "config", "gpg.program", "/nonexistent")
    self.run_cmd("agent-task", "--agent", "agent-a", "close", source,
                 "cancelled", "--reason", "moved", "--transfer", target,
                 code=5)
    self.git(self.tasks, "config", "commit.gpgsign", "false")
    self.run_cmd("agent-changelog", "list", code=5)
    self.run_cmd("agent-changelog", "--agent", "agent-a", "recover")
    self.assertIn("tasks: " + target,
                  self.run_cmd("agent-changelog", "show", entry))

  def test_transfer_with_two_tasks_needs_source(self):
    self.init()
    first = self.task("First")
    second = self.task("Second")
    target = self.task("Target")
    entry = self.entry(first, second)
    self.run_cmd("agent-changelog", "--agent", "agent-a", "update",
                 entry, "--transfer", target, "--reason", "move", code=2)
    self.run_cmd("agent-changelog", "--agent", "agent-a", "update",
                 entry, "--transfer", target, "--from-task", first,
                 "--reason", "move")

  def test_new_entry_racing_close_cannot_orphan_record(self):
    self.init()
    lock_script = (
      "import fcntl, sys\n"
      "with open(sys.argv[1], 'r+b') as stream:\n"
      "  fcntl.flock(stream, fcntl.LOCK_EX)\n"
      "  print('READY', flush=True)\n"
      "  sys.stdin.readline()\n")
    for turn in range(4):
      task = self.task("Race " + str(turn))
      self.run_cmd("agent-task", "--agent", "agent-a", "claim", task)
      slug = "race-" + str(turn)
      entry_command = [sys.executable, str(BIN / "agent-changelog"),
                       "--agent", "agent-a", "new", "--scope", "example",
                       "--slug", slug, "--kind", "scratch",
                       "--location", "scratch", "--why", "needed",
                       "--cleanup-when", "later", "--cleanup-how", "remove",
                       "--task", task]
      close_command = [sys.executable, str(BIN / "agent-task"),
                       "--agent", "agent-a", "close", task, "cancelled",
                       "--reason", "done"]
      holder = subprocess.Popen(
        [sys.executable, "-c", lock_script,
         str(self.tasks / ".records.lock")], env=self.env,
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
      self.assertEqual(holder.stdout.readline().strip(), "READY")
      commands = ((entry_command, close_command) if turn % 2 == 0 else
                  (close_command, entry_command))
      processes = [subprocess.Popen(command, env=self.env, cwd=self.base,
                                    stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE)
                   for command in commands]
      try:
        time.sleep(0.15)
        self.assertTrue(all(process.poll() is None
                            for process in processes))
      finally:
        holder.stdin.write("release\n")
        holder.stdin.flush()
        holder.communicate(timeout=2)
      results = [process.communicate(timeout=8) for process in processes]
      self.assertEqual(sorted(process.returncode for process in processes),
                       [0, 1], results)
      archive = list((self.tasks / "archive").glob(task + "-*.md"))
      entries = list((self.changes / "entries").glob("*" + slug + ".md"))
      self.assertEqual(bool(archive), not bool(entries), results)
      if entries:
        self.assertIn("tasks: " + task, entries[0].read_text())

  def test_mutation_push_finishes_before_sync_pull(self):
    self.init()
    remote = self.base / "remote.git"
    subprocess.run(["git", "init", "--bare", "-q", str(remote)],
                   env=self.env, check=True)
    self.git(self.tasks, "remote", "add", "origin", str(remote))
    self.git(self.tasks, "push", "-qu", "origin", "HEAD")
    cfg = self.base / "agent-kit" / "records.json"
    cfg.parent.mkdir(exist_ok=True)
    cfg.write_text(json.dumps({"push": True}))
    fake = self.base / "fake-bin"
    fake.mkdir()
    wrapper = fake / "git"
    wrapper.write_text(
      "#!/bin/sh\n"
      "if [ \"$7\" = push ]; then\n"
      "  : > \"$PUSH_STARTED\"\n  sleep 1\n"
      "  : > \"$PUSH_FINISHED\"\nfi\n"
      "if [ \"$7\" = pull ] && [ ! -e \"$PUSH_FINISHED\" ]; then\n"
      "  : > \"$OVERLAP\"\nfi\n"
      "exec \"$REAL_GIT\" \"$@\"\n")
    wrapper.chmod(0o755)
    started = self.base / "push-started"
    finished = self.base / "push-finished"
    overlap = self.base / "overlap"
    env = dict(self.env, PATH=str(fake) + os.pathsep + self.env["PATH"],
               REAL_GIT=shutil.which("git"), PUSH_STARTED=str(started),
               PUSH_FINISHED=str(finished), OVERLAP=str(overlap))
    new = subprocess.Popen(
      [sys.executable, str(BIN / "agent-task"), "--agent", "agent-a",
       "new", "--title", "Push lock"], env=env, cwd=self.base,
      stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    deadline = time.monotonic() + 5
    while not started.exists() and time.monotonic() < deadline:
      time.sleep(0.02)
    self.assertTrue(started.exists())
    sync = subprocess.Popen(
      [sys.executable, str(BIN / "agent-task"), "--agent", "agent-a",
       "sync"], env=env, cwd=self.base,
      stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    new.communicate(timeout=8)
    sync.communicate(timeout=8)
    self.assertEqual((new.returncode, sync.returncode), (0, 0))
    self.assertFalse(overlap.exists())
