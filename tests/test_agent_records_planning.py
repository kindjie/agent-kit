"""Dependencies and per-model planning estimates through the public CLI."""

import json
import subprocess

from tests.agent_records_support import RecordsFixture


class PlanningTest(RecordsFixture):
  def setUp(self):
    super().setUp()
    self.init()

  def task(self, title="Example", *extra):
    return self.run_cmd("agent-task", "--agent", "agent-a", "new",
                        "--title", title, "--no-changes", *extra).strip()

  def fields(self, task):
    return json.loads(self.run_cmd("agent-task", "show", task,
                                   "--json"))["fields"]

  def command(self, *args, code=0, agent="agent-a"):
    return self.run_cmd("agent-task", "--agent", agent, *args, code=code)

  def snapshot(self):
    files = {str(p.relative_to(self.tasks)): p.read_bytes()
             for p in self.tasks.rglob("*") if p.is_file() and
             ".git" not in p.parts and p.name != ".records.lock"}
    state = [subprocess.check_output(["git", "-C", str(self.tasks), *args],
                                     env=self.env)
             for args in (("rev-parse", "HEAD"), ("status", "--porcelain"))]
    return files, state

  def test_declare_revoke_and_creation(self):
    first = self.task("Prerequisite")
    second = self.task("Dependent", "--depends-on", first)
    third = self.task("Other")
    self.command("dependency", "add", second, third)
    self.command("dependency", "add", second, third)
    self.assertEqual(self.fields(second)["depends-on"], first + ", " + third)
    self.assertEqual(self.fields(first)["depends-on"], "")
    self.command("dependency", "remove", second, first, "--reason", "obsolete")
    self.assertEqual(self.fields(second)["depends-on"], third)
    self.assertIn("obsolete", self.run_cmd("agent-task", "show", second))
    self.command("dependency", "remove", second, first, code=1)
    self.run_cmd("agent-task", "lint")

  def test_invalid_graph_does_not_change_records(self):
    first = self.task("First")
    second = self.task("Second", "--depends-on", first)
    third = self.task("Third", "--depends-on", second)
    before = self.snapshot()
    for target in (first, third, "T-9999"):
      self.command("dependency", "add", first, target, code=1)
    for target in ("bad", first + " ", first + "," + second):
      self.command("dependency", "add", first, target, code=2)
      self.command("new", "--title", "Invalid", "--depends-on", target,
                   code=2)
    self.command("new", "--title", "Invalid", "--depends-on", "T-9999",
                 code=1)
    self.assertEqual(before, self.snapshot())

  def test_permissions_and_closed_task(self):
    first = self.task()
    second = self.task()
    self.command("claim", first)
    self.command("helper", "add", first, "agent-b")
    for actor in ("agent-b", "agent-c"):
      self.command("dependency", "add", first, second, agent=actor, code=1)
      self.command("estimate", "set", first, "model-x", "--tokens", "10",
                   agent=actor, code=1)
    self.command("close", first, "cancelled", "--reason", "stopped")
    self.command("estimate", "set", first, "model-x", "--tokens", "10",
                 code=1)
    self.command("dependency", "add", first, second, code=1)

  def test_next_waits_for_done_and_reopening(self):
    first = self.task("Prerequisite", "--priority", "P3")
    second = self.task("Dependent", "--priority", "P0", "--depends-on", first)
    self.assertTrue(self.run_cmd("agent-task", "next").startswith(first))
    self.command("claim", first)
    for key in ("merged", "cleanup", "docs", "work-reviewed"):
      self.command("check", first, key, "--na", "no changes")
    self.command("close", first, "done")
    self.assertTrue(self.run_cmd("agent-task", "next").startswith(second))
    self.command("reopen", first, "--reason", "more work")
    self.assertTrue(self.run_cmd("agent-task", "next").startswith(first))
    self.command("claim", first)
    self.command("close", first, "cancelled", "--reason", "stopped")
    self.assertEqual(self.run_cmd("agent-task", "next"), "")
    self.command("dependency", "remove", second, first)
    self.assertTrue(self.run_cmd("agent-task", "next").startswith(second))

  def test_model_estimates_merge_and_remove(self):
    task = self.task()
    self.command("estimate", "set", task, "provider/model-v1",
                 "--wall-seconds", "12.5", "--tokens", "1000")
    self.command("estimate", "set", task, "model-v2[1m]", "--tokens", "0")
    self.command("estimate", "set", task, "provider/model-v1",
                 "--tokens", "2000")
    estimates = json.loads(self.fields(task)["estimates"])
    self.assertEqual(estimates, {
      "provider/model-v1": {"wall-seconds": 12.5, "tokens": 2000},
      "model-v2[1m]": {"tokens": 0}})
    self.command("estimate", "remove", task, "model-v2[1m]",
                 "--reason", "retired calibration candidate")
    self.command("estimate", "remove", task, "model-v2[1m]", code=1)
    self.assertIn("retired calibration candidate",
                  self.run_cmd("agent-task", "show", task))
    self.run_cmd("agent-task", "lint")

  def test_invalid_estimates_do_not_write(self):
    task = self.task()
    before = self.snapshot()
    for extra in ((), ("--tokens", "-1"), ("--tokens", "1.5"),
                  ("--wall-seconds", "-1"), ("--wall-seconds", "nan"),
                  ("--wall-seconds", "inf")):
      self.command("estimate", "set", task, "model-x", *extra, code=2)
    self.command("estimate", "set", task, "bad model", "--tokens", "1",
                 code=2)
    self.command("estimate", "remove", task, "model-x", "--tokens", "1",
                 code=2)
    self.assertEqual(self.snapshot(), before)

  def test_old_records_remain_valid_and_editable(self):
    task = self.task()
    path = next(self.tasks.glob(task + "-*.md"))
    path.write_text("\n".join(line for line in path.read_text().splitlines()
                              if not line.startswith(("depends-on:",
                                                      "estimates:",
                                                      "storypoints:"))) + "\n")
    subprocess.run(["git", "-C", str(self.tasks), "add", path.name],
                   env=self.env, check=True)
    subprocess.run(["git", "-C", str(self.tasks), "commit", "-qm", "Legacy"],
                   env=self.env, check=True)
    self.run_cmd("agent-task", "lint")
    self.command("estimate", "set", task, "model-x", "--tokens", "5")
    self.command("set", task, "--storypoints", "2")
    other = self.task("Prerequisite")
    self.command("dependency", "add", task, other)
    self.assertIn(other, self.run_cmd("agent-task", "dependency", "show", task))
    self.assertTrue(self.run_cmd("agent-task", "next").startswith(other))
    self.run_cmd("agent-task", "lint")

  def test_dangling_dependency_does_not_hide_other_available_tasks(self):
    bad = self.task("Broken", "--priority", "P0")
    available = self.task("Available", "--priority", "P1")
    path = next(self.tasks.glob(bad + "-*.md"))
    path.write_text(path.read_text().replace("depends-on: \n",
                                           "depends-on: T-9999\n"))
    self.assertTrue(self.run_cmd("agent-task", "next").startswith(available))
    path.write_text(path.read_text().replace("T-9999", "bad"))
    result = json.loads(self.run_cmd("agent-task", "dependency", "show",
                                     available, "--json"))
    self.assertFalse(result["authoritative"])

  def test_lint_rejects_corrupt_planning_fields(self):
    first = self.task("First")
    second = self.task("Second", "--depends-on", first)
    path = next(self.tasks.glob(first + "-*.md"))
    original = path.read_text()
    for field, value, error in (
        ("depends-on", second, "dependency cycle"),
        ("depends-on", "T-9999", "missing dependency"),
        ("depends-on", first, "dependency cycle"),
        ("depends-on", "bad", "invalid or duplicate dependency task ID"),
        ("estimates", "[]", "estimates must be a model-to-metrics object"),
        ("estimates", '{"x":{"tokens":true}}', "invalid estimated tokens"),
        ("estimates", '{"x":{"tokens":-1}}', "invalid estimated tokens"),
        ("estimates", '{"x":{"wall-seconds":NaN}}',
         "invalid estimated wall-seconds"),
        ("estimates", '{"x":{"other":1}}', "invalid estimate metrics"),
        ("estimates", '{"x":{"tokens":1},"x":{"tokens":2}}',
         "duplicate estimate key"),
        ("storypoints", "4", "invalid storypoints")):
      lines = original.splitlines()
      lines = [field + ": " + value if line.startswith(field + ":")
               else line for line in lines]
      path.write_text("\n".join(lines) + "\n")
      self.assertIn(error, self.run_cmd("agent-task", "lint", code=1))
    path.write_text(original)
    self.run_cmd("agent-task", "lint")

  def test_relationship_view_includes_status_in_both_directions(self):
    first = self.task("Prerequisite")
    second = self.task("Dependent", "--depends-on", first)
    third = self.task("Downstream", "--depends-on", second)
    self.command("claim", first)
    self.command("close", first, "cancelled", "--reason", "stopped")
    self.command("claim", third)
    output = self.run_cmd("agent-task", "dependency", "show", second)
    self.assertIn(first + " cancelled Prerequisite", output)
    self.assertIn(third + " in-progress Downstream", output)
    relationships = json.loads(self.run_cmd(
      "agent-task", "dependency", "show", second, "--json"))
    self.assertEqual(relationships["prerequisites"][0]["id"], first)
    self.assertEqual(relationships["dependents"][0]["id"], third)
    self.assertTrue(relationships["authoritative"])
    self.run_cmd("agent-task", "dependency", "show", second, first, code=2)
    self.run_cmd("agent-task", "dependency", "add", second, code=2)

  def test_storypoints_creation_update_and_validation(self):
    task = self.task("Sized", "--storypoints", "3")
    self.assertEqual(self.fields(task)["storypoints"], "3")
    for points in (1, 2, 3, 5, 8, 13, 20):
      self.command("set", task, "--storypoints", str(points))
      self.assertEqual(self.fields(task)["storypoints"], str(points))
    before = self.fields(task)
    for points in ("0", "4", "21", "1.5", "bad"):
      self.command("set", task, "--storypoints", points, code=2)
      self.command("new", "--title", "Invalid", "--storypoints", points,
                   code=2)
    self.assertEqual(self.fields(task), before)
    self.command("claim", task)
    self.command("helper", "add", task, "agent-b")
    self.command("set", task, "--storypoints", "5", agent="agent-b", code=1)
    self.run_cmd("agent-task", "lint")
