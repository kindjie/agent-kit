"""Presentation for agent-quota: text renderers, colour, --timeline, --live.

Not an importable module. bin/agent-quota executes this file in its own
namespace (see the include there), so this code shares that script's
globals exactly as if it were written inline; the split only keeps each
file a reviewable size. Use names from agent-quota freely.
"""


def service_label(limit: dict[str, Any], service_name: str) -> str:
  bucket = limit.get("bucket", {})
  window = limit.get("window", {})
  bucket_name = bucket.get("name", "unknown")
  scope_kind = bucket.get("scope_kind")
  window_label = window.get("label", "unknown")
  if scope_kind == "account":
    return f"{service_name} {window_label}"
  return f"{service_name} {bucket_name} {window_label}"


def format_percent(value: Any) -> str:
  parsed = finite_percentage(value)
  if parsed is None:
    return "UNKNOWN"
  return f"{compact_number(parsed)}%"


def format_duration(seconds: Any) -> str:
  if not isinstance(seconds, (int, float)) or isinstance(seconds, bool):
    return "UNKNOWN"
  total = max(0, int(seconds))
  days, rest = divmod(total, 86400)
  hours, rest = divmod(rest, 3600)
  minutes = rest // 60
  if days:
    return f"{days}d {hours}h"
  if hours:
    return f"{hours}h {minutes}m"
  return f"{minutes}m"


def format_signed_percent(value: Any) -> str:
  if (
    not isinstance(value, (int, float))
    or isinstance(value, bool)
    or not math.isfinite(value)
  ):
    return "UNKNOWN"
  return f"{compact_number(value)}%"


def brief_pace(limit: dict[str, Any]) -> str | None:
  pace = limit.get("pace")
  if not isinstance(pace, dict) or pace.get("reset_in_seconds") is None:
    return None
  reset_in = format_duration(pace.get("reset_in_seconds"))
  share = pace.get("window_share_remaining_percent")
  state = str(pace.get("state", "unknown")).upper()
  if share is None:
    text = f"Resets in {reset_in}; pace UNKNOWN (window length unknown)."
  elif state == "EARLY":
    text = (
      f"Resets in {reset_in} ({format_percent(share)} of window). "
      "Pace: EARLY (too little of the window elapsed to project)."
    )
  else:
    projected = format_signed_percent(pace.get("projected_unused_percent"))
    text = (
      f"Resets in {reset_in} ({format_percent(share)} of window). "
      f"Pace: {state}; projected {projected} unused at reset."
    )
  if pace.get("reset_soon"):
    text += " RESET SOON."
  burn = limit.get("burn")
  if isinstance(burn, dict) and burn.get("rate_percent_per_hour") is not None:
    rate = burn["rate_percent_per_hour"]
    span = format_duration(burn.get("span_seconds"))
    if burn.get("exhausts_before_reset"):
      text += (
        f" Recent burn {rate}%/h over {span}: EXHAUSTS BEFORE RESET "
        f"({burn.get('exhausts_at')})."
      )
    else:
      text += f" Recent burn {rate}%/h over {span}."
  return text


def brief_velocity(limit: dict[str, Any]) -> str | None:
  velocity = limit.get("velocity")
  if not isinstance(velocity, dict):
    return None
  parts: list[str] = []
  known = False
  for label, seconds in VELOCITY_WINDOWS:
    window = velocity.get(label)
    used = window.get("used_percent") if isinstance(window, dict) else None
    if used is None:
      parts.append(f"{label} UNKNOWN")
      continue
    known = True
    text = f"{format_signed_percent(used)} in {label}"
    span = window.get("span_seconds")
    if (
      isinstance(span, (int, float))
      and not isinstance(span, bool)
      and span < seconds - HISTORY_MIN_GAP_SECONDS
    ):
      text += f" (covers {format_duration(span)})"
    parts.append(text)
  if not known:
    return None
  return "Velocity: " + ", ".join(parts) + "."


def brief_binding(service: dict[str, Any], service_name: str) -> str:
  binding = service.get("binding_limit_id")
  for item in service.get("limits", []):
    if isinstance(item, dict) and item.get("limit_id") == binding:
      pace = item.get("pace")
      state = (
        str(pace.get("state", "unknown")).upper()
        if isinstance(pace, dict)
        else "UNKNOWN"
      )
      return (
        f"Binding (period-average): "
        f"{service_label(item, service_name)} ({state})."
      )
  return "Binding (period-average): none projected."


def brief_limit(limit: dict[str, Any], service_name: str) -> list[str]:
  label = service_label(limit, service_name)
  observation = limit.get("last_observation")
  if not isinstance(observation, dict):
    return [f"{label}: current remaining UNKNOWN [NO OBSERVATION]."]

  remaining = format_percent(observation.get("remaining_percent"))
  observed = observation.get("observed_at") or "UNKNOWN"
  reset = observation.get("reset_at") or "UNKNOWN"
  relation = observation.get("period_relation")
  freshness = str(observation.get("freshness", "unknown")).upper()

  if relation == "current":
    lines = [
      f"{label}: {remaining} remaining [CURRENT PERIOD; {freshness}].",
      f"Observed {observed}; resets {reset}.",
    ]
    pace_line = brief_pace(limit)
    if pace_line:
      lines.append(pace_line)
  elif relation == "ended":
    lines = [
      f"{label}: current remaining UNKNOWN [PERIOD ENDED].",
      f"Last observation: {remaining} remaining at {observed}; "
      f"that period ended {reset}.",
    ]
  else:
    lines = [
      f"{label}: current remaining UNKNOWN [PERIOD UNKNOWN].",
      f"Last observed {remaining} remaining at {observed}; whether it "
      "applies to the current period is UNKNOWN.",
    ]
  velocity_line = brief_velocity(limit)
  if velocity_line:
    lines.append(velocity_line)
  return lines


