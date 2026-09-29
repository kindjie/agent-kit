from __future__ import annotations

import importlib.machinery
import importlib.util
import io
import json
import math
import re
import stat
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any
from unittest.mock import Mock, patch


KIT = Path(__file__).parents[1] / "bin"
SCRIPT = KIT / "agent-quota"
NOW = datetime(2026, 8, 19, 21, 30, tzinfo=timezone.utc)
OBSERVED = datetime(2026, 8, 19, 20, 0, tzinfo=timezone.utc)
RESET = "2026-08-21T18:00:00Z"


def load_script(name: str, path: Path) -> ModuleType:
  sys_module = __import__("sys")
  sys_module.dont_write_bytecode = True
  loader = importlib.machinery.SourceFileLoader(name, str(path))
  spec = importlib.util.spec_from_loader(loader.name, loader)
  assert spec is not None
  module = importlib.util.module_from_spec(spec)
  loader.exec_module(module)
  return module


AGENT_QUOTA = load_script("agent_quota", SCRIPT)


def usage_record(
  kind: str,
  percent: Any,
  *,
  reset: Any = RESET,
  model: str | None = None,
  model_id: str | None = None,
  active: bool | None = None,
) -> dict[str, Any]:
  record: dict[str, Any] = {
    "kind": kind,
    "percent": percent,
    "resets_at": reset,
  }
  if model is not None:
    record["scope"] = {
      "model": {
        "display_name": model,
        "id": model_id or model.lower().replace(" ", "-"),
      }
    }
  if active is not None:
    record["is_active"] = active
  return record


def limit_record(
  limit_id: str,
  used: float,
  *,
  observed: datetime = OBSERVED,
  reset: str | None = RESET,
  duration: int | None = 7 * 24 * 60 * 60,
  label: str = "weekly",
  bucket: dict[str, Any] | None = None,
) -> dict[str, Any]:
  observation = AGENT_QUOTA.make_observation(
    used,
    observed,
    AGENT_QUOTA.parse_timestamp(reset) if reset else None,
    3600,
    "test",
    "documented",
    NOW,
  )
  return {
    "limit_id": limit_id,
    "bucket": bucket
    or {"id": "account", "name": "All models", "scope_kind": "account"},
    "window": {"label": label, "duration_seconds": duration},
    "non_additive": False,
    "availability": None,
    "last_observation": observation,
  }


CLAUDE_HELP = """Usage: claude [options]

Options:
  --effort <level>                      Effort level for the current session
                                        (low, medium, high, xhigh, max)
  --model <model>                       Model for the current session.
"""


