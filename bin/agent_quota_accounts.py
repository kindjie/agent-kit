"""Shared eligible alternate-account snapshots; never mutates saved history."""
from datetime import datetime, timezone
import re


def parse_timestamp(value):
  if not isinstance(value, str):
    return None
  try:
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
  except ValueError:
    return None
  return parsed.astimezone(timezone.utc) if parsed.tzinfo else None


def alternate_accounts(document, service_id, cache_name):
  services = document.get('services')
  services = services if isinstance(services, dict) else {}
  cache = document.get(cache_name)
  if service_id not in services or not isinstance(cache, dict):
    return []
  active_service = services.get(service_id)
  active_service = active_service if isinstance(active_service, dict) else {}
  active = active_service.get("account")
  active_key = active.get("key") if isinstance(active, dict) else None
  provider_checks = {}
  if service_id == "codex":
    candidates = list(cache.items()) + [(active_key, active_service)]
    for candidate_key, candidate in candidates:
      if not isinstance(candidate, dict) or not candidate.get("limits"):
        continue
      identity = candidate.get("account") or {}
      if (not isinstance(identity, dict)
          or identity.get("source") != "codex.account/rateLimits/read"
          or identity.get("key") != candidate_key
          or not isinstance(candidate_key, str)
          or not re.fullmatch(r"[0-9a-f]{64}", candidate_key)):
        continue
      label = str(identity.get("label") or "").strip().casefold()
      checked = parse_timestamp(identity.get("observed_at"))
      if label and checked and (
          label not in provider_checks or checked > provider_checks[label]):
        provider_checks[label] = checked
  result = []
  for key, service in cache.items():
    if not isinstance(service, dict) or key == active_key:
      continue
    account = service.get("account")
    if not isinstance(account, dict) or account.get("key") != key:
      continue
    if not service.get("limits"):
      continue
    # Presentation only: a later quota-bound identity supersedes this
    # login's legacy email snapshot, but never joins distinct quota IDs
    # or transfers burn/credit history between them.
    if account.get("source") == "codex.account/read":
      label = str(account.get("label") or "").strip().casefold()
      checked = parse_timestamp(account.get("observed_at"))
      newer = provider_checks.get(label)
      if checked and newer and newer > checked:
        continue
    result.append(service)
  return result
