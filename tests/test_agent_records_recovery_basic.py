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


class RecoveryBasicTest(RecordsFixture):

  def test_changelog_stub_without_tasks_needs_recovery(self):
    self.run_cmd("agent-changelog", "init", self.changes)
    self.env["AGENT_CHANGELOG_DIR"] = str(self.changes)
    stub = self.changes / ".records-journal.json"
    stub.write_text(json.dumps({"id": "example", "cross_stub": True}))
    self.run_cmd("agent-changelog", "list", code=5)
    self.assertTrue(stub.exists())

  def test_hook_and_signing_failure_leave_record_unchanged(self):
    self.init()
    hook = self.tasks / ".git" / "hooks" / "pre-commit"
    hook.write_text("#!/bin/sh\nexit 0\n")
    hook.chmod(0o755)
    self.run_cmd("agent-task", "--agent", "agent-a", "new",
                 "--title", "Blocked", code=2)
    hook.unlink()
    empty = self.base / "empty-hooks"
    empty.mkdir()
    subprocess.check_call(["git", "-C", str(self.tasks), "config",
                           "core.hooksPath", str(empty)], env=self.env)
    subprocess.check_call(["git", "-C", str(self.tasks), "config",
                           "commit.gpgsign", "true"], env=self.env)
    subprocess.check_call(["git", "-C", str(self.tasks), "config",
                           "gpg.program", "/does/not/exist"], env=self.env)
    self.run_cmd("agent-task", "--agent", "agent-a", "new",
                 "--title", "Blocked", code=1)
    self.assertFalse(list(self.tasks.glob("T-*.md")))
    self.assertEqual((self.tasks / ".next-id").read_text(), "1\n")

  def test_killed_writer_recovered_by_read(self):
    self.init()
    task = self.run_cmd("agent-task", "--agent", "agent-a", "new",
                        "--title", "Original").strip()
    path = next(self.tasks.glob("T-0001-*.md"))
    before = path.read_bytes()
    script = """
import sys, time
from pathlib import Path
import agent_records_core as core
root = Path(sys.argv[1])
path = sys.argv[2]
original = core.git
def pause(repo, *args, **kwargs):
  if args and args[0] == 'commit':
    print('READY', flush=True)
    time.sleep(60)
  return original(repo, *args, **kwargs)
core.git = pause
with core.locks([root], [root], 1, 'agent-a', 'test'):
  core.mutate(root, {path: (root / path).read_bytes().replace(
    b'title: Original', b'title: Pending')}, 'Edit task', 'agent-a')
"""
    env = dict(self.env, PYTHONPATH=str(BIN))
    process = subprocess.Popen(
      [sys.executable, "-c", script, str(self.tasks), path.name],
      cwd=self.base, env=env, stdout=subprocess.PIPE, text=True)
    self.assertEqual(process.stdout.readline().strip(), "READY")
    process.kill()
    process.wait(timeout=2)
    process.stdout.close()
    self.assertNotEqual(path.read_bytes(), before)
    self.run_cmd("agent-task", "doctor", code=5)
    self.assertTrue((self.tasks / ".records-journal.json").exists())
    self.assertIn(task, self.run_cmd("agent-task", "list"))
    self.assertEqual(path.read_bytes(), before)
    self.assertFalse((self.tasks / ".records-journal.json").exists())

  def test_conflicting_recovery_needs_discard(self):
    self.init()
    self.run_cmd("agent-task", "--agent", "agent-a", "new",
                 "--title", "Original")
    path = next(self.tasks.glob("T-0001-*.md"))
    before = path.read_bytes()
    after = before.replace(b"title: Original", b"title: Pending")
    head = subprocess.check_output(
      ["git", "-C", str(self.tasks), "rev-parse", "HEAD"],
      env=self.env).decode().strip()
    journal = {"id": "example", "operation": "Edit task", "agent": "agent-a",
               "head": head, "status": "", "paths": {path.name: {
                 "pre": base64.b64encode(before).decode(),
                 "post": base64.b64encode(after).decode()}}}
    (self.tasks / ".records-journal.json").write_text(json.dumps(journal))
    path.write_bytes(before.replace(b"title: Original", b"title: Manual"))
    self.run_cmd("agent-task", "list", code=5)
    self.run_cmd("agent-task", "--agent", "agent-a", "recover",
                 "--discard", path.name)
    self.assertEqual(path.read_bytes(), before)

  def test_recover_keep_commits_valid_hand_edit(self):
    self.init()
    self.run_cmd("agent-task", "--agent", "agent-a", "new",
                 "--title", "Original")
    path = next(self.tasks.glob("T-0001-*.md"))
    before = path.read_bytes()
    after = before.replace(b"title: Original", b"title: Pending")
    head = subprocess.check_output(
      ["git", "-C", str(self.tasks), "rev-parse", "HEAD"],
      env=self.env).decode().strip()
    journal = {"id": "example", "operation": "Edit task", "agent": "agent-a",
               "head": head, "status": "", "paths": {path.name: {
                 "pre": base64.b64encode(before).decode(),
                 "post": base64.b64encode(after).decode()}}}
    (self.tasks / ".records-journal.json").write_text(json.dumps(journal))
    path.write_bytes(before.replace(b"title: Original", b"title: Kept"))
    self.run_cmd("agent-task", "--agent", "agent-a", "recover",
                 "--keep", path.name)
    self.assertIn(b"title: Kept", path.read_bytes())
    self.assertFalse((self.tasks / ".records-journal.json").exists())
    self.run_cmd("agent-task", "lint")

  def test_permission_denied_preserves_records(self):
    self.init()
    before = (self.tasks / ".next-id").read_bytes()
    head = subprocess.check_output(
      ["git", "-C", str(self.tasks), "rev-parse", "HEAD"],
      env=self.env).strip()
    self.tasks.chmod(0o555)
    try:
      self.run_cmd("agent-task", "--agent", "agent-a", "new",
                   "--title", "Denied", code=1)
    finally:
      self.tasks.chmod(0o755)
    self.assertEqual((self.tasks / ".next-id").read_bytes(), before)
    self.assertEqual(subprocess.check_output(
      ["git", "-C", str(self.tasks), "rev-parse", "HEAD"],
      env=self.env).strip(), head)

  def test_cross_journal_recovers_on_each_side_of_first_commit(self):
    for pause_side in ("changes", "tasks", "after-tasks"):
      with self.subTest(pause_side=pause_side):
        self.init() if not self.tasks.exists() else None
        task_before = (self.tasks / "README.md").read_bytes()
        change_before = (self.changes / "README.md").read_bytes()
        script = """
import sys, time
from pathlib import Path
import agent_records_core as core
tasks, changes = map(Path, sys.argv[1:3])
pause_side = sys.argv[3]
original = core.git
def pause(repo, *args, **kwargs):
  if args and args[0] == 'commit' and pause_side == 'after-tasks' and (
      repo == tasks):
    result = original(repo, *args, **kwargs)
    print('READY', flush=True)
    time.sleep(60)
    return result
  if args and args[0] == 'commit' and (
      (pause_side == 'tasks' and repo == tasks) or
      (pause_side == 'changes' and repo == changes)):
    print('READY', flush=True)
    time.sleep(60)
  return original(repo, *args, **kwargs)
core.git = pause
with core.locks([tasks, changes], [tasks, changes], 1, 'agent-a', 'cross'):
  core.mutate_cross(tasks, changes,
    {'README.md': (tasks / 'README.md').read_bytes() + b'task post\\n'},
    {'README.md': (changes / 'README.md').read_bytes() + b'change post\\n'},
    'Cross edit', 'agent-a')
"""
        env = dict(self.env, PYTHONPATH=str(BIN))
        process = subprocess.Popen(
          [sys.executable, "-c", script, str(self.tasks), str(self.changes),
           pause_side], cwd=self.base, env=env, stdout=subprocess.PIPE,
          stderr=subprocess.PIPE, text=True)
        self.assertEqual(process.stdout.readline().strip(), "READY")
        process.kill()
        process.wait(timeout=2)
        process.stdout.close()
        process.stderr.close()
        if pause_side == "after-tasks":
          (self.changes / ".records-journal.json").unlink()
        self.run_cmd("agent-task", "list")
        if pause_side in ("tasks", "after-tasks"):
          self.assertEqual((self.tasks / "README.md").read_bytes(),
                           task_before + b"task post\n")
          self.assertEqual((self.changes / "README.md").read_bytes(),
                           change_before + b"change post\n")
        else:
          self.assertEqual((self.tasks / "README.md").read_bytes(),
                           task_before)
          self.assertEqual((self.changes / "README.md").read_bytes(),
                           change_before)
        self.assertFalse((self.tasks / ".records-journal.json").exists())
        self.assertFalse((self.changes / ".records-journal.json").exists())

  def test_impossible_cross_order_is_left_for_reconciliation(self):
    self.init()
    task_head = subprocess.check_output(
      ["git", "-C", str(self.tasks), "rev-parse", "HEAD"],
      env=self.env).decode().strip()
    change_head = subprocess.check_output(
      ["git", "-C", str(self.changes), "rev-parse", "HEAD"],
      env=self.env).decode().strip()
    task_pre = (self.tasks / "README.md").read_bytes()
    change_pre = (self.changes / "README.md").read_bytes()
    task_post = task_pre + b"task post\n"
    change_post = change_pre + b"change post\n"
    ident = "11111111-2222-4333-8444-555555555555"
    (self.tasks / "README.md").write_bytes(task_post)
    subprocess.check_call(["git", "-C", str(self.tasks), "add", "README.md"],
                          env=self.env)
    subprocess.check_call(
      ["git", "-C", str(self.tasks), "commit", "-qm", "Cross edit",
       "-m", "Records-Journal: " + ident], env=self.env)
    def side(head, before, after):
      return {"head": head, "status": "", "paths": {"README.md": {
        "pre": base64.b64encode(before).decode(),
        "post": base64.b64encode(after).decode()}}}
    journal = {"id": ident, "cross": True, "operation": "Cross edit",
               "agent": "agent-a", "tasks": side(task_head, task_pre,
                                                task_post),
               "changelog": side(change_head, change_pre, change_post)}
    (self.tasks / ".records-journal.json").write_text(json.dumps(journal))
    (self.changes / ".records-journal.json").write_text(json.dumps(
      {"id": ident, "cross_stub": True,
       "changelog": journal["changelog"]}))
    self.run_cmd("agent-task", "list", code=5)
    self.assertEqual((self.tasks / "README.md").read_bytes(), task_post)
    self.assertEqual((self.changes / "README.md").read_bytes(), change_pre)
    self.assertTrue((self.tasks / ".records-journal.json").exists())

  def test_cross_recover_keep_and_discard_hand_edits(self):
    self.init()

    def prepare(tag):
      task_head = subprocess.check_output(
        ["git", "-C", str(self.tasks), "rev-parse", "HEAD"],
        env=self.env).decode().strip()
      change_head = subprocess.check_output(
        ["git", "-C", str(self.changes), "rev-parse", "HEAD"],
        env=self.env).decode().strip()
      task_pre = (self.tasks / "README.md").read_bytes()
      change_pre = (self.changes / "README.md").read_bytes()
      task_post = task_pre + ("task " + tag + "\n").encode()
      change_post = change_pre + ("change " + tag + "\n").encode()
      manual = change_pre + ("manual " + tag + "\n").encode()
      def side(head, before, after):
        return {"head": head, "status": "", "paths": {"README.md": {
          "pre": base64.b64encode(before).decode(),
          "post": base64.b64encode(after).decode()}}}
      ident = "11111111-2222-4333-8444-" + tag.zfill(12)
      journal = {"id": ident, "cross": True, "operation": "Cross edit",
                 "agent": "agent-a", "tasks": side(task_head, task_pre,
                                                  task_post),
                 "changelog": side(change_head, change_pre, change_post)}
      (self.tasks / ".records-journal.json").write_text(json.dumps(journal))
      (self.changes / ".records-journal.json").write_text(json.dumps(
        {"id": ident, "cross_stub": True,
         "changelog": journal["changelog"]}))
      (self.tasks / "README.md").write_bytes(task_post)
      (self.changes / "README.md").write_bytes(manual)
      return task_pre, change_pre, task_post, manual

    _, _, task_post, manual = prepare("1")
    self.run_cmd("agent-task", "--agent", "agent-a", "recover",
                 "--keep", "changelog:README.md")
    self.assertEqual((self.tasks / "README.md").read_bytes(), task_post)
    self.assertEqual((self.changes / "README.md").read_bytes(), manual)
    self.assertFalse((self.tasks / ".records-journal.json").exists())
    task_pre, change_pre, _, _ = prepare("2")
    self.run_cmd("agent-changelog", "--agent", "agent-a", "recover",
                 "--discard", "changelog:README.md")
    self.assertEqual((self.tasks / "README.md").read_bytes(), task_pre)
    self.assertEqual((self.changes / "README.md").read_bytes(), change_pre)
    self.assertFalse((self.tasks / ".records-journal.json").exists())

  def test_unjournaled_edit_during_failure_needs_recovery(self):
    self.init()
    fake_bin = self.base / "fake-bin"
    fake_bin.mkdir()
    wrapper = fake_bin / "git"
    wrapper.write_text(
      "#!/bin/sh\n"
      "if [ \"$7\" = commit ]; then\n"
      "  printf external > \"$EXTERNAL\"\n"
      "  exit 1\n"
      "fi\n"
      "exec \"$REAL_GIT\" \"$@\"\n")
    wrapper.chmod(0o755)
    external = self.tasks / "external.txt"
    env = dict(self.env, PATH=str(fake_bin) + os.pathsep + self.env["PATH"],
               REAL_GIT=shutil.which("git"), EXTERNAL=str(external))
    self.run_cmd("agent-task", "--agent", "agent-a", "new", "--title",
                 "Failure", env=env, code=5)
    self.assertTrue(external.exists())
    self.assertEqual((self.tasks / ".next-id").read_text(), "1\n")
    self.assertTrue((self.tasks / ".records-journal.json").exists())

  def test_commit_excludes_unrelated_staged_file(self):
    self.init()
    unrelated = self.tasks / "README.md"
    unrelated.write_text("keep staged\n")
    subprocess.check_call(["git", "-C", str(self.tasks), "add",
                           "README.md"], env=self.env)
    self.run_cmd("agent-task", "--agent", "agent-a", "new", "--title",
                 "Only task")
    result = subprocess.run(
      ["git", "-C", str(self.tasks), "cat-file", "-e",
       "HEAD:README.md"], env=self.env, capture_output=True)
    self.assertEqual(result.returncode, 0)
    self.assertNotEqual(result.stdout, b"keep staged\n")
    staged = subprocess.check_output(
      ["git", "-C", str(self.tasks), "diff", "--cached", "--name-only"],
      env=self.env).decode().splitlines()
    self.assertEqual(staged, ["README.md"])

  def test_commit_completed_before_timeout_is_reported_as_committed(self):
    self.init()
    fake_bin = self.base / "fake-bin"
    fake_bin.mkdir()
    wrapper = fake_bin / "git"
    wrapper.write_text(
      "#!/bin/sh\n"
      "if [ \"$7\" = commit ]; then\n"
      "  \"$REAL_GIT\" \"$@\" || exit $?\n"
      "  sleep 60\n"
      "fi\n"
      "exec \"$REAL_GIT\" \"$@\"\n")
    wrapper.chmod(0o755)
    env = dict(self.env, PATH=str(fake_bin) + os.pathsep + self.env["PATH"],
               REAL_GIT=shutil.which("git"),
               # Only the commit, which sleeps 60 s after committing, must
               # time out; other git calls need headroom on a busy machine.
               AGENT_RECORDS_GIT_TIMEOUT="5")
    task = self.run_cmd("agent-task", "--agent", "agent-a", "new",
                        "--title", "Committed", env=env).strip()
    self.assertEqual(task, "T-0001")
    self.assertEqual((self.tasks / ".next-id").read_text(), "2\n")
    self.assertFalse((self.tasks / ".records-journal.json").exists())

  def test_git_operation_state_blocks_mutation(self):
    self.init()
    git_dir = self.tasks / ".git"
    (git_dir / "MERGE_HEAD").write_text("0" * 40 + "\n")
    self.run_cmd("agent-task", "--agent", "agent-a", "new", "--title",
                 "Blocked", code=5)
    (git_dir / "MERGE_HEAD").unlink()
    (git_dir / "rebase-merge").mkdir()
    self.run_cmd("agent-task", "--agent", "agent-a", "new", "--title",
                 "Blocked", code=5)
    (git_dir / "rebase-merge").rmdir()
    subprocess.check_call(["git", "-C", str(self.tasks), "switch",
                           "--detach", "-q", "HEAD"], env=self.env)
    self.run_cmd("agent-task", "--agent", "agent-a", "new", "--title",
                 "Blocked", code=5)

  def test_lock_wait_and_unlocked_read(self):
    self.init()
    self.run_cmd("agent-task", "--agent", "agent-a", "new",
                 "--title", "Lock")
    script = """
import fcntl, json, sys, time
with open(sys.argv[1], 'r+b') as stream:
  fcntl.flock(stream, fcntl.LOCK_EX)
  stream.seek(0)
  stream.truncate()
  stream.write(json.dumps({'pid': 123, 'agent': 'holder',
    'operation': 'test', 'started': '2026-01-01 00:00:00 +0000'}).encode())
  stream.flush()
  print('READY', flush=True)
  time.sleep(60)
"""
    process = subprocess.Popen(
      [sys.executable, "-c", script, str(self.tasks / ".records.lock")],
      cwd=self.base, env=self.env, stdout=subprocess.PIPE, text=True)
    self.assertEqual(process.stdout.readline().strip(), "READY")
    result = subprocess.run(
      [sys.executable, str(BIN / "agent-task"), "--wait", "0.1", "list"],
      env=self.env, cwd=self.base, capture_output=True, text=True)
    self.assertEqual(result.returncode, 1, result.stderr)
    self.assertRegex(result.stderr,
                     r"lock held by 123 holder test for \d+s; retry")
    process.kill()
    process.wait(timeout=2)
    process.stdout.close()
    lock = self.tasks / ".records.lock"
    lock.chmod(0)
    try:
      self.run_cmd("agent-task", "list", code=1)
      output = self.run_cmd("agent-task", "--unlocked", "list")
      self.assertIn("UNVERIFIED", output)
    finally:
      lock.chmod(0o600)