def brief_credits(service: dict[str, Any]) -> list[str]:
  credits = service.get("credits", [])
  if not credits:
    return ["Codex credits: UNKNOWN (not reported)."]
  lines = []
  for credit in credits:
    balance = credit_balance(credit.get("balance"))
    if credit.get("unlimited") is True:
      value = "UNLIMITED"
    elif balance is not None:
      value = f"{balance:,.2f} credits"
    elif credit.get("has_credits") is True:
      value = "AVAILABLE; balance UNKNOWN"
    elif credit.get("has_credits") is False:
      value = "NONE; balance UNKNOWN"
    else:
      value = "UNKNOWN"
    label = credit["bucket"]["name"]
    freshness = str(credit.get("freshness", "unknown")).upper()
    lines.append(f"{label} credits: {value} [{freshness}]; "
                 "separate from subscription quota.")
    lines.append(f"Observed {credit.get('observed_at') or 'UNKNOWN'}.")
    pace = credit.get("pace", {})
    rate = pace.get("rate_credits_per_hour")
    if credit.get("unlimited") is True:
      lines.append("Credit pace: not applicable (unlimited credits).")
    elif rate is None:
      lines.append("Credit pace: UNKNOWN (needs balance observations "
                   "at least 15m apart without a top-up).")
    else:
      span = format_duration(pace.get("span_seconds"))
      text = f"Credit pace: {rate:,.2f} credits/h over {span}."
      if pace.get("exhausts_at"):
        remaining = format_duration(pace.get("seconds_until_empty"))
        text += (f" Estimated {remaining} until empty "
                 f"({pace['exhausts_at']}).")
      elif rate == 0:
        text += " No observed consumption."
      lines.append(text)
  return lines


def text_table(headers: list[str], rows: list[list[str]],
               styles: list[list[tuple[str, ...]]] | None = None,
               color: bool = False) -> list[str]:
  """Aligned columns; optional per-cell styles are painted after padding,
  so the visible text is identical with or without colour."""
  widths = [max(len(row[i]) for row in [headers, *rows])
            for i in range(len(headers))]

  def line(row: list[str], row_styles: Any) -> str:
    cells = []
    for index, (value, width) in enumerate(zip(row, widths)):
      style = row_styles[index] if row_styles and index < len(row_styles) \
        else ()
      cells.append(paint(value, tuple(style or ()), color)
                   + " " * (width - len(value)))
    return "  ".join(cells).rstrip()

  return [line(headers, [("bold",)] * len(headers)),
          paint("  ".join("-" * width for width in widths), ("dim",), color),
          *(line(row, styles[index] if styles else None)
            for index, row in enumerate(rows))]


PACE_STYLES = {
  "Exhausted": ("bold", "reverse", "red"),
  "Recent burn too high": ("bold", "red"),
  "Behind": ("red",),
  "Blocked": ("yellow",),
  "Surplus": ("blue",),
}
# Words the prose modes highlight, in one pass so nothing is painted twice.
HIGHLIGHTS = [
  (r"EXHAUSTS BEFORE RESET", ("bold", "red")),
  (r"LIKELY AVAILABLE|^Likely available", ("bold", "green")),
  (r"\bBEHIND\b|\bexhausted\b", ("red",)),
  (r"\bSURPLUS\b", ("blue",)),
  (r"\bRESET SOON\b|\bblocked\b|\[[A-Z ;]*(?:STALE|UNKNOWN)[A-Z ;]*\]",
   ("yellow",)),
  (r"\bRESET(?= \d{4}-)", ("green",)),
  (r"^Attention:", ("bold", "red")),
]
HIGHLIGHT_RE = re.compile("|".join(f"(?P<h{index}>{pattern})" for index,
                                   (pattern, _) in enumerate(HIGHLIGHTS)),
                          re.MULTILINE)


def highlight(text: str, color: bool) -> str:
  if not color:
    return text

  def replace(match: re.Match[str]) -> str:
    index = int(match.lastgroup[1:])
    return paint(match.group(0), HIGHLIGHTS[index][1], True)

  return HIGHLIGHT_RE.sub(replace, text)


def stale_marker(observation: dict[str, Any]) -> str:
  state = observation.get("freshness", "unknown")
  return "" if state == "fresh" else f" [{str(state).upper()}]"


def spent_account_buckets(service: dict[str, Any]) -> list[str]:
  """Account-wide buckets at 0% in their current period.

  Model-scoped buckets such as Fable are sub-limits: their use also counts
  against the account-wide buckets, so a spent account bucket blocks them
  whatever their own remaining percentage says.
  """
  name = str(service.get("display_name", service.get("service_id")))
  spent = []
  for limit in service.get("limits", []):
    if not isinstance(limit, dict):
      continue
    observation = limit.get("last_observation")
    observation = observation if isinstance(observation, dict) else {}
    scope = (limit.get("bucket") or {}).get("scope_kind")
    if (scope in (None, "account")
        and observation.get("period_relation") == "current"
        and observation.get("remaining_percent") == 0):
      spent.append(service_label(limit, name))
  return spent


def sub_limit_blocked(limit: dict[str, Any], service: dict[str, Any]) -> bool:
  """A model bucket with capacity left, blocked by a spent account bucket."""
  observation = limit.get("last_observation") or {}
  scope = (limit.get("bucket") or {}).get("scope_kind")
  return (scope not in (None, "account")
          and bool(observation.get("remaining_percent"))
          and bool(spent_account_buckets(service)))


def archived_availability(
  service: dict[str, Any], now: datetime,
) -> dict[str, Any]:
  """Resets an unqueried account has passed since it was last checked.

  The account is likely available when at least one bucket has reset and
  no account-wide bucket is still exhausted; an exhausted model-scoped
  bucket leaves the account usable for other models.
  """
  name = str(service.get("display_name", service.get("service_id")))
  resets: list[dict[str, Any]] = []
  blocked: list[dict[str, Any]] = []
  for limit in service.get("limits", []):
    if not isinstance(limit, dict):
      continue
    observation = limit.get("last_observation")
    observation = observation if isinstance(observation, dict) else {}
    relation = observation.get("period_relation")
    reset = parse_timestamp(observation.get("reset_at"))
    scope = (limit.get("bucket") or {}).get("scope_kind")
    if relation == "ended" and reset is not None and reset <= now:
      resets.append({"limit": limit, "reset": reset,
                     "label": service_label(limit, name),
                     "was": observation.get("remaining_percent")})
    elif (relation == "current" and scope in (None, "account")
          and observation.get("remaining_percent") == 0):
      blocked.append(limit)
  return {
    "resets": resets,
    "blocked": [service_label(limit, name) for limit in blocked],
    "likely_available": bool(resets) and not blocked,
  }


