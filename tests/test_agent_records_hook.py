"""agent-records-hook: reminders after state-creating commands, and a
session-start summary of the agent's records."""

from __future__ import annotations

import importlib.machinery
import importlib.util
import io
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "bin/agent-records-hook"
LOADER = importlib.machinery.SourceFileLoader("agent_records_hook",
                                             str(SCRIPT))
SPEC = importlib.util.spec_from_loader("agent_records_hook", LOADER)
HOOK = importlib.util.module_from_spec(SPEC)
LOADER.exec_module(HOOK)


class ReminderTests(unittest.TestCase):
  def kinds(self, command):
    return [kind for kind, _ in HOOK.reminders(command)]

  def test_state_creating_commands(self):
    for command, kind in (
        ("git worktree add -b fix ../app-fix origin/main", "worktree"),
        ("cd ~/git/app && git -C x worktree add ../y", "worktree"),
        ("git stash", "stash"), ("git stash push -m wip", "stash"),
        ("git checkout -b topic", "branch"), ("git switch -c topic", "branch"),
        ("git branch keep-this origin/main", "branch"),
        ("brew install ripgrep", "tool"), ("pipx install black", "tool"),
        ("npm install -g typescript", "tool"),
        ("uv tool install ruff", "tool"),
        ("launchctl bootstrap gui/501 x.plist", "service"),
        ("systemctl --user enable --now foo", "service"),
        ("docker run -d --name db postgres", "service")):
      self.assertEqual(self.kinds(command), [kind], command)

  def test_reading_and_cleaning_up_is_quiet(self):
    for command in ("git worktree list", "git worktree remove ../y",
                    "git stash list", "git stash pop", "git stash drop",
                    "git branch -d topic", "git branch --show-current",
                    "git branch", "git checkout main", "brew list",
                    "pip install -r requirements.txt", "npm install",
                    "docker run --rm alpine true", "echo git worktree add"):
      self.assertEqual(self.kinds(command), [], command)

  def test_shell_wrappers_inspect_inner_command_only(self):
    self.assertEqual(self.kinds("bash -lc 'git stash'"), ["stash"])
    self.assertEqual(self.kinds('sh -c "git worktree add ../x"'),
                     ["worktree"])
    self.assertEqual(self.kinds('echo "git stash"'), [])
    self.assertEqual(self.kinds("printf '%s' 'git stash'"), [])

  def test_shell_wrapper_prefixes_and_options(self):
    for command in (
        "env X=1 bash -c 'git stash'",
        "env bash -c 'git stash'",
        "env -i X=1 bash -c 'git stash'",
        "sudo -u x bash -c 'git stash'",
        "sudo -g staff -E bash -c 'git stash'",
        "nohup sh -c 'git stash'",
        "time bash -c 'git stash'",
        "command bash -c 'git stash'",
        "nice -n 5 bash -c 'git stash'",
        "xargs sh -c 'git stash'",
        "bash --norc -c 'git stash'",
        "bash --rcfile x -c 'git stash'"):
      with self.subTest(command=command):
        self.assertEqual(self.kinds(command), ["stash"])
    self.assertEqual(self.kinds("bash --rcfile c git stash"), [])
    self.assertEqual(self.kinds("bash --norc 'git stash'"), [])

  def test_nested_shell_wrappers(self):
    self.assertEqual(self.kinds("bash -c 'bash -c \"git stash\"'"),
                     ["stash"])

  def test_heredoc_edge_forms(self):
    # Space after <<, data bodies stay quiet.
    self.assertEqual(self.kinds("cat << EOF\ngit stash\nEOF"), [])
    self.assertEqual(self.kinds("cat <<- 'EOF'\n\tgit stash\n\tEOF"), [])
    # A herestring is not a heredoc; the next line is a real command.
    self.assertEqual(self.kinds("cat <<<EOF\ngit stash"), ["stash"])
    # << inside quotes starts nothing.
    self.assertEqual(self.kinds("echo 'a <<EOF'\ngit stash"), ["stash"])
    # A heredoc fed to a shell is commands, not data.
    for shell in ("bash -s", "sh", "/bin/zsh", "dash -e", "sudo bash",
                  "env X=1 sh"):
      with self.subTest(shell=shell):
        self.assertEqual(
          self.kinds(shell + " <<EOF\ngit stash\nEOF"), ["stash"])

  def test_command_prefixes_before_direct_commands(self):
    for command in ("env FOO=1 git stash", "env -i git stash",
                    "nohup git stash", "sudo -u bob git stash",
                    "sudo -E git stash", "time git stash",
                    "command git stash", "nice -n 5 git stash"):
      with self.subTest(command=command):
        self.assertEqual(self.kinds(command), ["stash"])
    self.assertEqual(self.kinds("env echo git stash"), [])

  def test_prefix_matching_stays_fast(self):
    import time
    for command in ("sudo -a " * 40 + "x", "sudo -u sudo " * 40 + "x",
                    "env -u env " * 40 + "x", "nice -n nice " * 40 + "x"):
      start = time.monotonic()
      self.kinds(command)
      self.assertLess(time.monotonic() - start, 0.5, command[:30])

  def test_more_prefix_and_shell_heredoc_forms(self):
    for command in ("env -u X git stash", "nice -5 git stash",
                    "echo | xargs git stash", "sudo -u bob -E git stash",
                    "cat <<EOF | bash\ngit stash\nEOF",
                    "cat <<EOF | sudo sh -s\ngit stash\nEOF"):
      with self.subTest(command=command):
        self.assertEqual(self.kinds(command), ["stash"])
    self.assertEqual(self.kinds("cat <<EOF | grep x\ngit stash\nEOF"), [])

  def test_heredoc_bodies_are_data(self):
    for command in (
        "cat <<EOF\ngit stash\nEOF",
        "cat <<'EOF'\ngit stash\nEOF",
        "cat <<-EOF\n\tgit stash\n\tEOF"):
      with self.subTest(command=command):
        self.assertEqual(self.kinds(command), [])
    self.assertEqual(self.kinds("cat <<EOF\ngit stash\nEOF\ngit stash"),
                     ["stash"])
    self.assertEqual(self.kinds("cat <<EOF\ngit stash"), [])

  def test_command_is_found_in_either_tool_shape(self):
    self.assertEqual(HOOK.command_of({"command": "git stash"}), "git stash")
    self.assertEqual(HOOK.command_of({"command": ["bash", "-lc",
                                                   "git stash"]}),
                     "bash -lc 'git stash'")
    self.assertEqual(HOOK.command_of({"cmd": "git stash"}), "git stash")
    script = 'await tools.exec_command({cmd:"git worktree add ../x"})'
    self.assertIn("worktree add", HOOK.command_of({"input": script}))

  def test_codex_command_with_escaped_quote(self):
    script = 'await tools.exec_command({cmd:"git -C \\"repo\\" stash"})'
    self.assertEqual(HOOK.command_of({"input": script}),
                     'git -C "repo" stash')
    self.assertEqual(self.kinds(HOOK.command_of({"input": script})),
                     ["stash"])
    structured = json.dumps({"cmd": 'git -C "repo" stash'})
    self.assertEqual(HOOK.command_of({"input": structured}),
                     'git -C "repo" stash')


