"""agent-kit-rules: installing and replacing the always-loaded snippet."""

from __future__ import annotations

import importlib.machinery
import importlib.util
import io
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "bin/agent-kit-rules"
LOADER = importlib.machinery.SourceFileLoader("agent_kit_rules", str(SCRIPT))
SPEC = importlib.util.spec_from_loader("agent_kit_rules", LOADER)
RULES = importlib.util.module_from_spec(SPEC)
LOADER.exec_module(RULES)

LEGACY = subprocess.run(
  ["git", "-C", str(ROOT), "show", "87647ff:AGENTS.snippet.md"],
  capture_output=True, text=True).stdout


class RulesTest(unittest.TestCase):
  def setUp(self):
    self.tmp = tempfile.TemporaryDirectory()
    self.root = Path(self.tmp.name)
    self.target = self.root / "AGENTS.md"

  def tearDown(self):
    self.tmp.cleanup()

  def install(self, *paths, records=False, force=False, dry_run=False):
    out = io.StringIO()
    with patch.object(RULES, "records_ready",
                      return_value=(records, None if records else "absent")):
      code = RULES.install([Path(p) for p in paths or [self.target]],
                           records="auto", force=force, dry_run=dry_run,
                           out=out)
    return code, out.getvalue()

  def block(self, text):
    begin = text.index("<!-- BEGIN agent-kit")
    return text[begin:text.index("<!-- END agent-kit -->") + 22]

  def test_appends_to_a_new_or_existing_file(self):
    code, out = self.install()
    self.assertEqual(code, 0)
    text = self.target.read_text()
    self.assertTrue(text.startswith("<!-- BEGIN agent-kit sha256="))
    self.assertIn("## Commit blockers", text)
    self.assertIn("appended", out)
    self.target.write_text("# Mine\n\nKeep this.")
    self.install()
    text = self.target.read_text()
    self.assertTrue(text.startswith("# Mine\n\nKeep this.\n\n<!-- BEGIN"))

  def test_rerun_is_a_no_op(self):
    self.install()
    first = self.target.read_text()
    code, out = self.install()
    self.assertEqual((code, self.target.read_text()), (0, first))
    self.assertIn("up to date", out)

  def test_moved_snippet_is_replaced_where_it_is(self):
    self.install()
    block = self.block(self.target.read_text())
    self.target.write_text(f"# Top\n\n{block}\n\n## After\n\nMine.\n")
    code, out = self.install(records=True)
    self.assertEqual(code, 0, out)
    text = self.target.read_text()
    self.assertTrue(text.startswith("# Top\n\n<!-- BEGIN agent-kit"))
    self.assertTrue(text.endswith("<!-- END agent-kit -->\n\n## After\n\n"
                                  "Mine.\n"))
    self.assertIn("## Task and state records", text)
    self.assertIn("replaced", out)

  def test_released_legacy_snippet_is_recognised(self):
    self.assertIn("<!-- BEGIN agent-kit -->", LEGACY)
    self.target.write_text("Intro.\n\n" + LEGACY + "\nOutro.\n")
    code, out = self.install()
    self.assertEqual(code, 0, out)
    text = self.target.read_text()
    self.assertIn("## Session plans", text)
    self.assertNotIn("<!-- BEGIN agent-kit -->", text)
    self.assertTrue(text.startswith("Intro.\n\n"))
    self.assertTrue(text.endswith("\nOutro.\n"))

  def test_edited_snippet_is_refused_unless_forced(self):
    self.install()
    edited = self.target.read_text().replace("No secrets", "No SECRETS")
    self.target.write_text(edited)
    code, out = self.install(records=True)
    self.assertEqual(code, 1)
    self.assertEqual(self.target.read_text(), edited)
    self.assertIn("edited since it was installed", out)
    code, _ = self.install(records=True, force=True)
    self.assertEqual(code, 0)
    self.assertIn("## Task and state records", self.target.read_text())

  def test_malformed_markers_are_refused(self):
    self.install()
    block = self.block(self.target.read_text())
    for text in (block + "\n" + block, block.replace("<!-- END agent-kit -->",
                                                      ""),
                 "<!-- END agent-kit -->\n" + block.split("\n", 1)[1]
                 .replace("<!-- END agent-kit -->", "")
                 + "<!-- BEGIN agent-kit -->\n"):
      self.target.write_text(text)
      code, out = self.install(force=True)
      self.assertEqual(code, 1, text)
      self.assertEqual(self.target.read_text(), text)
      self.assertIn("markers", out)

  def test_records_section_follows_the_doctors(self):
    self.install(records=True)
    self.assertIn("agent-changelog new", self.target.read_text())
    code, out = self.install(records=False)
    self.assertEqual(code, 0)
    text = self.target.read_text()
    self.assertNotIn("agent-changelog", text)
    self.assertNotIn("agent-kit:records", text)
    self.assertIn("records section omitted: absent", out)

  def test_cleanup_authority_is_conditional_with_or_without_records(self):
    for records in (True, False):
      with self.subTest(records=records):
        body = RULES.snippet_body(records)
        flat = " ".join(body.split())
        self.assertNotIn("needs no explicit permission", flat)
        self.assertIn("standing authorization", flat)
        self.assertIn("otherwise ask first", flat)
        self.assertIn("one by one", flat)
        self.assertIn("no unique uncommitted, unpushed, or untracked work",
                      flat)
        self.assertIn("introducing commit", flat)
        if not records:
          self.assertNotIn("agent-changelog", body)
          self.assertNotIn("agent-task", body)

  def test_agent_id_prefers_an_assigned_identity(self):
    flat = " ".join(RULES.snippet_body(True).split())
    self.assertIn("ID you were assigned when given one; otherwise "
                  "`$(agent-id show)`", flat)

  def test_dry_run_changes_nothing_and_shows_a_diff(self):
    self.target.write_text("Mine.\n")
    code, out = self.install(dry_run=True)
    self.assertEqual((code, self.target.read_text()), (0, "Mine.\n"))
    self.assertIn("+<!-- BEGIN agent-kit", out)

  def test_symlink_and_mode_are_kept(self):
    real = self.root / "real.md"
    real.write_text("Mine.\n")
    real.chmod(0o640)
    self.target.symlink_to(real)
    self.install()
    self.assertTrue(self.target.is_symlink())
    self.assertIn("agent-kit", real.read_text())
    self.assertEqual(stat.S_IMODE(real.stat().st_mode), 0o640)

  def test_crlf_files_keep_their_line_endings(self):
    self.target.write_bytes(b"Line one.\r\nLine two.\r\n")
    self.install()
    raw = self.target.read_bytes()
    self.assertTrue(raw.startswith(b"Line one.\r\nLine two.\r\n\r\n"))
    self.assertNotIn(b"\n", raw.replace(b"\r\n", b""))
    code, out = self.install(records=True)
    self.assertEqual(code, 0, out)
    self.assertIn("replaced", out)
    self.assertNotIn(b"\n", self.target.read_bytes().replace(b"\r\n", b""))
    code, out = self.install(records=True)
    self.assertIn("up to date", out)

  def test_unresolvable_links_are_refused_untouched(self):
    loop_a, loop_b = self.root / "a.md", self.root / "b.md"
    loop_a.symlink_to(loop_b)
    loop_b.symlink_to(loop_a)
    dangling = self.root / "d.md"
    dangling.symlink_to(self.root / "missing" / "x.md")
    code, out = self.install(loop_a, dangling)
    self.assertEqual(code, 1)
    self.assertTrue(loop_a.is_symlink() and dangling.is_symlink())
    self.assertFalse((self.root / "missing").exists())
    self.assertIn("symlink loop", out)
    self.assertIn("dangling symlink", out)

  def test_examples_in_code_are_not_installs(self):
    example = ("# Docs\n\n```md\n<!-- BEGIN agent-kit -->\nexample\n"
               "<!-- END agent-kit -->\n```\n\nInline: "
               "`<!-- BEGIN agent-kit -->`.\n")
    self.target.write_text(example)
    code, out = self.install(force=True)
    self.assertEqual(code, 0, out)
    self.assertIn("appended", out)
    self.assertTrue(self.target.read_text().startswith(example))

  def test_unreadable_or_racing_files_are_refused(self):
    self.target.write_bytes(b"caf\xe9\n")
    code, out = self.install()
    self.assertEqual((code, self.target.read_bytes()), (1, b"caf\xe9\n"))
    self.assertIn("not UTF-8", out)
    self.target.write_text("Mine.\n")
    planned = RULES.planned

    def racing(text, block, force):
      self.target.write_text("Mine.\nTheirs.\n")
      return planned(text, block, force)
    with patch.object(RULES, "planned", racing):
      code, out = self.install()
    self.assertEqual(code, 1)
    self.assertEqual(self.target.read_text(), "Mine.\nTheirs.\n")
    self.assertIn("changed by something else", out)
    self.assertEqual([p.name for p in self.root.iterdir()], ["AGENTS.md"])

  def test_default_targets_skip_absent_tools(self):
    home = self.root / "home"
    (home / ".claude").mkdir(parents=True)
    self.assertEqual(RULES.default_targets(home),
                     [home / ".claude/CLAUDE.md"])

  def test_doctors_run_once_and_report_why(self):
    tools = self.root / "bin"
    tools.mkdir()
    for name, body in (("agent-task", "exit 0"),
                       ("agent-changelog", "echo 'not configured' >&2; "
                                           "exit 2")):
      path = tools / name
      path.write_text("#!/bin/sh\n" + body + "\n")
      path.chmod(0o755)
    ready, why = RULES.records_ready(tools)
    self.assertFalse(ready)
    self.assertEqual(why, "agent-changelog doctor: not configured")
    (tools / "agent-changelog").write_text("#!/bin/sh\nexit 0\n")
    self.assertEqual(RULES.records_ready(tools), (True, None))

  def test_command_line(self):
    result = subprocess.run(
      [sys.executable, str(SCRIPT), "--records", "off", str(self.target)],
      capture_output=True, text=True,
      env={**os.environ, "NO_COLOR": "1"})
    self.assertEqual(result.returncode, 0, result.stderr)
    self.assertIn("## Commit blockers", self.target.read_text())


if __name__ == "__main__":
  unittest.main()