def likely_available_line(document: dict[str, Any]) -> str | None:
  """One line naming unqueried accounts whose quota has likely refilled."""
  now = parse_timestamp(document.get("generated_at")) or utc_now()
  items = []
  for service in archived_accounts(document, now):
    availability = archived_availability(service, now)
    if not availability["likely_available"]:
      continue
    name = str(service.get("display_name", service.get("service_id")))
    account = service["account"]
    resets = ", ".join(
      f"{item['label'].removeprefix(name + ' ')} reset "
      f"{format_duration((now - item['reset']).total_seconds())} ago"
      for item in availability["resets"])
    items.append(f"{name} {account.get('label', 'UNKNOWN')} "
                 f"({account.get('plan', 'UNKNOWN')}): {resets}")
  if not items:
    return None
  return ("Likely available (reset since last check; not verified): "
          + "; ".join(items) + ".")


def other_account_lines(document: dict[str, Any]) -> list[str]:
  """Describe accounts seen before but not checked in this report."""
  now = parse_timestamp(document.get("generated_at"))
  moment = now or utc_now()
  lines: list[str] = []
  for service in archived_accounts(document, now):
    account = service["account"]
    name = str(service.get("display_name", service.get("service_id")))
    ident = account["key"][:8] if account.get("key") else "workspace unknown"
    availability = archived_availability(service, moment)
    lines.append(
      f"  {account.get('label', 'UNKNOWN')} [{ident}] "
      f"({account.get('plan', 'UNKNOWN')}); checked "
      f"{account.get('observed_at', 'UNKNOWN')}."
      + (" LIKELY AVAILABLE." if availability["likely_available"] else "")
    )
    for limit in service.get("limits", []):
      if not isinstance(limit, dict):
        continue
      observation = limit.get("last_observation")
      observation = observation if isinstance(observation, dict) else {}
      relation = observation.get("period_relation")
      reset = observation.get("reset_at")
      label = service_label(limit, name)
      remaining = format_percent(observation.get("remaining_percent"))
      passed = parse_timestamp(reset)
      if relation == "ended" and passed is not None:
        ago = format_duration((moment - passed).total_seconds())
        if availability["likely_available"]:
          outcome = f"likely 100% now (was {remaining})"
        else:
          outcome = f"was {remaining}, blocked"
        lines.append(f"    {label}: RESET {reset} ({ago} ago); {outcome}.")
        continue
      if relation == "current":
        due = format_duration((limit.get("pace") or {}).get(
          "reset_in_seconds"
        ))
        state = f"resets {reset} (in {due})"
      elif relation == "ended":
        state = f"reset {reset} (PASSED)"
      else:
        state = "reset time UNKNOWN"
      remaining += " remaining"
      if relation == "current" and sub_limit_blocked(limit, service):
        remaining += " (blocked)"
      lines.append(f"    {label}: last known {remaining}; {state}.")
  if lines:
    lines.insert(0, "Other accounts (last checked; not verified now):")
  return lines


def brief_row_styles(row: list[str]) -> list[tuple[str, ...]]:
  label, remaining, _, state = row
  return [("yellow",) if label.endswith("]") else (),
          ("bold", "red") if remaining == "0%" else (),
          (), PACE_STYLES.get(state, ("dim",) if state in (
            "On Pace", "Early", "Period ended", "Period unknown",
            "Not started") else ())]


def render_brief(document: dict[str, Any], color: bool = False) -> str:
  rows, credit_rows, alerts, notes, tokens = [], [], [], [], []
  for service_id, service in document.get("services", {}).items():
    name = str(service.get("display_name", service_id))
    for limit in service.get("limits", []):
      observation = limit.get("last_observation") or {}
      label = service_label(limit, name)
      marker = stale_marker(observation)
      relation = observation.get("period_relation")
      pace = limit.get("pace") or {}
      remaining, reset, state = "UNKNOWN", "—", "Period unknown"
      if observation.get("window_not_started"):
        remaining, state = "100%", "Not started"
      elif relation == "ended":
        state = "Period ended"
      elif relation == "current":
        remaining = format_percent(observation.get("remaining_percent"))
        reset = format_duration(pace.get("reset_in_seconds"))
        state = str(pace.get("state", "unknown")).replace("_", " ").title()
        if observation.get("remaining_percent") == 0:
          state = "Exhausted"
          alerts.append(f"{label} exhausted{marker}")
        elif (limit.get("burn") or {}).get("exhausts_before_reset"):
          state = "Recent burn too high"
          alerts.append(f"{label}: recent burn exhausts before reset{marker}")
        elif pace.get("state") == "behind":
          alerts.append(f"{label}: projected exhaustion before reset{marker}")
        if sub_limit_blocked(limit, service):
          state = "Blocked"
      rows.append([label + marker, remaining, reset, state])
    refresh = service.get("refresh") or {}
    if refresh.get("status") == "failed":
      error = refresh.get("error") or {}
      notes.append(f"{name} refresh failed: {error.get('message', 'unknown')}.")
    if not service.get("limits"):
      notes.append(f"{name} quota: unavailable.")
    for warning_item in service.get("warnings", []):
      notes.append(f"{name}: {warning_item.get('message', 'unknown warning')}")
    if service_id == "claude_code":
      tokens.append(brief_tokens(service.get("token_usage", {}),
                                 "Claude Code").replace(" [FRESH]", ""))
    if service_id != "codex":
      continue
    for credit in service.get("credits", []):
      balance = credit_balance(credit.get("balance"))
      value = f"{balance:,.2f}" if balance is not None else "UNKNOWN"
      pace = credit.get("pace") or {}
      rate = pace.get("rate_credits_per_hour")
      burn = f"{rate:,.2f}/h" if rate is not None else "Collecting history"
      until = "—"
      if pace.get("seconds_until_empty") is not None:
        until = "~" + format_duration(pace["seconds_until_empty"])
      elif rate == 0:
        until = "No observed burn"
      if credit.get("unlimited") is True:
        value, burn = "Unlimited", "—"
      elif balance is None:
        burn = "UNKNOWN"
        if credit.get("has_credits") is True:
          value = "Available (unknown)"
        elif credit.get("has_credits") is False:
          value = "None reported"
      credit_rows.append([
        credit["bucket"]["name"] + stale_marker(credit), value, burn, until,
      ])
    if not service.get("credits"):
      credit_rows.append(["Codex", "UNKNOWN", "—", "—"])
    usage = service.get("token_usage") or {}
    tokens.append(brief_tokens(usage).replace(" [FRESH]", ""))
    if usage.get("error"):
      notes.append(f"Token lookup failed: {usage['error']}")

  lines = ["Attention: " + "; ".join(alerts) + "."] if alerts else []
  available = likely_available_line(document)
  if available:
    lines.append(available)
  for service_id in ("claude_code", "codex"):
    if service_id in document.get("services", {}):
      if lines:
        lines.append("")
      lines.append(account_line(document["services"][service_id]))
  others = other_account_lines(document)
  if others:
    lines.extend(["", *others])
  lines = [highlight(line, color) for line in lines]
  if rows:
    if lines:
      lines.append("")
    lines.extend(text_table(["Quota", "Remaining", "Resets in", "Pace"], rows,
                            [brief_row_styles(row) for row in rows], color))
  if credit_rows:
    lines.append("")
    lines.extend(text_table(
      ["Credits", "Balance", "Burn", "Until empty"], credit_rows,
      color=color,
    ))
  if tokens:
    lines.extend(["", *tokens])
  if notes:
    lines.extend(["", *(paint(note, ("yellow",), color) for note in notes)])
  lines.extend(["", paint(
    f"Checked {document.get('generated_at', 'UNKNOWN')}; observations fresh "
    "unless marked. Quota buckets are separate.", ("dim",), color)])
  return "\n".join(lines)


