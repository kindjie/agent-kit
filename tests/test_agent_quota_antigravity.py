from __future__ import annotations

import contextlib
import io
import json
import os
import stat
import subprocess
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from tests.test_agent_quota import AGENT_QUOTA, NOW, RESET
from tests.test_agent_status import AGENT_STATUS, strip_tmux_styles


def bucket(native_id, window, fraction, reset=RESET):
  return {
    "id": native_id, "window": window, "remaining_fraction": fraction,
    "reset_time": reset,
  }


def usage_data():
  return {"groups": [
    {"name": "Gemini Models", "buckets": [
      bucket("gemini-weekly", "weekly", 0.65),
      bucket("gemini-5h", "5h", 0.05, "2026-08-20T01:00:00Z"),
    ]},
    {"name": "Claude and GPT models", "buckets": [
      bucket("3p-weekly", "weekly", 1, "1970-01-01T00:00:00Z"),
    ]},
  ]}


def command_result(command, data, *, turns=0):
  return json.dumps({
    "status": "SUCCESS", "num_turns": turns, "usage": {"total_tokens": 0},
    "command": {"name": command, "data": data},
    "response": "excluded fixture diagnostic",
  })


def registry(*commands):
  return command_result("help", {"commands": [{"name": n} for n in commands]})


class AntigravityQuotaTests(unittest.TestCase):
  def setUp(self):
    self.env = patch.dict(os.environ, {}, clear=True)
    self.env.start()
    self.addCleanup(self.env.stop)
    self.root = tempfile.TemporaryDirectory()
    self.addCleanup(self.root.cleanup)
    self.cache = Path(self.root.name) / "quota" / "report-v3.json"
    self.agy = Path(self.root.name) / "agy"
    self.agy.touch(mode=0o700)
    self.cache_root = patch.object(
      AGENT_QUOTA, "default_cache_path", return_value=self.cache,
    )
    self.cache_root.start()
    self.addCleanup(self.cache_root.stop)

  def test_missing_cli_is_unavailable(self):
    with patch.object(AGENT_QUOTA.subprocess, "run") as run:
      service = AGENT_QUOTA.get_antigravity(now=NOW, executable="")
    run.assert_not_called()
    self.assertEqual(service["service_id"], "antigravity")
    self.assertEqual(service["data_status"], "unavailable")
    self.assertIsNone(service["account"])

  def test_headless_query_is_unbound_and_renders_separate_buckets(self):
    results = [
      "1.5.18\n", registry("usage", "credits"),
      command_result("usage", usage_data()),
      command_result("credits", {"remaining_credits": 0, "upgrade_uri": "discard"}),
    ]
    with patch.object(AGENT_QUOTA.shutil, "which", return_value=str(self.agy)), \
        patch.object(AGENT_QUOTA, "agy_run", side_effect=results) as run:
      service = AGENT_QUOTA.get_antigravity(now=NOW)
    self.assertEqual(run.call_count, 4)
    self.assertEqual(service["identity_status"], "unverified")
    self.assertIsNone(service["account"])
    self.assertEqual(service["credits"][0]["balance"], "0")
    self.assertEqual(service["limits"][2]["last_observation"]["reset_at"], None)
    weekly, short, third_party = service["limits"]
    self.assertEqual(weekly["bucket"], short["bucket"])
    self.assertNotEqual(weekly["bucket"], third_party["bucket"])
    self.assertTrue(all(item["non_additive"] for item in service["limits"]))
    document = {
      "schema_version": 3, "generated_at": AGENT_QUOTA.iso_utc(NOW),
      "services": {"antigravity": service},
    }
    rendered = AGENT_QUOTA.render_brief(document)
    self.assertIn("identity not verified", rendered)
    self.assertIn("Antigravity Gemini 5h", rendered)
    self.assertIn("0.00", rendered)
    self.assertNotIn("excluded fixture diagnostic", json.dumps(document))
    self.assertNotIn("upgrade_uri", json.dumps(document))
    events = AGENT_QUOTA.timeline_document(document)["events"]
    self.assertTrue(events)
    self.assertTrue(all(event["provider"] == "antigravity" for event in events))
    tmux = strip_tmux_styles(AGENT_STATUS.render_tmux(document, 160, now=NOW))
    self.assertIn("Antigravity", tmux)
    self.assertIn("Gemini 5h", tmux)

  def test_unsupported_version_never_invokes_print_mode(self):
    with patch.object(AGENT_QUOTA.shutil, "which", return_value=str(self.agy)), \
        patch.object(AGENT_QUOTA, "agy_run", return_value="1.5.23\n") as run:
      service = AGENT_QUOTA.get_antigravity(now=NOW)
    self.assertEqual(run.call_count, 1)
    self.assertEqual(run.call_args.args[1:], (["--version"], 5))
    self.assertEqual(service["refresh"]["error"]["code"],
                     "antigravity_usage_unsupported")

  def test_missing_handler_never_turns_usage_into_a_prompt(self):
    results = ["1.5.17\n", registry()]
    with patch.object(AGENT_QUOTA.shutil, "which", return_value=str(self.agy)), \
        patch.object(AGENT_QUOTA, "agy_run", side_effect=results) as run:
      service = AGENT_QUOTA.get_antigravity(now=NOW)
    self.assertEqual(run.call_count, 2)
    self.assertIn("/help", run.call_args.args[1])
    self.assertEqual(service["data_status"], "unavailable")

  def test_credits_are_independent_of_quota_results(self):
    malformed = usage_data()
    malformed["groups"][0]["buckets"] = [
      bucket("gemini-weekly", "weekly", 0.5, "0001-01-01T00:00:00+01:00"),
    ]
    results = [
      "1.5.18\n", registry("usage", "credits"),
      command_result("usage", malformed),
      command_result("credits", {"remaining_credits": 0}),
    ]
    with patch.object(AGENT_QUOTA.shutil, "which", return_value=str(self.agy)), \
        patch.object(AGENT_QUOTA, "agy_run", side_effect=results) as run:
      service = AGENT_QUOTA.get_antigravity(now=NOW)
    self.assertEqual(run.call_count, 4)
    self.assertEqual(service["data_status"], "partial")
    self.assertEqual(service["credits"][0]["balance"], "0")

  def test_executable_replacement_does_not_bypass_version_gate(self):
    original = self.agy
    replacement = original.with_suffix(".next")
    replacement.write_text("unverified executable", encoding="utf-8")
    original.write_text("verified executable", encoding="utf-8")

    def run(executable, arguments, timeout):
      self.assertEqual(Path(executable).read_text(), "verified executable")
      if arguments == ["--version"]:
        replacement.replace(original)
        return "1.5.18\n"
      self.assertIn("/help", arguments)
      return registry()

    with patch.object(AGENT_QUOTA, "agy_run", side_effect=run) as fake:
      AGENT_QUOTA.get_antigravity(now=NOW, executable=str(original))
    self.assertEqual(fake.call_count, 2)
    self.assertEqual(original.read_text(), "unverified executable")

  def test_command_validates_zero_turns_and_ignores_diagnostics(self):
    with patch.object(AGENT_QUOTA, "agy_run",
                      return_value=command_result("usage", usage_data(), turns=1)):
      with self.assertRaisesRegex(ValueError, "Unexpected Antigravity"):
        AGENT_QUOTA.agy_command("agy", "usage")
    with patch.object(AGENT_QUOTA.subprocess, "run",
                      return_value=subprocess.CompletedProcess([], 0, "ok")) as run:
      self.assertEqual(AGENT_QUOTA.agy_run("agy", ["--version"], 5), "ok")
    self.assertEqual(run.call_args.kwargs["stderr"], subprocess.DEVNULL)
    self.assertEqual(run.call_args.kwargs["stdin"], subprocess.DEVNULL)
    self.assertEqual(run.call_args.kwargs["env"]["AGY_CLI_DISABLE_AUTO_UPDATE"],
                     "true")

  def test_invalid_disabled_and_duplicate_windows(self):
    rows = usage_data()
    rows["groups"][0]["buckets"] += [
      bucket("gemini-weekly", "weekly", 0.8),
      bucket("3p-5h", "5h", float("nan")),
    ]
    rows["groups"][1]["buckets"][0]["disabled"] = True
    buckets, partial = AGENT_QUOTA.agy_usage(rows)
    self.assertTrue(partial)
    self.assertEqual([(b["group"], b["window"]) for b in buckets],
                     [("gemini", "5h")])
    self.assertIsNone(AGENT_QUOTA.agy_credit({"remaining_credits": False}, NOW))
    self.assertIsNone(AGENT_QUOTA.agy_credit({"remaining_credits": -1}, NOW))

  def test_statusline_binds_only_same_observation_and_keeps_timestamp(self):
    payload = {
      "product": "antigravity", "email": "fixture@example.test",
      "version": "1.5.23",
      "quota": {"gemini-weekly": bucket("gemini-weekly", "weekly", 1)},
      "transcript_path": "/example/private/transcript.jsonl",
      "model": {"display_name": "fixture raw input"},
    }

    def capture(at):
      with patch.object(AGENT_QUOTA, "utc_now", return_value=at):
        AGENT_QUOTA.record_antigravity_statusline(io.BytesIO(
          json.dumps(payload).encode("utf-8"),
        ))

    capture(NOW)
    payload["quota"]["gemini-weekly"]["reset_in_seconds"] = 42
    later = NOW + timedelta(minutes=10)
    capture(later)
    path = AGENT_QUOTA.statusline_cache_path()
    self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
    self.assertNotIn("transcript.jsonl", path.read_text())
    self.assertNotIn("fixture raw input", path.read_text())
    self.assertTrue(AGENT_QUOTA.antigravity_enabled({}))
    self.assertEqual(AGENT_QUOTA.antigravity_source(), "statusline")
    with patch.object(AGENT_QUOTA.subprocess, "run") as run:
      service = AGENT_QUOTA.get_antigravity(now=later)
      self.assertEqual(service["account"]["observed_at"], AGENT_QUOTA.iso_utc(NOW))
      self.assertEqual(service["account"]["plan"], "unknown")
      self.assertEqual(service["identity_status"], "last_observed")
      self.assertNotIn("credits", service)
      self.assertEqual(service["limits"][0]["last_observation"]["freshness"], "stale")
      payload["email"] = "other@example.test"
      payload["quota"]["gemini-weekly"]["remaining_fraction"] = 0.72
      capture(later)
      alternate = AGENT_QUOTA.get_antigravity(now=later)
      self.assertNotEqual(service["account"]["key"], alternate["account"]["key"])
      self.assertEqual(alternate["limits"][0]["last_observation"]["remaining_percent"],
                       72)
      payload["email"] = "fixture@example.test"
      payload["quota"]["gemini-weekly"]["remaining_fraction"] = 1.0
      payload["plan_tier"] = "Current Plan"
      later += timedelta(minutes=1)
      capture(later)
      old = AGENT_QUOTA.get_antigravity(now=later)
      self.assertEqual(old["account"]["key"], service["account"]["key"])
      self.assertEqual(old["account"]["observed_at"], AGENT_QUOTA.iso_utc(NOW))
      self.assertEqual(old["limits"][0]["last_observation"]["freshness"], "stale")
      self.assertEqual(old["account"]["plan"], "Current Plan")
    run.assert_not_called()
    with patch.dict(os.environ, {"AGENT_QUOTA_ANTIGRAVITY_SOURCE": "cli"}):
      self.assertEqual(AGENT_QUOTA.antigravity_source(), "cli")
    with patch.dict(os.environ, {"AGENT_QUOTA_ANTIGRAVITY_SOURCE": "off"}):
      self.assertFalse(AGENT_QUOTA.antigravity_enabled({}))
    path.unlink()
    with patch.object(AGENT_QUOTA.shutil, "which", return_value="/fixture/agy"):
      self.assertFalse(AGENT_QUOTA.antigravity_enabled({"antigravity": old}))

  def test_unbound_timeline_rows_do_not_merge_across_providers(self):
    events = [AGENT_QUOTA.timeline_event(
      NOW, 0, "exhausted", provider, provider=provider,
      account="account unknown", remaining=0,
    ) for provider in ("codex", "antigravity")]
    self.assertEqual(len(AGENT_QUOTA.merge_events(events)), 2)

  def test_antigravity_notification_omits_captured_identity(self):
    event = AGENT_QUOTA.timeline_event(
      NOW, 0, "reset", "Gemini", provider="antigravity",
      account="fixture@example.test", remaining=0,
    )
    rows = {AGENT_QUOTA.timeline_row_key(event): event}
    self.assertEqual(AGENT_QUOTA.timeline_alerts(rows, [], NOW, set()),
                     ["Gemini reset for Antigravity"])

  def test_explicit_selection_is_cached_without_spawning_and_opt_in_is_optional(self):
    self.assertFalse(AGENT_QUOTA.antigravity_enabled({}))
    with patch.dict(os.environ, {"AGENT_QUOTA_ANTIGRAVITY_SOURCE": "cli"}):
      self.assertTrue(AGENT_QUOTA.antigravity_enabled({}))
    buckets, partial = AGENT_QUOTA.agy_usage(usage_data())
    service = AGENT_QUOTA.agy_service(buckets, partial, "agy.print.usage", NOW, NOW)
    AGENT_QUOTA.write_cache(self.cache, {
      "schema_version": 3, "services": {
        "codex": {"limits": []}, "antigravity": service,
      },
    })
    with patch.object(AGENT_QUOTA, "utc_now", return_value=NOW), \
        patch.object(AGENT_QUOTA.subprocess, "run") as run, \
        contextlib.redirect_stdout(io.StringIO()) as out:
      result = AGENT_QUOTA.main(["antigravity", "--cached", "--compact"])
    run.assert_not_called()
    self.assertEqual(result, 0)
    self.assertEqual(list(json.loads(out.getvalue())["services"]), ["antigravity"])
    with patch.dict(os.environ, {"AGENT_QUOTA_ANTIGRAVITY_SOURCE": "off"}), \
        patch.object(AGENT_QUOTA.subprocess, "run") as run, \
        contextlib.redirect_stdout(io.StringIO()) as out:
      result = AGENT_QUOTA.main(["antigravity", "--cached", "--compact"])
    run.assert_not_called()
    self.assertEqual(result, 1)
    self.assertNotIn("antigravity", json.loads(out.getvalue())["services"])
    self.assertIn("antigravity", AGENT_QUOTA.load_cache(self.cache)["services"])

  def test_four_tmux_windows_render_without_identity(self):
    data = usage_data()
    data["groups"][1]["buckets"].append(bucket("3p-5h", "5h", 0.6))
    buckets, partial = AGENT_QUOTA.agy_usage(data)
    service = AGENT_QUOTA.agy_service(buckets, partial, "agy.statusLine", NOW, NOW)
    service["account"] = {"label": "fixture@example.test"}
    document = {"schema_version": 3, "services": {"antigravity": service}}
    for width, labels in [(160, ("Gemini wk", "Gemini 5h", "Claude/GPT wk",
                                 "Claude/GPT 5h")),
                          (90, ("Gemwk", "Gem5h", "Clawk", "Cla5h"))]:
      rendered = strip_tmux_styles(AGENT_STATUS.render_tmux(document, width, now=NOW))
      for label in labels:
        self.assertIn(label, rendered)
      self.assertNotIn("example.test", rendered)

  def test_live_cache_obeys_selected_source_and_no_cache_flag(self):
    buckets, partial = AGENT_QUOTA.agy_usage(usage_data())
    cli = AGENT_QUOTA.agy_service(buckets, partial, "agy.print.usage", NOW, NOW)
    observer = AGENT_QUOTA.agy_service(buckets, partial, "agy.statusLine", NOW, NOW)
    document = {"schema_version": 3, "services": {"antigravity": cli}}
    observed = {"schema_version": 3, "services": {"antigravity": observer}}
    AGENT_QUOTA.write_cache(self.cache, document)
    args = SimpleNamespace(cached=False, no_cache=False, provider="antigravity",
                           claude_file="/fixture/claude", claude_bin="claude",
                           claude_timeout=1, codex_bin="codex", timeout=1)
    with patch.dict(os.environ, {"AGENT_QUOTA_ANTIGRAVITY_SOURCE": "statusline"}), \
        patch.object(AGENT_QUOTA, "build_document", return_value=observed) as build:
      actual = AGENT_QUOTA.live_document(args, self.cache, NOW.timestamp())
      self.assertEqual(actual["services"]["antigravity"]["source_mode"], "statusline")
      build.assert_called_once()
    with patch.dict(os.environ, {"AGENT_QUOTA_ANTIGRAVITY_SOURCE": "off"}), \
        patch.object(AGENT_QUOTA, "load_cache") as load, \
        patch.object(AGENT_QUOTA, "write_cache") as write, \
        patch.object(AGENT_QUOTA, "build_document", return_value=observed) as build:
      args.no_cache = True
      actual = AGENT_QUOTA.live_document(args, self.cache, NOW.timestamp())
      self.assertNotIn("antigravity", actual["services"])
      load.assert_not_called()
      write.assert_not_called()
      self.assertIsNone(build.call_args.args[-1])

  def test_strict_requires_opted_in_provider_when_cached_source_is_hidden(self):
    buckets, partial = AGENT_QUOTA.agy_usage(usage_data())
    service = AGENT_QUOTA.agy_service(buckets, partial, "agy.print.usage", NOW, NOW)
    cached = {"services": {"codex": service, "claude_code": service,
                           "antigravity": service}}
    with patch.dict(os.environ, {"AGENT_QUOTA_ANTIGRAVITY_SOURCE": "statusline"}):
      selected = AGENT_QUOTA.visible_antigravity(cached, "all")
      self.assertNotIn("antigravity", selected["services"])
      self.assertEqual(AGENT_QUOTA.exit_status(selected, "all", False), 0)
      self.assertEqual(AGENT_QUOTA.exit_status(selected, "all", True), 1)

  def test_live_no_cache_frame_does_not_use_derived_cache_age(self):
    args = SimpleNamespace(timeline=True, agents=False, interval=30, notify=False,
                           color_on=False, no_cache=True)
    cache = Mock(spec=Path)
    cache.stat.return_value.st_mtime = NOW.timestamp() - 3600
    out = io.StringIO()
    with patch.object(AGENT_QUOTA, "live_document", return_value={"services": {}}), \
        patch.object(AGENT_QUOTA, "live_consumers", return_value=None):
      result = AGENT_QUOTA.run_live(args, cache, out=out, wait=lambda _: "q",
                                    clock=lambda: NOW.timestamp())
    self.assertEqual(result, 0)
    self.assertIn("quota report uncached", out.getvalue())
    cache.stat.assert_not_called()