class AgentQuotaTest(unittest.TestCase):
  def setUp(self) -> None:
    self.temp_dir = tempfile.TemporaryDirectory()
    self.root = Path(self.temp_dir.name)
    self.state_path = self.root / ".claude.json"

  def tearDown(self) -> None:
    self.temp_dir.cleanup()

  def write_claude_cache(
    self,
    records: list[dict[str, Any]],
    *,
    observed: datetime = OBSERVED,
  ) -> None:
    cache = {
      "fetchedAtMs": int(observed.timestamp() * 1000),
      "utilization": {"limits": records},
    }
    self.state_path.write_text(
      json.dumps({"cachedUsageUtilization": cache}),
      encoding="utf-8",
    )

  def test_claude_schema_distinguishes_service_and_provider(self) -> None:
    self.write_claude_cache(
      [
        usage_record("weekly_all", 30),
        usage_record(
          "weekly_scoped",
          53,
          model="Fable 5",
          model_id="claude-fable-5",
        ),
      ]
    )

    service = AGENT_QUOTA.parse_claude_cache(self.state_path, now=NOW)

    self.assertEqual(service["provider_id"], "anthropic")
    self.assertEqual(service["service_id"], "claude_code")
    self.assertEqual(service["data_status"], "complete")
    self.assertEqual(service["refresh"]["status"], "succeeded")
    self.assertNotIn("source_path", json.dumps(service))

    weekly = service["limits"][0]
    self.assertEqual(weekly["bucket"]["scope_kind"], "account")
    self.assertEqual(weekly["last_observation"]["remaining_percent"], 70)
    self.assertEqual(
      weekly["last_observation"]["observed_at"],
      "2026-08-19T20:00:00Z",
    )
    self.assertEqual(
      weekly["last_observation"]["source"]["id"],
      "claude.cachedUsageUtilization",
    )

    fable = service["limits"][1]
    self.assertEqual(fable["bucket"]["name"], "Fable 5")
    self.assertEqual(fable["bucket"]["scope_kind"], "model")
    self.assertTrue(fable["non_additive"])

  def test_fetched_at_ms_is_converted_before_freshness_evaluation(self) -> None:
    self.write_claude_cache([usage_record("weekly_all", 30)])

    service = AGENT_QUOTA.parse_claude_cache(self.state_path, now=NOW)
    observation = service["limits"][0]["last_observation"]

    self.assertEqual(observation["freshness"], "stale")
    self.assertEqual(observation["period_relation"], "current")
    self.assertEqual(observation["fresh_until"], "2026-08-19T21:00:00Z")

  def test_ended_observation_remains_historical(self) -> None:
    self.write_claude_cache(
      [usage_record("weekly_all", 30, reset="2026-08-19T21:30:00Z")]
    )

    service = AGENT_QUOTA.parse_claude_cache(self.state_path, now=NOW)
    limit = service["limits"][0]

    self.assertNotIn("current_value", limit)
    self.assertEqual(
      limit["last_observation"]["period_relation"],
      "ended",
    )
    self.assertEqual(limit["last_observation"]["remaining_percent"], 70)
    self.assertFalse(AGENT_QUOTA.has_usable_current_value(service))

  def test_unknown_reset_never_claims_current_period(self) -> None:
    self.write_claude_cache([usage_record("weekly_all", 30, reset=None)])

    service = AGENT_QUOTA.parse_claude_cache(self.state_path, now=NOW)
    observation = service["limits"][0]["last_observation"]

    self.assertEqual(observation["period_relation"], "unknown")
    self.assertFalse(AGENT_QUOTA.has_usable_current_value(service))

  def test_future_observation_has_unknown_freshness_and_warning(self) -> None:
    self.write_claude_cache(
      [usage_record("weekly_all", 30)],
      observed=datetime(2026, 8, 19, 22, 0, tzinfo=timezone.utc),
    )

    service = AGENT_QUOTA.parse_claude_cache(self.state_path, now=NOW)
    observation = service["limits"][0]["last_observation"]

    self.assertEqual(observation["freshness"], "unknown")
    self.assertEqual(service["data_status"], "partial")
    self.assertIn(
      "future_observation",
      {warning["code"] for warning in service["warnings"]},
    )

  def test_invalid_records_warn_instead_of_clamping(self) -> None:
    self.write_claude_cache(
      [
        usage_record("weekly_all", 120),
        usage_record("five_hour", math.inf),
      ]
    )

    service = AGENT_QUOTA.parse_claude_cache(self.state_path, now=NOW)

    self.assertEqual(service["data_status"], "unavailable")
    self.assertEqual(service["limits"], [])
    self.assertEqual(
      {warning["code"] for warning in service["warnings"]},
      {"invalid_percentage"},
    )

  def test_invalid_timestamp_is_null_and_marks_partial(self) -> None:
    self.write_claude_cache(
      [usage_record("weekly_all", 30, reset="not-a-time")]
    )

    service = AGENT_QUOTA.parse_claude_cache(self.state_path, now=NOW)
    observation = service["limits"][0]["last_observation"]

    self.assertEqual(service["data_status"], "partial")
    self.assertIsNone(observation["reset_at"])
    self.assertEqual(observation["period_relation"], "unknown")
    self.assertIn(
      "invalid_timestamp",
      {warning["code"] for warning in service["warnings"]},
    )

  def test_duplicate_scoped_records_prefer_active_then_conservative(
    self,
  ) -> None:
    self.write_claude_cache(
      [
        usage_record("weekly_scoped", 90, model="Fable 5", active=False),
        usage_record("weekly_scoped", 40, model="Fable 5", active=True),
        usage_record("weekly_scoped", 55, model="Fable 5", active=True),
      ]
    )

    service = AGENT_QUOTA.parse_claude_cache(self.state_path, now=NOW)
    fable = service["limits"][0]

    self.assertEqual(fable["last_observation"]["used_percent"], 55)
    self.assertEqual(service["data_status"], "partial")
    self.assertIn(
      "conflicting_active_records",
      {warning["code"] for warning in service["warnings"]},
    )

  def test_codex_multi_bucket_schema_and_reset_boundary(self) -> None:
    result = {
      "rateLimitsByLimitId": {
        "codex": {
          "limitId": "codex",
          "planType": "pro",
          "primary": {
            "usedPercent": 25,
            "windowDurationMins": 300,
            "resetsAt": int(datetime(2026, 8, 19, 22, 0,
                                      tzinfo=timezone.utc).timestamp()),
          },
          "secondary": {
            "usedPercent": 54,
            "windowDurationMins": 10080,
            "resetsAt": int(datetime(2026, 8, 19, 21, 30,
                                      tzinfo=timezone.utc).timestamp()),
          },
        },
        "spark": {
          "limitId": "spark",
          "limitName": "GPT-5.3-Codex-Spark",
          "secondary": {
            "usedPercent": 0,
            "windowDurationMins": 10080,
            "resetsAt": int(datetime(2026, 8, 26, 21, 30,
                                      tzinfo=timezone.utc).timestamp()),
          },
        },
      }
    }

    service = AGENT_QUOTA.parse_codex_result(result, now=NOW)

    self.assertEqual(service["provider_id"], "openai")
    self.assertEqual(service["service_id"], "codex")
    self.assertEqual(len(service["limits"]), 3)
    weekly = next(
      item
      for item in service["limits"]
      if item["bucket"]["id"] == "codex"
      and item["window"]["label"] == "weekly"
    )
    self.assertEqual(
      weekly["last_observation"]["period_relation"],
      "ended",
    )
    spark = next(
      item for item in service["limits"]
      if item["bucket"]["id"] == "spark"
    )
    self.assertTrue(spark["non_additive"])
    self.assertEqual(
      spark["last_observation"]["source"]["documentation_status"],
      "documented",
    )

  def codex_with_credits(self, credits: Any) -> dict[str, Any]:
    return AGENT_QUOTA.parse_codex_result({"rateLimits": {
      "limitId": "codex",
      "credits": credits,
      "primary": {
        "usedPercent": 100,
        "windowDurationMins": 10080,
        "resetsAt": int(NOW.timestamp()) + 3600,
      },
    }}, now=NOW)

  def test_codex_credits_are_separate_from_exhausted_quota(self) -> None:
    service = self.codex_with_credits({
      "hasCredits": True, "unlimited": False, "balance": "1234.567800",
    })
    document = AGENT_QUOTA.reevaluate_document({
      "schema_version": 3, "services": {"codex": service},
    }, NOW)
    service = document["services"]["codex"]
    credit = service["credits"][0]
    self.assertEqual(credit["balance"], "1234.567800")
    self.assertTrue(credit["has_credits"])
    self.assertFalse(credit["unlimited"])
    self.assertEqual(credit["freshness"], "fresh")
    self.assertEqual(len(service["limits"]), 1)
    self.assertEqual(service["limits"][0]["pace"]["state"], "behind")
    output = AGENT_QUOTA.render_verbose(document)
    self.assertIn("Codex credits: 1,234.57 credits", output)
    self.assertIn("0% remaining", output)
    self.assertIn("separate from subscription quota", output)

  def test_codex_credit_states_and_invalid_values(self) -> None:
    for raw, expected in [
      (None, "UNKNOWN"),
      ({"hasCredits": False, "unlimited": False, "balance": "0"},
       "0.00 credits"),
      ({"hasCredits": True, "unlimited": True, "balance": None},
       "UNLIMITED"),
      ({"hasCredits": True, "unlimited": False, "balance": None},
       "AVAILABLE; balance UNKNOWN"),
    ]:
      with self.subTest(raw=raw):
        service = self.codex_with_credits(raw)
        output = AGENT_QUOTA.render_verbose({"services": {"codex": service}})
        self.assertIn("Codex credits: " + expected, output)
    for value in ("NaN", "Infinity", "-1", True, {}, "\x1b[31m"):
      with self.subTest(value=value):
        service = self.codex_with_credits({
          "hasCredits": True, "unlimited": False, "balance": value,
        })
        self.assertIsNone(service["credits"][0]["balance"])
        self.assertIn("invalid_credits", {
          item["code"] for item in service["warnings"]
        })

  def test_codex_credits_age_and_survive_failed_refresh(self) -> None:
    previous = self.codex_with_credits({
      "hasCredits": True, "unlimited": False, "balance": "12.5",
    })
    later = NOW + timedelta(seconds=AGENT_QUOTA.CODEX_FRESH_SECONDS)
    failed = AGENT_QUOTA.unavailable_service(
      "openai", "codex", "Codex", "test", "failed", later,
    )
    retained = AGENT_QUOTA.retain_last_good(failed, previous, now=later)
    self.assertEqual(retained["credits"][0]["balance"], "12.5")
    self.assertEqual(retained["credits"][0]["observed_at"],
                     AGENT_QUOTA.iso_utc(NOW))
    self.assertEqual(retained["credits"][0]["freshness"], "stale")
    cached = AGENT_QUOTA.reevaluate_service(previous, later)
    self.assertEqual(cached["credits"][0]["freshness"], "stale")
    fresh_without = self.codex_with_credits(None)
    refreshed = AGENT_QUOTA.retain_last_good(
      fresh_without, previous, now=later,
    )
    self.assertEqual(refreshed["credits"], [])

  def test_credit_pace_uses_balance_history_and_resets_after_topup(self):
    def snapshot(balance, when, previous=None):
      service = self.codex_with_credits({
        "hasCredits": True, "unlimited": False, "balance": balance,
      })
      service["credits"][0]["observed_at"] = AGENT_QUOTA.iso_utc(when)
      return AGENT_QUOTA.merge_credit_history(service, previous, when)

    first = snapshot("100", NOW)
    credit = first["credits"][0]
    self.assertIsNone(AGENT_QUOTA.credit_pace(credit, NOW)[
      "rate_credits_per_hour"
    ])
    later = NOW + timedelta(hours=1)
    second = snapshot("80", later, first)
    pace = AGENT_QUOTA.credit_pace(second["credits"][0], later)
    self.assertEqual(pace["rate_credits_per_hour"], 20)
    self.assertEqual(pace["seconds_until_empty"], 4 * 3600)
    self.assertEqual(pace["exhausts_at"],
                     AGENT_QUOTA.iso_utc(later + timedelta(hours=4)))
    aged = AGENT_QUOTA.credit_pace(
      second["credits"][0], later + timedelta(minutes=30),
    )
    self.assertEqual(aged["exhausts_at"], pace["exhausts_at"])
    self.assertEqual(aged["seconds_until_empty"], 3.5 * 3600)
    output = AGENT_QUOTA.render_verbose({"services": {
      "codex": AGENT_QUOTA.reevaluate_service(second, later),
    }})
    self.assertIn("20.00 credits/h", output)
    self.assertIn("4h 0m until empty", output)

    topup_time = later + timedelta(minutes=30)
    topped = snapshot("200", topup_time, second)
    self.assertIsNone(AGENT_QUOTA.credit_pace(topped["credits"][0],
                                            topup_time)[
      "rate_credits_per_hour"
    ])
    final_time = topup_time + timedelta(minutes=30)
    final = snapshot("190", final_time, topped)
    self.assertEqual(AGENT_QUOTA.credit_pace(final["credits"][0],
                                            final_time)[
      "rate_credits_per_hour"
    ], 20)
    repeated = snapshot("190", final_time, final)
    self.assertEqual(repeated["credits"][0]["history"],
                     final["credits"][0]["history"])
    stale = AGENT_QUOTA.credit_pace(final["credits"][0],
                                   final_time + timedelta(hours=4))
    self.assertIsNone(stale["rate_credits_per_hour"])

  def test_credit_pace_zero_unlimited_and_short_history(self):
    credit = self.codex_with_credits({
      "hasCredits": True, "unlimited": False, "balance": "100",
    })["credits"][0]
    for span, unlimited, expected in [(899, False, None),
                                       (900, False, 0),
                                       (900, True, None)]:
      with self.subTest(span=span, unlimited=unlimited):
        credit["unlimited"] = unlimited
        credit["history"] = [
          {"observed_at": AGENT_QUOTA.iso_utc(NOW - timedelta(seconds=span)),
           "balance": "100"},
          {"observed_at": AGENT_QUOTA.iso_utc(NOW), "balance": "100"},
        ]
        pace = AGENT_QUOTA.credit_pace(credit, NOW)
        self.assertEqual(pace["rate_credits_per_hour"], expected)
        self.assertIsNone(pace["exhausts_at"])

  def test_codex_invalid_percentage_is_rejected(self) -> None:
    service = AGENT_QUOTA.parse_codex_result(
      {
        "rateLimits": {
          "limitId": "codex",
          "primary": {
            "usedPercent": -1,
            "windowDurationMins": 300,
            "resetsAt": int(NOW.timestamp()) + 3600,
          },
        }
      },
      now=NOW,
    )

    self.assertEqual(service["data_status"], "unavailable")
    self.assertEqual(service["limits"], [])
    self.assertEqual(service["warnings"][0]["code"], "invalid_percentage")

  def test_failed_refresh_retains_last_good_observations(self) -> None:
    self.write_claude_cache([usage_record("weekly_all", 30)])
    previous = AGENT_QUOTA.parse_claude_cache(self.state_path, now=NOW)
    failed = AGENT_QUOTA.unavailable_service(
      "anthropic",
      "claude_code",
      "Claude Code",
      "cache_unavailable",
      "Claude Code usage cache is unavailable",
      NOW,
    )

    merged = AGENT_QUOTA.retain_last_good(failed, previous, now=NOW)

    self.assertEqual(merged["data_status"], "partial")
    self.assertEqual(merged["refresh"]["status"], "failed")
    self.assertTrue(merged["retained_last_good"])
    self.assertEqual(
      merged["limits"][0]["last_observation"]["remaining_percent"],
      70,
    )

  def test_brief_is_explicit_about_stale_and_historical_values(self) -> None:
    self.write_claude_cache(
      [
        usage_record("weekly_all", 30),
        usage_record(
          "weekly_scoped",
          53,
          reset="2026-08-19T21:00:00Z",
          model="Fable 5",
        ),
      ]
    )
    service = AGENT_QUOTA.parse_claude_cache(self.state_path, now=NOW)
    doc = {
      "schema_version": 3,
      "generated_at": "2026-08-19T21:30:00Z",
      "services": {"claude_code": service},
    }

    output = AGENT_QUOTA.render_verbose(doc)

    self.assertIn("Percentages are REMAINING", output)
    self.assertIn("do not add them", output)
    self.assertIn(
      "Claude Code weekly: 70% remaining [CURRENT PERIOD; STALE].",
      output,
    )
    self.assertIn(
      "Claude Code Fable 5 weekly: current remaining UNKNOWN "
      "[PERIOD ENDED].",
      output,
    )
    self.assertIn("Last observation: 47% remaining", output)

  def test_pace_projection_is_anchored_at_observation_time(self) -> None:
    # Observed 20:00 with a 5h window resetting 23:00: 40% elapsed.
    five_hour = 5 * 60 * 60
    reset = "2026-08-19T23:00:00Z"
    behind = limit_record("a", 50, reset=reset, duration=five_hour)
    on_pace = limit_record("b", 38, reset=reset, duration=five_hour)
    surplus = limit_record("c", 30, reset=reset, duration=five_hour)

    pace = AGENT_QUOTA.limit_pace(behind, NOW)
    self.assertEqual(pace["state"], "behind")
    self.assertEqual(pace["projected_unused_percent"], -25)
    self.assertEqual(pace["reset_in_seconds"], 5400)
    self.assertEqual(pace["window_share_remaining_percent"], 30)
    self.assertFalse(pace["reset_soon"])
    self.assertIn(pace["state"], AGENT_QUOTA.PACE_STATES)
    # Same record 45 minutes later: projection holds, time to reset moves.
    again = AGENT_QUOTA.limit_pace(behind, NOW + timedelta(minutes=45))
    self.assertEqual(again["projected_unused_percent"], -25)
    self.assertEqual(again["reset_in_seconds"], 2700)
    self.assertEqual(AGENT_QUOTA.limit_pace(on_pace, NOW)["state"], "on_pace")
    later = AGENT_QUOTA.limit_pace(surplus, NOW + timedelta(minutes=45))
    self.assertEqual(later["state"], "surplus")
    self.assertEqual(later["projected_unused_percent"], 25)
    self.assertEqual(later["reset_in_seconds"], 2700)
    self.assertTrue(later["reset_soon"])

  def test_pace_is_early_or_unknown_without_enough_inputs(self) -> None:
    week = 7 * 24 * 60 * 60
    early_reset = (OBSERVED + timedelta(hours=167)).strftime(
      "%Y-%m-%dT%H:%M:%SZ"
    )
    early = AGENT_QUOTA.limit_pace(
      limit_record("a", 2, reset=early_reset, duration=week), NOW
    )
    self.assertEqual(early["state"], "early")
    self.assertIsNone(early["projected_unused_percent"])
    self.assertIsNotNone(early["window_share_remaining_percent"])

    no_window = AGENT_QUOTA.limit_pace(
      limit_record("b", 40, duration=None, label="unknown"), NOW
    )
    self.assertEqual(no_window["state"], "unknown")
    self.assertIsNone(no_window["window_share_remaining_percent"])
    self.assertEqual(no_window["reset_in_seconds"], 160200)

    ended = AGENT_QUOTA.limit_pace(
      limit_record("c", 40, reset="2026-08-19T21:00:00Z"), NOW
    )
    self.assertEqual(ended["state"], "unknown")
    self.assertIsNone(ended["reset_in_seconds"])

  def test_binding_prefers_lowest_projection_and_skips_early(self) -> None:
    five_hour = 5 * 60 * 60
    reset = "2026-08-19T23:00:00Z"
    early_reset = (OBSERVED + timedelta(hours=167)).strftime(
      "%Y-%m-%dT%H:%M:%SZ"
    )
    service = {
      "limits": [
        limit_record("early", 1, reset=early_reset),
        limit_record("behind", 50, reset=reset, duration=five_hour),
        limit_record("surplus", 30),
      ]
    }
    evaluated = AGENT_QUOTA.reevaluate_service(service, NOW)
    self.assertEqual(evaluated["binding_limit_id"], "behind")
    only_early = AGENT_QUOTA.reevaluate_service(
      {"limits": [limit_record("early", 1, reset=early_reset)]}, NOW
    )
    self.assertIsNone(only_early["binding_limit_id"])

  def test_cached_document_without_pace_gains_it_on_reevaluation(self) -> None:
    document = {
      "schema_version": 3,
      "generated_at": "2026-08-19T20:00:00Z",
      "services": {"claude_code": {"limits": [limit_record("a", 30)]}},
    }
    result = AGENT_QUOTA.reevaluate_document(document, NOW)
    limit = result["services"]["claude_code"]["limits"][0]
    self.assertEqual(limit["pace"]["state"], "surplus")
    self.assertEqual(result["services"]["claude_code"]["binding_limit_id"], "a")
    self.assertNotIn("pace", document["services"]["claude_code"]["limits"][0])

  def test_verbose_reports_pace_and_binding(self) -> None:
    five_hour = 5 * 60 * 60
    document = AGENT_QUOTA.reevaluate_document(
      {
        "schema_version": 3,
        "services": {
          "claude_code": {
            "display_name": "Claude Code",
            "refresh": {"status": "succeeded", "attempted_at": "x"},
            "limits": [
              limit_record(
                "a",
                70,
                reset="2026-08-19T22:00:00Z",
                duration=five_hour,
                label="5h",
              ),
              limit_record("b", 30),
            ],
          }
        },
      },
      NOW,
    )
    output = AGENT_QUOTA.render_verbose(document)
    self.assertIn("SURPLUS = at least 25% projected unused", output)
    self.assertIn(
      "Resets in 30m (10% of window). Pace: BEHIND; projected -16.7% "
      "unused at reset. RESET SOON.",
      output,
    )
    self.assertIn("Pace: SURPLUS; projected 58.7% unused at reset.", output)
    self.assertIn(
      "Binding (period-average): Claude Code 5h (BEHIND).", output
    )

  def test_verbose_surfaces_nonbinding_burn_risk(self) -> None:
    scoped = limit_record(
      "astra",
      20,
      observed=NOW,
      bucket={
        "id": "astra",
        "name": "GPT-6 Astra",
        "scope_kind": "provider_defined",
      },
    )
    scoped["history"] = [
      {
        "observed_at": "2026-08-19T20:30:00Z",
        "used_percent": 10,
        "reset_at": RESET,
      },
      {
        "observed_at": "2026-08-19T21:30:00Z",
        "used_percent": 20,
        "reset_at": RESET,
      },
    ]
    document = AGENT_QUOTA.reevaluate_document(
      {
        "schema_version": 3,
        "services": {
          "codex": {
            "display_name": "Codex",
            "limits": [limit_record("account", 30, observed=NOW), scoped],
          },
        },
      },
      NOW,
    )
    service = document["services"]["codex"]
    self.assertEqual(service["binding_limit_id"], "account")
    self.assertEqual(service["limits"][1]["pace"]["state"], "surplus")
    self.assertTrue(service["limits"][1]["burn"]["exhausts_before_reset"])
    risk = "Recent-burn constraint: Codex GPT-6 Astra weekly [FRESH]"
    output = AGENT_QUOTA.render_verbose(document)
    self.assertIn("Binding (period-average): Codex weekly (SURPLUS).", output)
    self.assertIn(risk, output)
    self.assertIn("Conserve this bucket even if pace is SURPLUS.", output)
    stale = AGENT_QUOTA.reevaluate_document(
      document, NOW + timedelta(hours=1)
    )
    self.assertIn(
      "Recent-burn constraint: Codex GPT-6 Astra weekly [STALE]",
      AGENT_QUOTA.render_verbose(stale),
    )
    ended = AGENT_QUOTA.reevaluate_document(
      document, AGENT_QUOTA.parse_timestamp(RESET)
    )
    self.assertNotIn(
      "Recent-burn constraint:", AGENT_QUOTA.render_verbose(ended)
    )

  def test_models_document_reads_live_sources_and_fails_visibly(self) -> None:
    self.assertEqual(
      AGENT_QUOTA.parse_claude_effort_levels(CLAUDE_HELP),
      ["low", "medium", "high", "xhigh", "max"],
    )
    self.assertIsNone(AGENT_QUOTA.parse_claude_effort_levels("no flags"))

    models_path = self.root / "models_cache.json"
    models_path.write_text(
      json.dumps(
        {
          "fetched_at": "2026-08-19T20:00:00Z",
          "models": [
            {
              "slug": "gpt-test",
              "display_name": "GPT Test",
              "default_reasoning_level": "medium",
              "supported_reasoning_levels": [
                {"effort": "low"},
                {"effort": "ultra"},
              ],
              "service_tiers": [{"id": "priority", "name": "Fast"}],
            },
            {
              "slug": "gpt-hidden",
              "visibility": "hide",
              "supported_reasoning_levels": [],
            },
            {"display_name": "malformed"},
          ],
        }
      ),
      encoding="utf-8",
    )
    stub = self.root / "claude"
    stub.write_text("#!/bin/sh\ncat <<'EOF'\n" + CLAUDE_HELP + "EOF\n")
    stub.chmod(0o755)

    document = AGENT_QUOTA.build_models_document(str(stub), 5, models_path, NOW)
    self.assertEqual(document["document"], "models")
    codex = document["tools"]["codex"]
    self.assertEqual(
      codex["models"],
      [
        {
          "slug": "gpt-test",
          "display_name": "GPT Test",
          "default_effort": "medium",
          "effort_levels": ["low", "ultra"],
          "service_tiers": ["Fast"],
        }
      ],
    )
    self.assertEqual(codex["warnings"][0]["code"], "codex_models_schema")
    self.assertEqual(codex["source"]["documentation_status"], "undocumented")
    self.assertEqual(
      document["tools"]["claude_code"]["effort_levels"],
      ["low", "medium", "high", "xhigh", "max"],
    )
    self.assertEqual(AGENT_QUOTA.models_exit_status(document), 0)

    output = AGENT_QUOTA.render_models_brief(document)
    self.assertIn(
      "gpt-test: default medium; levels low, ultra; tiers Fast.", output
    )
    self.assertIn("Warning codex_models_schema", output)

    broken = AGENT_QUOTA.load_codex_models(self.root / "missing.json")
    self.assertEqual(broken["warnings"][0]["code"], "codex_models_unreadable")
    self.assertEqual(
      AGENT_QUOTA.models_exit_status(
        {"tools": {"claude_code": {}, "codex": broken}}
      ),
      1,
    )

  def test_history_merges_thins_prunes_and_keeps_earlier_periods(
    self,
  ) -> None:
    def entry(hours_before: float, used: float, reset: str = RESET) -> dict:
      observed = OBSERVED - timedelta(hours=hours_before)
      return {
        "observed_at": observed.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "reset_at": reset,
        "used_percent": used,
      }

    current = {"limits": [limit_record("a", 40)]}
    previous = {
      "limits": [
        {
          "limit_id": "a",
          "history": [
            entry(30, 5),
            entry(2, 20),
            entry(1, 30),
            entry(0.05, 40),
          ],
        }
      ]
    }
    merged = AGENT_QUOTA.merge_history(current, previous, NOW)
    history = merged["limits"][0]["history"]
    self.assertEqual([item["used_percent"] for item in history], [20, 30, 40])
    self.assertNotIn(
      "history", current["limits"][0], "merge must not mutate its input"
    )

    repeated = AGENT_QUOTA.merge_history(
      current, {"limits": [{"limit_id": "a", "history": history}]}, NOW
    )
    self.assertEqual(len(repeated["limits"][0]["history"]), 3)

    crossed = AGENT_QUOTA.merge_history(
      current,
      {
        "limits": [
          {"limit_id": "a", "history": [entry(1, 90, "2026-08-19T21:00:00Z")]}
        ]
      },
      NOW,
    )
    crossed_history = crossed["limits"][0]["history"]
    self.assertEqual(
      [i["used_percent"] for i in crossed_history],
      [90, 40],
      "earlier reset periods stay in history for velocity",
    )

    fresh = AGENT_QUOTA.merge_history(current, None, NOW)
    self.assertEqual(len(fresh["limits"][0]["history"]), 1)

  def test_burn_uses_trailing_window_and_flags_exhaustion(self) -> None:
    def with_history(limit: dict, points: list[tuple[float, float]]) -> dict:
      limit["history"] = [
        {
          "observed_at": (OBSERVED - timedelta(hours=h)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
          ),
          "reset_at": limit["last_observation"]["reset_at"],
          "used_percent": used,
        }
        for h, used in points
      ]
      return limit

    weekly = with_history(
      limit_record("a", 40), [(4, 0), (2, 20), (1, 30), (0, 40)]
    )
    burn = AGENT_QUOTA.limit_burn(weekly, NOW)
    self.assertEqual(burn["rate_percent_per_hour"], 10)
    self.assertEqual(burn["span_seconds"], 3600)
    self.assertEqual(burn["exhausts_at"], "2026-08-20T02:00:00Z")
    self.assertTrue(burn["exhausts_before_reset"])

    five_hour = with_history(
      limit_record("b", 40, reset="2026-08-19T23:00:00Z", duration=18000),
      [(1, 30), (0, 40)],
    )
    five_hour_burn = AGENT_QUOTA.limit_burn(five_hour, NOW)
    self.assertFalse(five_hour_burn["exhausts_before_reset"])

    single = with_history(limit_record("c", 40), [(0, 40)])
    single_burn = AGENT_QUOTA.limit_burn(single, NOW)
    self.assertIsNone(single_burn["rate_percent_per_hour"])
    short = with_history(limit_record("d", 40), [(0.1, 30), (0, 40)])
    short_burn = AGENT_QUOTA.limit_burn(short, NOW)
    self.assertIsNone(short_burn["rate_percent_per_hour"])
    falling = AGENT_QUOTA.limit_burn(
      with_history(limit_record("e", 40), [(1, 50), (0, 40)]), NOW
    )
    self.assertEqual(falling["rate_percent_per_hour"], -10)
    self.assertIsNone(falling["exhausts_at"])
    self.assertFalse(falling["exhausts_before_reset"])
    stale_points = with_history(
      limit_record("f", 40), [(5, 0), (4, 10), (0, 40)]
    )
    self.assertIsNone(
      AGENT_QUOTA.limit_burn(stale_points, NOW)["rate_percent_per_hour"]
    )

  def test_velocity_sums_increments_across_resets_per_window(self) -> None:
    def entry(hours_before: float, used: float, reset: str = RESET) -> dict:
      observed = NOW - timedelta(hours=hours_before)
      return {
        "observed_at": observed.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "reset_at": reset,
        "used_percent": used,
      }

    five_hour = limit_record(
      "a", 30, reset="2026-08-20T01:00:00Z", duration=18000, label="5h"
    )
    old_reset = "2026-08-19T20:00:00Z"
    five_hour["history"] = [
      entry(26, 0, old_reset),
      entry(20, 40, old_reset),
      entry(5.1, 90, old_reset),
      entry(1.5, 95, old_reset),
      entry(1.4, 5),
      entry(1.1, 10),
      entry(0.5, 10),
      entry(0, 30),
    ]
    velocity = AGENT_QUOTA.limit_velocity(five_hour, NOW)
    self.assertEqual(
      velocity["1h"],
      {
        "used_percent": 20,
        "rate_percent_per_hour": 18.18,
        "span_seconds": 3960,
      },
      "baseline is the last entry shortly before the cutoff",
    )
    self.assertEqual(
      velocity["5h"]["used_percent"],
      35,
      "a reset counts the new period's usage on top of the old period",
    )
    self.assertEqual(velocity["5h"]["span_seconds"], int(5.1 * 3600))
    self.assertEqual(
      velocity["24h"],
      {
        "used_percent": 85,
        "rate_percent_per_hour": 4.25,
        "span_seconds": 20 * 3600,
      },
      "a baseline far before the cutoff is skipped; coverage shrinks",
    )

    drifted = limit_record("b", 40)
    drifted["history"] = [
      entry(2, 30, "2026-08-21T18:00:30Z"),
      entry(1, 50),
      entry(0, 40),
    ]
    drifted_velocity = AGENT_QUOTA.limit_velocity(drifted, NOW)
    self.assertEqual(
      drifted_velocity["5h"]["used_percent"],
      20,
      "reset drift within tolerance is one period; decreases do not count",
    )

    sparse = limit_record("c", 40)
    sparse["history"] = [entry(0.1, 30), entry(0, 40)]
    self.assertEqual(
      AGENT_QUOTA.limit_velocity(sparse, NOW)["1h"],
      {
        "used_percent": None,
        "rate_percent_per_hour": None,
        "span_seconds": None,
      },
    )
    self.assertEqual(
      AGENT_QUOTA.limit_velocity(limit_record("d", 40), NOW)["24h"][
        "used_percent"
      ],
      None,
    )

    document = AGENT_QUOTA.reevaluate_document(
      {
        "schema_version": 3,
        "services": {
          "claude_code": {
            "display_name": "Claude Code",
            "refresh": {"status": "succeeded", "attempted_at": "x"},
            "limits": [five_hour, sparse],
          }
        },
      },
      NOW,
    )
    brief = AGENT_QUOTA.render_verbose(document)
    self.assertIn(
      "Velocity: 20% in 1h, 35% in 5h, 85% in 24h (covers 20h 0m).",
      brief,
    )
    self.assertNotIn("Velocity: 1h UNKNOWN", brief)
    covered = limit_record("e", 40)
    covered["history"] = [entry(2, 20), entry(0, 40)]
    covered_document = AGENT_QUOTA.reevaluate_document(
      {"schema_version": 3, "services": {"codex": {
        "display_name": "Codex",
        "refresh": {"status": "succeeded", "attempted_at": "x"},
        "limits": [covered],
      }}},
      NOW,
    )
    self.assertIn(
      "Velocity: 1h UNKNOWN, 20% in 5h (covers 2h 0m), "
      "20% in 24h (covers 2h 0m).",
      AGENT_QUOTA.render_verbose(covered_document),
    )

  def test_reevaluation_derives_burn_only_with_history(self) -> None:
    with_history = limit_record("a", 40)
    with_history["history"] = [
      {
        "observed_at": "2026-08-19T19:00:00Z",
        "reset_at": RESET,
        "used_percent": 30,
      },
      {
        "observed_at": "2026-08-19T20:00:00Z",
        "reset_at": RESET,
        "used_percent": 40,
      },
    ]
    document = AGENT_QUOTA.reevaluate_document(
      {
        "schema_version": 3,
        "services": {
          "claude_code": {
            "display_name": "Claude Code",
            "refresh": {"status": "succeeded", "attempted_at": "x"},
            "limits": [with_history, limit_record("b", 40)],
          }
        },
      },
      NOW,
    )
    limits = document["services"]["claude_code"]["limits"]
    self.assertTrue(limits[0]["burn"]["exhausts_before_reset"])
    self.assertIsNone(limits[1]["burn"]["rate_percent_per_hour"])
    self.assertIn(
      "Recent burn 10%/h over 1h 0m: EXHAUSTS BEFORE RESET "
      "(2026-08-20T02:00:00Z).",
      AGENT_QUOTA.render_verbose(document),
    )

  def test_atomic_cache_is_private_and_load_rejects_old_schema(self) -> None:
    path = self.root / "cache" / "report.json"
    doc = {
      "schema_version": 3,
      "generated_at": "2026-08-19T21:30:00Z",
      "services": {},
    }

    AGENT_QUOTA.write_cache(path, doc)

    self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
    self.assertEqual(AGENT_QUOTA.load_cache(path), doc)
    path.write_text('{"schema_version":2}', encoding="utf-8")
    self.assertIsNone(AGENT_QUOTA.load_cache(path))

  def test_cache_lock_rejects_a_concurrent_refresh(self) -> None:
    path = self.root / "cache" / "report.json"

    with AGENT_QUOTA.cache_lock(path) as first:
      with AGENT_QUOTA.cache_lock(path) as second:
        self.assertTrue(first)
        self.assertFalse(second)

  def test_exit_status_treats_missing_requested_service_as_strict_failure(
    self,
  ) -> None:
    self.write_claude_cache([usage_record("weekly_all", 30)])
    service = AGENT_QUOTA.parse_claude_cache(self.state_path, now=NOW)
    doc = {
      "schema_version": 3,
      "generated_at": "2026-08-19T21:30:00Z",
      "services": {"claude_code": service},
    }

    self.assertEqual(AGENT_QUOTA.exit_status(doc, "all", False), 0)
    self.assertEqual(AGENT_QUOTA.exit_status(doc, "all", True), 1)

  def test_compact_tables_prioritize_constraints_and_hide_details(self):
    service = self.codex_with_credits({
      "hasCredits": True, "unlimited": False, "balance": "2500",
    })
    service = AGENT_QUOTA.reevaluate_service(service, NOW)
    doc = {"generated_at": AGENT_QUOTA.iso_utc(NOW),
           "services": {"codex": service}}
    output = AGENT_QUOTA.render_brief(doc)
    self.assertTrue(output.startswith("Attention: Codex weekly exhausted"))
    self.assertIn("Remaining", output)
    self.assertIn("Resets in", output)
    self.assertIn("2,500.00", output)
    self.assertIn("Collecting history", output)
    self.assertNotIn("Observed ", output)
    self.assertNotIn("projected -", output)
    self.assertNotIn("Velocity:", output)
    self.assertLess(len(output.splitlines()), 18)
    stale = AGENT_QUOTA.reevaluate_service(
      service, NOW + timedelta(seconds=121),
    )
    output = AGENT_QUOTA.render_brief({"services": {"codex": stale}})
    self.assertIn("[STALE]", output)
    ended = AGENT_QUOTA.reevaluate_service(
      service, NOW + timedelta(hours=2),
    )
    output = AGENT_QUOTA.render_brief({"services": {"codex": ended}})
    self.assertNotIn("exhausted", output.lower())
    self.assertIn("Period ended", output)
    self.assertNotIn("0%", output)

  def test_compact_table_recent_burn_overrides_surplus(self):
    limit = limit_record("test", 10, observed=NOW)
    limit["pace"] = {"state": "surplus", "reset_in_seconds": 3600}
    limit["burn"] = {"exhausts_before_reset": True}
    output = AGENT_QUOTA.render_brief({"services": {"codex": {
      "display_name": "Codex", "limits": [limit],
      "refresh": {"status": "failed", "error": {"message": "offline"}},
    }}})
    self.assertIn("Recent burn too high", output)
    self.assertIn("offline", output)
    self.assertNotIn("Surplus", output)

  def test_cli_selects_tables_or_verbose_without_changing_json(self):
    cache = self.root / "layout.json"
    AGENT_QUOTA.write_cache(cache, {
      "schema_version": 3, "services": {"codex":
        self.codex_with_credits(None)},
    })
    for flags, renderer in ((["--brief"], "render_brief"),
                             (["--verbose"], "render_verbose"),
                             (["--brief", "--verbose"], "render_verbose")):
      with self.subTest(flags=flags), patch.object(
        AGENT_QUOTA, renderer, return_value="rendered",
      ) as render, patch("builtins.print") as printed:
        AGENT_QUOTA.main(["--cached", "--cache-file", str(cache), *flags])
        render.assert_called_once()
        printed.assert_called_once_with("rendered")
    with patch("builtins.print") as printed:
      AGENT_QUOTA.main(["--cached", "--cache-file", str(cache)])
      self.assertEqual(json.loads(printed.call_args.args[0])["schema_version"], 3)

  def test_token_averages_use_seven_calendar_days(self) -> None:
    buckets = [
      {"startDate": (NOW.date() - timedelta(days=day)).isoformat(),
       "tokens": 2400}
      for day in range(8)
    ]
    usage = AGENT_QUOTA.parse_token_usage({
      "dailyUsageBuckets": buckets,
    }, NOW)
    self.assertEqual(usage["status"], "complete")
    self.assertEqual(usage["period_start"], "2026-08-13")
    self.assertEqual(usage["period_end"], "2026-08-19")
    self.assertEqual(usage["average_tokens"], {
      "hour": 100, "day": 2400, "week": 16800,
    })
    output = AGENT_QUOTA.brief_tokens(usage)
    self.assertEqual(len(output.splitlines()), 1)
    self.assertIn("100/h | 2.4K/day | 16.8K/week", output)
    self.assertIn("7d through 2026-08-19", output)
    document = AGENT_QUOTA.reevaluate_document({"services": {"codex": {
      "token_usage": usage, "limits": [],
    }}}, NOW + timedelta(hours=1))
    self.assertEqual(document["services"]["codex"]["token_usage"][
      "freshness"
    ], "stale")

  def test_token_averages_reject_missing_or_invalid_daily_data(self) -> None:
    valid = [
      {"startDate": (NOW.date() - timedelta(days=day)).isoformat(),
       "tokens": 0} for day in range(7)
    ]
    for buckets in (None, [], valid[:-1], valid + [valid[0]],
                    [{**valid[0], "tokens": True}] + valid[1:],
                    [{**valid[0], "tokens": -1}] + valid[1:],
                    [{**valid[0], "startDate": "bad"}] + valid[1:]):
      with self.subTest(buckets=buckets):
        usage = AGENT_QUOTA.parse_token_usage({
          "dailyUsageBuckets": buckets,
        }, NOW)
        self.assertNotEqual(usage["status"], "complete")
        self.assertIsNone(usage["average_tokens"])
    zero = AGENT_QUOTA.parse_token_usage({"dailyUsageBuckets": valid}, NOW)
    self.assertEqual(zero["average_tokens"]["week"], 0)

  def test_token_lookup_failure_does_not_hide_quota(self) -> None:
    result = {"rateLimits": {
      "limitId": "codex", "primary": {
        "usedPercent": 50, "windowDurationMins": 300,
        "resetsAt": int(NOW.timestamp()) + 3600,
      },
    }}
    snapshot = self.account_snapshot()
    snapshot["limits"] = {"result": result}
    with patch.object(AGENT_QUOTA, "codex_rpc", return_value=snapshot):
      service = AGENT_QUOTA.get_codex(10, "codex", now=NOW)
    self.assertEqual(service["refresh"]["status"], "succeeded")
    self.assertEqual(service["data_status"], "complete")
    self.assertEqual(len(service["limits"]), 1)
    self.assertEqual(service["token_usage"]["status"], "unavailable")
    self.assertEqual(service["token_usage"]["error"], "unsupported method")

  def claude_auth(self, email="first@example.test", org="org-a", plan="max"):
    return {"loggedIn": True, "authMethod": "claude.ai",
            "apiProvider": "firstParty", "email": email, "orgId": org,
            "subscriptionType": plan}

  def claude_owned_cache(self, email="first@example.test", org="org-a",
                         owner="account-a", used=20, now=NOW):
    self.write_claude_cache([usage_record("weekly_all", used)], observed=now)
    data = json.loads(self.state_path.read_text())
    data["cachedUsageUtilization"]["accountUuid"] = owner
    data["oauthAccount"] = {"emailAddress": email, "organizationUuid": org,
                            "accountUuid": owner}
    self.state_path.write_text(json.dumps(data))

  def collect_claude_account(self, auth=None, previous=None, now=NOW,
                             refresh_status="succeeded", after=None):
    auth = auth or self.claude_auth()
    with (
      patch.object(AGENT_QUOTA, "claude_auth_status", side_effect=[
        auth, after or auth,
      ]),
      patch.object(AGENT_QUOTA, "refresh_claude_usage", return_value=
        AGENT_QUOTA.refresh_record(refresh_status, now, now)) as refresh,
    ):
      document = AGENT_QUOTA.build_document(
        "claude", self.state_path, "claude", 5, "codex", 5, previous, now,
      )
    return document, refresh

  def test_claude_switch_separates_history_and_forces_refresh(self):
    self.claude_owned_cache()
    first, _ = self.collect_claude_account()
    self.claude_owned_cache("second@example.test", "org-b", "account-b", 1,
                            NOW + timedelta(minutes=20))
    second, refresh = self.collect_claude_account(
      self.claude_auth("second@example.test", "org-b"), first,
      NOW + timedelta(minutes=20),
    )
    self.assertTrue(refresh.call_args.kwargs["force"])
    self.assertEqual(len(second["services"]["claude_code"][
      "limits"][0]["history"]), 1)
    self.claude_owned_cache(used=30, now=NOW + timedelta(minutes=40))
    returned, _ = self.collect_claude_account(
      previous=second, now=NOW + timedelta(minutes=40),
    )
    self.assertEqual([x["used_percent"] for x in returned["services"][
      "claude_code"]["limits"][0]["history"]], [20, 30])
    self.assertEqual(len(returned["claude_accounts"]), 2)

  def test_claude_recent_cache_is_reused_only_when_owner_verified(self):
    self.claude_owned_cache()
    first, _ = self.collect_claude_account()
    second, refresh = self.collect_claude_account(
      previous=first, refresh_status="not_needed",
    )
    self.assertFalse(refresh.call_args.kwargs["force"])
    self.assertEqual(second["services"]["claude_code"]["data_status"],
                     "complete")
    first.pop("claude_accounts")
    first["services"]["claude_code"].pop("account")
    _, refresh = self.collect_claude_account(previous=first)
    self.assertTrue(refresh.call_args.kwargs["force"])

  def test_claude_failed_switch_does_not_relabel_old_usage(self):
    self.claude_owned_cache()
    first, _ = self.collect_claude_account()
    failed, _ = self.collect_claude_account(
      self.claude_auth("second@example.test", "org-b"), first,
      refresh_status="failed",
    )
    self.assertEqual(failed["services"]["claude_code"]["limits"], [])
    same, _ = self.collect_claude_account(previous=first, refresh_status="failed")
    self.assertTrue(same["services"]["claude_code"]["retained_last_good"])

  def test_claude_rejects_cache_owner_and_mid_refresh_identity_mismatch(self):
    self.claude_owned_cache()
    data = json.loads(self.state_path.read_text())
    data["cachedUsageUtilization"]["accountUuid"] = "other"
    self.state_path.write_text(json.dumps(data))
    doc, _ = self.collect_claude_account()
    self.assertEqual(doc["services"]["claude_code"]["limits"], [])
    self.claude_owned_cache()
    doc, _ = self.collect_claude_account(after=self.claude_auth(org="org-b"))
    self.assertIsNone(doc["services"]["claude_code"]["account"])
    self.assertEqual(doc["services"]["claude_code"]["limits"], [])

  def test_claude_organization_and_plan_changes_do_not_share_history(self):
    self.claude_owned_cache()
    first, _ = self.collect_claude_account()
    for org, plan in (("org-b", "max"), ("org-a", "pro")):
      with self.subTest(org=org, plan=plan):
        self.claude_owned_cache(org=org, used=30)
        doc, refresh = self.collect_claude_account(
          self.claude_auth(org=org, plan=plan), first,
        )
        self.assertTrue(refresh.call_args.kwargs["force"])
        self.assertEqual(len(doc["services"]["claude_code"][
          "limits"][0]["history"]), 1)

  def test_claude_account_rendering_and_unknown_identity(self):
    self.claude_owned_cache()
    doc, _ = self.collect_claude_account()
    for render in (AGENT_QUOTA.render_brief, AGENT_QUOTA.render_verbose):
      self.assertIn("Claude Code account (last checked): first@example.test",
                    render(doc))
    doc, _ = self.collect_claude_account(auth={"loggedIn": False}, previous=doc)
    self.assertEqual(doc["services"]["claude_code"]["limits"], [])
    self.assertIsNone(doc["services"]["claude_code"]["account"])

  def test_claude_force_refresh_bypasses_recent_source_cache(self):
    self.claude_owned_cache()
    fake = self.root / "fake-claude-usage"
    fake.write_text(
      "#!/usr/bin/env python3\n"
      "import json,sys\n"
      "from pathlib import Path\n"
      "assert sys.argv[-1] == '/usage'\n"
      f"p=Path({str(self.state_path)!r})\n"
      "d=json.loads(p.read_text())\n"
      "d['cachedUsageUtilization']['fetchedAtMs'] += 1000\n"
      "p.write_text(json.dumps(d))\n"
    )
    fake.chmod(0o755)
    skipped = AGENT_QUOTA.refresh_claude_usage(self.state_path, str(fake), 3, NOW)
    self.assertEqual(skipped["status"], "not_needed")
    refreshed = AGENT_QUOTA.refresh_claude_usage(
      self.state_path, str(fake), 3, NOW, force=True,
    )
    self.assertEqual(refreshed["status"], "succeeded")
    self.assertEqual(AGENT_QUOTA.claude_cache_time(self.state_path),
                     NOW + timedelta(seconds=1))

  def test_claude_cache_change_during_reuse_retains_verified_snapshot(self):
    self.claude_owned_cache()
    first, _ = self.collect_claude_account()
    def replace_cache(*args, **kwargs):
      self.claude_owned_cache(used=99)
      return AGENT_QUOTA.refresh_record("not_needed", None, NOW)
    with (
      patch.object(AGENT_QUOTA, "claude_auth_status", return_value=
                   self.claude_auth()),
      patch.object(AGENT_QUOTA, "refresh_claude_usage", side_effect=replace_cache),
    ):
      document = AGENT_QUOTA.build_document(
        "claude", self.state_path, "claude", 5, "codex", 5, first, NOW,
      )
    service = document["services"]["claude_code"]
    self.assertTrue(service["retained_last_good"])
    self.assertEqual(service["limits"][0]["last_observation"]["used_percent"], 20)

  def test_claude_source_change_for_same_account_requires_refresh(self):
    self.claude_owned_cache()
    first, _ = self.collect_claude_account()
    self.claude_owned_cache(used=25, now=NOW + timedelta(seconds=10))
    _, refresh = self.collect_claude_account(previous=first)
    self.assertTrue(refresh.call_args.kwargs["force"])

  def test_claude_identity_rejects_override_credentials(self):
    for name in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN",
                 "CLAUDE_CODE_OAUTH_TOKEN", "CLAUDE_SECURESTORAGE_CONFIG_DIR"):
      with self.subTest(name=name), patch.dict("os.environ", {name: "test"}):
        self.assertIsNone(AGENT_QUOTA.claude_account(self.claude_auth(), NOW))

  def account_snapshot(self, email="first@example.test", used=20):
    account = {"account": {
      "type": "chatgpt", "email": email, "planType": "pro",
    }}
    return {
      "account_before": {"result": account},
      "account_after": {"result": account},
      "limits": {"result": {"rateLimits": {
        "limitId": "codex",
        "primary": {"usedPercent": used, "windowDurationMins": 10080,
                    "resetsAt": int(NOW.timestamp()) + 86400},
        "credits": {"hasCredits": True, "unlimited": False,
                    "balance": str(100 - used)},
      }}},
      "usage": {"error": "unsupported method"},
    }

  def collect_account(self, snapshot, previous=None, now=NOW):
    with patch.object(AGENT_QUOTA, "codex_rpc", return_value=snapshot):
      return AGENT_QUOTA.build_document(
        "codex", self.state_path, "claude", 5, "codex", 5, previous, now,
      )

  def test_switch_accounts_separates_history_and_restores_it(self):
    first = self.collect_account(self.account_snapshot())
    second = self.collect_account(
      self.account_snapshot("second@example.test", 1), first,
      NOW + timedelta(minutes=20),
    )
    service = second["services"]["codex"]
    self.assertEqual(service["account"]["label"], "second@example.test")
    self.assertEqual(len(service["limits"][0]["history"]), 1)
    self.assertIsNone(service["limits"][0]["burn"]["rate_percent_per_hour"])
    self.assertEqual(len(service["credits"][0]["history"]), 1)
    returned = self.collect_account(
      self.account_snapshot(used=30), second, NOW + timedelta(minutes=40),
    )["services"]["codex"]
    self.assertEqual(
      [x["used_percent"] for x in returned["limits"][0]["history"]], [20, 30],
    )
    self.assertEqual(
      [x["balance"] for x in returned["credits"][0]["history"]], ["80", "70"],
    )

  def test_failed_refresh_retains_only_verified_matching_account(self):
    previous = self.collect_account(self.account_snapshot())
    failed = self.account_snapshot()
    failed["limits"] = {"error": "offline"}
    same = self.collect_account(failed, previous)["services"]["codex"]
    self.assertTrue(same["retained_last_good"])
    other = self.account_snapshot("second@example.test")
    other["limits"] = {"error": "offline"}
    switched = self.collect_account(other, previous)["services"]["codex"]
    self.assertEqual(switched["limits"], [])
    unknown = self.account_snapshot()
    unknown["account_before"] = {"error": "offline"}
    unknown["account_after"] = {"error": "offline"}
    unknown["limits"] = {"error": "offline"}
    self.assertEqual(
      self.collect_account(unknown, previous)["services"]["codex"]["limits"],
      [],
    )

  def test_legacy_history_is_not_assigned_to_first_account(self):
    previous = self.collect_account(self.account_snapshot())
    previous.pop("codex_accounts", None)
    previous["services"]["codex"].pop("account")
    service = self.collect_account(
      self.account_snapshot(used=30), previous, NOW + timedelta(minutes=20),
    )["services"]["codex"]
    self.assertEqual(len(service["limits"][0]["history"]), 1)
    self.assertEqual(len(service["credits"][0]["history"]), 1)

  def test_switch_during_collection_rejects_mixed_snapshot(self):
    snapshot = self.account_snapshot()
    snapshot["account_after"] = self.account_snapshot(
      "second@example.test"
    )["account_after"]
    service = self.collect_account(snapshot)["services"]["codex"]
    self.assertEqual(service["limits"], [])
    self.assertEqual(service["data_status"], "unavailable")
    self.assertIsNone(service["account"])

  def test_identity_is_visible_and_cached_label_is_not_live_claim(self):
    document = self.collect_account(self.account_snapshot())
    for renderer in (AGENT_QUOTA.render_brief, AGENT_QUOTA.render_verbose):
      output = renderer(AGENT_QUOTA.reevaluate_document(document, NOW))
      self.assertIn("Codex account (last checked): first@example.test", output)
      self.assertIn("pro", output)

  def test_unknown_identity_does_not_hide_live_quota_or_reuse_history(self):
    previous = self.collect_account(self.account_snapshot())
    unknown = self.account_snapshot(used=30)
    unknown["account_before"] = {"error": "unavailable"}
    unknown["account_after"] = {"error": "unavailable"}
    service = self.collect_account(unknown, previous)["services"]["codex"]
    self.assertIsNone(service["account"])
    self.assertEqual(service["data_status"], "partial")
    self.assertEqual(len(service["limits"][0]["history"]), 1)
    self.assertEqual(service["limits"][0]["last_observation"][
      "remaining_percent"
    ], 70)

  def test_workspace_identity_is_display_only_without_workspace_id(self):
    snapshot = self.account_snapshot()
    for key in ("account_before", "account_after"):
      snapshot[key]["result"]["account"]["planType"] = "business"
    document = self.collect_account(snapshot)
    service = document["services"]["codex"]
    self.assertEqual(service["account"]["label"], "first@example.test")
    self.assertIsNone(service["account"]["key"])
    self.assertEqual(document["codex_accounts"], {})
    self.assertEqual(len(service["limits"]), 1)
    self.assertIn("workspace", AGENT_QUOTA.account_line(service))

  def test_plan_change_starts_new_burn_history(self):
    previous = self.collect_account(self.account_snapshot())
    snapshot = self.account_snapshot(used=30)
    for key in ("account_before", "account_after"):
      snapshot[key]["result"]["account"]["planType"] = "prolite"
    service = self.collect_account(snapshot, previous)["services"]["codex"]
    self.assertEqual(len(service["limits"][0]["history"]), 1)
    self.assertEqual(len(service["credits"][0]["history"]), 1)

  def test_snapshot_rpc_uses_one_process_and_survives_usage_timeout(self):
    fake = self.root / "fake-snapshot"
    fake.write_text(
      "#!/usr/bin/env python3\n"
      "import json,sys\n"
      "seen=[]\n"
      "for line in sys.stdin:\n"
      "  r=json.loads(line); method=r['method']; seen.append(method)\n"
      "  if method == 'account/read':\n"
      "    assert r['params'] == {'refreshToken': False}\n"
      "  if method == 'account/usage/read': continue\n"
      "  if r.get('id',0) >= 2:\n"
      "    print(json.dumps({'id':r['id'],'result':{'seen':seen}}), "
      "flush=True)\n"
    )
    fake.chmod(0o755)
    result = AGENT_QUOTA.codex_rpc(.5, str(fake), snapshot=True)
    self.assertIn("timed out", result["usage"]["error"])
    self.assertEqual(result["account_after"]["result"]["seen"], [
      "initialize", "initialized", "account/read", "account/rateLimits/read",
      "account/usage/read", "account/read",
    ])

  def test_account_archive_is_bounded(self):
    document = None
    for index in range(12):
      document = self.collect_account(
        self.account_snapshot(f"user{index}@example.test"), document,
        NOW + timedelta(minutes=index),
      )
    self.assertLessEqual(len(document["codex_accounts"]), 8)
    self.assertIn(document["services"]["codex"]["account"]["key"],
                  document["codex_accounts"])

  def test_codex_rpc_can_request_token_usage(self) -> None:
    fake = self.root / "fake-codex-usage"
    fake.write_text(
      "#!/usr/bin/env python3\n"
      "import sys, json\n"
      "for line in sys.stdin:\n"
      "  request = json.loads(line)\n"
      "  if request.get('id') == 2:\n"
      "    print(json.dumps({'id': 2, 'result': "
      "{'requested': request['method']}}), flush=True)\n",
      encoding="utf-8",
    )
    fake.chmod(0o755)
    # A generous timeout costs nothing when the reply is prompt, and a
    # loaded machine can take seconds just to start the fake.
    result = AGENT_QUOTA.codex_rpc(
      timeout=30, codex_bin=str(fake), method="account/usage/read",
    )
    self.assertEqual(result, {"requested": "account/usage/read"})

  def test_codex_startup_failure_is_not_reported_as_timeout(self) -> None:
    fake_codex = self.root / "fake-codex"
    fake_codex.write_text(
      "#!/bin/sh\n"
      "echo 'failed to initialize sqlite state runtime' >&2\n"
      "exit 1\n",
      encoding="utf-8",
    )
    fake_codex.chmod(0o755)

    # Waiting out the timeout would take 30 s; a prompt failure takes a
    # fraction of that even on a loaded machine.
    started = time.monotonic()
    with self.assertRaisesRegex(
      RuntimeError,
      "exited with status 1.*run agent-quota outside the sandbox",
    ) as raised:
      AGENT_QUOTA.codex_rpc(timeout=30, codex_bin=str(fake_codex))

    self.assertLess(time.monotonic() - started, 10)
    self.assertNotIn("timed out", str(raised.exception))


  def archived_codex_document(self):
    """A report whose archive holds one account other than the live one."""
    archived = {
      "provider_id": "openai", "service_id": "codex",
      "display_name": "Codex", "warnings": [], "data_status": "complete",
      "account": {"key": "a" * 64, "label": "other@example.test",
                  "plan": "pro", "observed_at": "2026-08-19T20:00:00Z",
                  "source": "test"},
      "limits": [
        limit_record("codex:codex:primary", 100),
        limit_record("codex:fable:weekly", 10, bucket={
          "id": "fable", "name": "Fable 5", "scope_kind": "model",
        }),
      ],
    }
    active = AGENT_QUOTA.base_service("openai", "codex", "Codex", NOW)
    active["account"] = {"key": "b" * 64, "label": "live@example.test",
                         "plan": "prolite", "observed_at": AGENT_QUOTA.iso_utc(
                           NOW), "source": "test"}
    active["limits"] = [limit_record("codex:codex:primary", 5)]
    return {
      "schema_version": AGENT_QUOTA.SCHEMA_VERSION,
      "generated_at": AGENT_QUOTA.iso_utc(NOW),
      "services": {"codex": active},
      "claude_accounts": {},
      "codex_accounts": {"a" * 64: archived},
    }

  def test_other_account_state_is_rendered_with_elapsed_reset(self):
    first = self.collect_account(self.account_snapshot(used=100))
    second = self.collect_account(
      self.account_snapshot("second@example.test", 1), first,
      NOW + timedelta(minutes=20),
    )
    later = AGENT_QUOTA.reevaluate_document(second, NOW + timedelta(days=2))

    archive = later["codex_accounts"][
      first["services"]["codex"]["account"]["key"]
    ]
    self.assertEqual(
      archive["limits"][0]["last_observation"]["period_relation"], "ended",
    )
    for renderer in (AGENT_QUOTA.render_brief, AGENT_QUOTA.render_verbose):
      output = renderer(later)
      self.assertIn("Other accounts (last checked; not verified now)", output)
      self.assertIn("first@example.test", output)
      self.assertIn("(pro)", output)
      self.assertIn(
        "Codex weekly: RESET 2026-08-20T21:30:00Z (1d 0h ago); "
        "likely 100% now (was 0%).",
        output,
      )
      self.assertIn("(pro); checked 2026-08-19T21:30:00Z. LIKELY AVAILABLE.",
                    output)
      self.assertIn(
        "Likely available (reset since last check; not verified): "
        "Codex first@example.test (pro): weekly reset 1d 0h ago.",
        output,
      )
      self.assertEqual(output.count("second@example.test"), 1)
    brief = AGENT_QUOTA.render_brief(later).splitlines()
    self.assertTrue(brief[0].startswith("Likely available"))

  def test_account_still_exhausted_elsewhere_is_not_available(self):
    archived = {
      "display_name": "Claude Code",
      "account": {"key": "b" * 64, "label": "other@example.test",
                  "plan": "max", "observed_at": "2026-08-19T20:00:00Z"},
      "limits": [
        timeline_limit("c:5h", "5h", 81, None, relation="ended"),
        timeline_limit("c:week", "weekly", 0, timedelta(days=3)),
      ],
    }
    archived["limits"][0]["last_observation"]["reset_at"] = (
      AGENT_QUOTA.iso_utc(NOW - timedelta(hours=5)))
    availability = AGENT_QUOTA.archived_availability(archived, NOW)
    self.assertFalse(availability["likely_available"])
    self.assertEqual(availability["blocked"], ["Claude Code weekly"])
    document = {"generated_at": AGENT_QUOTA.iso_utc(NOW), "services": {}}
    with patch.object(AGENT_QUOTA, "archived_accounts",
                      return_value=[archived]):
      output = AGENT_QUOTA.render_brief(document)
    self.assertNotIn("Likely available", output)
    self.assertNotIn("LIKELY AVAILABLE", output)
    self.assertIn("Claude Code 5h: RESET", output)
    self.assertIn("(5h 0m ago); was 81%, blocked.", output)

  def spent_account_service(self, fable_reset: timedelta = timedelta(days=1)
                            ) -> dict[str, Any]:
    fable = {"id": "fable", "name": "Fable", "scope_kind": "model"}
    return timeline_service("Claude Code", "other@example.test", "max", [
      timeline_limit("c:week", "weekly", 0, timedelta(days=3)),
      timeline_limit("c:fable", "weekly", 56, fable_reset, bucket=fable),
    ], key="b" * 64)

  def test_model_bucket_is_blocked_when_its_account_bucket_is_spent(self):
    service = self.spent_account_service()
    self.assertEqual(AGENT_QUOTA.spent_account_buckets(service),
                     ["Claude Code weekly"])
    brief = AGENT_QUOTA.render_brief({"services": {"claude_code": service}})
    fable = next(line for line in brief.splitlines() if "Fable" in line)
    self.assertIn("56%", fable)
    self.assertIn("Blocked", fable)

    document = {"generated_at": AGENT_QUOTA.iso_utc(NOW), "services": {}}
    with patch.object(AGENT_QUOTA, "archived_accounts",
                      return_value=[service]):
      others = AGENT_QUOTA.render_brief(document)
      timeline = AGENT_QUOTA.render_timeline(document, timezone.utc)
    self.assertIn("Claude Code Fable weekly: last known 56% remaining "
                  "(blocked); resets", others)
    self.assertRegex(timeline, r"RESET +Fable +other@example\.test"
                               r" +was 56%, blocked")
    same_time = self.spent_account_service(timedelta(days=3))
    with patch.object(AGENT_QUOTA, "archived_accounts",
                      return_value=[same_time]):
      timeline = AGENT_QUOTA.render_timeline(document, timezone.utc)
    self.assertRegex(timeline, r"RESET +Claude, Fable +other@example\.test"
                               r" +was 0% / 56% → ~100%")
    self.assertNotIn("blocked", timeline)

  def test_other_accounts_show_time_to_reset_newest_first(self):
    first = self.collect_account(self.account_snapshot("a@example.test"))
    second = self.collect_account(
      self.account_snapshot("b@example.test"), first,
      NOW + timedelta(minutes=20),
    )
    third = self.collect_account(
      self.account_snapshot("c@example.test"), second,
      NOW + timedelta(minutes=40),
    )
    output = AGENT_QUOTA.render_brief(
      AGENT_QUOTA.reevaluate_document(third, NOW + timedelta(hours=1)),
    )

    self.assertIn(
      "Codex weekly: last known 80% remaining; "
      "resets 2026-08-20T21:30:00Z (in 23h 0m).",
      output,
    )
    self.assertLess(output.index("b@example.test"),
                    output.index("a@example.test"))
    self.assertEqual(output.count("c@example.test"), 1)

  def test_single_account_has_no_other_accounts_block(self):
    document = self.collect_account(self.account_snapshot())
    output = AGENT_QUOTA.render_brief(
      AGENT_QUOTA.reevaluate_document(document, NOW),
    )
    self.assertNotIn("Other accounts", output)

  def test_other_accounts_follow_provider_selection(self):
    document = self.archived_codex_document()

    self.assertIn("other@example.test", AGENT_QUOTA.render_brief(document))
    selected = AGENT_QUOTA.select_services(document, "claude")
    self.assertEqual(selected["codex_accounts"], {})
    self.assertNotIn("other@example.test", AGENT_QUOTA.render_brief(selected))


