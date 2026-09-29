"""Shared subprocess fixture for agent records tests."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BIN = ROOT / "bin"


class RecordsFixture(unittest.TestCase):
  @classmethod
  def setUpClass(cls):
    cls.template = tempfile.TemporaryDirectory()
    base = Path(cls.template.name)
    config = base / "gitconfig"
    config.write_text(
      "[user]\n\tname = Example User\n\temail = example.invalid\n"
      "[commit]\n\tgpgsign = false\n", encoding="utf-8")
    env = dict(os.environ, GIT_CONFIG_GLOBAL=str(config),
               GIT_CONFIG_NOSYSTEM="1", PYTHONDONTWRITEBYTECODE="1",
               XDG_STATE_HOME=str(base), XDG_CONFIG_HOME=str(base))
    for name in ("CLAUDE_CODE_SESSION_ID", "CODEX_THREAD_ID",
                 "CODEX_SESSION_ID", "AGENT_ID"):
      env.pop(name, None)
    cls.template_config = config
    cls.template_tasks = base / "tasks"
    cls.template_changes = base / "changes"
    for name, path in (("agent-task", cls.template_tasks),
                       ("agent-changelog", cls.template_changes)):
      subprocess.run([sys.executable, str(BIN / name), "init", str(path)],
                     env=env, check=True, capture_output=True)

  @classmethod
  def tearDownClass(cls):
    cls.template.cleanup()

  def setUp(self):
    self.tmp = tempfile.TemporaryDirectory()
    self.addCleanup(self.tmp.cleanup)
    self.base = Path(self.tmp.name)
    config = self.base / "gitconfig"
    config.write_text(self.template_config.read_text(), encoding="utf-8")
    self.env = dict(os.environ, GIT_CONFIG_GLOBAL=str(config),
                    GIT_CONFIG_NOSYSTEM="1", XDG_STATE_HOME=str(self.base),
                    XDG_CONFIG_HOME=str(self.base),
                    PYTHONDONTWRITEBYTECODE="1")
    for name in ("CLAUDE_CODE_SESSION_ID", "CODEX_THREAD_ID",
                 "CODEX_SESSION_ID", "AGENT_ID"):
      self.env.pop(name, None)
    self.tasks = self.base / "tasks"
    self.changes = self.base / "changes"

  def run_cmd(self, name, *args, code=0, env=None, cwd=None):
    result = subprocess.run(
      [sys.executable, str(BIN / name), *map(str, args)],
      cwd=cwd or self.base, env=env or self.env, text=True,
      stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=8)
    self.assertEqual(result.returncode, code,
                     (result.stdout, result.stderr))
    return result.stdout

  def init(self):
    shutil.copytree(self.template_tasks, self.tasks)
    shutil.copytree(self.template_changes, self.changes)
    self.env["AGENT_TASKS_DIR"] = str(self.tasks)
    self.env["AGENT_CHANGELOG_DIR"] = str(self.changes)