# A short-window (5h) event is shown only if it is noteworthy; this matches
# the tmux component's 5h threshold.
SHORT_WINDOW_LOW_PERCENT = 10


def timeline_quota(limit: dict[str, Any], name: str) -> str:
  """Short quota name: Claude, Claude 5h, Fable, Codex.

  Weekly is the default window and goes unnamed.
  """
  bucket = limit.get("bucket") or {}
  service = "Claude" if name.startswith("Claude") else name
  if bucket.get("scope_kind") not in (None, "account"):
    service = str(bucket.get("name") or service)
  window = str((limit.get("window") or {}).get("label") or "")
  if window.lower() in ("", "weekly"):
    return service
  return f"{service} {window[:1].upper()}{window[1:]}"


SGR = {"bold": "1", "dim": "2", "italic": "3", "reverse": "7",
       "red": "31", "green": "32", "yellow": "33", "blue": "34",
       "magenta": "35", "cyan": "36"}
# Distinguish accounts without reusing the red/yellow/green urgency colours.
ACCOUNT_COLOURS = ("cyan", "magenta", "blue")


def use_color(stream: Any) -> bool:
  """Colour only an interactive terminal, honouring NO_COLOR and TERM=dumb."""
  if os.environ.get("NO_COLOR") or os.environ.get("TERM") == "dumb":
    return False
  try:
    return bool(stream.isatty())
  except (AttributeError, ValueError):
    return False


def resolve_color(choice: str, stream: Any) -> bool:
  """--color: always and never override the environment; auto detects."""
  if choice == "always":
    return True
  if choice == "never":
    return False
  return use_color(stream)


def paint(text: str, styles: tuple[str, ...], on: bool) -> str:
  if not on or not styles or not text:
    return text
  return f"\033[{';'.join(SGR[style] for style in styles)}m{text}\033[0m"


def timeline_clock(moment: datetime, tz: Any = None) -> str:
  return moment.astimezone(tz).strftime("%a %d %b %H:%M")


def unblock_moment(service: dict[str, Any]) -> datetime | None:
  """When an account's spent account-wide buckets have all reset, if known.

  That is when an account blocked by a spent bucket becomes usable again.
  """
  resets = []
  for limit in service.get("limits", []):
    if not isinstance(limit, dict):
      continue
    observation = limit.get("last_observation") or {}
    scope = (limit.get("bucket") or {}).get("scope_kind")
    if (scope in (None, "account")
        and observation.get("period_relation") == "current"
        and observation.get("remaining_percent") == 0):
      reset = parse_timestamp(observation.get("reset_at"))
      if reset is None:
        return None
      resets.append(reset)
  return max(resets) if resets else None


def timeline_event(moment: datetime, order: int, kind: str, quota: str,
                   **fields: Any) -> dict[str, Any]:
  """One unmerged event; the text view merges and filters these."""
  event = {"moment": moment, "order": order, "type": kind, "quota": quota,
           "provider": None, "account": "account unknown", "active": True,
           "limit_id": None, "remaining": None, "unit": "percent",
           "restores": False, "blocked": False, "stale": False,
           "rate": None, "short_window": False, "reset_at": None}
  event.update(fields)
  return event


def timeline_limit_events(
  limit: dict[str, Any], name: str, base: dict[str, Any],
  unblock: datetime | None, now: datetime,
) -> list[dict[str, Any]]:
  observation = limit.get("last_observation") or {}
  if (observation.get("period_relation") != "current"
      or observation.get("window_not_started")):
    return []
  active = base["active"]
  remaining = observation.get("remaining_percent")
  seconds = (limit.get("window") or {}).get("duration_seconds") or 0
  fields = dict(base, limit_id=limit.get("limit_id"), remaining=remaining,
                short_window=0 < seconds <= 86400,
                stale=active and observation.get("freshness") != "fresh")
  quota = timeline_quota(limit, name)
  events = []
  burn = limit.get("burn") or {}
  exhausts = parse_timestamp(burn.get("exhausts_at"))
  reset = parse_timestamp(observation.get("reset_at"))
  if remaining == 0:
    if active:
      events.append(timeline_event(now, 0, "exhausted", quota, **fields))
  elif (active and exhausts is not None and burn.get("exhausts_before_reset")
        and not fields["stale"]):
    rate = burn.get("rate_percent_per_hour")
    events.append(timeline_event(max(exhausts, now), 1, "burn", quota,
                                 **dict(fields, rate=rate, reset_at=reset)))
  if reset is not None and reset > now:
    # While spent account-wide buckets block the account, a reset before
    # the last of them frees nothing; that last reset frees the account.
    if unblock is not None and reset < unblock:
      flags = {"blocked": True}
    elif unblock is not None and reset == unblock:
      flags = {"restores": True}
    else:
      flags = {"restores": remaining == 0}
    events.append(timeline_event(reset, 2, "reset", quota,
                                 **dict(fields, **flags)))
  return events


