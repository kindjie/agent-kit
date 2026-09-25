from __future__ import annotations

import importlib.machinery
import importlib.util
import re
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import ModuleType


KIT = Path(__file__).parents[1] / "bin"
SCRIPT = KIT / "agent-status"
NOW = datetime(2026, 8, 19, 21, 30, tzinfo=timezone.utc)


def load_script(name: str, path: Path) -> ModuleType:
  sys_module = __import__("sys")
  sys_module.dont_write_bytecode = True
  loader = importlib.machinery.SourceFileLoader(name, str(path))
  spec = importlib.util.spec_from_loader(loader.name, loader)
  assert spec is not None
  module = importlib.util.module_from_spec(spec)
  loader.exec_module(module)
  return module


AGENT_STATUS = load_script("agent_status", SCRIPT)


def strip_tmux_styles(value: str) -> str:
  return re.sub(r"#\[[^]]*\]", "", value)


def observation(
  remaining: int,
  *,
  freshness: str = "fresh",
  period: str = "current",
  reset_at: str = "2026-08-21T18:00:00Z",
) -> dict[str, object]:
  return {
    "used_percent": 100 - remaining,
    "remaining_percent": remaining,
    "observed_at": "2026-08-19T20:00:00Z",
    "reset_at": reset_at,
    "fresh_until": (
      "2026-08-19T21:00:00Z"
      if freshness == "stale"
      else "2026-08-19T22:00:00Z"
    ),
    "freshness": freshness,
    "period_relation": period,
    "source": {"id": "fixture", "documentation_status": "documented"},
  }


def limit(
  service: str,
  bucket_id: str,
  bucket_name: str,
  remaining: int,
  *,
  scope_kind: str = "account",
  freshness: str = "fresh",
  period: str = "current",
  window: str = "weekly",
) -> dict[str, object]:
  is_weekly = window == "weekly"
  return {
    "limit_id": f"{service}:{bucket_id}:{window}",
    "bucket": {
      "id": bucket_id,
      "name": bucket_name,
      "scope_kind": scope_kind,
    },
    "window": {
      "label": window,
      "duration_seconds": 604800 if is_weekly else 18000,
    },
    "non_additive": scope_kind != "account",
    "availability": None,
    "last_observation": observation(
      remaining,
      freshness=freshness,
      period=period,
      reset_at=(
        "2026-08-21T18:00:00Z" if is_weekly else "2026-08-20T01:00:00Z"
      ),
    ),
  }


def report() -> dict[str, object]:
  refresh = {
    "status": "succeeded",
    "attempted_at": "2026-08-19T21:30:00Z",
    "last_success_at": "2026-08-19T21:30:00Z",
    "error": None,
  }
  return {
    "schema_version": 3,
    "generated_at": "2026-08-19T21:30:00Z",
    "services": {
      "claude_code": {
        "provider_id": "anthropic",
        "service_id": "claude_code",
        "display_name": "Claude Code",
        "refresh": dict(refresh),
        "data_status": "complete",
        "warnings": [],
        "limits": [
          limit("claude_code", "account", "All models", 99, window="5h"),
          limit(
            "claude_code",
            "account",
            "All models",
            70,
            freshness="stale",
          ),
          limit(
            "claude_code",
            "claude-fable-5",
            "Fable 5",
            47,
            scope_kind="model",
            freshness="stale",
          ),
        ],
      },
      "codex": {
        "provider_id": "openai",
        "service_id": "codex",
        "display_name": "Codex",
        "refresh": dict(refresh),
        "data_status": "complete",
        "warnings": [],
        "limits": [
          limit("codex", "codex", "Codex", 46),
          limit(
            "codex",
            "spark",
            "GPT-5.3-Codex-Spark",
            100,
            scope_kind="provider_defined",
          ),
        ],
      },
    },
  }


def archived_account(
  service: str,
  key: str,
  remaining: int,
  reset_at: str,
  *,
  window: str = "weekly",
) -> dict[str, object]:
  bucket = "account" if service == "claude_code" else "codex"
  item = limit(service, bucket, "All models", remaining, window=window)
  item["last_observation"] = observation(remaining, reset_at=reset_at)
  return {
    "service_id": service,
    "account": {"key": key, "label": f"{key}@example.com"},
    "limits": [item],
  }


