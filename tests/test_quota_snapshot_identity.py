"""Quota-bound account identity and partial usage presentation."""

from __future__ import annotations

import copy
import json
import hashlib
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from tests import test_agent_quota as fixtures
from tests.test_agent_quota_agents import AGENTS

QUOTA = fixtures.AGENT_QUOTA
NOW = fixtures.NOW


class SnapshotIdentityTest(unittest.TestCase):
  def snapshot(self, identity="workspace-a", plan="promax", used=20):
    result = fixtures.AgentQuotaTest().account_snapshot(used=used)
    for key in ("account_before", "account_after"):
      result[key] = copy.deepcopy(result[key])
    for key in ("account_before", "account_after"):
      result[key]["result"]["account"]["planType"] = plan
    if identity is not None:
      result["limits"]["result"]["accountId"] = identity
    return result

  def collect(self, snapshot, previous=None, now=NOW):
    with patch.object(QUOTA, "codex_rpc", return_value=snapshot):
      return QUOTA.build_document("codex", Path("unused"), "claude", 5,
                                  "codex", 5, previous, now)

  def test_provider_identity_is_hashed_and_never_serialized_raw(self):
    doc = self.collect(self.snapshot())
    account = doc["services"]["codex"]["account"]
    expected = hashlib.sha256(b"codex-account-id\0workspace-a").hexdigest()
    self.assertEqual(account["key"], expected)
    self.assertEqual(account["source"], "codex.account/rateLimits/read")
    self.assertNotIn("workspace-a", json.dumps(doc))
    self.assertEqual(doc["services"]["codex"]["data_status"], "complete")

  def test_same_email_plan_different_ids_isolates_and_restores_history(self):
    first = self.collect(self.snapshot())
    second = self.collect(self.snapshot("workspace-b", used=1), first,
                          NOW + timedelta(minutes=20))
    live = second["services"]["codex"]
    self.assertNotEqual(live["account"]["key"],
                        first["services"]["codex"]["account"]["key"])
    self.assertEqual(len(live["limits"][0]["history"]), 1)
    returned = self.collect(self.snapshot(used=30), second,
                            NOW + timedelta(minutes=40))["services"]["codex"]
    self.assertEqual([x["used_percent"] for x in returned["limits"][0]["history"]],
                     [20, 30])

  def test_absent_or_malformed_identity_keeps_unknown_plan_unkeyed(self):
    for identity in (None, "", " ", "bad\nidentity", 3, "a" * 513):
      with self.subTest(identity_type=type(identity).__name__):
        doc = self.collect(self.snapshot(identity))
        self.assertIsNone(doc["services"]["codex"]["account"]["key"])
    explicit_null = self.snapshot(None)
    explicit_null["limits"]["result"]["accountId"] = None
    self.assertIsNone(self.collect(explicit_null)["services"]["codex"][
      "account"]["key"])
    personal = self.collect(self.snapshot(None, plan="pro"))
    self.assertIsNotNone(personal["services"]["codex"]["account"]["key"])
    malformed = self.collect(self.snapshot("bad\nidentity", plan="pro"))
    self.assertIsNone(malformed["services"]["codex"]["account"]["key"])

  def test_identity_upgrade_and_failed_read_do_not_reuse_email_history(self):
    legacy = self.collect(self.snapshot(None, plan="pro"))
    current = self.collect(self.snapshot(plan="pro"), legacy,
                           NOW + timedelta(minutes=20))
    self.assertEqual(len(current["services"]["codex"]["limits"][0]["history"]),
                     1)
    failed = self.snapshot(plan="pro")
    failed["limits"] = {"error": "offline"}
    result = self.collect(failed, current, NOW + timedelta(minutes=40))
    self.assertEqual(result["services"]["codex"]["limits"], [])

  def test_missing_id_after_adoption_keeps_live_quota_without_old_history(self):
    current = self.collect(self.snapshot(plan="pro"))
    downgraded = self.collect(self.snapshot(None, plan="pro"), current,
                              NOW + timedelta(minutes=20))
    service = downgraded["services"]["codex"]
    self.assertIsNone(service["account"]["key"])
    self.assertEqual(service["data_status"], "partial")
    self.assertEqual(len(service["limits"][0]["history"]), 1)
    self.assertIn("codex_identity_downgrade",
                  [warning["code"] for warning in service["warnings"]])

  def test_changed_or_unavailable_profile_never_binds_provider_identity(self):
    snapshot = self.snapshot()
    snapshot["account_after"]["result"]["account"]["email"] = "other@example.test"
    self.assertEqual(self.collect(snapshot)["services"]["codex"]["limits"], [])
    snapshot = self.snapshot()
    snapshot["account_before"] = {"error": "offline"}
    self.assertIsNone(self.collect(snapshot)["services"]["codex"]["account"])

  def test_background_recheck_uses_quota_identity_and_rejects_switch(self):
    snapshot = self.snapshot()
    document = self.collect(snapshot)
    args = SimpleNamespace(timeout=5, codex_bin="codex")
    with patch.object(QUOTA, "codex_rpc", return_value=snapshot) as request:
      self.assertIsNone(AGENTS.check_summary_account("codex", document, args,
                                                    QUOTA))
    self.assertTrue(request.call_args.kwargs["snapshot"])
    self.assertFalse(request.call_args.kwargs["include_usage"])
    with patch.object(QUOTA, "codex_rpc", return_value=self.snapshot("workspace-b")):
      self.assertIn("differs", AGENTS.check_summary_account(
        "codex", document, args, QUOTA))

  def test_routing_changes_are_detected_without_cross_field_id_assumptions(self):
    snapshot = self.snapshot()
    for key in ("account_before", "account_after"):
      snapshot[key]["result"]["workspaceRouting"] = {
        "chatgptAccountId": "routing-identity"}
    account = self.collect(snapshot)["services"]["codex"]["account"]
    self.assertIsNotNone(account["key"])
    snapshot["account_after"]["result"]["workspaceRouting"][
      "chatgptAccountId"] = "different-routing-identity"
    self.assertEqual(self.collect(snapshot)["services"]["codex"]["limits"], [])
    snapshot["account_after"]["result"]["workspaceRouting"] = None
    self.assertIsNone(self.collect(snapshot)["services"]["codex"]["account"]["key"])
    snapshot["account_after"]["result"]["workspaceRouting"] = {
      "chatgptAccountId": "invalid\nidentity"}
    self.assertIsNone(self.collect(snapshot)["services"]["codex"]["account"]["key"])

  def test_identity_only_rpc_does_not_request_optional_usage(self):
    with tempfile.TemporaryDirectory() as directory:
      fake = Path(directory) / "fake-codex"
      fake.write_text(
        "#!/usr/bin/env python3\n"
        "import json,sys\n"
        "seen=[]\n"
        "for line in sys.stdin:\n"
        "  request=json.loads(line); seen.append(request['method'])\n"
        "  if request.get('id',0) >= 2:\n"
        "    print(json.dumps({'id':request['id'],'result':{'seen':seen}}), "
        "flush=True)\n")
      fake.chmod(0o755)
      snapshot = QUOTA.codex_rpc(5, str(fake), snapshot=True,
                                include_usage=False)
    self.assertNotIn("usage", snapshot)
    self.assertEqual(snapshot["account_after"]["result"]["seen"], [
      "initialize", "initialized", "account/read", "account/rateLimits/read",
      "account/read"])

  def test_partial_daily_usage_is_explained_without_zero_fill(self):
    usage = QUOTA.parse_token_usage({"dailyUsageBuckets": [
      {"startDate": "2026-08-19", "tokens": 10},
    ]}, NOW)
    self.assertEqual(usage["status"], "partial")
    self.assertIsNone(usage["average_tokens"])
    self.assertIn("incomplete seven-day usage data", QUOTA.brief_tokens(usage))