def timeline_credit_events(
  service: dict[str, Any], name: str, base: dict[str, Any], now: datetime,
) -> list[dict[str, Any]]:
  events = []
  for credit in service.get("credits", []):
    if not isinstance(credit, dict):
      continue
    pace = credit.get("pace") or {}
    exhausts = parse_timestamp(pace.get("exhausts_at"))
    if exhausts is None or credit.get("freshness", "fresh") != "fresh":
      continue
    balance = credit_balance(credit.get("balance"))
    events.append(timeline_event(
      max(exhausts, now), 1, "burn", f"{name} Credits", **dict(
        base, unit="credits", rate=pace.get("rate_credits_per_hour"),
        remaining=float(balance) if balance is not None else None)))
  return events


def timeline_past_resets(
  service: dict[str, Any], name: str, base: dict[str, Any], now: datetime,
) -> list[dict[str, Any]]:
  """Resets an unqueried account passed since its last check.

  Only an account that is likely available again is listed: a past reset
  behind a still-spent bucket changes nothing until that bucket resets.
  """
  availability = archived_availability(service, now)
  if not availability["likely_available"]:
    return []
  events = []
  for item in availability["resets"]:
    limit = item["limit"]
    seconds = (limit.get("window") or {}).get("duration_seconds") or 0
    events.append(timeline_event(
      item["reset"], 2, "reset", timeline_quota(limit, name), **dict(
        base, limit_id=limit.get("limit_id"), remaining=item["was"],
        restores=True, short_window=0 < seconds <= 86400)))
  return events


def timeline_events(document: dict[str, Any], now: datetime
                    ) -> list[dict[str, Any]]:
  """Every event for the checked and archived accounts, unmerged, in order."""
  services = document.get("services", {})
  sources = [(services[sid], sid, True) for sid in ("claude_code", "codex")
             if isinstance(services.get(sid), dict)]
  sources += [(item, item.get("service_id"), False)
              for item in archived_accounts(document, now)]
  events: list[dict[str, Any]] = []
  for service, service_id, active in sources:
    name = str(service.get("display_name", service_id or "?"))
    account = service.get("account")
    account = account if isinstance(account, dict) else {}
    base = {"provider": service_id, "active": active,
            "account": str(account.get("label") or "account unknown")}
    unblock = unblock_moment(service)
    for limit in service.get("limits", []):
      if isinstance(limit, dict):
        events += timeline_limit_events(limit, name, base, unblock, now)
    if active:
      events += timeline_credit_events(service, name, base, now)
    else:
      events += timeline_past_resets(service, name, base, now)
  events.sort(key=lambda event: (event["moment"], event["order"]))
  return events


def timeline_document(document: dict[str, Any]) -> dict[str, Any]:
  """Machine-readable timeline: every event, unmerged and unfiltered."""
  now = parse_timestamp(document.get("generated_at")) or utc_now()
  return {
    "schema_version": 1,
    "generated_at": iso_utc(now),
    "events": [{
      "at": iso_utc(event["moment"]), "type": event["type"],
      "provider": event["provider"], "account": event["account"],
      "active": event["active"], "quota": event["quota"],
      "limit_id": event["limit_id"], "short_window": event["short_window"],
      f"remaining_{event['unit']}" if event["unit"] == "credits"
      else "remaining_percent": event["remaining"],
      "unit": event["unit"], "restores": event["restores"],
      "blocked": event["blocked"], "stale": event["stale"],
      "rate_per_hour": event["rate"],
      "reset_at": iso_utc(event["reset_at"]) if event["reset_at"] else None,
    } for event in timeline_events(document, now)],
  }


def noteworthy(event: dict[str, Any]) -> bool:
  """Short-window items are noise unless they need attention."""
  if not event["short_window"] or event["type"] != "reset":
    return True
  remaining = event["remaining"]
  return bool(event["restores"]) or (
    event["active"] and remaining is not None
    and remaining < SHORT_WINDOW_LOW_PERCENT)


def note_parts(event: dict[str, Any]) -> tuple[str, str, str]:
  """(head, value, tail) of an event's note; merging joins the values."""
  if event["type"] == "exhausted":
    return "", "", ""
  remaining = event["remaining"]
  if remaining is None:
    value = ""
  elif event["unit"] == "credits":
    value = f"{remaining:,.2f}"
  else:
    value = format_percent(remaining)
  if event["active"]:
    head, tail = "", " → 100%" if event["restores"] else " left"
  else:
    head, tail = "was ", " → ~100%" if event["restores"] else ""
  extras = []
  if event["blocked"]:
    extras.append("blocked")
  if event["rate"] is not None:
    rate = event["rate"]
    extras.append(f"{rate:,.2f}/h" if event["unit"] == "credits"
                  else f"{rate}%/h")
  if event["stale"]:
    extras.append("stale")
  return head, value, tail + "".join(", " + extra for extra in extras)


def merge_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
  """Join events that share a moment, type, account and meaning."""
  rows: dict[tuple, dict[str, Any]] = {}
  for event in events:
    head, value, tail = note_parts(event)
    key = (event["moment"], event["order"], event["type"], event["account"],
           event["active"], event["restores"], event["stale"], head, tail)
    row = rows.get(key)
    if row is None:
      rows[key] = dict(event, quotas=[event["quota"]],
                       values=[value] if value else [], head=head, tail=tail)
    else:
      row["quotas"].append(event["quota"])
      if value:
        row["values"].append(value)
  for row in rows.values():
    values = " / ".join(row["values"])
    row["note"] = (f"{row['head']}{values}{row['tail']}" if values
                   else row["tail"].lstrip(", ").strip())
    row["quota"] = ", ".join(row["quotas"])
  return list(rows.values())