class ClaudeAuthStatusTest(unittest.TestCase):
  def test_waits_long_enough_for_a_loaded_machine(self) -> None:
    # The command usually answers in under a second, but a 5 s cap failed
    # under memory pressure and dropped Claude from the status line.
    completed = AGENT_QUOTA.subprocess.CompletedProcess([], 0, "{}", "")
    with patch.object(
      AGENT_QUOTA.subprocess, "run", return_value=completed,
    ) as run:
      AGENT_QUOTA.claude_auth_status("claude", 30)
      self.assertEqual(run.call_args.kwargs["timeout"], 15)
      AGENT_QUOTA.claude_auth_status("claude", 3)
      self.assertEqual(run.call_args.kwargs["timeout"], 3)




class FlagValidationTest(unittest.TestCase):
  def rejected(self, *argv: str) -> str:
    stderr = io.StringIO()
    with self.assertRaises(SystemExit) as raised, \
         patch("sys.stderr", stderr):
      AGENT_QUOTA.main(list(argv))
    self.assertEqual(raised.exception.code, 2)
    return stderr.getvalue()

  def test_agent_only_flags_require_agents(self):
    for flag in (["--no-summaries"], ["--agent-days", "5"],
                 ["--agent-limit", "3"], ["--no-cross-provider-summaries"],
                 ["--agent-d", "5"], ["--agent-days=5"]):
      message = self.rejected("--cached", *flag)
      self.assertIn("requires --agents", message, flag)

  def test_models_rejects_cache_and_quota_options(self):
    for flag in (["--cached"], ["--no-cache"], ["--strict"],
                 ["--cache-file", "x.json"], ["--timeout", "3"]):
      message = self.rejected("--models", *flag)
      self.assertIn("--models cannot be used with", message, flag)

  def test_query_timeouts_have_no_effect_with_cached(self):
    for flag in (["--timeout", "3"], ["--claude-timeout", "3"]):
      message = self.rejected("--cached", *flag)
      self.assertIn("cannot be used with --cached", message, flag)

  def test_meaningful_combinations_still_parse(self):
    calls = []
    agents = SimpleNamespace(main=lambda args, *_: calls.append(args) or 0)
    with patch.object(AGENT_QUOTA, "agents_module", return_value=agents):
      self.assertEqual(AGENT_QUOTA.main(
        ["--agents", "--cached", "--no-summaries", "--agent-days", "5",
         "--agent-limit", "3", "--claude-timeout", "3"]), 0)
    self.assertEqual(calls[0].agent_days, 5)
    self.assertEqual(calls[0].agent_limit, 3)
    with patch.object(AGENT_QUOTA, "agents_module", return_value=agents):
      AGENT_QUOTA.main(["--agents"])
    self.assertEqual((calls[1].agent_days, calls[1].agent_limit), (1, 20))


