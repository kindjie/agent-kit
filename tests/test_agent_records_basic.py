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


class SetupTest(RecordsFixture):

  def test_agent_id_override_beats_shared_session(self):
    # An in-process subagent shares the parent's session variable; its own
    # minted ID, exported as AGENT_ID, must win for implicit identity.
    env = dict(self.env, CLAUDE_CODE_SESSION_ID="parent-session")
    parent = self.run_cmd("agent-id", "show", env=env).strip()
    own = self.run_cmd("agent-id", "new", "helper", env=env).strip()
    env["AGENT_ID"] = own
    self.assertEqual(self.run_cmd("agent-id", "show", env=env).strip(), own)
    self.assertNotEqual(own, parent)
    env["AGENT_ID"] = ""
    env.pop("CLAUDE_CODE_SESSION_ID")
    self.run_cmd("agent-id", "show", env=env, code=1)
    env["CLAUDE_CODE_SESSION_ID"] = "parent-session"
    self.assertEqual(self.run_cmd("agent-id", "show", env=env).strip(),
                     parent)
    for bad in ("bad id!", " ", "\t"):
      env["AGENT_ID"] = bad
      self.run_cmd("agent-id", "show", env=env, code=2)

  def show(self, env):
    return subprocess.run(
      [sys.executable, str(BIN / "agent-id"), "show"], env=env,
      capture_output=True, text=True)

  def test_session_bound_id_needs_a_matching_session(self):
    claude = dict(self.env, CLAUDE_CODE_SESSION_ID="parent-session")
    own = self.run_cmd("agent-id", "new", "helper", env=claude).strip()
    # No session variable at all: a session-bound ID cannot be proven.
    bare = dict(self.env, AGENT_ID=own)
    shown = self.show(bare)
    self.assertEqual((shown.returncode, shown.stdout), (1, ""))
    self.assertIn("ignoring AGENT_ID=" + own, shown.stderr)
    # Same session passes.
    shown = self.show(dict(claude, AGENT_ID=own))
    self.assertEqual((shown.stdout.strip(), shown.stderr), (own, ""))
    # An ID minted with no session stays trusted without one.
    sessionless = self.run_cmd("agent-id", "new", "bare").strip()
    shown = self.show(dict(self.env, AGENT_ID=sessionless))
    self.assertEqual((shown.stdout.strip(), shown.stderr), (sessionless, ""))

  def test_mixed_provider_sessions_are_ambiguous(self):
    # Either order of nesting leaves variables from two providers set, and
    # variable precedence does not say which session is current.
    for first, second in ((("CLAUDE_CODE_SESSION_ID", "claude-sess"),
                           ("CODEX_THREAD_ID", "codex-thread")),
                          (("CODEX_THREAD_ID", "codex-thread"),
                           ("CLAUDE_CODE_SESSION_ID", "claude-sess"))):
      parent = dict(self.env, **dict([first]))
      own = self.run_cmd("agent-id", "new", "helper", env=parent).strip()
      self.assertEqual(self.show(dict(parent, AGENT_ID=own)).stdout.strip(),
                       own)
      nested = dict(parent, AGENT_ID=own, **dict([second]))
      shown = self.show(nested)
      self.assertIn("ignoring AGENT_ID=" + own, shown.stderr)
      self.assertNotEqual(shown.stdout.strip(), own)
      # Even when the ambiguous environment still contains the minting
      # session's variable, AGENT_ID is not honoured.
      self.assertIn("ambiguous", shown.stderr)

  def test_show_refuses_mixed_provider_sessions(self):
    # A codex exec child of Claude Code (or the reverse) sees both
    # providers' variables; neither Claude Code nor Codex sets a variable
    # only the innermost process has, so precedence would silently return
    # the parent's ID. Refuse instead, whichever provider is inside.
    for first, second in ((("CLAUDE_CODE_SESSION_ID", "claude-sess"),
                           ("CODEX_THREAD_ID", "codex-thread")),
                          (("CODEX_THREAD_ID", "codex-thread"),
                           ("CLAUDE_CODE_SESSION_ID", "claude-sess")),
                          (("CLAUDE_CODE_SESSION_ID", "claude-sess"),
                           ("CODEX_SESSION_ID", "codex-session"))):
      both = dict(self.env, **dict([first, second]))
      shown = self.show(both)
      self.assertEqual((shown.returncode, shown.stdout), (1, ""))
      for word in ("several providers", "--agent", "--holder", "AGENT_ID"):
        self.assertIn(word, shown.stderr)
      # Each provider alone still derives its own ID.
      for single in (first, second):
        alone = self.show(dict(self.env, **dict([single])))
        self.assertEqual(alone.returncode, 0)
        self.assertEqual(alone.stderr, "")
        self.assertEqual(alone.stdout.strip(), self.run_cmd(
          "agent-id", "show", env=dict(self.env, **dict([single]))).strip())
      # An ID trusted in this environment is still returned.
      handmade = self.show(dict(both, AGENT_ID="handmade-1"))
      self.assertEqual((handmade.returncode, handmade.stdout.strip()),
                       (0, "handmade-1"))
      # Minting still works, but binds to no session: the child cannot say
      # which session it is, and a claude-derived binding would be wrong.
      own = self.run_cmd("agent-id", "new", "helper", env=both).strip()
      row = json.loads((self.base / "agent-kit" / "agent-ids.log")
                       .read_text().splitlines()[-1])
      self.assertEqual((row["id"], row["minted_session"]), (own, None))
      self.assertEqual(self.show(dict(both, AGENT_ID=own)).stdout.strip(),
                       own)

  def test_agent_id_ignored_by_a_nested_session(self):
    env = dict(self.env, CLAUDE_CODE_SESSION_ID="parent-session")
    own = self.run_cmd("agent-id", "new", "helper", env=env).strip()
    row = json.loads(
      (self.base / "agent-kit" / "agent-ids.log").read_text().splitlines()[-1])
    self.assertEqual(row["minted_session"],
                     self.run_cmd("agent-id", "show", env=env).strip())
    # A nested claude -p or codex exec inherits AGENT_ID, not the session.
    nested = dict(env, AGENT_ID=own, CLAUDE_CODE_SESSION_ID="child-session")
    child = self.run_cmd("agent-id", "show",
                         env={k: v for k, v in nested.items()
                              if k != "AGENT_ID"}).strip()
    self.assertNotEqual(child, own)
    shown = subprocess.run(
      [sys.executable, str(BIN / "agent-id"), "show"], env=nested,
      capture_output=True, text=True)
    self.assertEqual((shown.returncode, shown.stdout.strip()), (0, child))
    self.assertIn("ignoring AGENT_ID=" + own, shown.stderr)
    self.assertEqual(shown.stderr.count("\n"), 1)
    quiet = subprocess.run(
      [sys.executable, str(BIN / "agent-id"), "show"],
      env=dict(env, AGENT_ID=own), capture_output=True, text=True)
    self.assertEqual((quiet.stdout.strip(), quiet.stderr), (own, ""))
    codex = dict(nested, CODEX_THREAD_ID="thread")
    codex.pop("CLAUDE_CODE_SESSION_ID")
    self.assertTrue(self.run_cmd("agent-id", "show", env=codex)
                    .startswith("codex-"))
    # IDs the registry has not seen, or minted with no session, are trusted.
    nested["AGENT_ID"] = "handmade-1"
    self.assertEqual(self.run_cmd("agent-id", "show", env=nested).strip(),
                     "handmade-1")
    bare = self.run_cmd("agent-id", "new", "bare").strip()
    nested["AGENT_ID"] = bare
    self.assertEqual(self.run_cmd("agent-id", "show", env=nested).strip(),
                     bare)
    # An ID minted with no session variable records no minting session.
    self.assertEqual(
      json.loads((self.base / "agent-kit" / "agent-ids.log").read_text()
                 .splitlines()[-1])["minted_session"], None)

  def test_id_derivation_and_registry(self):
    env = dict(self.env, CLAUDE_CODE_SESSION_ID="same-prefix-111")
    first = self.run_cmd("agent-id", "show", env=env).strip()
    env["CLAUDE_CODE_SESSION_ID"] = "same-prefix-222"
    second = self.run_cmd("agent-id", "show", env=env).strip()
    self.assertNotEqual(first, second)
    env = dict(self.env, CODEX_THREAD_ID="thread-value",
               CODEX_SESSION_ID="session-value")
    self.assertEqual(self.run_cmd("agent-id", "show", env=env).strip(),
                     "codex-" + hashlib.sha256(
                       b"thread-value").hexdigest()[:16])
    env.pop("CODEX_THREAD_ID")
    self.assertEqual(self.run_cmd("agent-id", "show", env=env).strip(),
                     "codex-" + hashlib.sha256(
                       b"session-value").hexdigest()[:16])
    for name in ("CLAUDE_CODE_SESSION_ID", "CODEX_THREAD_ID",
                 "CODEX_SESSION_ID"):
      env.pop(name, None)
    self.run_cmd("agent-id", "show", env=env, code=1)
    minted = self.run_cmd("agent-id", "new", "helper").strip()
    self.assertIn(minted, self.run_cmd("agent-id", "recent"))
    blocked = self.base / "state-file"
    blocked.write_text("not a directory\n")
    bad_env = dict(self.env, XDG_STATE_HOME=str(blocked))
    self.assertEqual(self.run_cmd("agent-id", "new", "helper",
                                  env=bad_env, code=1), "")

  def test_configuration_precedence_and_machine(self):
    self.init()
    cfg = self.base / "agent-kit" / "records.json"
    cfg.parent.mkdir(exist_ok=True)
    cfg.write_text(json.dumps({"tasks_dir": "/missing/tasks",
                               "machine": "configured-machine"}))
    self.assertIn("configured-machine (config)",
                  self.run_cmd("agent-id", "machine"))
    env = dict(self.env, AGENT_MACHINE="environment-machine")
    self.assertIn("environment-machine (environment)",
                  self.run_cmd("agent-id", "machine", env=env))
    self.run_cmd("agent-task", "list")
    no_env = dict(self.env)
    no_env.pop("AGENT_TASKS_DIR")
    self.run_cmd("agent-task", "list", env=no_env, code=2)
    self.run_cmd("agent-task", "--dir", self.tasks, "list", env=no_env)

  def test_unconfigured_directory_is_usage_error(self):
    env = dict(self.env)
    env.pop("AGENT_TASKS_DIR", None)
    env.pop("AGENT_CHANGELOG_DIR", None)
    self.run_cmd("agent-task", "list", env=env, code=2)
    self.run_cmd("agent-changelog", "list", env=env, code=2)

  def test_close_requires_changelog_configuration(self):
    self.run_cmd("agent-task", "init", self.tasks)
    self.env["AGENT_TASKS_DIR"] = str(self.tasks)
    task = self.run_cmd("agent-task", "--agent", "agent-a", "new",
                        "--title", "Needs changelog").strip()
    self.run_cmd("agent-task", "--agent", "agent-a", "claim", task)
    self.run_cmd("agent-task", "--agent", "agent-a", "close", task,
                 "cancelled", "--reason", "stopped", code=2)

  def test_init_task_claim_and_changelog(self):
    self.init()
    self.run_cmd("agent-task", "new", "--title", "Example task", code=2)
    inherited = dict(self.env, CLAUDE_CODE_SESSION_ID="inherited")
    self.run_cmd("agent-task", "new", "--title", "Example task",
                 env=inherited, code=2)
    task = self.run_cmd("agent-task", "--agent", "agent-a", "new",
                        "--title", "Example task").strip()
    self.assertEqual(task, "T-0001")
    self.run_cmd("agent-task", "--agent", "agent-a", "claim", task)
    self.assertIn("in-progress", self.run_cmd("agent-task", "show", task))
    entry = self.run_cmd(
      "agent-changelog", "--agent", "agent-a", "new", "--scope",
      "example", "--slug", "scratch", "--kind", "scratch",
      "--location", "scratch", "--why", "example",
      "--cleanup-when", "after work", "--cleanup-how", "remove",
      "--task", task,
    ).strip()
    self.assertIn(entry, self.run_cmd("agent-task", "show", task))
    self.run_cmd("agent-task", "--agent", "agent-a", "close", task,
                 "cancelled", "--reason", "stopped", code=1)
    self.run_cmd("agent-changelog", "--agent", "agent-a", "close",
                 entry, "--what", "removed")
    self.run_cmd("agent-task", "--agent", "agent-a", "close", task,
                 "cancelled", "--reason", "stopped")
    self.assertTrue(list((self.tasks / "archive").glob("T-0001-*.md")))

  def test_help_and_missing_git(self):
    commands = {
      "agent-id": ("show", "new", "recent", "machine", "repo-keys",
                   "repo-rebind"),
      "agent-task": ("init", "doctor", "new", "show", "list", "next",
                     "claim", "release", "handoff", "helper", "status",
                     "set", "check", "link", "log", "close", "reopen",
                     "watch", "sync", "recover", "lint"),
      "agent-changelog": ("init", "doctor", "new", "update", "close",
                          "mistake", "list", "show", "watch", "sync",
                          "recover", "migrate", "lint"),
    }
    for name, subcommands in commands.items():
      self.run_cmd(name, "--help")
      for subcommand in subcommands:
        self.run_cmd(name, subcommand, "--help")
    for subcommand in ("new", "update"):
      self.run_cmd("agent-changelog", "mistake", subcommand, "--help")
    no_git = dict(self.env, PATH="")
    self.run_cmd("agent-id", "show", env=no_git, code=2)
    self.run_cmd("agent-task", "doctor", env=no_git, code=2)
    self.run_cmd("agent-changelog", "doctor", env=no_git, code=2)

  def test_adopt_and_nested_init(self):
    repository = self.base / "existing"
    repository.mkdir()
    subprocess.check_call(["git", "-C", str(repository), "init", "-q"],
                          env=self.env)
    (repository / "README.md").write_text("Existing instructions\n")
    subprocess.check_call(["git", "-C", str(repository), "add", "README.md"],
                          env=self.env)
    subprocess.check_call(["git", "-C", str(repository), "commit", "-qm",
                           "Initial"], env=self.env)
    self.run_cmd("agent-task", "init", repository, "--adopt")
    self.assertEqual((repository / "README.md").read_text(),
                     "Existing instructions\n")
    self.run_cmd("agent-changelog", "init", repository / "nested", code=2)

  def test_separate_git_dir_checks_hooks_operations_and_index_lock(self):
    repository = self.base / "separate"
    gitdir = self.base / "separate-git"
    subprocess.run(["git", "init", "-q", "--separate-git-dir=" + str(gitdir),
                    str(repository)], env=self.env, check=True)
    self.assertTrue((repository / ".git").is_file())
    hook = gitdir / "hooks" / "pre-commit"
    hook.write_text("#!/bin/sh\nexit 0\n")
    hook.chmod(0o755)
    self.run_cmd("agent-task", "init", repository, "--adopt", code=2)
    hook.unlink()
    self.run_cmd("agent-task", "init", repository, "--adopt")
    env = dict(self.env, AGENT_TASKS_DIR=str(repository))
    (gitdir / "MERGE_HEAD").write_text("in progress\n")
    self.run_cmd("agent-task", "--agent", "agent-a", "new", "--title",
                 "Blocked", env=env, code=5)
    (gitdir / "MERGE_HEAD").unlink()
    (repository / ".records-journal.json").write_text('{"id":"pending"}')
    (gitdir / "index.lock").write_text("occupied\n")
    result = subprocess.run(
      [sys.executable, str(BIN / "agent-task"), "list"], env=env,
      cwd=self.base, capture_output=True, text=True)
    self.assertEqual(result.returncode, 5, result.stderr)
    self.assertIn("index.lock", result.stderr)

  def test_non_git_records_directory_is_configuration_error(self):
    directory = self.base / "plain"
    directory.mkdir()
    result = subprocess.run(
      [sys.executable, str(BIN / "agent-task"), "--dir", str(directory),
       "list"], env=self.env, cwd=self.base, capture_output=True,
      text=True)
    self.assertEqual(result.returncode, 2, result.stderr)
    self.assertIn("git repository", result.stderr)
    self.assertNotIn("fatal:", result.stderr)

  def test_init_refuses_unrelated_files_and_adopt_allow(self):
    nonempty = self.base / "nonempty"
    nonempty.mkdir()
    (nonempty / "unrelated.txt").write_text("keep\n")
    self.run_cmd("agent-task", "init", nonempty, code=2)
    self.assertFalse((nonempty / ".git").exists())
    repository = self.base / "adopt-extra"
    repository.mkdir()
    subprocess.check_call(["git", "-C", str(repository), "init", "-q"],
                          env=self.env)
    (repository / "LICENSE").write_text("example\n")
    subprocess.check_call(["git", "-C", str(repository), "add", "LICENSE"],
                          env=self.env)
    subprocess.check_call(["git", "-C", str(repository), "commit", "-qm",
                           "Initial"], env=self.env)
    self.run_cmd("agent-task", "init", repository, "--adopt", code=1)
    self.run_cmd("agent-task", "init", repository, "--adopt", "--allow",
                 "LICENSE")
    self.run_cmd("agent-task", "--dir", repository, "lint")

  def test_failed_init_leaves_destination_unchanged(self):
    config = self.base / "signing-config"
    config.write_text(
      "[user]\n\tname = Example User\n\temail = example.invalid\n"
      "[commit]\n\tgpgsign = true\n"
      "[gpg]\n\tprogram = /does/not/exist\n")
    env = dict(self.env, GIT_CONFIG_GLOBAL=str(config))
    destination = self.base / "failed-init"
    self.run_cmd("agent-task", "init", destination, env=env, code=1)
    self.assertFalse(destination.exists())

  def test_concurrent_ids_and_handmade_task(self):
    self.init()
    commands = [[sys.executable, str(BIN / "agent-task"), "--agent",
                 "agent-a", "new", "--title", title]
                for title in ("First", "Second")]
    processes = [subprocess.Popen(cmd, cwd=self.base, env=self.env,
                                  stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, text=True)
                 for cmd in commands]
    results = [process.communicate(timeout=8) for process in processes]
    self.assertEqual([process.returncode for process in processes], [0, 0],
                     results)
    self.assertEqual({result[0].strip() for result in results},
                     {"T-0001", "T-0002"})
    first = next(self.tasks.glob("T-*-first.md"))
    template = first.read_text()
    original_id = first.name.split("-", 2)[:2]
    (self.tasks / "T-0010-handmade.md").write_text(
      template.replace("id: " + "-".join(original_id),
                       "id: T-0010").replace(
        "title: First", "title: Handmade"))
    self.assertEqual(self.run_cmd("agent-task", "--agent", "agent-a", "new",
                                  "--title", "After hand edit").strip(),
                     "T-0011")

  def test_repository_basename_collision(self):
    self.init()
    first = self.base / "first" / "same"
    second = self.base / "second" / "same"
    for path in (first, second):
      path.mkdir(parents=True)
      subprocess.check_call(["git", "-C", str(path), "init", "-q"],
                            env=self.env)
      (path / "README.md").write_text("example\n")
      subprocess.check_call(["git", "-C", str(path), "add", "README.md"],
                            env=self.env)
      subprocess.check_call(["git", "-C", str(path), "commit", "-qm",
                             "Initial"], env=self.env)
    self.run_cmd("agent-task", "--agent", "agent-a", "new", "--title",
                 "First repo", cwd=first)
    self.run_cmd("agent-task", "--agent", "agent-a", "new", "--title",
                 "Second repo", cwd=second, code=2)

  def test_simultaneous_repository_key_claim_has_one_winner(self):
    first = self.base / "first" / "same"
    second = self.base / "second" / "same"
    for path in (first, second):
      path.mkdir(parents=True)
      subprocess.check_call(["git", "-C", str(path), "init", "-q"],
                            env=self.env)
      (path / "README.md").write_text("example\n")
      subprocess.check_call(["git", "-C", str(path), "add", "README.md"],
                            env=self.env)
      subprocess.check_call(["git", "-C", str(path), "commit", "-qm",
                             "Initial"], env=self.env)
    env = dict(self.env, PYTHONPATH=str(BIN))
    script = ("import sys\n"
              "from agent_records_core import repo_key, RecordsError\n"
              "try:\n"
              "  print(repo_key())\n"
              "except RecordsError as exc:\n"
              "  sys.exit(exc.code)\n")
    command = [sys.executable, "-c", script]
    processes = [subprocess.Popen(command, cwd=path, env=env,
                                  stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, text=True)
                 for path in (first, second)]
    results = [process.communicate(timeout=8) for process in processes]
    self.assertEqual(sorted(process.returncode for process in processes),
                     [0, 2], results)
    registry = json.loads((self.base / "agent-kit" /
                           "repo-keys.json").read_text())
    self.assertIn(registry["keys"]["same"],
                  (str(first.resolve()), str(second.resolve())))

  def test_repo_rebind_records_forced_reason_in_registry(self):
    self.init()
    original = self.base / "original"
    replacement = self.base / "replacement"
    for path in (original, replacement):
      path.mkdir()
      subprocess.check_call(["git", "-C", str(path), "init", "-q"],
                            env=self.env)
      (path / "README.md").write_text("example\n")
      subprocess.check_call(["git", "-C", str(path), "add", "README.md"],
                            env=self.env)
      subprocess.check_call(["git", "-C", str(path), "commit", "-qm",
                             "Initial"], env=self.env)
    self.run_cmd("agent-task", "--agent", "agent-a", "new", "--title",
                 "Original checkout", cwd=original)
    self.run_cmd("agent-id", "--agent", "agent-a", "repo-rebind",
                 "original", replacement, code=1)
    self.run_cmd("agent-id", "--agent", "agent-a", "repo-rebind",
                 "original", replacement, "--force", "owner instructed move")
    registry = json.loads((self.base / "agent-kit" /
                           "repo-keys.json").read_text())
    self.assertEqual(registry["keys"]["original"],
                     str(replacement.resolve()))
    self.assertEqual(registry["events"][-1]["reason"],
                     "owner instructed move")
    self.assertTrue(registry["events"][-1]["forced"])
    self.assertEqual(registry["events"][-1]["agent"], "agent-a")

  def test_doctor_preserves_records_and_head(self):
    self.init()
    before = {path.relative_to(self.tasks): path.read_bytes()
              for path in self.tasks.iterdir() if path.is_file() and
              not path.name.startswith(".records")}
    head = subprocess.check_output(
      ["git", "-C", str(self.tasks), "rev-parse", "HEAD"],
      env=self.env).strip()
    self.run_cmd("agent-task", "doctor")
    after = {path.relative_to(self.tasks): path.read_bytes()
             for path in self.tasks.iterdir() if path.is_file() and
             not path.name.startswith(".records")}
    self.assertEqual(before, after)
    self.assertEqual(subprocess.check_output(
      ["git", "-C", str(self.tasks), "rev-parse", "HEAD"],
      env=self.env).strip(), head)

  def test_linked_worktree_uses_main_repository_key(self):
    self.init()
    main = self.base / "main-checkout"
    linked = self.base / "linked-checkout"
    main.mkdir()
    subprocess.check_call(["git", "-C", str(main), "init", "-q"],
                          env=self.env)
    (main / "README.md").write_text("example\n")
    subprocess.check_call(["git", "-C", str(main), "add", "README.md"],
                          env=self.env)
    subprocess.check_call(["git", "-C", str(main), "commit", "-qm",
                           "Initial"], env=self.env)
    subprocess.check_call(["git", "-C", str(main), "worktree", "add",
                           "--detach", "-q", str(linked)], env=self.env)
    task = self.run_cmd("agent-task", "--agent", "agent-a", "new",
                        "--title", "Linked", cwd=linked).strip()
    self.assertIn("repos: main-checkout",
                  self.run_cmd("agent-task", "show", task))
    registry = json.loads((self.base / "agent-kit" /
                           "repo-keys.json").read_text())
    self.assertEqual(registry["keys"]["main-checkout"], str(main.resolve()))
    self.run_cmd("agent-id", "--agent", "agent-a", "repo-rebind",
                 "main-checkout", linked, code=2)
