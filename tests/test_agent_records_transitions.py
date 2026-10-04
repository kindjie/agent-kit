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

  def git_head(self):
    return subprocess.run(["git", "-C", str(self.tasks), "rev-parse",
                           "HEAD"], env=self.env, check=True,
                          capture_output=True, text=True).stdout

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
    self.run_task("agent-a", "uncheck", task, "docs", "--reason", "wrong",
                  code=1)
    force("agent-a", "uncheck", task, "docs", "--reason", "wrong")
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

  def test_uncheck_and_recheck_correct_checklist(self):
    self.init()
    task = self.task("Corrections", "--check", "extra item", "--known",
                     "- [ ] docs: documentation updated")
    self.run_task("agent-a", "claim", task)
    self.run_task("agent-a", "helper", "add", task, "agent-c")
    self.run_task("agent-a", "check", task, "docs", "--na", "No docs")
    shown = self.run_cmd("agent-task", "show", task)
    self.assertIn("- [x] docs: n/a -- No docs", shown)
    self.assertIn("## Known\n\n- [ ] docs: documentation updated\n", shown)
    self.run_task("agent-a", "check", task, "docs", "--evidence", "README",
                  "--reason", "docs were updated")
    shown = self.run_cmd("agent-task", "show", task)
    self.assertIn("- [x] docs: documentation updated -- evidence: README",
                  shown)
    self.assertIn("Rechecked docs: docs were updated", shown)
    self.run_task("agent-c", "check", task, "5", "--evidence", "ref")
    self.assertIn("Checked extra item\n",
                  self.run_cmd("agent-task", "show", task))
    self.run_task("agent-c", "uncheck", task, "5", "--reason", "no",
                  code=1)
    self.run_task("agent-b", "uncheck", task, "5", "--reason", "no",
                  code=1)
    self.run_task("agent-a", "uncheck", task, "docs", code=2)
    self.run_task("agent-a", "uncheck", task, "docs", "--reason",
                  "ticked in error")
    shown = self.run_cmd("agent-task", "show", task)
    self.assertIn("- [ ] docs: documentation updated\n- [ ] work", shown)
    self.assertIn("Unchecked docs: ticked in error", shown)
    self.run_task("agent-a", "uncheck", task, "docs", "--reason", "again",
                  code=1)
    self.run_task("agent-a", "uncheck", task, "5", "--reason", "not done")
    self.assertIn("- [ ] extra item\n",
                  self.run_cmd("agent-task", "show", task))
    self.run_task("agent-a", "uncheck", task, "9", "--reason", "none",
                  code=1)
    self.run_task("agent-a", "uncheck", task, "\u00b2", "--reason", "none",
                  code=1)
    head = self.git_head()
    self.run_task("agent-a", "uncheck", task, "5", "--reason", "again",
                  code=1)
    self.assertEqual(self.git_head(), head)
    self.run_cmd("agent-task", "lint")

  def test_added_checks_keep_their_text_and_section(self):
    self.init()
    task = self.task("Added", "--check", "docs: API page", "--check",
                     "first", "--check", "target", "--check", "other")
    self.run_task("agent-a", "claim", task)
    self.run_task("agent-a", "check", task, "5", "--evidence", "page")
    self.run_task("agent-a", "uncheck", task, "5", "--reason", "not yet")
    shown = self.run_cmd("agent-task", "show", task)
    self.assertIn("- [ ] docs: API page\n", shown)
    self.assertIn("- [ ] docs: documentation updated\n", shown)
    self.run_task("agent-a", "check", task, "6", "--evidence",
                  "a\u2028- [ ] fake")
    self.run_task("agent-a", "check", task, "7", "--evidence", "done")
    shown = self.run_cmd("agent-task", "show", task)
    self.assertIn("- [x] target -- evidence: done\n- [ ] other\n", shown)
    self.run_task("agent-a", "set", task, "--known", "- [ ] other")
    self.run_task("agent-a", "set", task, "--remove-check", "8")
    self.run_task("agent-a", "set", task, "--add-check", "last")
    shown = self.run_cmd("agent-task", "show", task)
    self.assertIn("## Known\n\n- [ ] other\n", shown)
    self.assertIn("- [x] target -- evidence: done\n- [ ] last\n\n## Log",
                  shown)
    self.run_cmd("agent-task", "lint")

  def test_added_check_keeps_embedded_evidence_marker(self):
    self.init()
    item = "review -- evidence: draft text"
    task = self.task("Evidence marker", "--check", item)
    self.run_task("agent-a", "claim", task)
    self.run_task("agent-a", "check", task, item, "--evidence", "approved")
    self.assertIn("- [x] " + item + " -- evidence: approved",
                  self.run_cmd("agent-task", "show", task))
    self.run_task("agent-a", "uncheck", task, item, "--reason", "retry")
    self.assertIn("- [ ] " + item + "\n",
                  self.run_cmd("agent-task", "show", task))

  def test_added_check_keeps_embedded_na_marker(self):
    self.init()
    item = "review: n/a -- draft text"
    task = self.task("N/A marker", "--check", item)
    self.run_task("agent-a", "claim", task)
    self.run_task("agent-a", "check", task, item, "--na", "deferred")
    self.assertIn("- [x] " + item + ": n/a -- deferred",
                  self.run_cmd("agent-task", "show", task))
    self.run_task("agent-a", "uncheck", task, item, "--reason", "retry")
    self.assertIn("- [ ] " + item + "\n",
                  self.run_cmd("agent-task", "show", task))

  def test_added_check_without_colon_is_findable_by_text(self):
    self.init()
    task = self.task("Plain check")
    self.run_task("agent-a", "claim", task)
    self.run_task("agent-a", "set", task, "--add-check", "plain item")
    self.run_task("agent-a", "check", task, "plain item", "--evidence",
                  "done")
    self.run_task("agent-a", "uncheck", task, "plain item", "--reason",
                  "retry")
    self.assertIn("- [ ] plain item\n",
                  self.run_cmd("agent-task", "show", task))

  def test_uncheck_rejects_empty_reason_without_writing(self):
    self.init()
    task = self.task("Uncheck reason")
    self.run_task("agent-a", "claim", task)
    self.run_task("agent-a", "check", task, "docs", "--evidence", "done")
    head = self.git_head()
    self.run_task("agent-a", "uncheck", task, "docs", "--reason", "",
                  code=2)
    self.assertEqual(self.git_head(), head)
    self.run_task("agent-a", "uncheck", task, "docs", "--reason", "  ",
                  code=2)
    self.assertEqual(self.git_head(), head)

  def test_reopen_rejects_empty_reason_without_writing(self):
    self.init()
    task = self.task("Reopen reason")
    self.run_task("agent-a", "claim", task)
    self.run_task("agent-a", "close", task, "cancelled", "--reason", "done")
    head = self.git_head()
    self.run_task("agent-b", "reopen", task, "--reason", "", code=2)
    self.assertEqual(self.git_head(), head)
    self.run_task("agent-b", "reopen", task, "--reason", "  ", code=2)
    self.assertEqual(self.git_head(), head)

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
