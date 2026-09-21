from __future__ import annotations

import importlib.util
import json
import os
import subprocess
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
          "message": {"content": "Review the parser"},
        },
        message(5),
        message(7),
        message(7),
      ],
      "parent/subagents/agent-child.jsonl",
    )
    agent = AGENTS.parse_session(path, "claude")
    self.assertEqual(agent["id"], "child")
    self.assertEqual(agent["parent_id"], "parent")
    self.assertEqual(agent["tokens"]["total"], 137)
    self.assertEqual(agent["tokens"]["input"], 130)
    self.assertEqual(agent["tokens"]["cached"], 100)
    self.assertEqual(agent["effort"], "high")
    self.assertEqual(agent["models"], ["sonnet"])

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
    document["services"]["codex"] = service([5])
    providers, _ = AGENTS.summary_providers(agent, document)
    self.assertEqual(providers, ["claude"])
    document["services"]["codex"] = service([5.1])
    providers, _ = AGENTS.summary_providers(agent, document)
    self.assertEqual(providers, ["claude", "codex"])
    document["services"]["codex"] = service([5])
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
    service["limits"][0]["pace"]["state"] = "early"
    self.assertIsNone(AGENTS.quota_block(service))
    service["limits"][0]["pace"]["state"] = "unknown"
    self.assertIsNotNone(AGENTS.quota_block(service))
    service["limits"][0]["pace"]["state"] = "on_pace"
    service["limits"][0]["burn"]["exhausts_before_reset"] = True
    self.assertIsNotNone(AGENTS.quota_block(service))
    service["limits"][0]["burn"] = {}
    service["limits"][0]["last_observation"]["freshness"] = "stale"
    self.assertIsNotNone(AGENTS.quota_block(service))
    self.assertIsNotNone(AGENTS.quota_block({}))

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
    mid = self.render_at(agents, 120)
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