def with_archived_accounts(doc: dict[str, object]) -> dict[str, object]:
  """Active accounts are "active-cl" and "active-cx". NOW is Wed 21:30Z."""
  doc["services"]["claude_code"]["account"] = {"key": "active-cl"}
  doc["services"]["codex"]["account"] = {"key": "active-cx"}
  doc["claude_accounts"] = {
    "active-cl": archived_account(
      "claude_code", "active-cl", 70, "2026-08-19T23:00:00Z"
    ),
    "later-cl": archived_account(
      "claude_code", "later-cl", 5, "2026-08-21T07:00:00Z"
    ),
    "soon-cl": archived_account(
      "claude_code", "soon-cl", 0, "2026-08-20T18:00:00Z"
    ),
    "passed-cl": archived_account(
      "claude_code", "passed-cl", 0, "2026-08-19T21:00:00Z"
    ),
  }
  doc["codex_accounts"] = {
    "far-cx": archived_account(
      "codex", "far-cx", 0, "2026-08-21T10:00:00Z"
    ),
  }
  return doc


class AgentStatusTest(unittest.TestCase):
  def test_claude_row_contains_model_effort_weekly_and_context(self) -> None:
    session = {
      "model": {"display_name": "Opus 5"},
      "effort": {"level": "high"},
      "context_window": {"used_percentage": 12.4},
    }

    rendered = AGENT_STATUS.render_claude(session, report(), now=NOW)

    self.assertIn("Opus 5", rendered)
    self.assertIn("high", rendered)
    self.assertIn("5h 99%", rendered)
    self.assertIn("wk ~70%", rendered)
    self.assertIn("Fable 5 ~47%", rendered)
    self.assertIn("ctx 12% used", rendered)
    self.assertLess(rendered.index("5h 99%"), rendered.index("wk ~70%"))

  def test_claude_row_labels_scoped_session_windows(self) -> None:
    doc = report()
    doc["services"]["claude_code"]["limits"].append(
      limit(
        "claude_code",
        "fable",
        "Fable",
        88,
        scope_kind="model",
        window="5h",
      )
    )

    rendered = AGENT_STATUS.render_claude({}, doc, now=NOW)

    self.assertIn("Fable 5h 88%", rendered)

  def test_ended_value_is_clearly_historical(self) -> None:
    doc = report()
    claude = doc["services"]["claude_code"]
    claude["limits"][1]["last_observation"] = observation(
      70,
      reset_at="2026-08-19T21:00:00Z",
    )

    rendered = AGENT_STATUS.render_claude({}, doc, now=NOW)

    self.assertIn("wk ? (was 70%)", rendered)

  def test_tmux_wide_contains_each_weekly_bucket(self) -> None:
    rendered = AGENT_STATUS.render_tmux(report(), 160, now=NOW)

    self.assertEqual(
      strip_tmux_styles(rendered),
      "Claude ·~70%@1h30m (Fable ·~47%@1h30m) │ "
      "Codex ·46% (Spark ·100%) │ ",
    )

  def test_tmux_narrow_abbreviates_without_dropping_special_buckets(
    self,
  ) -> None:
    rendered = AGENT_STATUS.render_tmux(report(), 80, now=NOW)

    self.assertEqual(
      strip_tmux_styles(rendered),
      "Cl·~70%@1h30m (Fab·~47%@1h30m) │ Cx·46% (Spa·100%) │ ",
    )

  def test_tmux_colours_only_actionable_quota_values(self) -> None:
    doc = report()
    codex = doc["services"]["codex"]
    codex["limits"][0]["last_observation"] = observation(20)
    codex["limits"][1]["last_observation"] = observation(10)

    rendered = AGENT_STATUS.render_tmux(doc, 160, now=NOW)

    self.assertIn(
      "#[fg=yellow,nodim]20%#[fg=colour8,dim]",
      rendered,
    )
    self.assertIn("#[fg=red,nodim]10%#[fg=colour8,dim]", rendered)
    self.assertIn(
      "#[fg=yellow,nodim]~#[fg=colour8,dim]70%",
      rendered,
    )
    self.assertNotIn("#[fg=green]", rendered)

  def test_tmux_pace_glyph_colours_only_off_track_buckets(self) -> None:
    doc = report()
    claude = doc["services"]["claude_code"]
    codex = doc["services"]["codex"]
    claude["limits"][1]["pace"] = {"state": "behind"}
    claude["limits"][2]["pace"] = {"state": "surplus"}
    codex["limits"][0]["pace"] = {"state": "on_pace"}
    codex["limits"][1]["pace"] = {"state": "early"}

    rendered = AGENT_STATUS.render_tmux(doc, 160, now=NOW)

    self.assertIn("Claude #[fg=red,nodim]▼#[fg=colour8,dim]", rendered)
    self.assertIn("Fable #[fg=blue,nodim]▲#[fg=colour8,dim]", rendered)
    self.assertIn("Codex #[fg=colour8,dim]●#[fg=colour8,dim]46%", rendered)
    self.assertIn("Spark #[fg=colour8,dim]·#[fg=colour8,dim]100%", rendered)

  def test_tmux_pace_glyph_prefers_burn_and_ignores_stale_periods(
    self,
  ) -> None:
    doc = report()
    claude = doc["services"]["claude_code"]
    codex = doc["services"]["codex"]
    claude["limits"][1]["pace"] = {"state": "surplus"}
    claude["limits"][1]["burn"] = {"exhausts_before_reset": True}
    codex["limits"][0]["pace"] = {"state": "behind"}
    codex["limits"][0]["last_observation"] = observation(
      46, period="ended", reset_at="2026-08-19T21:00:00Z"
    )

    rendered = AGENT_STATUS.render_tmux(doc, 160, now=NOW)

    self.assertIn(
      "Claude #[fg=red,nodim,reverse]▼#[fg=colour8,dim,noreverse]",
      rendered,
    )
    self.assertIn("Codex #[fg=colour8,dim]·#[fg=colour8,dim]", rendered)

  def test_tmux_reverses_an_exhausted_quota(self) -> None:
    doc = report()
    codex = doc["services"]["codex"]
    codex["limits"][0]["last_observation"] = observation(0)

    rendered = AGENT_STATUS.render_tmux(doc, 160, now=NOW)

    self.assertIn(
      "#[fg=red,nodim,reverse]0%#[fg=colour8,dim,noreverse]",
      rendered,
    )

  def test_tmux_shows_a_session_window_about_to_run_out_instead(
    self,
  ) -> None:
    doc = report()
    claude = doc["services"]["claude_code"]["limits"]
    claude[0]["last_observation"] = observation(
      9, reset_at="2026-08-20T01:00:00Z"
    )
    reset = datetime(2026, 8, 20, 1, tzinfo=timezone.utc)
    when = AGENT_STATUS.reset_text(reset, NOW, None)

    wide = strip_tmux_styles(AGENT_STATUS.render_tmux(doc, 160, now=NOW))
    narrow = strip_tmux_styles(AGENT_STATUS.render_tmux(doc, 80, now=NOW))

    self.assertEqual(
      wide,
      f"Claude 5h ·9% reset {when} (Fable ·~47%@1h30m) │ "
      "Codex ·46% (Spark ·100%) │ ",
    )
    self.assertEqual(
      narrow,
      f"Cl5h·9%↻{when} (Fab·~47%@1h30m) │ Cx·46% (Spa·100%) │ ",
    )

  def test_tmux_session_window_replaces_only_its_own_bucket(self) -> None:
    doc = report()
    claude = doc["services"]["claude_code"]["limits"]
    claude.append(limit(
      "claude_code", "claude-fable-5", "Fable 5", 9,
      scope_kind="model", window="5h",
    ))

    rendered = strip_tmux_styles(AGENT_STATUS.render_tmux(doc, 160, now=NOW))

    self.assertIn("Claude ·~70%@1h30m (Fable 5h ·9% reset ", rendered)

  def test_tmux_keeps_weekly_unless_session_window_is_the_constraint(
    self,
  ) -> None:
    session_reset = "2026-08-20T01:00:00Z"
    exhausting = {"exhausts_before_reset": True}
    cases = {
      "plenty left": (observation(99, reset_at=session_reset), 70, None),
      "at threshold": (observation(10, reset_at=session_reset), 70, None),
      "burn alone": (observation(84, reset_at=session_reset), 90, exhausting),
      "weekly lower": (observation(8, reset_at=session_reset), 5, None),
      "period ended": (
        observation(0, period="ended", reset_at="2026-08-19T21:00:00Z"),
        70,
        None,
      ),
    }
    for name, (session, weekly, burn) in cases.items():
      with self.subTest(name):
        doc = report()
        claude = doc["services"]["claude_code"]["limits"]
        claude[0]["last_observation"] = session
        if burn is not None:
          claude[0]["burn"] = burn
        claude[1]["last_observation"] = observation(weekly)

        rendered = strip_tmux_styles(
          AGENT_STATUS.render_tmux(doc, 160, now=NOW)
        )

        self.assertNotIn("5h", rendered)
        self.assertIn(f"Claude ·{weekly}%", rendered)

  def test_tmux_shows_soonest_inactive_account_reset_within_36h(
    self,
  ) -> None:
    doc = with_archived_accounts(report())
    soon = datetime(2026, 8, 20, 18, tzinfo=timezone.utc)
    when = AGENT_STATUS.reset_text(soon, NOW, None)

    wide = strip_tmux_styles(AGENT_STATUS.render_tmux(doc, 160, now=NOW))
    narrow = strip_tmux_styles(AGENT_STATUS.render_tmux(doc, 80, now=NOW))

    self.assertEqual(
      wide,
      f"Claude ·~70%@1h30m (Fable ·~47%@1h30m) alt reset {when} │ "
      "Codex ·46% (Spark ·100%) │ ",
    )
    self.assertEqual(
      narrow,
      f"Cl·~70%@1h30m (Fab·~47%@1h30m) alt↻{when} │ "
      "Cx·46% (Spa·100%) │ ",
    )

  def test_tmux_ignores_resets_that_are_not_useful_to_switch_for(
    self,
  ) -> None:
    doc = with_archived_accounts(report())
    claude = doc["claude_accounts"]
    del claude["soon-cl"]
    del claude["later-cl"]
    claude["full-cl"] = archived_account(
      "claude_code", "full-cl", 100, "2026-08-20T01:00:00Z"
    )
    claude["session-cl"] = archived_account(
      "claude_code", "session-cl", 0, "2026-08-20T01:00:00Z", window="5h"
    )
    claude["mismatched"] = archived_account(
      "claude_code", "other", 0, "2026-08-20T01:00:00Z"
    )

    rendered = strip_tmux_styles(AGENT_STATUS.render_tmux(doc, 160, now=NOW))

    self.assertNotIn("alt", rendered)

  def test_tmux_inactive_reset_is_dim_and_never_names_the_account(
    self,
  ) -> None:
    doc = with_archived_accounts(report())

    rendered = AGENT_STATUS.render_tmux(doc, 160, now=NOW)

    self.assertIn("#[fg=colour8,dim] alt reset ", rendered)
    self.assertNotIn("example.com", rendered)
    self.assertNotIn("soon-cl", rendered)

  def test_reset_suffix_uses_local_timezone_only_when_near(self) -> None:
    near = observation(
      12,
      reset_at="2026-08-19T23:00:00Z",
    )
    far = observation(
      70,
      reset_at="2026-08-21T18:00:00Z",
    )

    near_text = AGENT_STATUS.format_observation(
      near,
      now=NOW,
      local_tz=timezone.utc,
      compact=False,
    )
    far_text = AGENT_STATUS.format_observation(
      far,
      now=NOW,
      local_tz=timezone.utc,
      compact=False,
    )

    self.assertEqual(near_text, "12% ↻23:00")
    self.assertEqual(far_text, "70%")

  def test_unsupported_schema_is_rejected(self) -> None:
    self.assertFalse(AGENT_STATUS.valid_report({"schema_version": 2}))


if __name__ == "__main__":
  unittest.main()