ESC = re.compile(r"\x1b\[[0-9;]*m")


class ColourTest(unittest.TestCase):
  def quota_document(self) -> dict[str, Any]:
    fable = {"id": "fable", "name": "Fable", "scope_kind": "model"}
    limits = [
      timeline_limit("c:5h", "5h", 60, timedelta(hours=2), state="surplus"),
      timeline_limit("c:week", "weekly", 0, timedelta(days=3)),
      timeline_limit("c:fable", "weekly", 40, timedelta(days=3),
                     bucket=fable),
    ]
    codex = [timeline_limit("x:week", "weekly", 30, timedelta(days=2),
                            runs_out_in=timedelta(days=1), state="behind")]
    return {"generated_at": AGENT_QUOTA.iso_utc(NOW), "services": {
      "claude_code": timeline_service("Claude Code", "me@example.test",
                                      "max", limits),
      "codex": timeline_service("Codex", "me@example.test", "pro", codex),
    }}

  def test_color_choice_resolution(self):
    tty = SimpleNamespace(isatty=lambda: True)
    pipe = SimpleNamespace(isatty=lambda: False)
    with patch.dict("os.environ", {"NO_COLOR": "1"}, clear=True):
      self.assertTrue(AGENT_QUOTA.resolve_color("always", pipe))
      self.assertFalse(AGENT_QUOTA.resolve_color("auto", tty))
    with patch.dict("os.environ", {"TERM": "xterm"}, clear=True):
      self.assertFalse(AGENT_QUOTA.resolve_color("never", tty))
      self.assertTrue(AGENT_QUOTA.resolve_color("auto", tty))

  def test_styled_tables_keep_their_alignment(self):
    rows = [["alpha", "1"], ["b", "22"]]
    styles = [[("red",), ()], [(), ("bold", "blue")]]
    plain = AGENT_QUOTA.text_table(["Name", "N"], rows)
    styled = AGENT_QUOTA.text_table(["Name", "N"], rows, styles, color=True)
    self.assertEqual([ESC.sub("", line) for line in styled], plain)
    self.assertTrue(styled[0].startswith("\x1b[1mName\x1b[0m"))
    self.assertIn("\x1b[31malpha\x1b[0m", styled[2])

  def test_brief_colours_pace_states_and_alerts(self):
    document = self.quota_document()
    plain = AGENT_QUOTA.render_brief(document)
    styled = AGENT_QUOTA.render_brief(document, color=True)
    self.assertEqual(ESC.sub("", styled), plain)
    self.assertIn("\x1b[1;31mAttention:\x1b[0m", styled)
    self.assertIn("\x1b[1;7;31mExhausted\x1b[0m", styled)
    self.assertIn("\x1b[1;31mRecent burn too high\x1b[0m", styled)
    self.assertIn("\x1b[34mSurplus\x1b[0m", styled)
    self.assertIn("\x1b[33mBlocked\x1b[0m", styled)
    self.assertIn("\x1b[1;31m0%\x1b[0m", styled)
    self.assertIn("\x1b[1mQuota\x1b[0m", styled)

  def test_verbose_highlights_key_words(self):
    document = self.quota_document()
    plain = AGENT_QUOTA.render_verbose(document)
    styled = AGENT_QUOTA.render_verbose(document, color=True)
    self.assertEqual(ESC.sub("", styled), plain)
    self.assertIn("\x1b[1;31mEXHAUSTS BEFORE RESET\x1b[0m", styled)
    self.assertIn("\x1b[31mBEHIND\x1b[0m", styled)

  def test_models_brief_bolds_model_names(self):
    document = {"generated_at": "t", "claude": {"effort_levels": ["low"]},
                "codex": {"models": [{"slug": "gpt-x", "default_effort":
                                      "medium", "efforts": ["low"],
                                      "tiers": []}]}}
    try:
      plain = AGENT_QUOTA.render_models_brief(document)
    except (KeyError, TypeError):
      self.skipTest("models fixture shape differs")
    styled = AGENT_QUOTA.render_models_brief(document, color=True)
    self.assertEqual(ESC.sub("", styled), plain)

  def test_color_is_rejected_for_json_output(self):
    for argv in (["--cached", "--compact", "--color", "always"],
                 ["--cached", "--color", "never"],
                 ["--cached", "--timeline", "--compact", "--color=always"]):
      stderr = io.StringIO()
      with self.assertRaises(SystemExit), patch("sys.stderr", stderr):
        AGENT_QUOTA.main(argv)
      self.assertIn("--color needs a text view", stderr.getvalue(), argv)

