"""Explicit local starter setup for both records stores."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

from tests.agent_records_support import BIN, RecordsFixture


class RecordsInitTest(RecordsFixture):
  def setUp(self):
    super().setUp()
    for key in ("AGENT_TASKS_DIR", "AGENT_CHANGELOG_DIR", "AGENT_MACHINE"):
      self.env.pop(key, None)
    self.env["HOME"] = str(self.base / "home")
    self.env.pop("XDG_CONFIG_HOME", None)
    self.env.pop("XDG_DATA_HOME", None)

  def invoke(self, name, *args, env=None):
    return subprocess.run(
      [sys.executable, str(BIN / name), *map(str, args)],
      cwd=self.base, env=env or self.env, text=True,
      capture_output=True, timeout=8)

  def paths(self, env=None):
    env = env or self.env
    home = Path(env["HOME"])
    config = Path(env.get("XDG_CONFIG_HOME", home / ".config"))
    data = Path(env.get("XDG_DATA_HOME", home / ".local/share"))
    return ((config / "agent-kit/records.json").resolve(),
            (data / "agent-kit/tasks").resolve(),
            (data / "agent-kit/changelog").resolve())

  def test_default_initializes_both_and_reports_every_value(self):
    cfg, tasks, changes = self.paths()
    result = self.invoke("agent-records", "init", "--default",
                         "--machine", "workstation")
    self.assertEqual(result.returncode, 0, result.stderr)
    self.assertEqual(json.loads(cfg.read_text()), {
      "tasks_dir": str(tasks), "changelog_dir": str(changes),
      "machine": "workstation", "push": False})
    for kind, root in (("tasks", tasks), ("changelog", changes)):
      self.assertEqual(json.loads((root / ".agent-records").read_text())
                       ["kind"], kind)
      self.assertTrue((root / ".git").is_dir())
      self.assertTrue((root / ".records.lock").is_file())
      self.assertIn(str(root), result.stdout)
    self.assertIn(str(cfg), result.stdout)
    self.assertIn("workstation", result.stdout)
    self.assertIn("false", result.stdout)
    self.assertIn("local", result.stdout.lower())
    self.assertEqual(self.invoke("agent-task", "list").returncode, 0)
    self.assertEqual(self.invoke("agent-changelog", "list").returncode, 0)

  def test_distinct_homes_and_xdg_paths_never_share_default_state(self):
    first = dict(self.env, HOME=str(self.base / "first"))
    second = dict(self.env, HOME=str(self.base / "second"))
    for env in (first, second):
      result = self.invoke("agent-records", "init", "--default", env=env)
      self.assertEqual(result.returncode, 0, result.stderr)
    self.assertNotEqual(self.paths(first), self.paths(second))
    for env in (first, second):
      cfg, tasks, changes = self.paths(env)
      self.assertEqual(json.loads(cfg.read_text())["tasks_dir"], str(tasks))
      self.assertEqual(json.loads(cfg.read_text())["changelog_dir"],
                       str(changes))
    xdg = dict(self.env, XDG_CONFIG_HOME=str(self.base / "custom-config"),
               XDG_DATA_HOME=str(self.base / "custom-data"))
    self.assertEqual(self.invoke("agent-records", "init", "--default",
                                 env=xdg).returncode, 0)
    self.assertTrue(self.paths(xdg)[0].exists())

  def test_config_paths_resolve_until_environment_overrides_them(self):
    cfg, tasks, changes = self.paths()
    self.assertEqual(self.invoke("agent-records", "init", "--default",
                                 "--machine", "configured").returncode, 0)
    self.assertEqual(self.invoke("agent-task", "list").returncode, 0)
    self.assertEqual(self.invoke("agent-changelog", "list").returncode, 0)
    task_override = dict(self.env, AGENT_TASKS_DIR=str(self.base / "missing"))
    result = self.invoke("agent-task", "list", env=task_override)
    self.assertEqual(result.returncode, 2)
    self.assertIn(str(self.base / "missing"), result.stderr)
    self.assertNotEqual(self.invoke("agent-changelog", "list",
                                    env=task_override).returncode, 0)
    change_override = dict(self.env,
                           AGENT_CHANGELOG_DIR=str(self.base / "missing"))
    result = self.invoke("agent-changelog", "list", env=change_override)
    self.assertEqual(result.returncode, 2)
    self.assertIn(str(self.base / "missing"), result.stderr)
    self.assertTrue(tasks.exists())
    self.assertTrue(changes.exists())

  def test_refuses_existing_config_without_touching_it(self):
    cfg, tasks, changes = self.paths()
    cfg.parent.mkdir(parents=True)
    cfg.write_text('{"existing": true}\n')
    result = self.invoke("agent-records", "init", "--default")
    self.assertNotEqual(result.returncode, 0)
    self.assertEqual(cfg.read_text(), '{"existing": true}\n')
    self.assertFalse(tasks.exists())
    self.assertFalse(changes.exists())

  def test_refuses_environment_overrides_and_occupied_destination(self):
    cfg, tasks, changes = self.paths()
    for key in ("AGENT_TASKS_DIR", "AGENT_CHANGELOG_DIR",
                "AGENT_MACHINE"):
      env = dict(self.env, **{key: str(self.base / "override")})
      result = self.invoke("agent-records", "init", "--default", env=env)
      self.assertNotEqual(result.returncode, 0, (key, result.stdout))
      self.assertFalse(cfg.exists())
      self.assertFalse(tasks.exists())
      self.assertFalse(changes.exists())
    changes.mkdir(parents=True)
    (changes / "keep").write_text("existing")
    result = self.invoke("agent-records", "init", "--default")
    self.assertNotEqual(result.returncode, 0)
    self.assertEqual((changes / "keep").read_text(), "existing")
    self.assertFalse(tasks.exists())
    self.assertFalse(cfg.exists())

  def test_refuses_dangling_symlink_destinations(self):
    cfg, tasks, changes = self.paths()
    for target in (cfg, tasks, changes):
      target.parent.mkdir(parents=True, exist_ok=True)
      target.symlink_to(self.base / "missing-target")
      result = self.invoke("agent-records", "init", "--default")
      self.assertNotEqual(result.returncode, 0)
      self.assertTrue(target.is_symlink())
      target.unlink()
    self.assertFalse(cfg.exists())
    self.assertFalse(tasks.exists())
    self.assertFalse(changes.exists())

  def test_second_store_failure_leaves_first_and_guides_recovery(self):
    cfg, tasks, changes = self.paths()
    real_git = shutil.which("git")
    wrapper = self.base / "fake-bin/git"
    wrapper.parent.mkdir()
    flag = self.base / "first-git-init"
    wrapper.write_text(
      "#!/bin/sh\n"
      "case \"$*\" in\n"
      "  *' init -q'*)\n"
      "    if [ -e '" + str(flag) + "' ]; then exit 19; fi\n"
      "    touch '" + str(flag) + "' ;;\n"
      "esac\n"
      "exec '" + real_git + "' \"$@\"\n")
    wrapper.chmod(0o755)
    env = dict(self.env, PATH=str(wrapper.parent) + os.pathsep +
               self.env.get("PATH", ""))
    result = self.invoke("agent-records", "init", "--default", env=env)
    self.assertNotEqual(result.returncode, 0)
    self.assertTrue((tasks / ".agent-records").exists())
    self.assertFalse(changes.exists())
    self.assertFalse(cfg.exists())
    self.assertIn(str(tasks), result.stderr)
    self.assertIn("recover", result.stderr.lower())
    retry = self.invoke("agent-records", "init", "--default")
    self.assertNotEqual(retry.returncode, 0)
    self.assertTrue((tasks / ".agent-records").exists())
    self.assertFalse(cfg.exists())

  def test_unconfigured_reads_exit_two_without_creating_state(self):
    cfg, tasks, changes = self.paths()
    for name, args in (("agent-task", ("list",)),
                       ("agent-changelog", ("list",))):
      result = self.invoke(name, *args)
      self.assertEqual(result.returncode, 2, result.stderr)
      self.assertIn("agent-records init --default", result.stderr)
    for path in (cfg, tasks, changes):
      self.assertFalse(path.exists())

  def test_doctor_reports_explicit_starter_configuration(self):
    cfg, tasks, changes = self.paths()
    self.assertEqual(self.invoke("agent-records", "init", "--default",
                                 "--machine", "fixture").returncode, 0)
    result = self.invoke("agent-doctor", "--records-config", cfg, "--json")
    self.assertEqual(result.returncode, 0, result.stderr)
    report = json.loads(result.stdout)
    codes = {item["code"] for item in report["checks"]}
    self.assertIn("config.valid", codes)
    self.assertNotIn("records.unconfigured", codes)
    self.assertFalse(report["signing_verified"])
    self.assertEqual(json.loads(cfg.read_text())["machine"], "fixture")
    changed = json.loads(cfg.read_text())
    changed["changelog_dir"] = str(self.base / "absent")
    cfg.write_text(json.dumps(changed))
    result = self.invoke("agent-doctor", "--records-config", cfg, "--json")
    self.assertEqual(result.returncode, 1, result.stderr)
    report = json.loads(result.stdout)
    self.assertIn("records.directory",
                  {item["code"] for item in report["checks"]})
