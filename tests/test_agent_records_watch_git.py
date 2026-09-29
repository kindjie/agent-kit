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


class WatchGitTest(RecordsFixture):

  def test_watch_immediate_and_timeout(self):
    self.init()
    task = self.run_cmd("agent-task", "--agent", "agent-a", "new",
                        "--title", "Watch").strip()
    output = self.run_cmd("agent-task", "watch", task, "--until",
                          "status=open", "--timeout", "0.1",
                          "--interval", "1")
    self.assertEqual(sum(line.startswith("cursor: ") for line in
                         output.splitlines()), 1)
    self.run_cmd("agent-task", "watch", task, "--until", "closed",
                 "--timeout", "0.1", "--interval", "1", code=4)

  def test_watch_message_cursor_and_log_text(self):
    self.init()
    task = self.run_cmd("agent-task", "--agent", "agent-a", "new",
                        "--title", "Message").strip()
    shown = self.run_cmd("agent-task", "show", task)
    cursor = next(line.split("cursor: ", 1)[1] for line in
                  shown.splitlines() if line.startswith("cursor: "))
    self.run_cmd("agent-task", "--agent", "agent-a", "log", task,
                 "A -> B -- stays text", "--to", "agent-b,agent-c")
    self.run_cmd("agent-task", "lint")
    watched = self.run_cmd("agent-task", "watch", task, "--for",
                           "agent-b", "--after", cursor, "--until",
                           "message", "--timeout", "0.2", "--interval", "1")
    self.assertIn("A -> B -- stays text", watched)
    self.assertIn("cursor: 2:", watched)

  def test_changelog_watch_resumes_from_hash(self):
    self.init()
    entry = self.run_cmd(
      "agent-changelog", "--agent", "agent-a", "new", "--scope",
      "example", "--slug", "scratch", "--kind", "scratch",
      "--location", "scratch", "--why", "example",
      "--cleanup-when", "later", "--cleanup-how", "remove",
    ).strip()
    shown = self.run_cmd("agent-changelog", "show", entry)
    cursor = next(line.split("cursor: ", 1)[1] for line in
                  shown.splitlines() if line.startswith("cursor: "))
    self.run_cmd("agent-changelog", "--agent", "agent-a", "close",
                 entry, "--what", "removed")
    immediate = self.run_cmd("agent-changelog", "watch", entry,
                             "--until", "closed", "--timeout", "0.1")
    self.assertEqual(sum(line.startswith("cursor: ") for line in
                         immediate.splitlines()), 1)
    watched = self.run_cmd("agent-changelog", "watch", entry,
                           "--after", cursor, "--until", "change",
                           "--timeout", "0.2", "--interval", "1")
    self.assertIn("status: closed", watched)

  def test_watch_rewrite_and_archive_move(self):
    self.init()
    task = self.run_cmd("agent-task", "--agent", "agent-a", "new",
                        "--title", "Rewrite").strip()
    self.run_cmd("agent-task", "--agent", "agent-a", "claim", task)
    shown = self.run_cmd("agent-task", "show", task)
    old_cursor = next(line.split("cursor: ", 1)[1] for line in
                      shown.splitlines() if line.startswith("cursor: "))
    path = next(self.tasks.glob("T-0001-*.md"))
    path.write_bytes(path.read_bytes().replace(b"Created task",
                                               b"Edited log"))
    subprocess.check_call(["git", "-C", str(self.tasks), "add", path.name],
                          env=self.env)
    subprocess.check_call(["git", "-C", str(self.tasks), "commit", "-qm",
                           "Edit log"], env=self.env)
    watched = self.run_cmd("agent-task", "watch", task, "--after",
                           old_cursor, "--until", "change",
                           "--timeout", "0.2", "--interval", "1", code=5)
    self.assertIn("log rewritten since cursor", watched)
    self.assertIn("Edited log", watched)
    new_cursor = next(line.split("cursor: ", 1)[1] for line in
                      watched.splitlines() if line.startswith("cursor: "))
    self.run_cmd("agent-task", "--agent", "agent-a", "close", task,
                 "cancelled", "--reason", "stopped")
    watched = self.run_cmd("agent-task", "watch", task, "--after",
                           new_cursor, "--until", "closed",
                           "--timeout", "0.2", "--interval", "1")
    self.assertIn("Closed cancelled", watched)

  def test_unlocked_watch_only_satisfies_relative_conditions(self):
    self.init()
    task = self.run_cmd("agent-task", "--agent", "agent-a", "new",
                        "--title", "Unlocked").strip()
    shown = self.run_cmd("agent-task", "show", task)
    cursor = next(line.split("cursor: ", 1)[1] for line in
                  shown.splitlines() if line.startswith("cursor: "))
    self.run_cmd("agent-task", "--agent", "agent-a", "log", task,
                 "Message", "--to", "agent-b")
    lock = self.tasks / ".records.lock"
    lock.chmod(0)
    try:
      result = self.run_cmd("agent-task", "--unlocked", "watch", task,
                            "--for", "agent-b", "--after", cursor,
                            "--until", "message", "--timeout", "0.2",
                            "--interval", "1")
      self.assertIn("UNVERIFIED", result)
      self.run_cmd("agent-task", "--unlocked", "watch", task,
                   "--until", "status=open", "--timeout", "0.1",
                   "--interval", "1", code=4)
    finally:
      lock.chmod(0o600)

  def test_sync_needs_upstream(self):
    self.init()
    untracked = self.tasks / "untracked.txt"
    untracked.write_text("pending\n")
    self.run_cmd("agent-task", "--agent", "agent-a", "sync", code=1)
    untracked.unlink()
    self.run_cmd("agent-task", "--agent", "agent-a", "sync", code=2)

  def test_sync_push_policy_and_rejection(self):
    self.init()
    remote = self.base / "remote.git"
    subprocess.check_call(["git", "init", "--bare", "-q", str(remote)],
                          env=self.env)
    subprocess.check_call(["git", "-C", str(self.tasks), "remote", "add",
                           "origin", str(remote)], env=self.env)
    subprocess.check_call(["git", "-C", str(self.tasks), "push", "-qu",
                           "origin", "HEAD"], env=self.env)
    old_remote = subprocess.check_output(
      ["git", "--git-dir", str(remote), "rev-parse", "HEAD"],
      env=self.env).strip()
    self.run_cmd("agent-task", "--agent", "agent-a", "new",
                 "--title", "Local only")
    self.run_cmd("agent-task", "--agent", "agent-a", "sync")
    self.assertEqual(subprocess.check_output(
      ["git", "--git-dir", str(remote), "rev-parse", "HEAD"],
      env=self.env).strip(), old_remote)
    hook = remote / "hooks" / "pre-receive"
    hook.write_text("#!/bin/sh\nexit 1\n")
    hook.chmod(0o755)
    cfg = self.base / "agent-kit" / "records.json"
    cfg.parent.mkdir()
    cfg.write_text(json.dumps({"push": True}))
    self.run_cmd("agent-task", "--agent", "agent-a", "sync", code=3)
    self.assertEqual(subprocess.check_output(
      ["git", "--git-dir", str(remote), "rev-parse", "HEAD"],
      env=self.env).strip(), old_remote)

  def test_push_policy_is_per_directory(self):
    self.init()
    remotes = {}
    for name, root in (("tasks", self.tasks), ("changes", self.changes)):
      remote = self.base / (name + ".git")
      subprocess.check_call(["git", "init", "--bare", "-q", str(remote)],
                            env=self.env)
      subprocess.check_call(["git", "-C", str(root), "remote", "add",
                             "origin", str(remote)], env=self.env)
      subprocess.check_call(["git", "-C", str(root), "push", "-qu",
                             "origin", "HEAD"], env=self.env)
      remotes[name] = remote
    subprocess.check_call(["git", "-C", str(self.tasks), "remote", "remove",
                           "origin"], env=self.env)
    cfg = self.base / "agent-kit" / "records.json"
    cfg.parent.mkdir()
    cfg.write_text(json.dumps({"push": {"changelog": True}}))

    def remote_head(name):
      return subprocess.check_output(
        ["git", "--git-dir", str(remotes[name]), "rev-parse", "HEAD"],
        env=self.env).strip()

    self.run_cmd("agent-task", "--agent", "agent-a", "new",
                 "--title", "No remote needed")
    old = remote_head("changes")
    self.run_cmd("agent-changelog", "--agent", "agent-a", "new",
                 "--scope", "example-repo", "--slug", "pushed",
                 "--kind", "scratch", "--location", "/path/to/scratch",
                 "--why", "test", "--cleanup-when", "never",
                 "--cleanup-how", "nothing")
    self.assertNotEqual(remote_head("changes"), old)
    for bad in ({"push": {"tasks": "yes"}}, {"push": {"other": True}},
                {"push": "true"}):
      cfg.write_text(json.dumps(bad))
      self.run_cmd("agent-task", "--agent", "agent-a", "list", code=2)

  def test_sync_rebase_conflict_aborts_cleanly(self):
    self.init()
    remote = self.base / "remote.git"
    clone = self.base / "other"
    subprocess.check_call(["git", "init", "--bare", "-q", str(remote)],
                          env=self.env)
    subprocess.check_call(["git", "-C", str(self.tasks), "remote", "add",
                           "origin", str(remote)], env=self.env)
    subprocess.check_call(["git", "-C", str(self.tasks), "push", "-qu",
                           "origin", "HEAD"], env=self.env)
    subprocess.check_call(["git", "clone", "-q", str(remote), str(clone)],
                          env=self.env)
    (self.tasks / "README.md").write_text("local change\n")
    subprocess.check_call(["git", "-C", str(self.tasks), "add", "README.md"],
                          env=self.env)
    subprocess.check_call(["git", "-C", str(self.tasks), "commit", "-qm",
                           "Local change"], env=self.env)
    (clone / "README.md").write_text("remote change\n")
    subprocess.check_call(["git", "-C", str(clone), "add", "README.md"],
                          env=self.env)
    subprocess.check_call(["git", "-C", str(clone), "commit", "-qm",
                           "Remote change"], env=self.env)
    subprocess.check_call(["git", "-C", str(clone), "push", "-q"],
                          env=self.env)
    old_head = subprocess.check_output(
      ["git", "-C", str(self.tasks), "rev-parse", "HEAD"],
      env=self.env).strip()
    self.run_cmd("agent-task", "--agent", "agent-a", "sync", code=5)
    self.assertEqual(subprocess.check_output(
      ["git", "-C", str(self.tasks), "rev-parse", "HEAD"],
      env=self.env).strip(), old_head)
    self.assertEqual((self.tasks / "README.md").read_text(), "local change\n")

  def test_git_timeout_kills_process_group(self):
    self.init()
    fake_bin = self.base / "fake-bin"
    fake_bin.mkdir()
    wrapper = fake_bin / "git"
    wrapper.write_text(
      "#!/bin/sh\n"
      "if [ \"$7\" = commit ]; then\n"
      "  ( sleep 8; printf leaked > \"$MARKER\" ) &\n"
      "  sleep 60\n"
      "fi\n"
      "exec \"$REAL_GIT\" \"$@\"\n")
    wrapper.chmod(0o755)
    marker = self.base / "leaked"
    env = dict(self.env, PATH=str(fake_bin) + os.pathsep + self.env["PATH"],
               REAL_GIT=shutil.which("git"), MARKER=str(marker),
               # The group is killed at 5 s, before the 8 s leak; other git
               # calls keep headroom on a busy machine.
               AGENT_RECORDS_GIT_TIMEOUT="5")
    started = time.monotonic()
    self.run_cmd("agent-task", "--agent", "agent-a", "new", "--title",
                 "Timeout", env=env, code=1)
    time.sleep(max(0, 8.5 - (time.monotonic() - started)))
    self.assertFalse(marker.exists())
    self.assertFalse(list(self.tasks.glob("T-*.md")))
    self.assertEqual((self.tasks / ".next-id").read_text(), "1\n")