def timeline_limit(
  limit_id: str,
  label: str,
  remaining: float,
  reset_in: timedelta | None,
  *,
  runs_out_in: timedelta | None = None,
  state: str = "on_pace",
  freshness: str = "fresh",
  relation: str = "current",
  bucket: dict[str, Any] | None = None,
) -> dict[str, Any]:
  reset = AGENT_QUOTA.iso_utc(NOW + reset_in) if reset_in else None
  exhausts = AGENT_QUOTA.iso_utc(NOW + runs_out_in) if runs_out_in else None
  return {
    "limit_id": limit_id,
    "bucket": bucket
    or {"id": "account", "name": "All models", "scope_kind": "account"},
    "window": {"label": label,
               "duration_seconds": 18000 if label == "5h" else 604800},
    "last_observation": {
      "remaining_percent": remaining, "used_percent": 100 - remaining,
      "reset_at": reset, "observed_at": AGENT_QUOTA.iso_utc(NOW),
      "freshness": freshness, "period_relation": relation,
    },
    "pace": {"state": state,
             "reset_in_seconds": reset_in.total_seconds() if reset_in
             else None},
    "burn": {"exhausts_at": exhausts,
             "exhausts_before_reset": bool(
               runs_out_in and reset_in and runs_out_in < reset_in),
             "rate_percent_per_hour": 2.5 if runs_out_in else None},
  }