def burn_details(row: dict[str, Any], now: datetime,
                 consumers: dict[str, list[tuple[str, float]]] | None
                 ) -> list[str]:
  """Optional BURN notes, most useful first; width decides how many show.

  How long before its reset the quota runs out, the rate that would last
  until then, and the agents using most of it lately.
  """
  if row["type"] != "burn" or len(row["quotas"]) != 1:
    return []
  details = []
  reset = row["reset_at"]
  if row["unit"] == "percent" and reset is not None:
    details.append("out " + format_duration(
      (reset - row["moment"]).total_seconds()) + " before reset")
    hours = (reset - now).total_seconds() / 3600
    if row["remaining"] is not None and hours > 0:
      details.append(f"lasts at ≤{row['remaining'] / hours:.2f}%/h")
  shares = (consumers or {}).get(row["provider"])
  if shares:
    details.append("15m: " + ", ".join(
      f"{label} {share:.0f}%" for label, share in shares))
  return details


def timeline_when(moment: datetime, now: datetime) -> str:
  seconds = (moment - now).total_seconds()
  if seconds < 0:
    return format_duration(-seconds) + " ago"
  return "now" if seconds == 0 else "in " + format_duration(seconds)


def timeline_day(moment: datetime, now: datetime, tz: Any) -> str:
  day = moment.astimezone(tz).date()
  offset = (day - now.astimezone(tz).date()).days
  names = {-1: "Yesterday", 0: "Today", 1: "Tomorrow"}
  return names.get(offset, moment.astimezone(tz).strftime("%a %d %b"))


def timeline_line(row: dict[str, Any], now: datetime, tz: Any,
                  account_colour: str, widths: tuple[int, ...],
                  color: bool, changed: bool = False) -> str:
  """One row in aligned columns: when, time, label, quota, account, note.

  Padding sits outside the escape codes, so the plain text is identical
  whether or not colour is on.
  """
  seconds = (row["moment"] - now).total_seconds()
  soon = 0 <= seconds < 86400
  highlight = row["restores"]
  quiet: tuple[str, ...] = ()
  if (not row["active"] and not highlight) or row["stale"]:
    quiet = ("dim", "italic")
  label = row["type"].upper()
  note_style: tuple[str, ...] = ("dim", "italic")
  if label == "EXHAUSTED":
    label_style: tuple[str, ...] = ("bold", "reverse", "red")
  elif label == "BURN":
    label_style = ("bold", "red" if soon else "yellow")
  else:
    label_style = ("bold", "green")
  if highlight:
    when_style: tuple[str, ...] = ("bold", "green")
    note_style = ("bold", "green")
  elif seconds == 0:
    when_style = ("bold", "red")
  else:
    when_style = ("bold",) if soon else ()
  if quiet:
    label_style = (*quiet, label_style[-1])
    when_style = quiet
  if changed:
    label_style = ("reverse", *label_style)
  when_width, label_width, quota_width, who_width = widths
  time_of_day = row["moment"].astimezone(tz).strftime("%H:%M")
  cells = [
    (timeline_when(row["moment"], now), when_style, when_width),
    (time_of_day, quiet, 5),
    (label, label_style, label_width),
    (row["quota"], quiet or ("bold",), quota_width),
    (row["account"], (*quiet, account_colour), who_width),
    (row["note"], note_style, 0),
  ]
  parts = [paint(text, style, color) + " " * (width - len(text))
           for text, style, width in cells if text]
  return ("    " + "  ".join(parts)).rstrip()


def timeline_rows(document: dict[str, Any], now: datetime
                  ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], bool]:
  """(events, displayed rows, whether any routine event was hidden)."""
  events = timeline_events(document, now)
  hidden = any(not noteworthy(event) for event in events)
  return events, merge_events([event for event in events
                               if noteworthy(event)]), hidden


def timeline_row_key(row: dict[str, Any]) -> tuple[str, str, str, str]:
  """A row's identity across redraws; its relative time is not part of it."""
  return (iso_utc(row["moment"]), row["type"], row["account"], row["quota"])


def render_timeline(document: dict[str, Any], tz: Any = None,
                    color: bool = False, marked: Any = frozenset(),
                    width: int | None = None,
                    consumers: dict[str, list[tuple[str, float]]]
                    | None = None) -> str:
  """Upcoming resets and projected run-outs, grouped by day.

  Run-out times come from each quota's recent burn and appear only when a
  quota would run out before it resets. Accounts other than the one each
  service last checked contribute their last known resets, unverified.
  Rows whose key is in `marked` are highlighted as changed. BURN rows add
  details in priority order while they fit `width` (all when None);
  `consumers` maps a service to its busiest agents and their shares.
  """
  now = parse_timestamp(document.get("generated_at")) or utc_now()
  events, rows, hidden = timeline_rows(document, now)
  colours: dict[str, str] = {}
  for event in events:
    colours.setdefault(event["account"], ACCOUNT_COLOURS[
      len(colours) % len(ACCOUNT_COLOURS)])
  zone = now.astimezone(tz).tzname() or "local"
  lines = [paint("Timeline", ("bold",), color) + paint(
    f" ({zone}; now {timeline_clock(now, tz)})", ("dim",), color)]
  widths = (max([9, *(len(timeline_when(row["moment"], now)) for row in rows)]),
            *(max((len(row[field]) for row in rows), default=0)
              for field in ("type", "quota", "account")))
  day = None
  for row in rows:
    heading = timeline_day(row["moment"], now, tz)
    if heading != day:
      day = heading
      lines.append("  " + paint(heading, ("bold",), color))
    details = burn_details(row, now, consumers)
    shown = row
    for count in range(len(details), -1, -1):
      shown = dict(row, note=row["note"] + "".join(
        ", " + detail for detail in details[:count]))
      if width is None or len(timeline_line(
          shown, now, tz, "", widths, False)) <= width:
        break
    lines.append(timeline_line(shown, now, tz, colours[row["account"]],
                               widths, color,
                               timeline_row_key(row) in marked))
  if not rows:
    lines.append("  No upcoming resets or projected run-outs.")
  footer = []
  if any(row["type"] == "burn" for row in rows):
    footer += [
      "BURN extrapolates the last "
      f"{BURN_RECENT_SECONDS // 3600}h of use and is listed only when a "
      "quota",
      "would run out before it resets. As width allows, it adds how long",
      "before the reset it runs out, the hourly rate that would last until",
      "then, and the busiest agents' shares of the last 15 minutes.",
    ]
  if hidden:
    footer.append("5h resets are shown only when low, exhausted, running out"
                  " or freeing an account.")
  if any(not row["active"] for row in rows):
    footer.append("Other accounts: \"was\" is the last known value, "
                  "unverified" + ("; ~100% follows a reset." if any(
                    row["restores"] for row in rows) else "."))
  if footer:
    lines.extend(["", *(paint(line, ("dim", "italic"), color)
                        for line in footer)])
  return "\n".join(lines)


