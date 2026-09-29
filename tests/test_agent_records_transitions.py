"""Task transition, permission and close-gate coverage."""

from __future__ import annotations

import subprocess

from tests.agent_records_support import RecordsFixture


class TransitionTest(RecordsFixture):
  def task(self, title="Example", *extra):
    return self.run_cmd("agent-task", "--agent", "agent-a", "new",
                        "--title", title, *extra).strip()

  def run_task(self, agent, *args, code=0):
    return self.run_cmd("agent-task", "--agent", agent, *args, code=code)

  def expire(self, task):
    path = next(self.tasks.glob(task + "-*.md"))
    text = path.read_text()
    expiry = next(line for line in text.splitlines()
                  if line.startswith("expires: "))
    path.write_text(text.replace(
      expiry, "expires: 2000-01-01 00:00:00 +0000"))
    for args in (("add", path.name), ("commit", "-qm", "Expire claim")):
      subprocess.run(["git", "-C", str(self.tasks), *args],
                     env=self.env, check=True, capture_output=True)
    return path

  def test_allowed_transitions_and_expired_takeover(self):
    self.init()
    task = self.task()
    self.run_task("agent-a", "status", task, "blocked", "--reason", "wait")
    self.run_task("agent-b", "status", task, "open", "--reason", "ready")
    self.run_task("agent-a", "claim", task)
    self.run_task("agent-a", "status", task, "in-review")
    self.run_task("agent-a", "helper", "add", task, "agent-c")
    self.expire(task)
    self.run_task("agent-b", "claim", task)
    shown = self.run_cmd("agent-task", "show", task)
    self.assertIn("status: in-review", shown)
    self.assertIn("helpers: \n", shown)
    self.assertIn("from agent-a (expiry 2000-01-01", shown)
    self.run_task("agent-b", "status", task, "blocked", "--reason", "wait")
    self.run_task("agent-b", "status", task, "in-progress")
    self.run_task("agent-b", "release", task, "--status", "blocked",
                  "--note", "pause")
    self.assertIn("status: blocked", self.run_cmd("agent-task", "show", task))
    self.run_task("agent-a", "claim", task)
    self.run_task("agent-a", "helper", "add", task, "agent-c")
    self.expire(task)
    self.run_task("agent-b", "handoff", task, "--to", "agent-d",
                  "--note", "owner unavailable")
    shown = self.run_cmd("agent-task", "show", task)
    self.assertIn("owner: agent-d", shown)
    self.assertIn("helpers: \n", shown)

  def test_disallowed_transitions_and_force_cases(self):
    self.init()
    task = self.task()
    forced_count = 0

    def force(agent, *args):
      nonlocal forced_count
      self.run_task(agent, *args, "--force", "owner instruction")
      forced_count += 1
      shown = self.run_cmd("agent-task", "show", task)
      forced_lines = [line for line in shown.splitlines()
                      if "FORCED by " in line]
      self.assertEqual(len(forced_lines), forced_count)
      self.assertIn("FORCED by " + agent + ": owner instruction",
                    forced_lines[-1])
    for state in ("open", "in-progress", "in-review"):
      self.run_task("agent-a", "status", task, state, code=1)
    self.run_task("agent-a", "status", task, "blocked", "--reason", "wait")
    for state in ("blocked", "in-progress", "in-review"):
      self.run_task("agent-a", "status", task, state, code=1)
    self.run_task("agent-a", "claim", task)
    self.run_task("agent-b", "status", task, "open", "--reason", "ready",
                  code=1)
    self.run_task("agent-b", "claim", task, code=1)
    force("agent-b", "claim", task)
    self.run_task("agent-a", "helper", "add", task, "agent-c", code=1)
    force("agent-a", "helper", "add", task, "agent-c")
    self.run_task("agent-c", "set", task, "--priority", "P1", code=1)
    force("agent-c", "set", task, "--priority", "P1")
    self.run_task("agent-a", "status", task, "in-review", code=1)
    force("agent-a", "status", task, "in-review")
    self.run_task("agent-a", "check", task, "docs", "--na", "No docs",
                  code=1)
    force("agent-a", "check", task, "docs", "--na", "No docs")
    self.run_task("agent-a", "link", task, "--ref", "note", code=1)
    force("agent-a", "link", task, "--ref", "note")
    self.run_task("agent-a", "handoff", task, "--to", "agent-d",
                  "--note", "handoff", code=1)
    force("agent-a", "handoff", task, "--to", "agent-d", "--note",
          "handoff")
    self.run_task("agent-a", "log", task, "message", "--force", "reason",
                  code=2)

  def test_done_review_owner_block_mistake_abandoned_reopen(self):
    self.init()
    task = self.task("Gates", "--no-changes")
    self.run_task("agent-a", "claim", task)
    for key in ("merged", "cleanup", "docs", "work-reviewed"):
      self.run_task("agent-a", "check", task, key, "--na", "No changes")
    for review in ("required", "pending", "failed example"):
      self.run_task("agent-a", "set", task, "--review", review)
      self.run_task("agent-a", "close", task, "done", code=1)
    self.run_task("agent-a", "set", task, "--review", "n/a")
    self.run_task("agent-a", "set", task, "--blocked-on-owner", "yes",
                  "--verified", "owner replied")
    self.run_task("agent-a", "close", task, "done", code=1)
    self.run_task("agent-a", "set", task, "--blocked-on-owner", "no")
    mistake = self.run_cmd(
      "agent-changelog", "--agent", "agent-a", "mistake", "new",
      "--slug", "example", "--severity", "low", "--scope", "example",
      "--summary", "issue", "--impact", "none", "--cause", "unknown",
      "--detection", "review", "--cleanup-options", "inspect",
      "--prevention", "check", "--task", task).strip()
    self.run_task("agent-a", "close", task, "done", code=1)
    self.run_cmd("agent-changelog", "--agent", "agent-a", "mistake",
                 "update", mistake, "--status", "mitigated")
    self.run_task("agent-a", "close", task, "done")
    self.run_task("agent-b", "reopen", task, "--reason", "more work")
    self.assertIn("status: open", self.run_cmd("agent-task", "show", task))
    self.run_task("agent-a", "claim", task)
    self.run_task("agent-a", "close", task, "abandoned", "--reason",
                  "no longer needed")

  def test_unowned_set_and_block_force(self):
    self.init()
    task = self.task()
    self.run_task("agent-b", "set", task, "--priority", "P1")
    self.assertIn("Set task fields", self.run_cmd("agent-task", "show", task))
    self.run_task("agent-b", "set", task, "--blocked-on-owner", "yes",
                  "--verified", "owner replied", code=1)
    self.run_task("agent-b", "set", task, "--blocked-on-owner", "yes",
                  "--verified", "owner replied", "--force",
                  "owner instruction")
    self.assertIn("FORCED by agent-b", self.run_cmd("agent-task", "show", task))

  def test_watch_owner_condition_and_owned_blocked_open_refusal(self):
    self.init()
    task = self.task()
    self.run_cmd("agent-task", "watch", task, "--until", "owner=none")
    self.run_task("agent-a", "claim", task)
    self.run_cmd("agent-task", "watch", task, "--until", "owner=agent-a")
    self.run_task("agent-a", "status", task, "blocked", "--reason", "wait")
    self.run_task("agent-a", "status", task, "open", "--reason", "ready",
                  code=1)

  def test_related_link_stays_symmetric_with_archived_task(self):
    self.init()
    archived = self.task("Archived")
    live = self.task("Live")
    self.run_task("agent-a", "claim", archived)
    self.run_task("agent-a", "close", archived, "cancelled", "--reason",
                  "finished")
    self.run_task("agent-b", "link", live, "--related", archived)
    self.assertIn("related: " + archived,
                  self.run_cmd("agent-task", "show", live))
    self.assertIn("related: " + live,
                  self.run_cmd("agent-task", "show", archived))
    self.run_cmd("agent-task", "lint")
