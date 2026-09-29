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


class RecordsTest(RecordsFixture):

  def test_transition_and_helper_permissions(self):
    self.init()
    task = self.run_cmd("agent-task", "--agent", "agent-a", "new",
                        "--title", "Transitions").strip()
    self.run_cmd("agent-task", "--agent", "agent-a", "status", task,
                 "blocked", "--reason", "waiting")
    self.run_cmd("agent-task", "--agent", "agent-b", "status", task,
                 "open", "--reason", "ready")
    self.run_cmd("agent-task", "--agent", "agent-a", "status", task,
                 "in-progress", code=1)
    self.run_cmd("agent-task", "--agent", "agent-a", "claim", task)
    self.run_cmd("agent-task", "--agent", "agent-a", "helper", "add",
                 task, "agent-b")
    self.run_cmd("agent-task", "--agent", "agent-b", "set", task,
                 "--known", "Finding")
    self.run_cmd("agent-task", "--agent", "agent-b", "set", task,
                 "--priority", "P1", code=1)
    self.run_cmd("agent-task", "--agent", "agent-b", "status", task,
                 "in-review")
    self.run_cmd("agent-task", "--agent", "agent-b", "close", task,
                 "cancelled", "--reason", "stopped", code=1)
    self.run_cmd("agent-task", "--agent", "agent-a", "release", task,
                 "--note", "unclaimed")
    self.assertIn("status: open", self.run_cmd("agent-task", "show", task))

  def test_expiry_ends_helper_rights_and_reclaim_clears_helpers(self):
    self.init()
    task = self.run_cmd("agent-task", "--agent", "agent-a", "new",
                        "--title", "Expiry").strip()
    self.run_cmd("agent-task", "--agent", "agent-a", "claim", task,
                 "--hours", "24")
    path = next(self.tasks.glob("T-0001-*.md"))
    expiry = next(line for line in path.read_text().splitlines()
                  if line.startswith("expires: "))
    self.run_cmd("agent-task", "--agent", "agent-a", "log", task, "Update")
    self.assertIn(expiry, path.read_text())
    self.run_cmd("agent-task", "--agent", "agent-a", "helper", "add",
                 task, "agent-b")
    text = path.read_text()
    text = text.replace(next(line for line in text.splitlines()
                             if line.startswith("expires: ")),
                        "expires: 2000-01-01 00:00:00 +0000")
    path.write_text(text)
    subprocess.check_call(["git", "-C", str(self.tasks), "add", path.name],
                          env=self.env)
    subprocess.check_call(["git", "-C", str(self.tasks), "commit", "-qm",
                           "Expire claim"], env=self.env)
    self.run_cmd("agent-task", "--agent", "agent-b", "check", task,
                 "docs", "--na", "No docs", code=1)
    self.run_cmd("agent-task", "--agent", "agent-a", "log", task,
                 "After expiry")
    self.assertIn("After expiry (not owner)", path.read_text())
    self.run_cmd("agent-task", "--agent", "agent-a", "claim", task)
    self.assertIn("helpers: \n", path.read_text())

  def test_closed_entry_force_adds_correction(self):
    self.init()
    entry = self.run_cmd(
      "agent-changelog", "--agent", "agent-a", "new", "--scope",
      "example", "--slug", "state", "--kind", "scratch",
      "--location", "scratch", "--why", "example",
      "--cleanup-when", "later", "--cleanup-how", "remove",
    ).strip()
    self.run_cmd("agent-changelog", "--agent", "agent-a", "close",
                 entry, "--what", "removed")
    original = self.run_cmd("agent-changelog", "show", entry)
    self.run_cmd("agent-changelog", "--agent", "agent-a", "close",
                 entry, "--what", "corrected", code=1)
    self.run_cmd("agent-changelog", "--agent", "agent-a", "close",
                 entry, "--what", "corrected", "--force",
                 "owner requested correction")
    updated = self.run_cmd("agent-changelog", "show", entry)
    closed_line = next(line for line in original.splitlines()
                       if line.startswith("closed: "))
    self.assertIn(closed_line, updated)
    self.assertIn("FORCED by agent-a: owner requested correction", updated)

  def test_open_record_cannot_lose_last_task(self):
    self.init()
    first = self.run_cmd("agent-task", "--agent", "agent-a", "new",
                         "--title", "First").strip()
    second = self.run_cmd("agent-task", "--agent", "agent-a", "new",
                          "--title", "Second").strip()
    entry = self.run_cmd(
      "agent-changelog", "--agent", "agent-a", "new", "--scope",
      "example", "--slug", "state", "--kind", "scratch",
      "--location", "scratch", "--why", "example",
      "--cleanup-when", "later", "--cleanup-how", "remove",
      "--task", first,
    ).strip()
    self.run_cmd("agent-changelog", "--agent", "agent-a", "update",
                 entry, "--remove-task", first, "--reason", "move", code=1)
    self.run_cmd("agent-changelog", "--agent", "agent-a", "update",
                 entry, "--transfer", second, "--reason", "move")
    self.assertIn("tasks: " + second,
                  self.run_cmd("agent-changelog", "show", entry))
    self.assertIn("Transferred " + entry,
                  self.run_cmd("agent-task", "show", second))

  def test_mistake_lint_and_association(self):
    self.init()
    task = self.run_cmd("agent-task", "--agent", "agent-a", "new",
                        "--title", "Incident").strip()
    mistake = self.run_cmd(
      "agent-changelog", "--agent", "agent-a", "mistake", "new",
      "--slug", "example", "--severity", "low", "--scope", "example",
      "--summary", "Incorrect action", "--impact", "none",
      "--cause", "unknown", "--detection", "review",
      "--cleanup-options", "inspect", "--prevention", "check",
      "--task", task,
    ).strip()
    self.assertIn(mistake, self.run_cmd("agent-task", "show", task))
    self.assertIn(mistake, self.run_cmd("agent-task", "list",
                                            "--with-changes"))
    self.run_cmd("agent-task", "lint")
    self.run_cmd("agent-changelog", "lint")
    self.run_cmd("agent-changelog", "--agent", "agent-a", "mistake",
                 "update", mistake, "--status", "mitigated",
                 "--cleanup-done", "No action needed")
    self.run_cmd("agent-changelog", "lint")

  def test_changelog_dates_accept_annotations(self):
    self.init()
    entry = self.changes / "entries" / "2026-01-01-0900-example-note.md"
    header = ("machine: example-machine\nagent: example\nkind: scratch\n"
              "status: open\nlocation: scratch\nwhy: test\n"
              "cleanup-when: later\ncleanup-how: remove\n")
    mistake = self.changes / "mistakes" / "2026-01-01-example.md"
    mistake.parent.mkdir(exist_ok=True)
    mistake.write_text(
      "date: 2026-01-01 (approximate; recorded later)\n"
      "machine: example-machine\nagent: example\nseverity: low\n"
      "status: open\nscope: example\nsummary: s\nimpact: none\n"
      "cause: c\ndetection: d\ncleanup-options: none\n"
      "cleanup-done: none\nprevention: p\n")
    for date, code in (("2026-01-01 09:00 -0800 (inferred)", 0),
                       ("2026-01-01 09:00:30 -0800 (from a log)", 0),
                       ("2026-01-01 09:00 -0800", 0),
                       ("2026-01-01 (inferred)", 0),
                       ("2026-01-01 9am", 1),
                       ("2026-01-01 09:00 -0800 inferred", 1)):
      entry.write_text("date: " + date + "\n" + header)
      subprocess.check_call(["git", "-C", str(self.changes), "add", "-A"],
                            env=self.env)
      subprocess.check_call(["git", "-C", str(self.changes), "commit", "-qm",
                             "Hand entry"], env=self.env)
      self.run_cmd("agent-changelog", "lint", code=code)

  def test_newlines_in_header_and_log_are_refused(self):
    self.init()
    self.run_cmd("agent-task", "--agent", "agent-a", "new", "--title",
                 "Bad\ntitle", code=2)
    self.assertEqual((self.tasks / ".next-id").read_text(), "1\n")
    task = self.run_cmd("agent-task", "--agent", "agent-a", "new",
                        "--title", "Valid").strip()
    self.run_cmd("agent-task", "--agent", "agent-a", "log", task,
                 "Bad\nmessage", code=2)
    self.run_cmd(
      "agent-changelog", "--agent", "agent-a", "new", "--scope",
      "example", "--slug", "state", "--kind", "scratch",
      "--location", "scratch", "--why", "Bad\nreason",
      "--cleanup-when", "later", "--cleanup-how", "remove", code=2)

  def test_migrate_is_dry_until_write(self):
    self.init()
    entry = self.run_cmd(
      "agent-changelog", "--agent", "agent-a", "new", "--scope",
      "example", "--slug", "state", "--kind", "scratch",
      "--location", "scratch", "--why", "example",
      "--cleanup-when", "later", "--cleanup-how", "remove",
    ).strip()
    path = self.changes / entry
    before = path.read_bytes()
    mapping = self.base / "mapping.json"
    mapping.write_text(json.dumps({entry: ["example-repo"]}))
    self.run_cmd("agent-changelog", "migrate", "--set-repos", mapping)
    self.assertEqual(path.read_bytes(), before)
    self.run_cmd("agent-changelog", "--agent", "agent-a", "migrate",
                 "--set-repos", mapping, "--write")
    self.assertIn(b"repos: example-repo", path.read_bytes())

  def test_legacy_minute_timestamp_survives_update(self):
    self.init()
    entry = self.run_cmd(
      "agent-changelog", "--agent", "agent-a", "new", "--scope",
      "example", "--slug", "state", "--kind", "scratch",
      "--location", "scratch", "--why", "example",
      "--cleanup-when", "later", "--cleanup-how", "remove",
    ).strip()
    path = self.changes / entry
    text = path.read_text()
    date = next(line for line in text.splitlines()
                if line.startswith("date: "))
    minute = date[:len("date: YYYY-MM-DD HH:MM")] + date[-6:]
    path.write_text(text.replace(date, minute))
    subprocess.check_call(["git", "-C", str(self.changes), "add", entry],
                          env=self.env)
    subprocess.check_call(["git", "-C", str(self.changes), "commit", "-qm",
                           "Legacy date"], env=self.env)
    self.run_cmd("agent-changelog", "lint")
    self.run_cmd("agent-changelog", "--agent", "agent-a", "update",
                 entry, "--why", "updated")
    self.assertIn(minute, path.read_text())

  def test_unknown_header_and_body_preserved(self):
    self.init()
    task = self.run_cmd("agent-task", "--agent", "agent-a", "new",
                        "--title", "Preserve").strip()
    path = next(self.tasks.glob("T-0001-*.md"))
    raw = path.read_bytes().replace(
      b"\n\n## Known", b"\nx-custom:   spaced  value\n\n## Known")
    raw += b"\nCustom body line  \n\n"
    path.write_bytes(raw)
    subprocess.check_call(["git", "-C", str(self.tasks), "add", path.name],
                          env=self.env)
    subprocess.check_call(["git", "-C", str(self.tasks), "commit", "-qm",
                           "Hand edit"], env=self.env)
    self.run_cmd("agent-task", "--agent", "agent-a", "log", task,
                 "New message")
    updated = path.read_bytes()
    self.assertIn(b"x-custom:   spaced  value\n", updated)
    self.assertIn(b"Custom body line  \n\n- ", updated)
