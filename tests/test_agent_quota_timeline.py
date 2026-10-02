"""Tests for agent-quota --timeline, --live and archived-account ready times.

Split from test_agent_quota.py to keep each file a reviewable size; the
fixtures come from there.
"""
from __future__ import annotations

import io
import json
import re
import tempfile
import unittest
from datetime import timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock, patch

from tests.test_agent_quota import (
  AGENT_QUOTA,
  NOW,
  timeline_limit,
  timeline_service,
)


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
    self.row(output, "BURN", "Claude", "me@example.test",
             "40% left, 2.5%/h, out 3d 0h before reset, lasts at ≤0.42%/h")
    self.row(output, "RESET", "Claude", "me@example.test", "40% left")

  def burning(self) -> dict[str, Any]:
    """40% left at 2.5%/h: out in 1 day, 3 days before a 4-day reset."""
    document = {"services": {"claude_code": self.claude(
      timeline_limit("c:week", "weekly", 40, timedelta(days=4),
                     runs_out_in=timedelta(days=1)))}}
    document.setdefault("generated_at", AGENT_QUOTA.iso_utc(NOW))
    return document

  def test_burn_details_follow_in_priority_order_as_width_allows(self):
    shares = {"claude_code": [("cl:9cf6", 60), ("cl:015c", 30)]}
    details = ("40% left, 2.5%/h", ", out 3d 0h before reset",
               ", lasts at ≤0.42%/h", ", 15m: cl:9cf6 60%, cl:015c 30%")
    piped = AGENT_QUOTA.render_timeline(self.burning(), timezone.utc,
                                        consumers=shares)
    self.row(piped, "BURN", "Claude", "me@example.test", "".join(details))
    line = next(line for line in piped.splitlines() if "BURN" in line)
    # Each narrower width drops the lowest-priority detail still shown.
    for kept in range(len(details), 0, -1):
      width = len(line) - sum(map(len, details[kept:]))
      output = AGENT_QUOTA.render_timeline(
        self.burning(), timezone.utc, consumers=shares, width=width)
      self.row(output, "BURN", "Claude", "me@example.test",
               "".join(details[:kept]))
      output = AGENT_QUOTA.render_timeline(
        self.burning(), timezone.utc, consumers=shares, width=width - 1)
      self.assertNotIn(details[kept - 1] if kept > 1 else "\x00", output)
    # Without agent data the consumers detail is simply absent.
    self.row(AGENT_QUOTA.render_timeline(self.burning(), timezone.utc),
             "BURN", "Claude", "me@example.test", "".join(details[:3]))

  def test_burn_json_carries_the_reset(self):
    events = AGENT_QUOTA.timeline_document(self.burning())["events"]
    burn = next(event for event in events if event["type"] == "burn")
    self.assertEqual(burn["reset_at"],
                     AGENT_QUOTA.iso_utc(NOW + timedelta(days=4)))

  def reset_history(self, *points) -> dict[str, Any]:
    """A Codex weekly limit whose history is (minutes ago, reset_at, used)."""
    limit = timeline_limit("x:week", "weekly", 97, timedelta(days=7))
    limit["history"] = [
      {"observed_at": AGENT_QUOTA.iso_utc(NOW - timedelta(minutes=ago)),
       "reset_at": AGENT_QUOTA.iso_utc(reset), "used_percent": used}
      for ago, reset, used in points]
    limit["last_observation"]["reset_at"] = points[-1][1] and \
      AGENT_QUOTA.iso_utc(points[-1][1])
    return {"generated_at": AGENT_QUOTA.iso_utc(NOW), "services": {
      "codex": timeline_service("Codex", "me@example.test", "pro", [limit])}}

  def test_early_reset_shows_both_times_for_an_hour(self):
    due = NOW + timedelta(days=5, hours=9)
    new = NOW + timedelta(days=7)
    document = self.reset_history(
      (25, due, 99), (14, new - timedelta(minutes=11), 0),
      # A fresh window rolls its reset forward until first used.
      (9, new - timedelta(minutes=8), 0), (0, new, 3))
    output = AGENT_QUOTA.render_timeline(document, timezone.utc)
    self.row(output, "14m ago", "21:16", "RESET", "Codex", "me@example.test",
             "early: 1% → 100% left, due Tue 25 Aug 06:30 (5d 9h early)")
    self.assertEqual(output.count("RESET"), 2)   # the event and the new one
    events = AGENT_QUOTA.timeline_document(document)["events"]
    early = [event for event in events if event.get("early")]
    self.assertEqual(len(early), 1)
    self.assertEqual(early[0]["due_at"], AGENT_QUOTA.iso_utc(due))
    self.assertEqual(early[0]["at"], AGENT_QUOTA.iso_utc(
      NOW - timedelta(minutes=14)))
    # An hour later only the upcoming reset remains.
    later = dict(document, generated_at=AGENT_QUOTA.iso_utc(
      NOW + timedelta(minutes=47)))
    self.assertNotIn("early", AGENT_QUOTA.render_timeline(later,
                                                          timezone.utc))

  def test_scheduled_reset_is_shown_without_early(self):
    due = NOW - timedelta(minutes=20)
    document = self.reset_history(
      (30, due, 40), (10, due + timedelta(days=7), 0),
      (0, due + timedelta(days=7), 1))
    output = AGENT_QUOTA.render_timeline(document, timezone.utc)
    self.row(output, "10m ago", "21:20", "RESET", "Codex", "me@example.test",
             "60% → 100% left")
    self.assertNotIn("early", output)

  def test_a_rolling_unstarted_window_is_not_a_reset(self):
    start = NOW + timedelta(days=7)
    output = AGENT_QUOTA.render_timeline(self.reset_history(
      (20, start - timedelta(minutes=20), 0),
      (10, start - timedelta(minutes=10), 0), (0, start, 0)), timezone.utc)
    self.assertNotIn("ago", output)

  def test_early_reset_alerts_once(self):
    due = NOW + timedelta(days=5)
    document = self.reset_history(
      (5, due, 99), (0, NOW + timedelta(days=7), 0))
    _, rows, _ = AGENT_QUOTA.timeline_rows(document, NOW)
    sent: set = set()
    alerts = AGENT_QUOTA.timeline_alerts({}, rows, NOW, sent)
    self.assertEqual(alerts, ["Codex reset early for me@example.test "
                              "(1% → 100% left)"])
    self.assertEqual(AGENT_QUOTA.timeline_alerts({}, rows, NOW, sent), [])
    # The same reset under a reordered merged quota name is not new.
    renamed = [dict(row, quota="Fable, " + row["quota"]) for row in rows]
    self.assertEqual(AGENT_QUOTA.timeline_alerts({}, renamed, NOW, sent), [])
    # When the detected row leaves the timeline, no "reset" alert follows.
    previous = {AGENT_QUOTA.timeline_row_key(row): row for row in rows}
    self.assertEqual(AGENT_QUOTA.timeline_alerts(
      previous, [], NOW + timedelta(hours=2), sent), [])

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
                        side_effect=OSError("no agents")),
                        display_width=lambda: None)), \
         patch.object(AGENT_QUOTA, "live_consumers", return_value=None), \
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
  def setUp(self) -> None:
    # Never read the real agents cache for BURN consumers.
    consumers = patch.object(AGENT_QUOTA, "live_consumers", return_value=None)
    consumers.start()
    self.addCleanup(consumers.stop)

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

  def run_frames(self, documents, keys=None,
                 **overrides) -> tuple[str, list[str]]:
    out, sent = io.StringIO(), []
    frames = iter(documents)
    # Waits are split into spinner steps; stop once every frame has drawn.
    budget = [(len(documents) - 1) * (overrides.get("interval") or 30)]
    typed = iter(keys or [])

    def wait(seconds):
      pressed = next(typed, "")
      if budget[0] <= 0:
        raise KeyboardInterrupt
      budget[0] -= seconds
      return pressed
    with patch.object(AGENT_QUOTA, "live_document",
                      side_effect=lambda *_: next(frames)), \
         patch.object(AGENT_QUOTA, "notify", side_effect=sent.append):
      code = AGENT_QUOTA.run_live(self.args(**overrides), Path("/none"),
                                  out=out, clock=lambda: NOW.timestamp(),
                                  wait=wait)
    self.assertEqual(code, 0)
    return out.getvalue(), sent

  def test_loop_uses_the_alternate_screen_and_restores_it(self):
    output, _ = self.run_frames([self.document(False)])
    self.assertTrue(output.startswith(AGENT_QUOTA.ENTER_SCREEN))
    self.assertTrue(output.endswith(AGENT_QUOTA.LEAVE_SCREEN))
    self.assertIn("every 30s", output)
    self.assertIn("Timeline", output)
    frame = output.split("\033[H\033[2J", 1)[1]
    self.assertNotEqual(frame.splitlines()[1], '')
    self.assertEqual(frame.count('Timeline'), 1)

  def test_live_uses_pane_dimensions_over_environment(self):
    from types import SimpleNamespace
    import os
    with patch.dict(os.environ, COLUMNS='1000', LINES='1000'), \
         patch.object(os, 'get_terminal_size',
                      return_value=os.terminal_size((40, 10))) as size:
      self.assertEqual(AGENT_QUOTA.live_terminal_size(
        SimpleNamespace(fileno=lambda: 9)), (40, 10))
      size.assert_called_once_with(9)

  def test_live_line_clipping_prevents_wrap_over_spinner(self):
    line = '\x1b[31m' + '界' * 30 + '\x1b[0m'
    clipped = AGENT_QUOTA.clip_live_line(line, 20)
    plain = __import__('re').sub(r'\x1b\[[0-9;]*m', '', clipped)
    self.assertLessEqual(sum(2 if c == '界' else 1 for c in plain), 20)
    self.assertIn('\x1b[31m', clipped)
    self.assertTrue(clipped.endswith('\x1b[0m'))
    output, _ = self.run_frames([self.document(False)])
    self.assertIn('Timeline', output)

  def test_spinner_ticks_slowly_between_frames(self):
    output, _ = self.run_frames([self.document(False)] * 2, interval=5)
    first, second = output.split("\033[H\033[2J")[1:]
    spinner = AGENT_QUOTA.SPINNER
    self.assertIn(spinner[0] + " Timeline", first)
    # One repaint of the spinner cell per second, and nothing else.
    ticks = first.split("\033[1;1H")[1:]
    self.assertEqual(ticks, list(spinner[1:6]))
    self.assertIn(spinner[5] + " Timeline", second)
    self.assertEqual(AGENT_QUOTA.SPINNER_STEP, 1.0)

  def test_q_and_escape_quit_but_arrow_keys_do_not(self):
    for keys, ticks in ((["", "q"], 1), (["Q"], 0), (["", "", "\x1b"], 2),
                        (["\x1b[A", "\x1bOB", "q"], 2)):
      output, _ = self.run_frames([self.document(False)] * 3, keys=keys)
      self.assertEqual(output.count("\033[H\033[2J"), 1, keys)
      self.assertEqual(output.count("\033[1;1H"), ticks, keys)
      self.assertTrue(output.endswith(AGENT_QUOTA.LEAVE_SCREEN), keys)
    self.assertIn("· q to quit", output)

  def test_key_wait_sleeps_without_a_terminal(self):
    with patch.object(AGENT_QUOTA.sys, "stdin", io.StringIO()), \
         patch.object(AGENT_QUOTA.time, "sleep") as sleep:
      self.assertEqual(AGENT_QUOTA.key_wait(0.5), "")
    sleep.assert_called_once_with(0.5)

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
