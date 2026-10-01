"""Configured execution estimate checks through the actual CLI."""

import json
import subprocess
import sys

from tests import test_agent_records_planning as planning
from tests.agent_records_support import BIN, RecordsFixture


class EstimatePolicyTest(RecordsFixture):
  task = planning.PlanningTest.task
  fields = planning.PlanningTest.fields
  command = planning.PlanningTest.command
  snapshot = planning.PlanningTest.snapshot

  def setUp(self):
    super().setUp()
    self.init()

  def policy(self, mode):
    path = self.base / "agent-kit" / "records.json"
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps({"estimate_policy": mode}))

  def test_default_off_and_warning(self):
    task = self.task()
    result = subprocess.run(
      [sys.executable, str(BIN / "agent-task"), "--agent", "agent-a",
       "claim", task], env=self.env, capture_output=True, text=True)
    self.assertEqual(result.returncode, 0, result.stderr)
    self.assertEqual(result.stderr, "")
    self.assertNotIn("execution-model", self.fields(task))
    self.assertNotIn("estimate-model-unknown", self.fields(task))
    self.policy("warn")
    result = subprocess.run(
      [sys.executable, str(BIN / "agent-task"), "--agent", "agent-a",
       "claim", task], env=self.env, capture_output=True, text=True)
    self.assertEqual(result.returncode, 0, result.stderr)
    self.assertIn("warning: missing execution estimates: execution model",
                  result.stderr)

  def test_require_refusal_is_atomic_and_other_model_is_insufficient(self):
    task = self.task()
    self.command("estimate", "set", task, "other", "--tokens", "20",
                 "--wall-seconds", "10")
    self.policy("require")
    before = self.snapshot()
    self.command("claim", task, code=1)
    self.command("claim", task, "--model", "chosen", code=1)
    self.assertEqual(before, self.snapshot())
    self.command("estimate", "set", task, "chosen", "--tokens", "20")
    self.command("claim", task, "--model", "chosen", code=1)
    self.command("estimate", "set", task, "chosen", "--wall-seconds", "10")
    self.command("claim", task, "--model", "chosen")
    self.assertEqual(self.fields(task)["execution-model"], "chosen")
    self.command("claim", task)

  def test_unknown_metrics_and_replacement(self):
    task = self.task()
    self.policy("require")
    self.command("estimate", "set", task, "chosen", "--wall-seconds", "10",
                 "--tokens-unknown", "context size unavailable")
    self.command("claim", task, "--model", "chosen")
    self.command("estimate", "set", task, "chosen", "--tokens", "20")
    metrics = json.loads(self.fields(task)["estimates"])["chosen"]
    self.assertNotIn("tokens-unknown", metrics)
    self.command("estimate", "set", task, "chosen", "--wall-unknown",
                 "waiting for device access")
    metrics = json.loads(self.fields(task)["estimates"])["chosen"]
    self.assertNotIn("wall-seconds", metrics)
    self.command("claim", task)
    self.run_cmd("agent-task", "lint")

  def test_unknown_model_and_takeover(self):
    task = self.task()
    self.policy("require")
    self.command("claim", task, "--model-unknown", "runtime hides model ID")
    self.assertIn("runtime hides model ID", self.run_cmd("agent-task",
                                                        "show", task))
    self.command("release", task, "--note", "handover")
    self.assertNotIn("estimate-model-unknown", self.fields(task))
    self.command("claim", task, agent="agent-b", code=1)
    self.command("claim", task, "--model-unknown", "runtime hides model ID",
                 agent="agent-b")

  def test_handoff_and_resume_are_checked(self):
    task = self.task()
    self.command("claim", task)
    self.command("status", task, "blocked", "--reason", "waiting")
    self.policy("require")
    before = self.snapshot()
    self.command("status", task, "in-progress", code=1)
    self.command("handoff", task, "--to", "agent-b", "--note", "ready",
                 code=1)
    self.assertEqual(before, self.snapshot())
    self.command("handoff", task, "--to", "agent-b", "--note", "ready",
                 "--model-unknown", "recipient chooses model")
    self.command("status", task, "in-progress", agent="agent-b")

  def test_closed_tasks_unchanged_and_known_zero_is_valid(self):
    task = self.task()
    self.command("claim", task)
    for key in ("merged", "cleanup", "docs", "work-reviewed"):
      self.command("check", task, key, "--na", "no source changes")
    self.command("close", task, "done")
    self.policy("require")
    self.run_cmd("agent-task", "lint")
    before = self.snapshot()
    self.command("claim", task, "--model", "chosen", code=1)
    self.assertEqual(before, self.snapshot())
    other = self.task("Known zero")
    self.command("estimate", "set", other, "chosen", "--wall-seconds", "0",
                 "--tokens", "0")
    self.command("claim", other, "--model", "chosen")

  def test_known_model_handoff_and_resume_after_estimate_removal(self):
    task = self.task()
    self.policy("require")
    for model in ("first", "second"):
      self.command("estimate", "set", task, model, "--wall-seconds", "10",
                   "--tokens", "20")
    self.command("claim", task, "--model", "first")
    self.command("handoff", task, "--to", "agent-b", "--note", "ready",
                 "--model", "second")
    self.command("status", task, "blocked", "--reason", "waiting",
                 agent="agent-b")
    self.command("status", task, "in-progress", agent="agent-b")
    self.command("estimate", "remove", task, "second", "--tokens-unknown",
                 "unknown", agent="agent-b", code=2)
    self.command("estimate", "remove", task, "second", agent="agent-b")
    self.command("status", task, "blocked", "--reason", "waiting",
                 agent="agent-b")
    before = self.snapshot()
    self.command("status", task, "in-progress", agent="agent-b", code=1)
    self.assertEqual(before, self.snapshot())

  def test_force_does_not_bypass_policy(self):
    task = self.task()
    self.command("claim", task, "--model-unknown", "runtime hides model")
    self.policy("require")
    before = self.snapshot()
    self.command("claim", task, "--force", "owner requested takeover",
                 agent="agent-b", code=1)
    self.assertEqual(before, self.snapshot())
    self.command("claim", task, "--force", "owner requested takeover",
                 "--model-unknown", "different runtime hides model",
                 agent="agent-b")

  def test_helper_model_selection_is_separate_and_checked(self):
    task = self.task()
    self.command("claim", task, "--model-unknown", "owner model unavailable")
    self.policy("require")
    before = self.snapshot()
    self.command("helper", "add", task, "agent-b", "--model", "worker",
                 code=1)
    self.assertEqual(before, self.snapshot())
    self.command("estimate", "set", task, "worker", "--wall-seconds", "30",
                 "--tokens", "40")
    self.command("helper", "add", task, "agent-b", "--model", "worker")
    fields = self.fields(task)
    self.assertEqual(json.loads(fields["helper-models"]),
                     {"agent-b": {"model": "worker"}})
    self.assertEqual(fields["estimate-model-unknown"],
                     "owner model unavailable")
    self.command("helper", "add", task, "agent-b")
    self.command("helper", "add", task, "agent-c", "--model-unknown",
                 "worker runtime hides model")
    self.command("helper", "remove", task, "agent-b", "--model", "worker",
                 code=2)
    self.command("helper", "remove", task, "agent-b")
    self.assertNotIn("agent-b", json.loads(self.fields(task)["helper-models"]))
    self.command("release", task, "--note", "handover")
    self.assertNotIn("helper-models", self.fields(task))
    self.run_cmd("agent-task", "lint")

  def test_closure_clears_helper_selection(self):
    task = self.task()
    self.command("claim", task, "--model-unknown", "owner model unavailable")
    self.command("helper", "add", task, "agent-b", "--model-unknown",
                 "worker model unavailable")
    self.command("close", task, "cancelled", "--reason", "no longer needed")
    self.assertNotIn("helper-models", self.fields(task))
    self.run_cmd("agent-task", "lint")

  def test_warnings_explain_how_to_record_selection(self):
    task = self.task()
    self.policy("warn")
    result = subprocess.run(
      [sys.executable, str(BIN / "agent-task"), "--agent", "agent-a",
       "claim", task], env=self.env, capture_output=True, text=True)
    self.assertEqual(result.returncode, 0, result.stderr)
    self.assertIn("claim " + task + " --model", result.stderr)
    self.assertIn("--model-unknown", result.stderr)
    self.assertIn("estimate set", result.stderr)
    result = subprocess.run(
      [sys.executable, str(BIN / "agent-task"), "--agent", "agent-a",
       "handoff", task, "--to", "agent-b", "--note", "ready"],
      env=self.env, capture_output=True, text=True)
    self.assertEqual(result.returncode, 0, result.stderr)
    self.assertIn("handoff " + task + " --to agent-b --note REASON --model",
                  result.stderr)

  def test_invalid_configuration_and_unknown_inputs_do_not_write(self):
    task = self.task()
    for mode in (True, "strict", {}, None):
      self.policy(mode)
      before = self.snapshot()
      self.command("claim", task, code=2)
      self.assertEqual(before, self.snapshot())
    self.policy("off")
    before = self.snapshot()
    self.command("claim", task, "--model-unknown", " ", code=2)
    self.command("estimate", "set", task, "chosen", "--tokens-unknown",
                 " ", code=2)
    self.command("estimate", "set", task, "chosen", "--tokens", "20",
                 "--tokens-unknown", "unknown", code=2)
    self.assertEqual(before, self.snapshot())