def render_verbose(document: dict[str, Any], color: bool = False) -> str:
  return highlight(render_verbose_text(document), color)


def render_verbose_text(document: dict[str, Any]) -> str:
  generated = document.get("generated_at", "UNKNOWN")
  lines = [
    f"Quota report generated {generated}.",
    "Percentages are REMAINING in separate buckets; do not add them.",
    "Pace: BEHIND = exhaustion before reset; "
    f"SURPLUS = at least {PACE_SURPLUS_UNUSED_PERCENT}% projected unused.",
    "Recent-burn exhaustion overrides SURPLUS; stale/unknown data or "
    "RESET SOON alone do not justify speeding up.",
  ]
  available = likely_available_line(document)
  if available:
    lines.append(available)
  services = document.get("services", {})
  for service_id in ("claude_code", "codex"):
    if service_id in services:
      lines.append(account_line(services[service_id]))
  others = other_account_lines(document)
  if others:
    lines.extend(others)
    lines.append(
      "Those accounts were not queried here; switching back is what "
      "confirms a reset."
    )
  for service_id in ("claude_code", "codex"):
    service = services.get(service_id)
    if not isinstance(service, dict):
      continue
    name = str(service.get("display_name", service_id))
    refresh = service.get("refresh", {})
    status = str(refresh.get("status", "unknown")).upper()
    attempted = refresh.get("attempted_at") or "not attempted"
    lines.append("")
    if refresh.get("status") == "not_needed":
      last_success = refresh.get("last_success_at") or "UNKNOWN"
      lines.append(
        f"{name} refresh: NOT NEEDED; cached provider data is from "
        f"{last_success}."
      )
    else:
      lines.append(f"{name} refresh: {status} at {attempted}.")
    if refresh.get("status") == "failed" and service.get("limits"):
      lines.append("Showing the last successful observations.")
    error = refresh.get("error")
    if isinstance(error, dict):
      lines.append(
        f"Refresh error {error.get('code', 'unknown')}: "
        f"{error.get('message', 'unknown error')}"
      )
    if service_id == "codex":
      lines.extend(brief_credits(service))
      lines.append(brief_tokens(service.get("token_usage", {})))
    elif service_id == "claude_code":
      lines.append(brief_tokens(service.get("token_usage", {}), "Claude Code"))
    for limit in service.get("limits", []):
      if isinstance(limit, dict):
        lines.extend(["", *brief_limit(limit, name)])
    if service.get("limits"):
      lines.append(brief_binding(service, name))
    for limit in service.get("limits", []):
      if not isinstance(limit, dict):
        continue
      burn = limit.get("burn")
      observation = limit.get("last_observation")
      if (
        isinstance(burn, dict)
        and burn.get("exhausts_before_reset") is True
        and isinstance(observation, dict)
        and observation.get("period_relation") == "current"
      ):
        freshness = str(observation.get("freshness", "unknown")).upper()
        lines.append(
          f"Recent-burn constraint: {service_label(limit, name)} "
          f"[{freshness}]; EXHAUSTS BEFORE RESET. "
          "Conserve this bucket even if pace is SURPLUS."
        )
    for item in service.get("warnings", []):
      if isinstance(item, dict):
        lines.append(
          f"Warning {item.get('code', 'unknown')}: "
          f"{item.get('message', 'unknown warning')}"
        )
  return "\n".join(lines)


# --live: redraw every interval; query services only when the cached quota
# is older than LIVE_QUOTA_REFRESH, and never with --cached.
LIVE_INTERVAL = 30
LIVE_MIN_INTERVAL = 5
LIVE_QUOTA_REFRESH = 300
ENTER_SCREEN, LEAVE_SCREEN = "\033[?1049h\033[?25l", "\033[?25h\033[?1049l"
# Between frames a dim dot circles once every eight seconds at the start of
# the status line, so a quiet screen visibly still has a live loop behind it.
# Braille cells are single-width everywhere and have no emoji forms.
SPINNER = "⠁⠈⠐⠠⢀⡀⠄⠂"
SPINNER_STEP = 1.0
# A lone Escape quits; one followed by `[` or `O` starts an arrow or other
# key sequence and does not.
QUIT_RE = re.compile(r"[qQ]|\x1b(?![\[O])")


def key_wait(seconds: float) -> str:
  """Sleep up to seconds, returning any keys typed on a terminal stdin."""
  import select
  try:
    fd = sys.stdin.fileno()
    terminal = os.isatty(fd)
  except (AttributeError, OSError, ValueError):
    terminal = False
  if not terminal:
    time.sleep(seconds)
    return ""
  ready, _, _ = select.select([fd], [], [], seconds)
  return os.read(fd, 64).decode(errors="replace") if ready else ""


@contextmanager
def keys_unbuffered():
  """cbreak mode on a terminal stdin: keys arrive unechoed and unbuffered
  while Ctrl-C still interrupts. Restores the previous mode on exit."""
  try:
    import termios
    import tty
    fd = sys.stdin.fileno()
    saved = termios.tcgetattr(fd) if os.isatty(fd) else None
  except (ImportError, AttributeError, OSError, ValueError):
    saved = None
  if saved is None:
    yield
    return
  tty.setcbreak(fd)
  try:
    yield
  finally:
    termios.tcsetattr(fd, termios.TCSADRAIN, saved)


def live_document(args: argparse.Namespace, cache_path: Path,
                  now: float) -> dict[str, Any]:
  """The quota document for one live frame, refreshed only when stale."""
  try:
    age = now - cache_path.stat().st_mtime
  except OSError:
    age = float("inf")
  previous = None if args.no_cache else load_cache(cache_path)
  if not args.cached and (previous is None or age > LIVE_QUOTA_REFRESH):
    with cache_lock(cache_path) as acquired:
      if acquired:
        previous = load_cache(cache_path)
        document = build_document(
          args.provider, Path(args.claude_file).expanduser(),
          args.claude_bin, args.claude_timeout, args.codex_bin,
          args.timeout, previous)
        write_cache(cache_path, document)
        return document
  if previous is None:
    return {"generated_at": iso_utc(), "services": {}}
  return select_services(reevaluate_document(previous), args.provider)


