"""Crash and transaction recovery regressions with real git processes."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from unittest import mock

from tests.agent_records_support import BIN, RecordsFixture


class RecoveryTest(RecordsFixture):
  def git(self, root, *args, check=True):
    return subprocess.run(["git", "-C", str(root), *args], env=self.env,
                          capture_output=True, text=True, check=check)

  def task(self, title="Example"):
    return self.run_cmd("agent-task", "--agent", "agent-a", "new",
                        "--title", title).strip()

  def ssh_signing(self, root, delay=1):
    key = self.base / "signing-key"
    subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "",
                    "-f", str(key)], check=True, capture_output=True)
    marker = self.base / "signing-started"
    done = self.base / "signing-done"
    wrapper = self.base / "ssh-sign"
    wrapper.write_text("#!/bin/sh\n: > \"$SIGN_MARKER\"\n"
                       "sleep " + str(delay) + "\n"
                       "ssh-keygen \"$@\"\n"
                       "status=$?\n: > \"$SIGN_DONE\"\nexit $status\n")
    wrapper.chmod(0o755)
    self.git(root, "config", "commit.gpgsign", "true")
    self.git(root, "config", "gpg.format", "ssh")
    self.git(root, "config", "user.signingkey", str(key) + ".pub")
    self.git(root, "config", "gpg.ssh.program", str(wrapper))
    return dict(self.env, SIGN_MARKER=str(marker), SIGN_DONE=str(done)), marker

  def test_killed_writer_git_child_cannot_outlive_lock(self):
    self.init()
    task = self.task("Signed")
    path = next(self.tasks.glob("T-0001-*.md"))
    self.git(self.tasks, "config", "core.fsmonitor", "true")
    env, marker = self.ssh_signing(self.tasks)
    proc = subprocess.Popen(
      [sys.executable, str(BIN / "agent-task"), "--agent", "agent-a",
       "claim", task], env=env, stdout=subprocess.PIPE,
      stderr=subprocess.PIPE)
    deadline = time.monotonic() + 5
    while not marker.exists() and time.monotonic() < deadline:
      time.sleep(0.02)
    self.assertTrue(marker.exists())
    proc.kill()
    proc.wait(timeout=2)
    proc.stdout.close()
    proc.stderr.close()
    deadline = time.monotonic() + 5
    while (not (self.base / "signing-done").exists() and
           time.monotonic() < deadline):
      time.sleep(0.02)
    self.assertTrue((self.base / "signing-done").exists())
    time.sleep(0.1)
    probe = subprocess.run(
      [sys.executable, "-c", "import fcntl, sys\n"
       "with open(sys.argv[1], 'r+b') as lock:\n"
       "  fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)\n",
       str(self.tasks / ".records.lock")], env=env, capture_output=True,
      text=True)
    self.assertEqual(probe.returncode, 0, probe.stderr)
    self.run_cmd("agent-task", "list", env=env, code=5)
    self.run_cmd("agent-task", "--agent", "agent-a", "recover", env=env)
    self.assertFalse((self.tasks / ".records-journal.json").exists())
    self.assertEqual(path.read_bytes(), self.git(
      self.tasks, "show", "HEAD:" + path.name).stdout.encode())
    self.assertFalse(self.git(self.tasks, "status", "--porcelain",
                              "--", path.name).stdout)

  def test_git_disables_daemons_and_inherits_locks_only_for_writes(self):
    sys.path.insert(0, str(BIN))
    try:
      import agent_records_core as core
      lock = os.open(str(self.base / "held.lock"),
                     os.O_CREAT | os.O_RDWR, 0o600)
      token = core.HELD_LOCKS.set((lock,))
      try:
        with mock.patch.object(core.subprocess, "Popen") as popen:
          popen.return_value.communicate.return_value = (b"", b"")
          popen.return_value.returncode = 0
          core.git(self.base, "status", "--porcelain")
          read_call = popen.call_args
          core.git(self.base, "add", "file")
          write_call = popen.call_args
      finally:
        core.HELD_LOCKS.reset(token)
        os.close(lock)
    finally:
      sys.path.remove(str(BIN))
    for call in (read_call, write_call):
      self.assertEqual(call.args[0][3:7],
                       ["-c", "core.fsmonitor=false", "-c", "gc.auto=0"])
    self.assertEqual(read_call.kwargs["pass_fds"], ())
    self.assertEqual(write_call.kwargs["pass_fds"], (lock,))

  def test_existing_index_lock_needs_explicit_recovery(self):
    self.init()
    task = self.task()
    path = next(self.tasks.glob("T-0001-*.md"))
    before = path.read_bytes()
    journal = {"id": "example", "operation": "Claim", "agent": "a",
               "head": self.git(self.tasks, "rev-parse", "HEAD").stdout.strip(),
               "status": "", "paths": {path.name: {
                 "pre": __import__("base64").b64encode(before).decode(),
                 "post": __import__("base64").b64encode(
                   before.replace(b"status: open", b"status: blocked")
                 ).decode()}}}
    (self.tasks / ".records-journal.json").write_text(json.dumps(journal))
    (self.tasks / ".git" / "index.lock").write_text("occupied")
    result = subprocess.run(
      [sys.executable, str(BIN / "agent-task"), "list"], env=self.env,
      capture_output=True, text=True)
    self.assertEqual(result.returncode, 5)
    self.assertIn("index.lock", result.stderr)

  def test_cross_close_transfer_recovers_after_staged_deletion(self):
    self.init()
    source = self.task("Source")
    target = self.task("Target")
    self.run_cmd("agent-task", "--agent", "agent-a", "claim", source)
    entry = self.run_cmd(
      "agent-changelog", "--agent", "agent-a", "new", "--scope",
      "example", "--slug", "scratch", "--kind", "scratch",
      "--location", "scratch", "--why", "needed",
      "--cleanup-when", "later", "--cleanup-how", "remove",
      "--task", source).strip()
    self.git(self.tasks, "config", "commit.gpgsign", "true")
    self.git(self.tasks, "config", "gpg.program", "/nonexistent")
    self.run_cmd("agent-task", "--agent", "agent-a", "close", source,
                 "cancelled", "--reason", "moved", "--transfer", target,
                 code=5)
    self.git(self.tasks, "config", "commit.gpgsign", "false")
    self.run_cmd("agent-task", "--agent", "agent-a", "recover")
    self.assertIn("tasks: " + target,
                  self.run_cmd("agent-changelog", "show", entry))
    self.assertFalse((self.tasks / ".records-journal.json").exists())

  def test_staged_post_state_is_reset_when_file_is_pre_state(self):
    self.init()
    task = self.task()
    path = next(self.tasks.glob("T-0001-*.md"))
    before = path.read_bytes()
    after = before.replace(b"status: open", b"status: blocked")
    path.write_bytes(after)
    self.git(self.tasks, "add", "--", path.name)
    path.write_bytes(before)
    import base64
    journal = {"id": "example", "operation": "Edit", "agent": "a",
               "head": self.git(self.tasks, "rev-parse", "HEAD").stdout.strip(),
               "status": "", "paths": {path.name: {
                 "pre": base64.b64encode(before).decode(),
                 "post": base64.b64encode(after).decode()}}}
    (self.tasks / ".records-journal.json").write_text(json.dumps(journal))
    self.run_cmd("agent-task", "--agent", "agent-a", "recover")
    self.assertFalse(self.git(self.tasks, "status", "--porcelain",
                              "--", path.name).stdout)

  def test_committed_post_state_restores_working_file(self):
    self.init()
    task = self.task()
    path = next(self.tasks.glob("T-0001-*.md"))
    before = path.read_bytes()
    after = before.replace(b"status: open", b"status: blocked")
    old = self.git(self.tasks, "rev-parse", "HEAD").stdout.strip()
    path.write_bytes(after)
    self.git(self.tasks, "add", path.name)
    self.git(self.tasks, "commit", "-qm", "Edit", "-m",
             "Records-Journal: exact-id")
    path.write_bytes(before)
    import base64
    journal = {"id": "exact-id", "operation": "Edit", "agent": "a",
               "head": old, "status": "", "paths": {path.name: {
                 "pre": base64.b64encode(before).decode(),
                 "post": base64.b64encode(after).decode()}}}
    (self.tasks / ".records-journal.json").write_text(json.dumps(journal))
    self.run_cmd("agent-task", "list", code=5)
    self.run_cmd("agent-task", "--agent", "agent-a", "recover")
    self.assertEqual(path.read_bytes(), after)
    self.assertFalse(self.git(self.tasks, "status", "--porcelain",
                              "--", path.name).stdout)

  def test_cross_rollback_resets_staged_post_with_pre_working_files(self):
    self.init()
    import base64
    sides = {}
    for label, root in (("tasks", self.tasks),
                        ("changelog", self.changes)):
      path = root / "README.md"
      before = path.read_bytes()
      after = before + b"post\n"
      path.write_bytes(after)
      self.git(root, "add", "README.md")
      path.write_bytes(before)
      head = self.git(root, "rev-parse", "HEAD").stdout.strip()
      sides[label] = {"head": head,
                      "status": "", "paths": {"README.md": {
                        "pre": base64.b64encode(before).decode(),
                        "post": base64.b64encode(after).decode()}}}
    journal = {"id": "cross-staged", "cross": True,
               "operation": "Cross edit", "agent": "a", **sides}
    (self.tasks / ".records-journal.json").write_text(json.dumps(journal))
    (self.changes / ".records-journal.json").write_text(json.dumps(
      {"id": journal["id"], "cross_stub": True,
       "changelog": sides["changelog"]}))
    self.run_cmd("agent-changelog", "list", code=5)
    self.run_cmd("agent-changelog", "--agent", "agent-a", "recover")
    for root in (self.tasks, self.changes):
      self.assertFalse(self.git(root, "status", "--porcelain",
                                "--", "README.md").stdout)

  def test_real_git_signing_timeout_leaves_explicit_index_lock(self):
    self.init()
    task = self.task("Timeout")
    # Margins tolerate a loaded machine: other git steps must finish within
    # the timeout, while the signer still outlasts it by a wide gap.
    env, marker = self.ssh_signing(self.tasks, delay=12)
    env["AGENT_RECORDS_GIT_TIMEOUT"] = "3"
    self.run_cmd("agent-task", "--agent", "agent-a", "claim", task,
                 env=env, code=5)
    self.assertTrue(marker.exists())
    index_lock = self.tasks / ".git" / "index.lock"
    self.assertTrue(index_lock.exists())
    result = subprocess.run(
      [sys.executable, str(BIN / "agent-task"), "list"], env=self.env,
      capture_output=True, text=True)
    self.assertEqual(result.returncode, 5)
    self.assertIn("index.lock", result.stderr)
    index_lock.unlink()
    self.run_cmd("agent-task", "--agent", "agent-a", "recover")

  def test_unexpected_journal_exception_is_single_line_exit_five(self):
    self.init()
    (self.tasks / ".records-journal.json").write_text("not json")
    result = subprocess.run(
      [sys.executable, str(BIN / "agent-task"), "list"], env=self.env,
      capture_output=True, text=True)
    self.assertEqual(result.returncode, 5)
    self.assertNotIn("Traceback", result.stderr)
    self.assertEqual(len(result.stderr.splitlines()), 1)
    explicit = dict(self.env)
    explicit.pop("AGENT_TASKS_DIR")
    explicit.pop("AGENT_CHANGELOG_DIR")
    result = subprocess.run(
      [sys.executable, str(BIN / "agent-task"),
       "--dir=" + str(self.tasks), "list"], env=explicit,
      capture_output=True, text=True)
    self.assertEqual(result.returncode, 5)

  def test_recovery_preserves_git_error_instead_of_lock_hint(self):
    self.init()
    source = self.task("Source")
    target = self.task("Target")
    self.run_cmd("agent-task", "--agent", "agent-a", "claim", source)
    self.run_cmd(
      "agent-changelog", "--agent", "agent-a", "new", "--scope",
      "example", "--slug", "scratch", "--kind", "scratch",
      "--location", "scratch", "--why", "needed",
      "--cleanup-when", "later", "--cleanup-how", "remove",
      "--task", source)
    self.git(self.tasks, "config", "commit.gpgsign", "true")
    self.git(self.tasks, "config", "gpg.program", "/nonexistent")
    self.run_cmd("agent-task", "--agent", "agent-a", "close", source,
                 "cancelled", "--reason", "moved", "--transfer", target,
                 code=5)
    result = subprocess.run(
      [sys.executable, str(BIN / "agent-task"), "--agent", "agent-a", "recover"], env=self.env,
      capture_output=True, text=True)
    self.assertEqual(result.returncode, 5)
    self.assertIn("/nonexistent", result.stderr)
    self.assertNotIn("pending recovery needs writable locks", result.stderr)

  def test_reader_waits_during_real_signed_tool_write(self):
    self.init()
    task = self.task("Signed")
    env, marker = self.ssh_signing(self.tasks, delay=1)
    proc = subprocess.Popen(
      [sys.executable, str(BIN / "agent-task"), "--agent", "agent-a",
       "claim", task], env=env, stdout=subprocess.PIPE,
      stderr=subprocess.PIPE)
    deadline = time.monotonic() + 5
    while not marker.exists() and time.monotonic() < deadline:
      time.sleep(0.02)
    self.assertTrue(marker.exists())
    start = time.monotonic()
    self.run_cmd("agent-task", "list", env=env)
    self.assertGreater(time.monotonic() - start, 0.5)
    proc.communicate(timeout=5)
    self.assertEqual(proc.returncode, 0)