def timeline_service(name: str, label: str, plan: str,
                     limits: list[dict[str, Any]], key: str = "k" * 64,
                     credits: list[dict[str, Any]] | None = None):
  return {
    "display_name": name,
    "account": {"key": key, "label": label, "plan": plan,
                "observed_at": AGENT_QUOTA.iso_utc(NOW)},
    "limits": limits, "credits": credits or [],
  }


class TimelineTest(unittest.TestCase):
  def render(self, document: dict[str, Any], color: bool = False) -> str:
    document.setdefault("generated_at", AGENT_QUOTA.iso_utc(NOW))
    return AGENT_QUOTA.render_timeline(document, timezone.utc, color=color)

  def claude(self, *limits: dict[str, Any]) -> dict[str, Any]:
    return timeline_service("Claude Code", "me@example.test", "max",
                            list(limits))

  def row(self, output: str, *cells: str) -> int:
    """Index of the line holding these cells in order, in their columns."""
    pattern = " {2,}".join(re.escape(cell) for cell in cells) + r"(  |$)"
    for number, line in enumerate(output.splitlines()):
      if re.search(pattern, line):
        return number
    self.fail(f"no row {cells!r} in:\n{output}")

  def with_archive(self, document, archived, color=False) -> str:
    with patch.object(AGENT_QUOTA, "archived_accounts",
                      return_value=[archived]):
      return self.render(document, color)

  def blocked_account(self) -> dict[str, Any]:
    """Weekly spent in 3 days' reset; Fable and a 5h window with capacity."""
    fable = {"id": "fable", "name": "Fable", "scope_kind": "model"}
    return timeline_service("Claude Code", "old@example.test", "max", [
      timeline_limit("c:5h", "5h", 81, timedelta(hours=3)),
      timeline_limit("c:week", "weekly", 0, timedelta(days=3)),
      timeline_limit("c:fable", "weekly", 56, timedelta(days=3),
                     bucket=fable),
      timeline_limit("c:fable2", "weekly", 40, timedelta(days=1),
                     bucket={"id": "sonnet", "name": "Sonnet",
                             "scope_kind": "model"}),
    ], key="a" * 64)

  # Names and layout

  def test_names_drop_the_default_weekly_window(self):
    fable = {"id": "fable", "name": "Fable", "scope_kind": "model"}
    output = self.render({"services": {
      "claude_code": self.claude(
        timeline_limit("c:5h", "5h", 5, timedelta(hours=2)),
        timeline_limit("c:week", "weekly", 40, timedelta(days=2)),
        timeline_limit("c:fable", "weekly", 30, timedelta(days=1),
                       bucket=fable)),
      "codex": timeline_service("Codex", "me@example.test", "pro", [
        timeline_limit("x:week", "weekly", 50, timedelta(days=3))]),
    }})
    for quota in ("Claude 5h", "Claude", "Fable", "Codex"):
      self.row(output, "RESET", quota, "me@example.test")
    self.assertNotIn("Weekly", output)

  def test_events_are_grouped_by_day_in_time_order(self):
    output = self.render({"services": {"claude_code": self.claude(
      timeline_limit("c:week", "weekly", 40, timedelta(days=4),
                     runs_out_in=timedelta(hours=25), state="behind"),
      timeline_limit("c:5h", "5h", 5, timedelta(hours=2)),
    )}})
    lines = output.splitlines()
    self.assertTrue(lines[0].startswith("Timeline (UTC; now Wed 19 Aug"))
    today = lines.index("  Today")
    tomorrow = lines.index("  Tomorrow")
    later = lines.index("  Sun 23 Aug")
    five = self.row(output, "in 2h 0m", "23:30", "RESET", "Claude 5h")
    burn = self.row(output, "in 1d 1h", "22:30", "BURN", "Claude")
    reset = self.row(output, "in 4d 0h", "21:30", "RESET", "Claude")
    self.assertLess(today, five)
    self.assertLess(five, tomorrow)
    self.assertLess(tomorrow, burn)
    self.assertLess(burn, later)
    self.assertLess(later, reset)
    self.assertNotIn("Aug 23:30", output)       # times only, under days
    starts = {line.index("me@example.test") for line in lines
              if "me@example.test" in line}
    self.assertEqual(len(starts), 1)

  # Notes

  def test_active_lines_show_what_is_left(self):
    output = self.render({"services": {"claude_code": self.claude(
      timeline_limit("c:week", "weekly", 40, timedelta(days=4),
                     runs_out_in=timedelta(days=1)),
    )}})
    self.row(output, "BURN", "Claude", "me@example.test", "40% left, 2.5%/h")
    self.row(output, "RESET", "Claude", "me@example.test", "40% left")

  def test_exhausted_quota_and_the_reset_that_restores_it(self):
    fable = {"id": "fable", "name": "Fable", "scope_kind": "model"}
    document = {"services": {"claude_code": self.claude(
      timeline_limit("c:week", "weekly", 85, timedelta(days=3)),
      timeline_limit("c:fable", "weekly", 0, timedelta(days=2),
                     bucket=fable),
    )}}
    plain = self.render(document)
    styled = self.render(document, color=True)
    self.assertLess(self.row(plain, "now", "21:30", "EXHAUSTED", "Fable"),
                    self.row(plain, "RESET", "Fable", "me@example.test",
                             "0% → 100%"))
    line = next(item for item in styled.splitlines()
                if "RESET" in item and "Fable" in item)
    self.assertIn("\x1b[1;32m0% → 100%\x1b[0m", line)
    self.assertIn("\x1b[1;7;31mEXHAUSTED\x1b[0m", styled)

  def test_run_out_after_reset_is_not_an_event(self):
    output = self.render({"services": {"claude_code": self.claude(
      timeline_limit("c:week", "weekly", 70, timedelta(days=2),
                     runs_out_in=timedelta(days=5)),
    )}})
    self.assertNotIn("BURN", output)
    self.row(output, "RESET", "Claude", "me@example.test", "70% left")

  def test_credit_depletion_is_an_event(self):
    credit = {
      "bucket": {"name": "Codex"}, "balance": "12.50",
      "pace": {"rate_credits_per_hour": 1.25,
               "exhausts_at": AGENT_QUOTA.iso_utc(NOW + timedelta(hours=10)),
               "seconds_until_empty": 36000},
      "freshness": "fresh",
    }
    codex = timeline_service("Codex", "me@example.test", "pro", [
      timeline_limit("x:week", "weekly", 50, timedelta(days=3)),
    ], credits=[credit])
    output = self.render({"services": {"codex": codex}})
    self.assertLess(self.row(output, "BURN", "Codex Credits",
                             "me@example.test", "12.50 left, 1.25/h"),
                    self.row(output, "RESET", "Codex", "me@example.test"))

  def test_stale_active_data_is_marked(self):
    output = self.render({"services": {"claude_code": self.claude(
      timeline_limit("c:week", "weekly", 40, timedelta(days=2),
                     runs_out_in=timedelta(hours=5), freshness="stale"),
    )}}, color=True)
    line = next(item for item in output.splitlines() if "RESET" in item)
    self.assertIn("40% left, stale", re.sub(r"\x1b\[[0-9;]*m", "", line))
    self.assertIn("\x1b[2;3m", line)
    self.assertNotIn("BURN", output)

  # Merging

  def test_simultaneous_events_merge(self):
    fable = {"id": "fable", "name": "Fable", "scope_kind": "model"}
    output = self.render({"services": {"claude_code": self.claude(
      timeline_limit("c:week", "weekly", 40, timedelta(days=2)),
      timeline_limit("c:fable", "weekly", 60, timedelta(days=2),
                     bucket=fable),
    )}})
    self.row(output, "RESET", "Claude, Fable", "me@example.test",
             "40% / 60% left")
    self.assertEqual(output.count("RESET"), 1)

  # Inactive accounts

  def test_blocked_account_highlights_the_reset_that_frees_it(self):
    document = {"services": {"claude_code": self.claude(
      timeline_limit("c:week2", "weekly", 50, timedelta(days=5)))}}
    archived = self.blocked_account()
    plain = self.with_archive(document, archived)
    styled = self.with_archive(document, archived, color=True)

    self.row(plain, "RESET", "Claude, Fable", "old@example.test",
             "was 0% / 56% → ~100%")
    self.row(plain, "RESET", "Sonnet", "old@example.test", "was 40%, blocked")
    self.assertNotIn("Claude 5h", plain)   # a blocked 5h reset is noise
    line = next(item for item in styled.splitlines()
                if "old@example.test" in item and "~100%" in item)
    self.assertIn("\x1b[1;32mwas 0% / 56% → ~100%\x1b[0m", line)
    self.assertNotIn("\x1b[2;3m", line)

  def test_past_resets_show_when_the_account_is_likely_available(self):
    limit = timeline_limit("x:week", "weekly", 0, None, relation="ended")
    limit["last_observation"]["reset_at"] = AGENT_QUOTA.iso_utc(
      NOW - timedelta(days=1, hours=5))
    archived = timeline_service("Codex", "old@example.test", "pro", [limit],
                                key="a" * 64)
    document = {"services": {"claude_code": self.claude(
      timeline_limit("c:week", "weekly", 70, timedelta(days=2)))}}
    plain = self.with_archive(document, archived)
    styled = self.with_archive(document, archived, color=True)

    lines = plain.splitlines()
    self.assertLess(lines.index("  Yesterday"),
                    self.row(plain, "1d 5h ago", "16:30", "RESET", "Codex",
                             "old@example.test", "was 0% → ~100%"))
    line = next(item for item in styled.splitlines() if "ago" in item)
    self.assertIn("\x1b[1;32m1d 5h ago\x1b[0m", line)
    self.assertIn('~100% follows a reset', plain)

  def test_past_resets_behind_a_spent_bucket_are_left_out(self):
    past = timeline_limit("c:5h", "5h", 81, None, relation="ended")
    past["last_observation"]["reset_at"] = AGENT_QUOTA.iso_utc(
      NOW - timedelta(hours=5))
    archived = timeline_service("Claude Code", "old@example.test", "max", [
      past, timeline_limit("c:week", "weekly", 0, timedelta(days=2))],
      key="a" * 64)
    plain = self.with_archive({"services": {}}, archived)
    self.assertNotIn(" ago ", plain)
    self.row(plain, "RESET", "Claude", "old@example.test", "was 0% → ~100%")

  # 5h windows

  def test_routine_5h_items_are_hidden(self):
    output = self.render({"services": {"claude_code": self.claude(
      timeline_limit("c:5h", "5h", 60, timedelta(hours=2)),
      timeline_limit("c:week", "weekly", 50, timedelta(days=2)),
    )}})
    self.assertNotIn("Claude 5h", output)
    self.assertIn("5h resets are shown only", output)

  def test_noteworthy_5h_items_are_shown(self):
    cases = {
      "low": timeline_limit("c:5h", "5h", 8, timedelta(hours=2)),
      "burn": timeline_limit("c:5h", "5h", 60, timedelta(hours=4),
                             runs_out_in=timedelta(hours=1)),
      "exhausted": timeline_limit("c:5h", "5h", 0, timedelta(hours=2)),
    }
    for name, limit in cases.items():
      output = self.render({"services": {"claude_code": self.claude(
        limit, timeline_limit("c:week", "weekly", 50, timedelta(days=2)))}})
      self.assertIn("Claude 5h", output, name)
    output = self.render({"services": {"claude_code": self.claude(
      cases["exhausted"],
      timeline_limit("c:week", "weekly", 50, timedelta(days=2)))}})
    self.row(output, "RESET", "Claude 5h", "me@example.test", "0% → 100%")

  # Other behaviour

  def test_ended_quotas_stay_out_and_empty_says_so(self):
    output = self.render({"services": {"claude_code": self.claude(
      timeline_limit("c:week", "weekly", 30, None, relation="ended"),
    )}})
    self.assertIn("No upcoming resets or projected run-outs.", output)

  def test_plain_timeline_has_no_escape_codes(self):
    output = self.render({"services": {"claude_code": self.claude(
      timeline_limit("c:week", "weekly", 70, timedelta(days=2)))}})
    self.assertNotIn("\x1b[", output)

  def test_colour_only_adds_styling_and_marks_urgency(self):
    document = {"services": {
      "claude_code": self.claude(
        timeline_limit("c:week", "weekly", 40, timedelta(days=4),
                       runs_out_in=timedelta(hours=10))),
      "codex": timeline_service("Codex", "you@example.test", "pro", [
        timeline_limit("x:week", "weekly", 60, timedelta(days=5),
                       runs_out_in=timedelta(days=2))], key="y" * 64),
    }}
    styled = self.render(document, color=True)
    plain = self.render(document)
    self.assertEqual(re.sub(r"\x1b\[[0-9;]*m", "", styled), plain)
    lines = styled.splitlines()
    near = next(line for line in lines if "BURN" in line and "me@" in line)
    far = next(line for line in lines if "BURN" in line and "you@" in line)
    reset = next(line for line in lines if "RESET" in line and "me@" in line)
    self.assertIn("\x1b[1;31mBURN\x1b[0m", near)
    self.assertIn("\x1b[1;33mBURN\x1b[0m", far)
    self.assertIn("\x1b[1;32mRESET\x1b[0m", reset)
    self.assertIn("\x1b[1mClaude\x1b[0m", reset)
    self.assertIn("\x1b[36mme@example.test\x1b[0m", reset)
    self.assertIn("\x1b[35myou@example.test\x1b[0m", far)
    self.assertIn("\x1b[1mTomorrow\x1b[0m", styled)
    self.assertTrue(lines[-1].startswith("\x1b[2;3m"))

  def test_colour_follows_terminal_and_no_color(self):
    tty = SimpleNamespace(isatty=lambda: True)
    pipe = SimpleNamespace(isatty=lambda: False)
    with patch.dict("os.environ", {"TERM": "xterm-256color"}, clear=True):
      self.assertTrue(AGENT_QUOTA.use_color(tty))
      self.assertFalse(AGENT_QUOTA.use_color(pipe))
    with patch.dict("os.environ", {"TERM": "xterm", "NO_COLOR": "1"},
                    clear=True):
      self.assertFalse(AGENT_QUOTA.use_color(tty))
    with patch.dict("os.environ", {"TERM": "dumb"}, clear=True):
      self.assertFalse(AGENT_QUOTA.use_color(tty))

  # Machine-readable events

  def test_json_events_are_complete_and_unmerged(self):
    fable = {"id": "fable", "name": "Fable", "scope_kind": "model"}
    document = {"generated_at": AGENT_QUOTA.iso_utc(NOW), "services": {
      "claude_code": dict(self.claude(
        timeline_limit("c:5h", "5h", 60, timedelta(hours=2)),
        timeline_limit("c:week", "weekly", 40, timedelta(days=2)),
        timeline_limit("c:fable", "weekly", 0, timedelta(days=2),
                       bucket=fable)), service_id="claude_code")}}
    result = AGENT_QUOTA.timeline_document(document)

    self.assertEqual(result["schema_version"], 1)
    self.assertEqual(result["generated_at"], AGENT_QUOTA.iso_utc(NOW))
    events = result["events"]
    self.assertEqual([event["type"] for event in events],
                     ["exhausted", "reset", "reset", "reset"])
    five = next(event for event in events if event["limit_id"] == "c:5h")
    self.assertEqual(five["quota"], "Claude 5h")
    self.assertTrue(five["short_window"])
    self.assertEqual(five["remaining_percent"], 60)
    fable_reset = next(event for event in events
                       if event["limit_id"] == "c:fable"
                       and event["type"] == "reset")
    self.assertTrue(fable_reset["restores"])
    self.assertEqual(fable_reset["provider"], "claude_code")
    self.assertTrue(fable_reset["active"])
    self.assertEqual(fable_reset["account"], "me@example.test")
    self.assertEqual(fable_reset["at"], AGENT_QUOTA.iso_utc(
      NOW + timedelta(days=2)))
    for key in ("blocked", "stale", "rate_per_hour", "unit"):
      self.assertIn(key, fable_reset)

  def cli(self, *flags: str) -> tuple[int, str]:
    document = {
      "schema_version": 3, "generated_at": AGENT_QUOTA.iso_utc(NOW),
      "services": {"claude_code": self.claude(
        timeline_limit("c:week", "weekly", 70, timedelta(days=2)))},
    }
    with tempfile.TemporaryDirectory() as tmp, \
         patch.object(AGENT_QUOTA, "load_cache", return_value=document), \
         patch.object(AGENT_QUOTA, "reevaluate_document",
                      side_effect=lambda doc, *_: doc), \
         patch.object(AGENT_QUOTA, "agents_module",
                      return_value=SimpleNamespace(claude_observation=Mock(
                        side_effect=OSError("no agents")))), \
         patch("builtins.print") as printed:
      status = AGENT_QUOTA.main(
        ["--cached", *flags, "--cache-file", f"{tmp}/c.json"])
    return status, printed.call_args.args[0]

  def test_cli_renders_timeline_text_and_json(self):
    status, output = self.cli("--timeline")
    self.assertIn(status, (0, 1))
    self.assertTrue(output.startswith("Timeline"))
    self.row(output, "RESET", "Claude", "me@example.test", "70% left")
    _, output = self.cli("--timeline", "--compact")
    self.assertEqual(json.loads(output)["events"][0]["type"], "reset")
    with self.assertRaises(SystemExit), patch("sys.stderr", io.StringIO()):
      AGENT_QUOTA.main(["--cached", "--timeline", "--brief"])