class HookTests(unittest.TestCase):
  def setUp(self):
    self.tmp = tempfile.TemporaryDirectory()
    self.config = Path(self.tmp.name)
    (self.config / "agent-kit").mkdir()
    (self.config / "agent-kit/records.json").write_text("{}")
    self.env = patch.dict(os.environ, {"XDG_CONFIG_HOME": self.tmp.name})
    self.env.start()
    for name in ("AGENT_TASKS_DIR", "AGENT_CHANGELOG_DIR"):
      os.environ.pop(name, None)

  def tearDown(self):
    self.env.stop()
    self.tmp.cleanup()

  def run_hook(self, mode, payload, provider="claude"):
    out = io.StringIO()
    with patch.object(HOOK.sys, "stdin", io.StringIO(json.dumps(payload))), \
         patch.object(HOOK.sys, "stdout", out):
      code = HOOK.main([mode, "--provider", provider])
    return code, out.getvalue()

  def test_post_tool_adds_context_once_per_command(self):
    code, out = self.run_hook("post-tool", {
      "hook_event_name": "PostToolUse", "tool_name": "Bash",
      "tool_input": {"command": "git worktree add ../x && git stash"}})
    self.assertEqual(code, 0)
    context = json.loads(out)["hookSpecificOutput"]
    self.assertEqual(context["hookEventName"], "PostToolUse")
    self.assertIn("agent-changelog new --kind worktree",
                  context["additionalContext"])
    self.assertIn("--kind stash", context["additionalContext"])

  def test_silent_without_records_or_on_bad_input(self):
    (self.config / "agent-kit/records.json").unlink()
    self.assertEqual(self.run_hook("post-tool", {
      "tool_input": {"command": "git stash"}}), (0, ""))
    (self.config / "agent-kit/records.json").write_text("{}")
    out = io.StringIO()
    with patch.object(HOOK.sys, "stdin", io.StringIO("not json")), \
         patch.object(HOOK.sys, "stdout", out):
      self.assertEqual(HOOK.main(["post-tool"]), 0)
    self.assertEqual(out.getvalue(), "")

  def test_session_start_summarises_the_agents_records(self):
    session = "0199aaaa-bbbb-7ccc-8ddd-eeeeffff0000"
    me = HOOK.records_id("claude", session)
    listing = {"tasks": [
      {"id": "T-0001", "title": "Mine", "status": "in-progress",
       "owner": me, "helpers": ""},
      {"id": "T-0002", "title": "Open here", "status": "open",
       "owner": "none", "helpers": ""}]}
    entries = {"records": [{"path": "entries/a.md", "fields": {
      "kind": "worktree", "why": "Kept for review"}}]}

    def fake(args, **kwargs):
      here = "--here" in args
      if args[1:2] == ["list"] or "list" in args:
        body = entries if "agent-changelog" in args[0] else (
          {"tasks": listing["tasks"][1:]} if here else listing)
        return subprocess.CompletedProcess(args, 0, json.dumps(body), "")
      return subprocess.CompletedProcess(args, 1, "", "")
    with patch.object(HOOK.shutil, "which", side_effect=lambda n: n), \
         patch.object(HOOK.subprocess, "run", side_effect=fake):
      code, out = self.run_hook("session-start", {
        "hook_event_name": "SessionStart", "session_id": session,
        "cwd": "/src/app", "source": "startup"})
    self.assertEqual(code, 0)
    text = json.loads(out)["hookSpecificOutput"]["additionalContext"]
    self.assertIn(f"Your agent ID is {me}", text)
    self.assertIn("T-0001 Mine (in-progress)", text)
    self.assertIn("T-0002 Open here", text)
    self.assertIn("entries/a.md: Kept for review", text)
    self.assertIn("agent-records skill", text)


if __name__ == "__main__":
  unittest.main()