def notify(message: str, title: str = "agent-quota") -> None:
  """Terminal bell plus a desktop notification where one is available."""
  sys.stdout.write("\a")
  sys.stdout.flush()
  if sys.platform == "darwin":
    command = ["osascript", "-e", "on run argv", "-e",
               "display notification (item 2 of argv) with title "
               "(item 1 of argv)", "-e", "end run", title, message]
  elif shutil.which("notify-send"):
    command = ["notify-send", title, message]
  else:
    return
  try:
    subprocess.run(command, capture_output=True, timeout=10)
  except (OSError, subprocess.TimeoutExpired):
    pass


def timeline_alerts(previous: dict[tuple, dict[str, Any]],
                    rows: list[dict[str, Any]], now: datetime,
                    sent: set[tuple]) -> list[str]:
  """Resets that just happened, accounts freed, and BURNs within an hour."""
  alerts = []
  current = {timeline_row_key(row): row for row in rows}
  for key, row in previous.items():
    if (row["type"] == "reset" and row["moment"] <= now
        and key not in current and ("reset", key) not in sent):
      sent.add(("reset", key))
      alerts.append(f"{row['quota']} reset for {row['account']}")
  for key, row in current.items():
    if row["type"] == "burn" and (row["moment"] - now).total_seconds() <= 3600:
      if ("burn", key) not in sent:
        sent.add(("burn", key))
        alerts.append(f"{row['quota']} for {row['account']} runs out "
                      + timeline_when(row["moment"], now))
    if row["restores"] and not row["active"] and row["moment"] <= now:
      if ("free", row["account"]) not in sent:
        sent.add(("free", row["account"]))
        alerts.append(f"{row['account']} is likely available again")
  return alerts


def live_consumers(args: argparse.Namespace, now: float
                   ) -> dict[str, list[tuple[str, float]]] | None:
  """Busiest agents per service for BURN rows, or None if unreadable."""
  try:
    return agents_module().timeline_consumers(
      args, SimpleNamespace(**globals()), now)
  except (OSError, ValueError, KeyError, TypeError):
    return None


def fit_screen(lines: list[str], height: int) -> list[str]:
  """Keep the header visible: drop what does not fit, and say so."""
  if len(lines) <= height:
    return lines
  kept = lines[:max(1, height - 1)]
  return kept + [f"… {len(lines) - len(kept)} more lines"]


def run_live(args: argparse.Namespace, cache_path: Path,
             out: Any = None, clock: Any = time.time,
             wait: Any = None) -> int:
  """Redraw --timeline or --agents until q, Escape or Ctrl-C.

  wait(seconds) sleeps and returns the keys typed meanwhile."""
  if wait is None:
    with keys_unbuffered():
      return run_live(args, cache_path, out, clock, key_wait)
  out = out or sys.stdout
  interval = args.interval or LIVE_INTERVAL
  view = "timeline" if args.timeline else "agents"
  previous_rows: dict[tuple, Any] = {}
  previous_states: dict[str, Any] = {}
  sent: set[tuple] = set()
  first, last_summaries, tick = True, 0.0, 0
  out.write(ENTER_SCREEN)
  try:
    while True:
      now = clock()
      moment = datetime.fromtimestamp(now, timezone.utc)
      document = live_document(args, cache_path, now)
      if view == "timeline":
        document["generated_at"] = iso_utc(moment)
        _, rows, _ = timeline_rows(document, moment)
        keys = {timeline_row_key(row): row for row in rows}
        marked = frozenset() if first else frozenset(
          set(keys) - set(previous_rows))
        body = render_timeline(
          document, color=args.color_on, marked=marked,
          width=shutil.get_terminal_size((100, 40)).columns,
          consumers=live_consumers(args, now))
        alerts = timeline_alerts(previous_rows, rows, moment, sent)
        previous_rows = keys
      else:
        module = agents_module()
        frame = module.agent_frame(args, SimpleNamespace(**globals()),
                                   Path(__file__).resolve(), now)
        agents = frame["agents"]
        # Mark state changes only: the current step changes nearly every
        # redraw, so marking it would mark almost every busy row.
        states = {agent["key"]: agent.get("state") for agent in agents}
        marked = frozenset() if first else frozenset(
          key for key, value in states.items()
          if previous_states.get(key) != value)
        header = module.live_header(document, agents,
                                    SimpleNamespace(**globals()), now,
                                    args.color_on)
        body = "\n".join([*header, "", module.render(
          agents, frame["cache"], SimpleNamespace(**globals()), now,
          args.verbose, args.color_on, marked)])
        alerts = module.agent_alerts(
          previous_states,
          agents, now, sent)
        previous_states = states
        if frame["command"] and now - last_summaries >= 300:
          module.start_summaries(frame["command"])
          last_summaries = now
      try:
        age = format_duration(now - cache_path.stat().st_mtime)
      except OSError:
        age = "UNKNOWN"
      status = paint(
        f"{SPINNER[tick % len(SPINNER)]} agent-quota --{view} --live · every "
        f"{f'{interval:g}s' if interval < 60 else format_duration(interval)} "
        f"· quota data {age} old · q to quit", ("dim",), args.color_on)
      size = shutil.get_terminal_size((100, 40))
      lines = fit_screen([status, "", *body.splitlines()], size.lines - 1)
      out.write("\033[H\033[2J" + "\n".join(lines) + "\n")
      out.flush()
      if args.notify and not first:
        for alert in alerts:
          notify(alert)
      first = False
      waited = 0.0
      while waited < interval:
        step = min(SPINNER_STEP, interval - waited)
        if QUIT_RE.search(wait(step)):
          return 0
        waited += step
        tick += 1
        # Repaint only the spinner cell; the frame stays as drawn.
        out.write("\033[1;1H" + paint(SPINNER[tick % len(SPINNER)],
                                       ("dim",), args.color_on))
        out.flush()
  except KeyboardInterrupt:
    pass
  finally:
    out.write(LEAVE_SCREEN)
    out.flush()
  return 0
