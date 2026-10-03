from __future__ import annotations

import copy
from types import SimpleNamespace
import importlib.util
import json
import re
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.test_agent_quota import AGENT_QUOTA

SPEC = importlib.util.spec_from_file_location(
  "quota_agents",
  Path(__file__).parents[1] / "bin/agent_quota_agents.py",
)
AGENTS = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(AGENTS)


class AgentViewTest(unittest.TestCase):
  def test_summary_rechecks_account_before_starting_batch(self):
    agent = {"key": "codex:one", "provider": "codex",
             "messages": ["Fix parser"], "last_seen": 999}
    with (
      patch.object(AGENTS, "summary_providers", return_value=(["codex"], None)),
      patch.object(AGENTS, "subscription_block", return_value=None),
      patch.object(AGENTS, "provider_block", return_value=None),
      patch.object(AGENTS, "summarize_batch") as generate,
    ):
      checks = iter([None, "Codex account differs from quota snapshot"])
      result = AGENTS.refresh_summaries(
        [agent], {}, {}, {}, now=1000, account_check=lambda provider: next(checks),
      )
    generate.assert_not_called()
    self.assertIn("account differs", result["codex:one"]["error"])

  def test_summary_defers_when_active_account_differs_from_quota(self):
    agent = {"key": "codex:one", "provider": "codex",
             "messages": ["Fix parser"], "last_seen": 999}
    with (
      patch.object(AGENTS, "summary_providers", return_value=(["codex"], None)),
      patch.object(AGENTS, "subscription_block", return_value=None),
      patch.object(AGENTS, "summarize_batch") as generate,
    ):
      result = AGENTS.refresh_summaries(
        [agent], {}, {}, {}, now=1000,
        account_check=lambda provider: "Codex account differs from quota snapshot",
      )
    generate.assert_not_called()
    self.assertIn("account differs", result["codex:one"]["skip_reason"])

  def test_claude_summary_checks_account_at_selection_and_before_batch(self):
    agent = {"key": "claude:one", "provider": "claude",
             "messages": ["Fix parser"], "last_seen": 999}
    for checks in (["Claude account differs"], [None, "Claude account differs"]):
      with (
        self.subTest(checks=checks),
        patch.object(AGENTS, "summary_providers", return_value=(["claude"], None)),
        patch.object(AGENTS, "subscription_block", return_value=None),
        patch.object(AGENTS, "provider_block", return_value=None),
        patch.object(AGENTS, "summarize_batch") as generate,
      ):
        results = iter(checks)
        def account_check(provider):
          self.assertEqual(provider, "claude")
          return next(results)
        result = AGENTS.refresh_summaries(
          [agent], {}, {}, {}, now=1000, account_check=account_check,
        )
      generate.assert_not_called()
      self.assertIn("account differs", str(result["claude:one"]))

  def setUp(self):
    self.tmp = tempfile.TemporaryDirectory()
    self.addCleanup(self.tmp.cleanup)
    self.root = Path(self.tmp.name)

  def transcript(self, rows, name="session.jsonl"):
    path = self.root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))
    return path

  def claude_rows(self, finished=False):
    usage = {"input_tokens": 10, "output_tokens": 5,
             "cache_read_input_tokens": 100,
             "cache_creation_input_tokens": 0}
    todos = [{"content": "Write code", "status": "completed"},
             {"content": "Run tests", "status": "in_progress"},
             {"content": "Open PR", "status": "pending"}]
    rows = [
      {"type": "user", "timestamp": "2026-08-19T21:00:00Z",
       "message": {"content": "Run the tests"}},
      {"type": "assistant", "timestamp": "2026-08-19T21:01:00Z",
       "message": {"id": "m1", "stop_reason": "tool_use", "usage": usage,
                   "content": [{"type": "tool_use", "id": "t1",
                                "name": "TodoWrite",
                                "input": {"todos": todos}}]}},
      {"type": "user", "timestamp": "2026-08-19T21:01:05Z",
       "message": {"content": [{"type": "tool_result",
                                "tool_use_id": "t1", "content": "ok"}]}},
      {"type": "assistant", "timestamp": "2026-08-19T21:02:00Z",
       "message": {"id": "m2", "stop_reason": "tool_use", "usage": usage,
                   "content": [{"type": "tool_use", "id": "t2",
                                "name": "Bash",
                                "input": {"command": "pytest -q",
                                          "description": "Run tests"}}]}},
    ]
    if finished:
      rows += [
        {"type": "user", "timestamp": "2026-08-19T21:05:00Z",
         "message": {"content": [{"type": "tool_result",
                                  "tool_use_id": "t2", "content": "ok"}]}},
        {"type": "assistant", "timestamp": "2026-08-19T21:06:00Z",
         "message": {"id": "m3", "stop_reason": "end_turn", "usage": usage,
                     "content": [{"type": "text", "text": "Done."}]}},
      ]
    return rows

  def test_claude_status_follows_turns_tools_and_todos(self):
    agent = AGENTS.parse_session(self.transcript(self.claude_rows()),
                                 "claude")
    status = agent["status"]
    self.assertEqual(status["state"], "working")
    self.assertEqual(status["action"], "Bash: pytest -q")
    self.assertEqual(status["progress"],
                     {"done": 1, "total": 3, "current": "Run tests"})
    self.assertEqual(status["turn_started"],
                     AGENTS.stamp("2026-08-19T21:00:00Z"))
    self.assertEqual(agent["token_events"][0]["cached"], 100)

    done = AGENTS.parse_session(
      self.transcript(self.claude_rows(finished=True), "done.jsonl"),
      "claude")["status"]
    self.assertEqual(done["state"], "waiting")
    self.assertIsNone(done["action"])
    self.assertEqual(done["durations"], [360])

  def codex_rows(self, ending):
    rows = [
      {"type": "session_meta", "timestamp": "2026-08-19T20:59:00Z",
       "payload": {"id": "x", "timestamp": "2026-08-19T20:59:00Z",
                   "cwd": "/src/git/app"}},
      {"type": "event_msg", "timestamp": "2026-08-19T21:00:00Z",
       "payload": {"type": "task_started", "turn_id": "t"}},
      {"type": "response_item", "timestamp": "2026-08-19T21:01:00Z",
       "payload": {"type": "function_call", "name": "update_plan",
                   "call_id": "p1", "arguments": json.dumps({"plan": [
                     {"step": "Build", "status": "completed"},
                     {"step": "Test", "status": "in_progress"}]})}},
      {"type": "response_item", "timestamp": "2026-08-19T21:01:01Z",
       "payload": {"type": "function_call_output", "call_id": "p1",
                   "output": "ok"}},
      {"type": "response_item", "timestamp": "2026-08-19T21:02:00Z",
       "payload": {"type": "custom_tool_call", "name": "exec",
                   "call_id": "c1",
                   "input": 'await tools.exec_command({cmd:"cargo test"})'}},
    ]
    for total, when in ((1000, "21:02:30"), (1600, "21:03:00")):
      rows.append({"type": "event_msg", "timestamp": f"2026-08-19T{when}Z",
                   "payload": {"type": "token_count", "info": {
                     "total_token_usage": {
                       "input_tokens": total, "cached_input_tokens":
                       total // 2, "output_tokens": 0,
                       "total_tokens": total}}}})
    if ending:
      rows.append({"type": "event_msg", "timestamp": "2026-08-19T21:05:00Z",
                   "payload": {"type": ending, "turn_id": "t",
                               "duration_ms": "300000"}})
    return rows

  def test_codex_status_turns_actions_plans_and_aborts(self):
    working = AGENTS.parse_session(
      self.transcript(self.codex_rows(None), "w.jsonl"), "codex")
    self.assertEqual(working["status"]["state"], "working")
    self.assertEqual(working["status"]["action"], "exec: cargo test")
    self.assertEqual(working["cwd"], "/src/git/app")
    self.assertEqual(working["status"]["progress"],
                     {"done": 1, "total": 2, "current": "Test"})
    self.assertEqual([(e["tokens"], e["cached"])
                      for e in working["token_events"]],
                     [(1000, 500), (600, 300)])
    done = AGENTS.parse_session(
      self.transcript(self.codex_rows("task_complete"), "d.jsonl"),
      "codex")["status"]
    self.assertEqual((done["state"], done["durations"], done["action"]),
                     ("waiting", [300], None))
    aborted = AGENTS.parse_session(
      self.transcript(self.codex_rows("turn_aborted"), "a.jsonl"),
      "codex")["status"]
    self.assertEqual(aborted["state"], "aborted")

  def test_action_summaries_prefer_commands_then_tool_names(self):
    self.assertEqual(AGENTS.action_summary(
      "exec", 'await tools.exec_command({cmd:"make test"})'),
      ("exec: make test", True))
    self.assertEqual(AGENTS.action_summary(
      "exec", "const r = await tools.wait({id: 1}); tools.read_file(x)"),
      ("exec: wait, read_file", None))
    self.assertEqual(AGENTS.action_summary(
      "Read", {"file_path": "/a/b.py"}), ("Read: /a/b.py", True))
    self.assertEqual(AGENTS.action_summary(
      "Agent", {"description": "Review the plan"}),
      ("Agent: Review the plan", False))
    self.assertEqual(AGENTS.action_summary("Agent", {}), ("Agent", False))
    self.assertEqual(AGENTS.action_summary(
      "SendMessage", {"to": "a5cf", "summary": "Hold PR 42"}),
      ("SendMessage: Hold PR 42", False))

  def test_commands_and_paths_keep_their_end(self):
    command = "cd ~/git/agent-changelog && " + "x" * 200 + " && git push"
    text, tail = AGENTS.action_summary("Bash", {"command": command})
    self.assertTrue(tail)
    self.assertEqual(len(text), AGENTS.ACTION_LIMIT)
    self.assertTrue(text.startswith("Bash: …x"))
    fitted = AGENTS.fit_action(text, 28, tail)
    self.assertEqual(fitted, "Bash: …xxxxxxxxx && git push")
    self.assertLessEqual(len(fitted), 28)
    # Heredocs and multi-line scripts are named by their first line.
    text, _ = AGENTS.action_summary(
      "Bash", {"command": "python3 - <<'EOF'\nprint(1)\nEOF"})
    self.assertEqual(text, "Bash: python3 - <<'EOF'")
    # Other details, and progress, keep their start.
    self.assertEqual(AGENTS.fit_action("Agent: " + "y" * 40, 12),
                     "Agent: yyyy…")
    # A tool name too long to leave room clips the whole text.
    self.assertEqual(AGENTS.fit_action("A" * 30 + ": cmd", 12, True),
                     "…" + ("A" * 30 + ": cmd")[-11:])

  def test_agent_alerts_for_stalls_and_dominance(self):
    stalled = dict(self.work_row("a", "Fix build", "Fix"), state="stalled",
                   recent_tokens=0, status={"turn_started": 5})
    busy = dict(self.work_row("b", "Refactor", "Refactor"), state="working",
                recent_tokens=900_000)
    other = dict(self.work_row("c", "Docs", "Docs"), state="working",
                 recent_tokens=100_000)
    sent = set()
    alerts = AGENTS.agent_alerts({"claude:a": "working"},
                                 [stalled, busy, other], 7200, sent)
    self.assertEqual(alerts, ["claude:a stalled: Fix build",
                              "claude:b is using 90% of recent claude "
                              "tokens"])
    self.assertEqual(AGENTS.agent_alerts({"claude:a": "stalled"},
                                         [stalled, busy, other], 7300,
                                         sent), [])

  def test_live_header_names_the_outlook_and_busiest_agents(self):
    limit = {"limit_id": "c:week", "window": {"label": "weekly"},
             "bucket": {"scope_kind": "account"},
             "last_observation": {"remaining_percent": 30},
             "burn": {"exhausts_before_reset": True,
                      "exhausts_at": AGENT_QUOTA.iso_utc(
                        AGENT_QUOTA.datetime.fromtimestamp(
                          1000 + 7200, AGENT_QUOTA.timezone.utc))},
             "pace": {}}
    document = {"services": {"claude_code": {
      "display_name": "Claude Code", "binding_limit_id": "c:week",
      "limits": [limit]}}}
    agents = [dict(self.work_row("a", "x", "x"), recent_tokens=750),
              dict(self.work_row("b", "y", "y"), recent_tokens=250)]
    header = AGENTS.live_header(document, agents, AGENT_QUOTA, 1000)
    self.assertEqual(header, ["Claude Code · weekly 30% left · runs out ~2h "
                              "0m · 15m: claude:a 75%, claude:b 25%"])
    document["services"]["claude_code"]["binding_limit_id"] = None
    limit["last_observation"]["period_relation"] = "current"
    self.assertIn("weekly 30% left", AGENTS.live_header(
      document, agents, AGENT_QUOTA, 1000)[0])

  def test_live_header_does_not_fall_back_to_previous_identity(self):
    document = {"services": {"codex": {
      "display_name": "Codex", "limits": [],
      "account": {"label": "same@example.test", "plan": "promax",
                  "key": None}}}, "codex_accounts": {"old": {
      "display_name": "Codex", "limits": [{
        "last_observation": {"remaining_percent": 82}}],
      "account": {"label": "same@example.test", "plan": "prolite",
                  "key": "old"}}}}
    header = AGENTS.live_header(document, [], AGENT_QUOTA, 1000)[0]
    self.assertIn("No quota readings for this account yet", header)
    self.assertNotIn("82%", header)

  def test_live_header_keeps_the_account_headline_and_fits(self):
    account = {"limit_id": "c:week", "window": {"label": "weekly"},
               "bucket": {"scope_kind": "account"},
               "last_observation": {"remaining_percent": 30,
                                    "period_relation": "current"},
               "burn": {}, "pace": {"reset_in_seconds": 3600}}
    fable = {"limit_id": "c:fable", "window": {"label": "weekly"},
             "bucket": {"scope_kind": "model", "name": "Fable"},
             "last_observation": {"remaining_percent": 0,
                                  "period_relation": "current"},
             "burn": {}, "pace": {}}
    document = {"services": {"claude_code": {
      "display_name": "Claude Code", "binding_limit_id": "c:fable",
      "limits": [account, fable]}}}
    agents = [dict(self.work_row(ident, "x", "x"), recent_tokens=tokens)
              for ident, tokens in (("0a1b2c3d9cf6", 500),
                                    ("0a1b2c3d015c", 300),
                                    ("0a1b2c3d39d4", 200))]
    document["services"]["codex"] = {
      "display_name": "Codex", "limits": [dict(account, limit_id="x:week")]}
    agents.append(dict(self.work_row("01a0aaaa4b37", "y", "y"),
                       provider="codex", key="codex:01a0aaaa4b37",
                       recent_tokens=100))
    wide, codex = AGENTS.live_header(document, agents, AGENT_QUOTA, 1000)
    # Each line names its own provider's agents.
    self.assertIn("15m: codex:01a0…4b37 100%", codex)
    self.assertNotIn("codex:", wide)
    self.assertIn("weekly 30% left · resets in 1h 0m · Fable blocked", wide)
    self.assertIn("claude:0a1b…9cf6 50%", wide)
    for width, expected in ((100, "cl:9cf6 50%, cl:015c 30%"), (60, None)):
      with patch.object(AGENTS, "display_width", return_value=width):
        line = AGENTS.live_header(document, agents, AGENT_QUOTA, 1000)[0]
      self.assertLessEqual(len(line), width)
      if expected:
        self.assertIn(expected, line)

  def test_recent_shares_use_compact_ids_busiest_first(self):
    agents = self.ladder_agents()
    agents[1]["recent_tokens"] = 300_000
    agents[2]["recent_tokens"] = 100_000
    shares = AGENTS.recent_shares(agents)
    self.assertEqual(shares["claude_code"], [("cl:9cf6", 100.0)])
    self.assertEqual([label for label, _ in shares["codex"]],
                     ["co:4b37", "co:61d4"])
    self.assertEqual([round(share) for _, share in shares["codex"]],
                     [75, 25])
    self.assertEqual(AGENTS.recent_shares(agents, count=1)["codex"],
                     [("co:4b37", 75.0)])
    for agent in agents:
      agent["recent_tokens"] = 0
    self.assertEqual(AGENTS.recent_shares(agents), {})

  def test_folded_now_skips_quiet_steps(self):
    agents = self.ladder_agents()
    agents[0].update(now="after Bash: make test", now_quiet=True)
    with patch.object(AGENTS, "display_width", return_value=60):
      output = AGENTS.render(agents, {"sessions": {}}, AGENT_QUOTA, 1000)
    self.assertNotIn("after", output.split("\n\n")[0])
    self.assertIn("Fix the build pipeline", output)

  def test_now_shows_only_while_every_label_fits_whole(self):
    agents = self.ladder_agents()
    with patch.object(AGENTS, "display_width", return_value=200):
      output = AGENTS.render(agents, {"sessions": {}}, AGENT_QUOTA, 1000)
    header = output.splitlines()[0]
    self.assertIn(" Now ", header)
    self.assertIn("Cache", header)
    # A label that fits whole only without Now folds Now before anything
    # else is compacted, and that label is then shown whole.
    long = "Weigh the whole-skeleton corrector against per-bone IK fixes"
    agents[1]["work"] = long
    # The table needs 182 columns beside Now and 165 without it.
    with patch.object(AGENTS, "display_width", return_value=175):
      output = AGENTS.render(agents, {"sessions": {}}, AGENT_QUOTA, 1000)
    header = output.splitlines()[0]
    self.assertNotIn(" Now ", header + " ")
    self.assertIn("Cache", header)
    self.assertIn(long, output)
    self.assertIn("working 12m", output)   # fits whole: states spelled out
    # Narrower, the label fits whole only once states are glyphs, which
    # still comes before any other compaction.
    with patch.object(AGENTS, "display_width", return_value=162):
      output = AGENTS.render(agents, {"sessions": {}}, AGENT_QUOTA, 1000)
    table = output.split("\n\n")[0]
    self.assertIn("▸ 12m", table)
    self.assertNotIn("working", table)
    self.assertIn("Cache", table.splitlines()[0])
    self.assertIn(long, table)
    self.assertIn("Fix the build pipeline · Bash: make test", output)

  def test_folded_now_follows_the_summary_in_spare_room(self):
    agents = self.ladder_agents()
    for width, expected in (
      (80, "Fix the build pipeline · Bash: make test"),
      (76, "Fix the build pipeline · Bash: …e test"),  # keeps its end
    ):
      with patch.object(AGENTS, "display_width", return_value=width):
        output = AGENTS.render(agents, {"sessions": {}}, AGENT_QUOTA, 1000)
      self.assertNotIn(" Now ", output.splitlines()[0] + " ")
      self.assertIn(expected, output)
    # A summary too long for Work falls back to its brief before any action
    # is given room, and the action never leads.
    agents[0]["work"] = "Fix the build pipeline and every flaky test " * 3
    with patch.object(AGENTS, "display_width", return_value=80):
      output = AGENTS.render(agents, {"sessions": {}}, AGENT_QUOTA, 1000)
    self.assertIn("Fix build · Bash: make test", output)

  def test_internal_sessions_share_one_row_unless_verbose(self):
    def guardian(ident, tokens):
      return dict(self.work_row(ident, "g", "g"), provider="codex",
                  key="codex:" + ident, label="guardian", internal=True,
                  state="waiting", turn_age=60, long_turn=False, now="—",
                  recent_tokens=tokens, cwd="/src/app",
                  summary_outdated=False, models=["m"], efforts=["low"],
                  speeds=[], warnings=[])
    agents = [guardian("g1", 100), dict(self.work_row("w", "Work", "Work"),
              state="working", turn_age=60, long_turn=False, now="—",
              recent_tokens=50, summary_outdated=False, models=["m"], label="w",
              efforts=["high"], speeds=[], warnings=[]), guardian("g2", 300)]
    with patch.object(AGENTS, "display_width", return_value=200):
      output = AGENTS.render(agents, {"sessions": {}}, AGENT_QUOTA, 1000)
      verbose = AGENTS.render(agents, {"sessions": {}}, AGENT_QUOTA, 1000,
                              verbose=True)
    row = next(line for line in output.splitlines() if "guardian" in line)
    self.assertTrue(row.startswith("codex:guardian"))
    self.assertIn("2 guardian sessions", row)
    self.assertIn("400", row)
    self.assertEqual(sum("guardian" in line
                         for line in output.splitlines()), 1)
    self.assertNotIn("guardian sessions", verbose)

  def ladder_agents(self):
    def row(ident, work, brief, **fields):
      agent = self.work_row(ident, work, brief)
      agent.update(recent_tokens=0, turn_age=None, long_turn=False, now="—",
                   cwd="/src/git/dotfiles-housekeeping")
      agent.update(fields)
      return agent
    working = row("0a1b2c3d9cf6", "Fix the build pipeline", "Fix build",
                  state="working", turn_age=720, now="Bash: make test",
                  now_tail=True,
                  recent_tokens=1_200_000, effort="medium")
    waiting = row("01a0aaaa4b37", "Review PR", "Review", state="waiting",
                  turn_age=60, provider="codex", key="codex:01a0aaaa4b37",
                  models=["codex-auto-review"], model="codex-auto-review")
    child = row("01a0bbbb61d4", "Subagent pass", "Pass", state="done",
                provider="codex", key="codex:01a0bbbb61d4",
                parent_id="01a0aaaa4b37")
    return [working, waiting, child]

  def applied(self, output):
    lines = output.splitlines()
    header, table = lines[0], "\n".join(lines[2:5])
    return [step for step, present in (
      ("fold_now", "Now" not in header),
      ("state_glyphs", "▸" in table),
      ("drop_cache", "Cache" not in header),
      ("drop_seen", "Seen" not in header),
      ("short_ids", "claude:" not in table),
      ("short_dir", "dotfiles…" in table or "Dir" not in header),
      ("drop_tokens", "Tokens" not in header),
      ("effort_prefix", " medium " not in table),
      ("short_model", "auto-re…" in table),
      ("drop_dir", "Dir" not in header)) if present]

  def test_compaction_follows_the_ladder_only_as_needed(self):
    agents = self.ladder_agents()
    previous = []
    for columns in range(200, 49, -1):
      with patch.object(AGENTS, "display_width", return_value=columns):
        output = AGENTS.render(agents, {"sessions": {}}, AGENT_QUOTA, 1000)
      steps = self.applied(output)
      self.assertEqual(steps, list(AGENTS.COMPACTION[:len(steps)]), columns)
      self.assertGreaterEqual(len(steps), len(previous), columns)
      previous = steps
    self.assertEqual(previous, list(AGENTS.COMPACTION))
    with patch.object(AGENTS, "display_width", return_value=None):
      piped = AGENTS.render(agents, {"sessions": {}}, AGENT_QUOTA, 1000)
    self.assertEqual(self.applied(piped), [])

  def test_no_line_exceeds_the_width_down_to_the_minimum(self):
    agents = self.ladder_agents()
    for columns in range(60, 201):
      with patch.object(AGENTS, "display_width", return_value=columns):
        output = AGENTS.render(agents, {"sessions": {}}, AGENT_QUOTA, 1000)
      for line in output.splitlines():
        self.assertLessEqual(len(line), columns, (columns, line))

  def test_compact_forms_and_legend(self):
    agents = self.ladder_agents()
    with patch.object(AGENTS, "display_width", return_value=60):
      output = AGENTS.render(agents, {"sessions": {}}, AGENT_QUOTA, 1000)
    self.assertIn("cl:9cf6", output)
    self.assertIn("└co:61d4", output)
    self.assertIn("▸ 12m", output)
    # Too narrow for a useful folded action: the summary keeps Work.
    self.assertIn("  Fix the build pipeline  ", output)
    self.assertNotIn("Bash:", output.split("\n\n")[0])
    self.assertNotIn(" Now ", output.splitlines()[0] + " ")
    self.assertIn(" Eff ", output.splitlines()[0] + " ")
    self.assertIn(" med ", output)     # as wide as its heading allows
    self.assertIn("▸\u00a0working", output)          # legend when glyphs show
    with patch.object(AGENTS, "display_width", return_value=200):
      wide = AGENTS.render(agents, {"sessions": {}}, AGENT_QUOTA, 1000)
    self.assertNotIn("▸\u00a0working", wide)
    self.assertIn("working 12m", wide)

  def test_glyphs_are_single_width(self):
    import unicodedata
    for glyph, _ in AGENTS.STATE_LEGEND.values():
      self.assertEqual(len(glyph), 1)
      self.assertIn(unicodedata.east_asian_width(glyph), ("N", "Na", "H"),
                    glyph)

  def test_derived_short_forms_grow_on_collision(self):
    self.assertEqual(AGENTS.unique_prefixes(["claude", "codex"]),
                     {"claude": "cl", "codex": "co"})
    self.assertEqual(AGENTS.unique_prefixes(["claude", "codex", "cursor"]),
                     {"claude": "cl", "codex": "co", "cursor": "cu"})
    self.assertEqual(AGENTS.unique_prefixes(["high", "medium", "mixed",
                                             "max"])["medium"], "me")
    self.assertEqual(AGENTS.unique_prefixes(["high", "low", "xhigh",
                                             "mixed", "max"], 3),
                     {"high": "hig", "low": "low", "xhigh": "xhi",
                      "mixed": "mix", "max": "max"})
    self.assertEqual(AGENTS.unique_suffix_length(
      ["aaaa1234", "bbbb1234", "cccc5678"]), 5)

  def test_unsized_terminal_uses_the_default_width(self):
    size = os.terminal_size((0, 0))
    with patch.dict(os.environ, {"COLUMNS": ""}), \
         patch.object(AGENTS.sys.stdout, "isatty", return_value=True,
                      create=True), \
         patch.object(AGENTS.sys.stdout, "fileno", return_value=1,
                      create=True), \
         patch.object(AGENTS.os, "get_terminal_size", return_value=size):
      self.assertEqual(AGENTS.display_width(), 120)

  def test_model_and_dir_short_forms_follow_rules(self):
    for model, label in (("claude-opus-5-5", "opus5.5"), ("gpt-6-sol", "6-sol"),
                         ("claude-haiku-4-5-20251001", "haiku4.5"),
                         ("claude-sonnet-5", "sonnet5"),
                         ("codex-auto-review", "auto-review"),
                         ("brand-new", "brand-new"), ("claude-", "claude-")):
      self.assertEqual(AGENTS.model_label(model), label)
    clip = AGENTS.middle_clip
    # Siblings keep their shared start and their differing end.
    self.assertEqual(clip("gameproj-toast-options", 16), "gameproj…options")
    self.assertEqual(clip("gameproj-hud-defects", 16), "gameproj…defects")
    self.assertEqual(clip("dotfiles", 16), "dotfiles")
    self.assertEqual(clip("averyveryverylongname", 12), "averyv…gname")
    self.assertEqual(len(clip("x" * 40, 5)), 5)

  def test_dir_labels_use_the_last_segment_unless_ambiguous(self):
    labels = AGENTS.dir_labels(
      ["/h/git/app", "/h/work/app", "/h/git/dotfiles", "/h", None],
      home="/h")
    self.assertEqual(labels, {"/h/git/app": "git/app",
                              "/h/work/app": "work/app",
                              "/h/git/dotfiles": "dotfiles", "/h": "~"})
    self.assertEqual(AGENTS.dir_labels(["/"]), {"/": "/"})

  def test_unknown_state_model_and_effort_render_in_full(self):
    agent = dict(self.work_row("x1", "Work", "Work"), state="paused",
                 turn_age=None, long_turn=False, now="—", recent_tokens=0,
                 models=["brand-new-model-9"], model="brand-new-model-9",
                 effort="hyper")
    self.assertEqual(AGENTS.state_text(agent, "paused", True, AGENT_QUOTA),
                     "paused")
    with patch.object(AGENTS, "display_width", return_value=200):
      output = AGENTS.render([agent], {"sessions": {}}, AGENT_QUOTA, 1000)
    self.assertIn("paused", output)
    self.assertIn("hyper", output)
    self.assertIn("brand-new-m…", output)

  def test_cache_version_change_keeps_summaries(self):
    path = self.root / "agents.json"
    summaries = {"claude:a": {"summary": "Fix build", "input_hash": "h"}}
    path.write_text(json.dumps({"version": AGENTS.CACHE_VERSION - 1,
                                "sessions": {"x": {}},
                                "summaries": summaries}))
    cache = AGENTS.load_cache(path)
    self.assertEqual(cache["version"], AGENTS.CACHE_VERSION)
    self.assertEqual(cache["sessions"], {})       # re-parsed under the new
    self.assertEqual(cache["summaries"], summaries)  # model calls kept
    path.write_text("not json")
    self.assertEqual(AGENTS.load_cache(path)["summaries"], {})

  def test_display_status_derives_stalled_idle_and_long(self):
    base = {"state": "working", "turn_started": 0, "last_event": 0,
            "action": "Bash: make", "progress": None,
            "durations": [300, 360, 420]}
    self.assertEqual(AGENTS.display_status(base, 600)[:2], ("working", 600))
    self.assertEqual(AGENTS.display_status(base, 1500)[0], "stalled")
    self.assertTrue(AGENTS.display_status(dict(base, last_event=1990),
                                          2000)[2])  # 33m vs ~6m usual
    waiting = dict(base, state="waiting", last_event=0)
    self.assertEqual(AGENTS.display_status(waiting, 600)[0], "waiting")
    self.assertEqual(AGENTS.display_status(waiting, 4000)[0], "idle")

  def test_recent_tokens_count_uncached_use_in_the_window(self):
    events = [{"observed_at": 100, "tokens": 900, "cached": 800},
              {"observed_at": 1000, "tokens": 500, "cached": 100},
              {"observed_at": 1100, "tokens": 300}]
    self.assertEqual(AGENTS.recent_tokens(events, 1200), 700)

  def test_table_shows_state_now_and_rate_busiest_first(self):
    quiet = dict(self.work_row("a", "Quiet", "Quiet"), recent_tokens=0,
                 state="idle", turn_age=None, long_turn=False, now="—")
    busy = dict(self.work_row("b", "Busy", "Busy"), recent_tokens=5_000_000,
                state="working", turn_age=720, long_turn=False,
                now="2/5 Run tests")
    stalled = dict(self.work_row("c", "Stuck", "Stuck"), recent_tokens=0,
                   state="stalled", turn_age=1800, long_turn=True,
                   now="Bash: make")
    agents = AGENTS.order_by_activity([quiet, stalled, busy])
    self.assertEqual([a["id"] for a in agents], ["b", "c", "a"])
    with patch.object(AGENTS, "display_width", return_value=200):
      plain = AGENTS.render(agents, {"sessions": {}}, AGENT_QUOTA, 2000)
      styled = AGENTS.render(agents, {"sessions": {}}, AGENT_QUOTA, 2000,
                             color=True)
    for header in ("State", "Now", "15m"):
      self.assertIn(header, plain)
    self.assertIn("working 12m", plain)
    self.assertIn("stalled 30m (long)", plain)
    self.assertIn("2/5 Run tests", plain)
    self.assertIn("5.0M", plain)
    self.assertIn("\x1b[1;31mstalled 30m (long)", styled)
    self.assertIn("\x1b[32mworking 12m", styled)
    self.assertEqual(re.sub(r"\x1b\[[0-9;]*m", "", styled), plain)

  def test_bounded_text_omits_images_and_injected_content(self):
    text = AGENTS.user_text(
      [
        {"type": "text", "text": "Inspect this screenshot"},
        {"type": "image", "source": {"data": "SECRET_IMAGE_BYTES"}},
        {"type": "image_url", "image_url": "https://private/image"},
      ]
    )
    self.assertIn("[image attached]", text)
    self.assertNotIn("SECRET", text)
    self.assertNotIn("https:", text)
    self.assertEqual(AGENTS.user_text("Base directory for this skill: x"), "")
    self.assertEqual(
      AGENTS.user_text("Another Claude session sent a message:x"), ""
    )
    self.assertEqual(AGENTS.user_text("# AGENTS.md instructions for x"), "")
    bounded = AGENTS.bound_messages(["x" * 3000] * 10)
    self.assertLessEqual(len(bounded), 6)
    self.assertLessEqual(sum(map(len, bounded)), 4000)
    self.assertTrue(all(len(m) <= 1000 for m in bounded))
    self.assertIn("[truncated]", bounded[-1])
    newest = "New work " + "y" * 980
    bounded = AGENTS.bound_messages(["x" * 2000] * 5 + [newest])
    self.assertEqual(bounded[-1], newest)
    self.assertLessEqual(sum(map(len, bounded)), 4000)

  def test_goal_context_extracts_only_objective(self):
    context = ('<codex_internal_context source="goal">\n'
               'Continue working toward the active thread goal.\n'
               '<objective>\nFix the parser\n</objective>\n'
               'Budget: 10000\nOther internal instructions\n'
               '</codex_internal_context>')
    self.assertEqual(AGENTS.user_text(context), "Fix the parser")
    for text in (
      '<codex_internal_context source="goal">No objective</codex_internal_context>',
      '<codex_internal_context source="goal"><objective>Unclosed',
      '<codex_internal_context source="goal"><objective> </objective>',
      '<codex_internal_context source="goal"><objective>Fix</objective>',
      '<codex_internal_context source="other"><objective>Ignore</objective>',
    ):
      with self.subTest(text=text):
        self.assertEqual(AGENTS.user_text(text), "")
    # Ordinary owner text containing similar markup is still owner text.
    self.assertEqual(AGENTS.user_text('Explain <objective> tags'),
                     'Explain <objective> tags')
    self.assertEqual(AGENTS.user_text("  " + context.replace('"', "'") + "\n"),
                     "Fix the parser")

  def test_goal_context_fallback_and_summary_input(self):
    rows = self.codex_rows(None)
    rows.append({
      "type": "response_item", "timestamp": "2026-08-19T21:05:00Z",
      "payload": {"type": "message", "role": "user", "content": [
        {"type": "input_text", "text":
         '<codex_internal_context source="goal">Continue working.\n'
         '<objective>Fix parser labels</objective>\nBudget: 10000\n'
         '</codex_internal_context>'}]}})
    agent = AGENTS.parse_session(self.transcript(rows), "codex")
    self.assertEqual(agent["messages"][-1], "Fix parser labels")
    view = AGENTS.view_agents(
      {"sessions": {"s": {"agent": agent}}, "summaries": {}},
      SimpleNamespace(agent_days=10 ** 6, agent_limit=10, provider="all",
                      cached=True), 1_790_000_000)
    self.assertEqual(view[0]["work"], "Fix parser labels")
    prompt = AGENTS.summary_prompt(agent, {})
    self.assertIn("Fix parser labels", prompt)
    self.assertNotIn("Continue working", prompt)
    self.assertNotIn("Budget", prompt)

  def test_claude_deduplicates_and_normalizes_cache_tokens(self):
    def message(out):
      return {
        "type": "assistant",
        "sessionId": "parent",
        "agentId": "child",
        "timestamp": "2026-08-19T21:30:00Z",
        "effort": "high",
        "message": {
          "id": "m1",
          "model": "sonnet",
          "usage": {
            "input_tokens": 10,
            "cache_read_input_tokens": 100,
            "cache_creation_input_tokens": 20,
            "output_tokens": out,
          },
        },
      }

    path = self.transcript(
      [
        {
          "type": "user",
          "sessionId": "parent",
          "agentId": "child",
          "cwd": "/src/git/parser",
          "message": {"content": "Review the parser"},
        },
        message(5),
        message(7),
        message(7),
        dict(message(3), effort="medium", cwd="/scratch/work",
             message={**message(3)["message"], "id": "m2",
                      "model": "opus"}),
      ],
      "parent/subagents/agent-child.jsonl",
    )
    agent = AGENTS.parse_session(path, "claude")
    self.assertEqual(agent["id"], "child")
    self.assertEqual(agent["parent_id"], "parent")
    # The starting directory, not where the shell later moved.
    self.assertEqual(agent["cwd"], "/src/git/parser")
    self.assertEqual(agent["tokens"]["total"], 137 + 133)
    self.assertEqual(agent["tokens"]["input"], 260)
    self.assertEqual(agent["tokens"]["cached"], 200)
    # The table shows the latest model and effort; lists keep the history.
    self.assertEqual((agent["model"], agent["effort"]), ("opus", "medium"))
    self.assertEqual(agent["models"], ["opus", "sonnet"])
    self.assertEqual(agent["efforts"], ["high", "medium"])

  def test_codex_fork_baseline_and_repeated_counters(self):
    def tokens(stamp, total):
      return {
        "type": "event_msg",
        "timestamp": stamp,
        "payload": {
          "type": "token_count",
          "info": {
            "total_token_usage": {
              "input_tokens": total,
              "cached_input_tokens": total // 2,
              "output_tokens": 0,
              "total_tokens": total,
            }
          },
        },
      }

    path = self.transcript(
      [
        {
          "type": "session_meta",
          "payload": {
            "id": "child",
            "timestamp": "2026-08-19T21:00:00Z",
            "forked_from_id": "parent",
            "source": {
              "subagent": {
                "thread_spawn": {
                  "parent_thread_id": "parent",
                  "agent_path": "/root/review",
                }
              }
            },
          },
        },
        tokens("2026-08-19T20:59:00Z", 100),
        {
          "type": "turn_context",
          "timestamp": "2026-08-19T21:01:00Z",
          "payload": {"model": "luna", "effort": "low"},
        },
        tokens("2026-08-19T21:01:00Z", 150),
        tokens("2026-08-19T21:01:00Z", 150),
        tokens("2026-08-19T21:02:00Z", 180),
      ]
    )
    agent = AGENTS.parse_session(path, "codex")
    self.assertEqual(agent["tokens"]["total"], 80)
    self.assertEqual(agent["parent_id"], "parent")
    self.assertEqual(agent["label"], "/root/review")

  def test_summary_cache_invalidation_cooldown_and_inactivity(self):
    agent = {"messages": ["Fix layout"], "last_seen": 1000}
    old = {
      "summary": "Fix layout",
      "input_hash": AGENTS.input_hash(agent),
      "attempted_at": 950,
      "updated_at": 950,
    }
    self.assertFalse(AGENTS.summary_due(agent, old, 1100))
    agent["messages"].append("Add tokens")
    self.assertFalse(AGENTS.summary_due(agent, old, 1100))
    self.assertTrue(AGENTS.summary_due(agent, old, 1300))
    self.assertTrue(AGENTS.summary_due(agent, old, 5000))
    self.assertTrue(AGENTS.summary_due(agent, {}, 5000))

  def test_provider_selection_uses_tightest_bucket_and_respects_optout(self):
    def service(remaining):
      return {
        "data_status": "complete",
        "refresh": {"status": "succeeded"},
        "limits": [
          {
            "bucket": {"scope_kind": "account"},
            "last_observation": {
              "remaining_percent": value,
              "freshness": "fresh",
              "period_relation": "current",
            },
            "pace": {"state": "on_pace"},
            "burn": {},
          }
          for value in remaining
        ],
      }

    document = {
      "services": {"codex": service([90, 20]), "claude_code": service([60, 40])}
    }
    agent = {"provider": "codex"}
    providers, _ = AGENTS.summary_providers(agent, document)
    self.assertEqual(providers, ["claude", "codex"])
    providers, _ = AGENTS.summary_providers(
      agent, document, cross_provider=False
    )
    self.assertEqual(providers, ["codex"])
    document["services"]["codex"] = service([90, 2.9])
    providers, _ = AGENTS.summary_providers(agent, document)
    self.assertEqual(providers, ["claude"])
    document["services"]["codex"] = service([3])
    providers, _ = AGENTS.summary_providers(agent, document)
    self.assertEqual(providers, ["claude", "codex"])
    document["services"]["codex"] = service([2])
    document["services"]["claude_code"] = service([0])
    providers, reason = AGENTS.summary_providers(agent, document)
    self.assertEqual(providers, [])
    self.assertIn("codex", reason)
    self.assertIn("claude", reason)

  def test_cross_provider_call_keeps_origin_and_records_summary_provider(self):
    agent = {
      "key": "codex:x",
      "id": "x",
      "provider": "codex",
      "messages": ["Fix layout"],
      "last_seen": 1000,
    }
    with (
      patch.object(
        AGENTS, "summary_providers", return_value=(["claude"], None)
      ),
      patch.object(AGENTS, "provider_block", return_value=None),
      patch.object(AGENTS, "subscription_block", return_value=None),
      patch.object(
        AGENTS,
        "summarize",
        return_value={"summary": "Fix layout", "brief": "Fix HUD"},
      ) as call,
    ):
      cache = AGENTS.refresh_summaries([agent], {}, {}, {}, now=1000)
    sent = call.call_args.args[0]
    self.assertEqual(sent["provider"], "codex")
    self.assertEqual(sent["summary_provider"], "claude")
    self.assertEqual(cache["codex:x"]["provider"], "claude")
    self.assertEqual(cache["codex:x"]["brief"], "Fix HUD")
    self.assertNotIn("summary_provider", agent)

  def test_local_claude_tokens_deduplicate_copied_requests_and_age(self):
    now = 2_000_000
    event = {"id": "shared", "observed_at": now - 10, "tokens": 16800}
    cache = {
      "observed_at": now,
      "sessions": {
        "first": {"agent": {"provider": "claude", "token_events": [event]}},
        "copy": {
          "agent": {
            "provider": "claude",
            "token_events": [
              event,
              {"id": "old", "observed_at": now - 8 * 86400, "tokens": 99999},
            ],
          }
        },
      },
    }
    usage = AGENTS.local_claude_usage(cache, now)
    self.assertEqual(usage["average_tokens"]["hour"], 100)
    self.assertEqual(usage["average_tokens"]["day"], 2400)
    self.assertEqual(usage["average_tokens"]["week"], 16800)
    self.assertEqual(usage["scope"], "observed_local_sessions")
    self.assertEqual(
      AGENTS.local_claude_usage(cache, now + 121)["freshness"], "stale"
    )

  def test_quota_gate_checks_every_applicable_bucket(self):
    service = {
      "data_status": "complete",
      "refresh": {"status": "succeeded"},
      "limits": [
        {
          "bucket": {"scope_kind": "account"},
          "last_observation": {
            "freshness": "fresh",
            "period_relation": "current",
            "remaining_percent": 50,
          },
          "pace": {"state": "surplus"},
          "burn": {},
        }
      ],
    }
    self.assertIsNone(AGENTS.quota_block(service))
    # Pace and burn never defer a provider that has 3% left.
    limit = service["limits"][0]
    limit["pace"]["state"] = "behind"
    limit["burn"]["exhausts_before_reset"] = True
    limit["last_observation"]["remaining_percent"] = 3
    self.assertIsNone(AGENTS.quota_block(service))
    limit["last_observation"]["remaining_percent"] = 2.5
    self.assertEqual(AGENTS.quota_block(service), "under 3% left")
    limit["last_observation"]["remaining_percent"] = 50
    service["limits"][0]["last_observation"]["freshness"] = "stale"
    self.assertIsNotNone(AGENTS.quota_block(service))
    self.assertIsNotNone(AGENTS.quota_block({}))
    limit["last_observation"]["freshness"] = "fresh"
    fable = copy.deepcopy(limit)
    fable["bucket"] = {"scope_kind": "model"}
    limit["last_observation"]["remaining_percent"] = 0
    service["limits"].append(fable)
    self.assertEqual(AGENTS.quota_block(service), "under 3% left")

  def test_batches_match_ids_and_reject_malformed_results(self):
    jobs = [
      (
        {
          "key": f"claude:{n}",
          "provider": "claude",
          "summary_provider": "claude",
          "messages": [f"Task {n}"],
        },
        {},
      )
      for n in range(3)
    ]
    rows = [
      {"id": str(n), "summary": f"Label {n}", "brief": f"L{n}"}
      for n in [2, 0, 1]
    ]
    with patch.object(
      AGENTS, "summary_response", return_value={"summaries": rows}
    ):
      result = AGENTS.summarize_batch(jobs, {})
    self.assertEqual(result["claude:0"], {"summary": "Label 0", "brief": "L0"})
    # A missing or overlong brief degrades the row; it never fails it.
    partial = [
      {"id": "0", "summary": "Label 0"},
      {"id": "1", "summary": "Label 1", "brief": "b" * 40},
      {"id": "2", "summary": "Label 2", "brief": 7},
    ]
    with patch.object(
      AGENTS, "summary_response", return_value={"summaries": partial}
    ):
      result = AGENTS.summarize_batch(jobs, {})
    for key in ("claude:0", "claude:1", "claude:2"):
      self.assertEqual(result[key]["brief"], "")
    # A truncated brief is discarded rather than shown in place of a summary
    # the renderer could have truncated further along.
    self.assertEqual(AGENTS.usable_brief("b" * 40), "")
    self.assertEqual(
      AGENTS.usable_brief(" Fix  HUD\nlayout "), "Fix HUD layout"
    )
    self.assertEqual(AGENTS.usable_brief(None), "")
    # Briefs cached before this check was added are already truncated to
    # exactly the limit, so length alone cannot detect them.
    truncated = AGENTS.clean("Investigate model import paths", 28)
    self.assertEqual(len(truncated), AGENTS.BRIEF_LIMIT)
    self.assertEqual(AGENTS.usable_brief(truncated), "")
    for bad in [
      rows[:-1],
      rows + [rows[0]],
      [{"id": "other", "summary": "wrong"}],
      [{"id": str(n), "summary": ""} for n in range(3)],
    ]:
      with patch.object(
        AGENTS, "summary_response", return_value={"summaries": bad}
      ):
        with self.assertRaises(ValueError):
          AGENTS.summarize_batch(jobs, {})

  def test_batches_keep_provider_and_per_thread_input_bounds(self):
    jobs = [
      (
        {
          "key": str(n),
          "provider": "codex",
          "summary_provider": "claude" if n % 2 else "codex",
          "messages": ["🙂" * 1000] * 5 + [f"Newest {n}"],
        },
        {},
      )
      for n in range(8)
    ]
    batches = AGENTS.summary_batches(jobs)
    self.assertEqual(sum(map(len, batches)), 8)
    for batch in batches:
      self.assertLessEqual(len(batch), 3)
      self.assertEqual(
        len({agent["summary_provider"] for agent, _ in batch}), 1
      )
      prompt = AGENTS.batch_prompt(batch)
      self.assertLessEqual(len(prompt.encode()), 36000)
      for agent, _ in batch:
        self.assertIn("Newest " + agent["key"], prompt)

  def test_parallel_summary_calls_are_capped_and_cached(self):
    agents = [
      {
        "key": f"claude:{n}",
        "provider": "claude",
        "id": str(n),
        "messages": ["Review parser"],
        "last_seen": 1000,
      }
      for n in range(6)
    ]
    running = 0
    peak = 0
    lock = threading.Lock()

    def summarize(jobs, binaries):
      nonlocal running, peak
      with lock:
        running += 1
        peak = max(peak, running)
      time.sleep(0.03)
      with lock:
        running -= 1
      self.assertLessEqual(len(jobs), 3)
      return {
        agent["key"]: {"summary": "Review parser", "brief": "Review"}
        for agent, _ in jobs
      }

    with (
      patch.object(AGENTS, "summarize_batch", side_effect=summarize),
      patch.object(AGENTS, "subscription_block", return_value=None),
      patch.object(AGENTS, "provider_block", return_value=None),
    ):
      cache = AGENTS.refresh_summaries(agents, {}, {}, {}, now=1000)
      self.assertEqual(peak, 2)
      self.assertEqual(len(cache), 6)
      again = AGENTS.refresh_summaries(agents, cache, {}, {}, now=1301)
      self.assertEqual(cache, again)

  def test_failed_batch_retains_each_old_label_and_cooldown(self):
    agents = [
      {
        "key": f"claude:{n}",
        "provider": "claude",
        "messages": ["New task"],
        "last_seen": 1000,
      }
      for n in range(3)
    ]
    old = {
      a["key"]: {"summary": "Old " + a["key"], "input_hash": "old"}
      for a in agents
    }
    with (
      patch.object(AGENTS, "subscription_block", return_value=None),
      patch.object(AGENTS, "provider_block", return_value=None),
      patch.object(AGENTS, "summary_response", return_value={"summaries": []}),
    ):
      cache = AGENTS.refresh_summaries(agents, old, {}, {}, now=1000)
    for agent in agents:
      entry = cache[agent["key"]]
      self.assertEqual(entry["summary"], old[agent["key"]]["summary"])
      self.assertEqual(entry["input_hash"], "old")
      self.assertEqual(entry["attempted_at"], 1000)
      self.assertIn("missing entries", entry["error"])
      self.assertFalse(AGENTS.summary_due(agent, entry, 1100))

  def test_summary_failure_retains_old_label_and_backs_off(self):
    agent = {
      "key": "codex:x",
      "provider": "codex",
      "id": "x",
      "messages": ["New work"],
      "last_seen": 1000,
    }
    old = {"summary": "Old work", "input_hash": "old", "attempted_at": 0}
    with (
      patch.object(AGENTS, "summarize", side_effect=ValueError("bad JSON")),
      patch.object(AGENTS, "subscription_block", return_value=None),
      patch.object(AGENTS, "provider_block", return_value=None),
    ):
      cache = AGENTS.refresh_summaries(
        [agent], {"codex:x": old}, {}, {}, now=1000
      )
    self.assertEqual(cache["codex:x"]["summary"], "Old work")
    self.assertEqual(cache["codex:x"]["attempted_at"], 1000)
    self.assertIn("bad JSON", cache["codex:x"]["error"])

  def test_collection_caches_unchanged_files_and_prunes_deleted_sessions(self):
    path = self.transcript(
      [
        {"type": "session_meta", "payload": {"id": "one"}},
        {
          "type": "response_item",
          "timestamp": "2026-08-19T21:30:00Z",
          "payload": {
            "role": "user",
            "content": [{"type": "input_text", "text": "Fix parser"}],
          },
        },
      ]
    )
    now = time.time()
    cache = {"version": 1, "sessions": {}, "summaries": {}}
    cache = AGENTS.collect(cache, self.root, self.root / "missing", now, 1)
    self.assertEqual(len(cache["sessions"]), 1)
    cache["summaries"]["codex:one"] = {"summary": "Fix parser"}
    with patch.object(AGENTS, "parse_session") as parse:
      again = AGENTS.collect(cache, self.root, self.root / "missing", now, 1)
      parse.assert_not_called()
    self.assertEqual(again["summaries"], cache["summaries"])
    # Upgrade legacy per-session parses without dropping retained summaries.
    del cache["sessions"][str(path)]["agent"]["records_ids"]
    with patch.object(AGENTS, "parse_session", wraps=AGENTS.parse_session) as parse:
      upgraded = AGENTS.collect(cache, self.root, self.root / "missing", now, 1)
      parse.assert_called_once_with(path, "codex")
    self.assertIn("records_ids", upgraded["sessions"][str(path)]["agent"])
    self.assertEqual(upgraded["summaries"], cache["summaries"])
    path.unlink()
    again = AGENTS.collect(cache, self.root, self.root / "missing", now, 1)
    self.assertEqual(again["sessions"], {})
    self.assertEqual(again["summaries"], {})

  def test_claude_copied_parent_messages_are_not_child_usage(self):
    def row(session, ident):
      return {
        "type": "assistant",
        "sessionId": session,
        "message": {
          "id": ident,
          "model": "sonnet",
          "usage": {"input_tokens": 100, "output_tokens": 10},
        },
      }

    path = self.transcript(
      [row("parent", "old"), row("child", "new")], "child.jsonl"
    )
    self.assertEqual(
      AGENTS.parse_session(path, "claude")["tokens"]["total"], 110
    )

  def test_summarizer_cli_contract_and_length_enforcement(self):
    fake = self.root / "fake-cli"
    fake.write_text(
      "#!/usr/bin/env python3\n"
      "import json,sys\n"
      "prompt=sys.stdin.read()\n"
      "assert len(prompt.encode()) <= 12000\n"
      'answer=json.dumps({"summary":"A"*200})\n'
      'if "exec" in sys.argv:\n'
      '  assert "--ephemeral" in sys.argv\n'
      '  print(json.dumps({"type":"item.completed","item":'
      '{"type":"agent_message","text":answer}}))\n'
      "else:\n"
      '  assert "--no-session-persistence" in sys.argv\n'
      '  assert sys.argv[sys.argv.index("--tools")+1]==""\n'
      '  print(json.dumps({"result":answer}))\n'
    )
    fake.chmod(0o755)
    for provider in ("codex", "claude"):
      label = AGENTS.summarize(
        {"provider": provider, "messages": ["task"]}, {}, {provider: str(fake)}
      )
      self.assertEqual(len(label["summary"]), AGENTS.LABEL_BOUND)
      self.assertEqual(label["brief"], "")

  def test_worker_refreshes_missing_quota_before_summary_calls(self):
    path = self.root / "agents.json"
    document = {"services": {}}
    module = AGENT_QUOTA.agents_module()
    with (
      patch.object(AGENT_QUOTA, "agents_module", return_value=module),
      patch.object(
        AGENT_QUOTA, "build_document", return_value=document
      ) as build,
      patch.object(module, "refresh_summaries") as summarize,
    ):
      AGENT_QUOTA.main(
        [
          "--agents",
          "--agent-refresh",
          "--agent-cache-file",
          str(path),
          "--cache-file",
          str(self.root / "quota.json"),
        ]
      )
    build.assert_called_once()
    self.assertEqual(summarize.call_args.args[2], document)

  def test_empty_claude_window_does_not_block_weekly_headroom(self):
    now = AGENT_QUOTA.utc_now()
    item = AGENT_QUOTA.claude_candidate(
      {"kind": "session", "percent": 0, "resets_at": None, "is_active": False},
      now,
      now,
      [],
    )
    service = {"data_status": "complete", "limits": [item]}
    self.assertIsNone(AGENTS.quota_block(service))
    item["last_observation"]["freshness"] = "stale"
    self.assertIsNotNone(AGENTS.quota_block(service))
    item["last_observation"]["freshness"] = "fresh"
    item["last_observation"].pop("window_not_started", None)
    self.assertIsNotNone(AGENTS.quota_block(service))

  def test_cached_agents_never_scan_or_start_a_process(self):
    path = self.root / "agents.json"
    AGENT_QUOTA.write_cache(
      path, {"version": 1, "sessions": {}, "summaries": {}}
    )
    with patch("subprocess.Popen") as spawn, patch("builtins.print"):
      AGENT_QUOTA.main(
        [
          "--agents",
          "--cached",
          "--agent-cache-file",
          str(path),
          "--cache-file",
          str(self.root / "quota.json"),
        ]
      )
      spawn.assert_not_called()
    self.assertEqual(path.stat().st_mode & 0o777, 0o600)

  def test_subscription_authentication_rejects_api_key_environment(self):
    with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "placeholder"}):
      self.assertIn("API-key", AGENTS.subscription_block("claude", {}))

  def test_quota_deferral_never_calls_cli(self):
    agent = {
      "key": "codex:x",
      "provider": "codex",
      "messages": ["Fix test"],
      "last_seen": 1000,
    }
    with (
      patch.object(AGENTS, "summarize") as summarize,
      patch.object(AGENTS, "subscription_block") as auth,
    ):
      cache = AGENTS.refresh_summaries([agent], {}, {}, {}, now=1000)
      summarize.assert_not_called()
      auth.assert_not_called()
    self.assertIn("quota", cache["codex:x"]["skip_reason"])

  def test_summary_timeout_kills_process_group(self):
    with (
      patch.object(AGENTS.subprocess, "Popen") as spawn,
      patch.object(AGENTS.os, "killpg") as kill,
    ):
      proc = spawn.return_value
      proc.pid = 123
      proc.communicate.side_effect = [
        subprocess.TimeoutExpired("cli", 45),
        ("", ""),
      ]
      with self.assertRaisesRegex(RuntimeError, "timed out"):
        AGENTS.summarize({"provider": "claude", "messages": ["Task"]}, {}, {})
      kill.assert_called_once_with(123, AGENTS.signal.SIGKILL)

  def test_claude_activity_noise_and_the_step_just_finished(self):
    rows = self.claude_rows(finished=True)
    rows[4]["message"]["content"] = [{"type": "tool_result",
                                      "tool_use_id": "t2", "content": "ok"}]
    # A new reply opens with a sentence, then works on without a tool.
    rows[5]["message"].update(stop_reason="tool_use", content=[
      {"type": "text", "text": "**Tests pass.** Next I open the PR."}])
    agent = AGENTS.parse_session(self.transcript(rows), "claude")
    # TodoWrite is progress, not activity; Bash uses its description.
    self.assertEqual(agent["activity"],
                     ["Bash: Run tests", "said: Tests pass."])
    noise = rows + [{"type": "user", "timestamp": "2026-08-19T21:07:00Z",
                     "message": {"content": text}}
                    for text in ("[Request interrupted by user for tool use]",
                                 "Restarted", "ok!", "Then deploy")]
    noisy = AGENTS.parse_session(self.transcript(noise, "n.jsonl"), "claude")
    self.assertEqual(noisy["messages"], ["Run the tests", "Then deploy"])
    # A new turn starts clean.
    self.assertIsNone(noisy["status"]["last_done"])
    status = agent["status"]
    self.assertIsNone(status["action"])
    self.assertEqual(status["last_done"], "Bash: pytest -q")
    status["progress"] = None  # a task list would take precedence
    view = AGENTS.view_agents(
      {"sessions": {"s": {"agent": agent}}, "summaries": {}},
      SimpleNamespace(agent_days=10 ** 6, agent_limit=10, provider="all"),
      1_790_000_000)
    self.assertEqual(view[0]["now"], "after Bash: pytest -q")

  def test_codex_activity_from_steps_replies_and_reasoning(self):
    rows = self.codex_rows(None)
    rows[1:1] = [
      {"type": "response_item", "timestamp": "2026-08-19T21:00:10Z",
       "payload": {"type": "reasoning", "summary": [
         {"type": "summary_text", "text": "**Checking the build**\n\nMore"}]}},
      {"type": "response_item", "timestamp": "2026-08-19T21:00:20Z",
       "payload": {"type": "message", "role": "assistant", "content": [
         {"type": "output_text", "text": "I'll build first. Then test."}]}},
    ]
    agent = AGENTS.parse_session(self.transcript(rows), "codex")
    self.assertEqual(agent["activity"], [
      "thinking: Checking the build", "said: I'll build first.",
      "exec: cargo test"])

  def test_activity_is_scrubbed_and_bounded(self):
    blob = "A" * 5000
    step = AGENTS.activity_step("Bash", {"description": "Send " + blob})
    self.assertEqual(step, "Bash: Send [data]")
    said = AGENTS.first_sentence(
      "See <image src='x.png'>ok</image> and data:image/png;base64,xyz here."
      " More")
    self.assertEqual(said, "See [image attached] and [image attached] here.")
    long = AGENTS.first_sentence("word " * 10_000)
    self.assertLessEqual(len(long), AGENTS.ACTIVITY_CHARS)
    agent = {"messages": ["Fix display"],
             "activity": ["said: " + "🙂" * 290] * 12 + ["said: newest"],
             "status": {"progress": {"done": 1, "total": 3,
                                     "current": "Run tests"}}}
    prompt = AGENTS.summary_prompt(agent, {})
    self.assertLessEqual(len(prompt.encode()), 12000)
    body = json.loads(prompt[prompt.index("\n") + 1:])
    self.assertEqual(body["agent_activity"][-1], "said: newest")
    self.assertLess(len(body["agent_activity"]), 12)
    self.assertEqual(body["owner_messages"], ["Fix display"])
    self.assertEqual(body["task_list"]["current"], "Run tests")

  def test_records_ids_match_agent_id(self):
    sys.path.insert(0, str(Path(AGENTS.__file__).parent))
    import agent_records_core as core
    for provider, variable in (("claude", "CLAUDE_CODE_SESSION_ID"),
                               ("codex", "CODEX_THREAD_ID")):
      session = "0199aaaa-bbbb-7ccc-8ddd-eeeeffff0000"
      with patch.dict(os.environ, {variable: session}, clear=False):
        for other in ("CLAUDE_CODE_SESSION_ID", "CODEX_THREAD_ID",
                      "CODEX_SESSION_ID"):
          if other != variable:
            os.environ.pop(other, None)
        expected = core.session_id()[0]
      self.assertEqual(AGENTS.records_id(
        {"provider": provider, "id": session, "parent_id": None}), expected)
    # A Claude subagent shares its parent's session; it has no ID of its own.
    self.assertIsNone(AGENTS.records_id(
      {"provider": "claude", "id": "a1", "parent_id": "p"}))

  def test_claimed_tasks_reach_work_json_and_summaries(self):
    owner = AGENTS.records_id({"provider": "claude", "id": "s1",
                               "parent_id": None})
    listing = {"tasks": [
      {"id": "T-0007", "title": "Fix the HUD", "status": "in-progress",
       "owner": owner, "helpers": ""},
      {"id": "T-0008", "title": "Help out", "status": "in-review",
       "owner": "someone", "helpers": f"x, {owner}"},
      {"id": "T-0009", "title": "Unowned", "status": "open",
       "owner": "none", "helpers": ""}]}
    result = subprocess.CompletedProcess([], 0, json.dumps(listing), "")
    with patch.object(AGENTS.shutil, "which", return_value="/x"), \
         patch.object(AGENTS.subprocess, "run", return_value=result):
      claims = AGENTS.claimed_tasks()
    self.assertEqual([t["id"] for t in claims[owner]], ["T-0007", "T-0008"])
    self.assertEqual([t["role"] for t in claims[owner]], ["owner", "helper"])
    with patch.object(AGENTS.shutil, "which", return_value=None):
      self.assertIsNone(AGENTS.claimed_tasks())
    agent = dict(self.work_row("s1", "Fix HUD layout", "Fix HUD"),
                 state="working", turn_age=60, long_turn=False, now="—",
                 recent_tokens=10, tasks=claims[owner])
    with patch.object(AGENTS, "display_width", return_value=200):
      output = AGENTS.render([agent], {"sessions": {}}, AGENT_QUOTA, 1000)
    self.assertIn("T-0007+1 · Fix HUD layout", output)
    prompt = AGENTS.summary_prompt({**agent, "messages": ["x"]}, {})
    body = json.loads(prompt[prompt.index("\n") + 1:])
    self.assertEqual(body["claimed_tasks"][0],
                     {"id": "T-0007", "title": "Fix the HUD",
                      "status": "in-progress"})

  def test_activity_refreshes_labels_at_most_every_fifteen_minutes(self):
    agent = {"messages": ["Fix parser"], "activity": ["Bash: Build"],
             "last_seen": 0}
    fresh = {"input_hash": AGENTS.input_hash(agent),
             "activity_hash": AGENTS.activity_hash(agent), "updated_at": 0}
    self.assertFalse(AGENTS.summary_due(agent, fresh, 5000))
    agent["activity"] = ["Bash: Build", "Bash: Test"]
    refresh = AGENTS.ACTIVITY_REFRESH
    self.assertFalse(AGENTS.summary_due(agent, fresh, refresh - 1))
    self.assertTrue(AGENTS.summary_due(agent, fresh, refresh))
    # A new owner message refreshes at once, activity or not.
    agent["messages"].append("Also fix the lexer")
    self.assertTrue(AGENTS.summary_due(agent, fresh, AGENTS.COOLDOWN))

  def test_budget_prompt_preserves_newest_when_utf8_expands(self):
    newest = "Fix display"
    agent = {"messages": ["🙂" * 1000] * 5 + [newest]}
    prompt = AGENTS.summary_prompt(agent, {"summary": "Previous work"})
    self.assertLessEqual(len(prompt.encode()), 12000)
    self.assertIn(newest, prompt)

  def work_row(self, ident, work, brief):
    return {
      "key": "claude:" + ident,
      "provider": "claude",
      "id": ident,
      "parent_id": None,
      "models": ["opus-5"],
      "model": "opus-5",
      "effort": "high",
      "tokens": {"total": 1000, "input": 900, "cached": 800},
      "work": work,
      "work_brief": brief,
      "work_source": "summary",
      "last_seen": 1000,
      "summary_status": None,
    }

  def render_at(self, agents, columns, cache=None, now=1000):
    with patch.object(AGENTS, "display_width", return_value=columns):
      output = AGENTS.render(
        agents, cache or {"sessions": {}}, AGENT_QUOTA, now
      )
    for line in output.splitlines():
      if columns is not None:
        self.assertLessEqual(len(line), columns, line)
    return output

  def work_line(self, output, ident):
    return next(
      line for line in output.splitlines() if "claude:" + ident in line
    )

  def test_colour_adds_only_escape_codes(self):
    agents = [self.work_row("a", "Fix display", "Fix"),
              dict(self.work_row("b", "raw excerpt", ""),
                   work_source="excerpt")]
    with patch.object(AGENTS, "display_width", return_value=160):
      plain = AGENTS.render(agents, {"sessions": {}}, AGENT_QUOTA, 1000)
      styled = AGENTS.render(agents, {"sessions": {}}, AGENT_QUOTA, 1000,
                             color=True)
    self.assertEqual(re.sub(r"\x1b\[[0-9;]*m", "", styled), plain)
    self.assertIn("\x1b[1mAgent\x1b[0m", styled)
    self.assertIn("\x1b[2;3m~ raw excerpt", styled)

  def test_work_column_spans_available_width_and_repairs_overruns(self):
    fits = "Reconcile unmerged branches for roadmap and delivery docs"
    over = "R" * 75
    agents = [
      self.work_row("a", fits, "Reconcile branches"),
      self.work_row("b", over, "Fix HUD layout"),
      self.work_row("c", over, ""),
    ]
    # Spare width shows every label whole, past the 40-column cap and past
    # the 60-character budget the model is asked for.
    wide = self.render_at(agents, 200)
    self.assertIn(fits, wide)
    self.assertIn(over, wide)
    # Once width runs out, an overlong label degrades to its complete brief
    # rather than to an ellipsis.
    # State, Now and 15m take their share first.
    mid = self.render_at(agents, 135)
    self.assertIn(fits, mid)
    self.assertIn("Fix HUD layout", self.work_line(mid, "b"))
    self.assertNotIn("R", self.work_line(mid, "b"))
    # Without a brief there is nothing to fall back to.
    self.assertIn("\u2026", self.work_line(mid, "c"))
    narrow = self.render_at(agents, 90)
    self.assertIn("Fix HUD layout", narrow)
    self.assertNotIn(fits, narrow)
    # A pipe constrains nothing, so every label is shown whole.
    piped = self.render_at(agents, None)
    self.assertIn(fits, piped)
    self.assertIn(over, piped)
    self.assertNotIn("\u2026", self.work_line(piped, "c"))

  def test_display_width_prefers_columns_then_terminal_then_pipe(self):
    with patch.dict(os.environ, {"COLUMNS": "143"}):
      self.assertEqual(AGENTS.display_width(), 143)
    for value in ("0", "", "wide"):
      with (
        patch.dict(os.environ, {"COLUMNS": value}),
        patch.object(AGENTS.sys.stdout, "isatty", return_value=False),
      ):
        self.assertIsNone(AGENTS.display_width())
    with (
      patch.dict(os.environ, {"COLUMNS": "0"}),
      patch.object(AGENTS.sys.stdout, "isatty", return_value=True),
      patch.object(
        AGENTS.os, "get_terminal_size", return_value=os.terminal_size((97, 24))
      ),
    ):
      self.assertEqual(AGENTS.display_width(), 97)
    with (
      patch.dict(os.environ, {"COLUMNS": "0"}),
      patch.object(AGENTS.sys.stdout, "isatty", side_effect=OSError),
    ):
      self.assertEqual(AGENTS.display_width(), 120)

  def test_prompt_version_refreshes_labels_cached_before_the_brief(self):
    agent = {"messages": ["Fix parser"], "last_seen": 0}
    legacy = AGENTS.hashlib.sha256(
      json.dumps(agent["messages"], ensure_ascii=False).encode()
    ).hexdigest()
    self.assertNotEqual(AGENTS.input_hash(agent), legacy)
    old = {"summary": "Fix parser", "input_hash": legacy}
    self.assertTrue(AGENTS.summary_due(agent, old, 5000))
    current = {"summary": "Fix parser", "input_hash": AGENTS.input_hash(agent)}
    self.assertFalse(AGENTS.summary_due(agent, current, 5000))

  def test_recent_hour_totals_deduplicate_and_reach_the_footer(self):
    now = 2_000_000

    def session(key, events):
      return {
        "agent": {"provider": "claude", "key": key, "token_events": events}
      }

    shared = {"id": "1", "observed_at": now - 600, "tokens": 500}
    cache = {
      "observed_at": now,
      "sessions": {
        "a": session(
          "claude:a",
          [shared, {"id": "2", "observed_at": now - 7200, "tokens": 900}],
        ),
        "b": session(
          "claude:b", [{"id": "3", "observed_at": now - 60, "tokens": 100}]
        ),
        "copy": session("claude:a", [shared]),
      },
    }
    usage = AGENTS.local_claude_usage(cache, now)
    self.assertEqual(usage["recent_hour"], {"tokens": 600, "agents": 2})
    self.assertEqual(usage["average_tokens"]["week"], 1500)
    footer = self.render_at([], 120, cache, now)
    self.assertIn("Last hour: 600 across 2 agents", footer)
    idle = dict(cache, sessions={"a": session("claude:a", [
      {"id": "2", "observed_at": now - 7200, "tokens": 900}])})
    self.assertIn(
      "Last hour: no observed activity", self.render_at([], 120, idle, now)
    )


if __name__ == "__main__":
  unittest.main()