class LiveTest(unittest.TestCase):
  def args(self, **overrides) -> SimpleNamespace:
    values = dict(timeline=True, agents=False, interval=None, notify=False,
                  color_on=False, cached=True, no_cache=False, provider="all",
                  verbose=False)
    values.update(overrides)
    return SimpleNamespace(**values)

  def document(self, burn: bool) -> dict[str, Any]:
    limits = [timeline_limit("c:week", "weekly", 40, timedelta(days=2),
                             runs_out_in=timedelta(minutes=30) if burn
                             else None)]
    return {"services": {"claude_code": timeline_service(
      "Claude Code", "me@example.test", "max", limits)}}

  def run_frames(self, documents, **overrides) -> tuple[str, list[str]]:
    out, sent = io.StringIO(), []
    frames = iter(documents)
    sleeps = iter([None] * (len(documents) - 1) + [KeyboardInterrupt()])

    def sleep(_):
      step = next(sleeps)
      if step:
        raise step
    with patch.object(AGENT_QUOTA, "live_document",
                      side_effect=lambda *_: next(frames)), \
         patch.object(AGENT_QUOTA, "notify", side_effect=sent.append):
      code = AGENT_QUOTA.run_live(self.args(**overrides), Path("/none"),
                                  out=out, clock=lambda: NOW.timestamp(),
                                  sleep=sleep)
    self.assertEqual(code, 0)
    return out.getvalue(), sent

  def test_loop_uses_the_alternate_screen_and_restores_it(self):
    output, _ = self.run_frames([self.document(False)])
    self.assertTrue(output.startswith(AGENT_QUOTA.ENTER_SCREEN))
    self.assertTrue(output.endswith(AGENT_QUOTA.LEAVE_SCREEN))
    self.assertIn("agent-quota --timeline --live · every 30s", output)
    self.assertIn("Timeline", output)

  def test_later_frames_mark_new_rows_and_alert(self):
    output, sent = self.run_frames(
      [self.document(False), self.document(True)], color_on=True,
      notify=True)
    first, second = output.split("\033[H\033[2J")[1:]
    self.assertNotIn("\x1b[7;", first)
    self.assertIn("\x1b[7;1;31mBURN\x1b[0m", second)
    self.assertEqual(len(sent), 1)
    self.assertIn("runs out in 30m", sent[0])

  def test_alerts_need_notify(self):
    _, sent = self.run_frames([self.document(False), self.document(True)])
    self.assertEqual(sent, [])

  def test_quota_is_queried_only_when_stale_and_not_cached(self):
    built = []
    with tempfile.TemporaryDirectory() as tmp:
      path = Path(tmp) / "report.json"
      path.write_text("{}")
      old = path.stat().st_mtime
      with patch.object(AGENT_QUOTA, "load_cache", return_value={"x": 1}), \
           patch.object(AGENT_QUOTA, "reevaluate_document",
                        side_effect=lambda doc, *_: {"services": {}}), \
           patch.object(AGENT_QUOTA, "select_services",
                        side_effect=lambda doc, _: doc), \
           patch.object(AGENT_QUOTA, "write_cache"), \
           patch.object(AGENT_QUOTA, "build_document",
                        side_effect=lambda *a: built.append(a) or {}):
        args = SimpleNamespace(cached=False, no_cache=False, provider="all",
                               claude_file="x", claude_bin="c",
                               claude_timeout=1, codex_bin="x", timeout=1)
        AGENT_QUOTA.live_document(args, path, old + 60)
        self.assertEqual(built, [])
        AGENT_QUOTA.live_document(args, path, old + 600)
        self.assertEqual(len(built), 1)
        args.cached = True
        AGENT_QUOTA.live_document(args, path, old + 600)
        self.assertEqual(len(built), 1)

  def test_timeline_alerts(self):
    moment = NOW
    reset = {"type": "reset", "moment": NOW - timedelta(minutes=1),
             "account": "me", "quota": "Claude", "restores": False,
             "active": True}
    burn = {"type": "burn", "moment": NOW + timedelta(minutes=20),
            "account": "me", "quota": "Codex", "restores": False,
            "active": True}
    freed = {"type": "reset", "moment": NOW - timedelta(hours=2),
             "account": "old", "quota": "Codex", "restores": True,
             "active": False}
    previous = {AGENT_QUOTA.timeline_row_key(reset): reset}
    sent: set = set()
    alerts = AGENT_QUOTA.timeline_alerts(previous, [burn, freed], moment,
                                         sent)
    self.assertEqual(alerts, ["Claude reset for me",
                              "Codex for me runs out in 20m",
                              "old is likely available again"])
    self.assertEqual(AGENT_QUOTA.timeline_alerts(previous, [burn, freed],
                                                 moment, sent), [])

  def test_screen_fitting_keeps_the_top(self):
    lines = [str(n) for n in range(10)]
    self.assertEqual(AGENT_QUOTA.fit_screen(lines, 20), lines)
    self.assertEqual(AGENT_QUOTA.fit_screen(lines, 4),
                     ["0", "1", "2", "… 7 more lines"])

  def test_notifications_use_the_platform_tool(self):
    with patch.object(AGENT_QUOTA.sys, "platform", "darwin"), \
         patch.object(AGENT_QUOTA.subprocess, "run") as run, \
         patch("sys.stdout", io.StringIO()):
      AGENT_QUOTA.notify("hello")
    self.assertEqual(run.call_args.args[0][0], "osascript")
    self.assertEqual(run.call_args.args[0][-1], "hello")
    with patch.object(AGENT_QUOTA.sys, "platform", "linux"), \
         patch.object(AGENT_QUOTA.shutil, "which", return_value="/x"), \
         patch.object(AGENT_QUOTA.subprocess, "run") as run, \
         patch("sys.stdout", io.StringIO()) as out:
      AGENT_QUOTA.notify("hello")
    self.assertEqual(run.call_args.args[0], ["notify-send", "agent-quota",
                                             "hello"])
    self.assertEqual(out.getvalue(), "\a")

  def test_live_flag_rules(self):
    for argv, message in (
        (["--cached", "--live"], "--live requires --timeline or --agents"),
        (["--cached", "--timeline", "--live", "--compact"],
         "--live cannot be used with --compact"),
        (["--cached", "--timeline", "--interval", "10"],
         "--interval requires --live"),
        (["--cached", "--timeline", "--notify"], "--notify requires --live"),
        (["--cached", "--timeline", "--live", "--interval", "2"],
         "at least 5 seconds")):
      stderr = io.StringIO()
      with self.assertRaises(SystemExit), patch("sys.stderr", stderr):
        AGENT_QUOTA.main(argv)
      self.assertIn(message, stderr.getvalue(), argv)

  def test_live_prints_once_when_not_a_terminal(self):
    document = {"schema_version": 3, "generated_at": AGENT_QUOTA.iso_utc(NOW),
                "services": {}}
    with patch.object(AGENT_QUOTA, "load_cache", return_value=document), \
         patch.object(AGENT_QUOTA, "reevaluate_document",
                      side_effect=lambda doc, *_: doc), \
         patch.object(AGENT_QUOTA, "run_live") as live, \
         patch("builtins.print") as printed:
      AGENT_QUOTA.main(["--cached", "--timeline", "--live"])
    live.assert_not_called()
    self.assertTrue(printed.call_args.args[0].startswith("Timeline"))


class ReadyAtTest(unittest.TestCase):
  def snapshot(self, *limits) -> dict[str, Any]:
    return timeline_service("Claude Code", "old@example.test", "max",
                            list(limits), key="a" * 64)

  def test_ready_at_agrees_with_likely_available(self):
    fable = {"id": "fable", "name": "Fable", "scope_kind": "model"}
    cases = {
      "blocked": self.snapshot(
        timeline_limit("c:5h", "5h", 81, timedelta(hours=3)),
        timeline_limit("c:week", "weekly", 0, timedelta(days=2)),
        timeline_limit("c:fable", "weekly", 56, timedelta(days=2),
                       bucket=fable)),
        "two blockers": self.snapshot(
        timeline_limit("c:5h", "5h", 0, timedelta(hours=3)),
        timeline_limit("c:week", "weekly", 0, timedelta(days=2))),
      "open": self.snapshot(
        timeline_limit("c:5h", "5h", 40, timedelta(hours=3)),
        timeline_limit("c:week", "weekly", 30, timedelta(days=2))),
      "spent model only": self.snapshot(
        timeline_limit("c:week", "weekly", 30, timedelta(days=2)),
        timeline_limit("c:fable", "weekly", 0, timedelta(days=1),
                       bucket=fable)),
    }
    for name, snapshot in cases.items():
      ready = AGENT_QUOTA.parse_timestamp(
        AGENT_QUOTA.archived_ready_at(snapshot))
      self.assertIsNotNone(ready, name)
      for offset in (timedelta(hours=1), timedelta(hours=4),
                     timedelta(days=1, hours=1), timedelta(days=3)):
        moment = NOW + offset
        later = AGENT_QUOTA.reevaluate_service(snapshot, moment)
        expected = AGENT_QUOTA.archived_availability(later, moment)[
          "likely_available"]
        self.assertEqual(ready <= moment, expected, (name, offset))

  def test_unknown_blocker_reset_means_no_ready_time(self):
    snapshot = self.snapshot(
      timeline_limit("c:5h", "5h", 40, timedelta(hours=3)),
      timeline_limit("c:week", "weekly", 0, None))
    self.assertIsNone(AGENT_QUOTA.archived_ready_at(snapshot))
    full = self.snapshot(timeline_limit("c:week", "weekly", 100,
                                        timedelta(days=2)))
    self.assertIsNone(AGENT_QUOTA.archived_ready_at(full))

  def test_account_cache_records_ready_at(self):
    active = self.snapshot(
      timeline_limit("c:week", "weekly", 0, timedelta(days=2)))
    cache = AGENT_QUOTA.account_cache(
      {"services": {"claude_code": active}}, "claude_code",
      "claude_accounts")
    self.assertEqual(cache["a" * 64]["ready_at"],
                     AGENT_QUOTA.iso_utc(NOW + timedelta(days=2)))

if __name__ == "__main__":
  unittest.main()
