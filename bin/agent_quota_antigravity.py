"""Antigravity observations. Included by agent-quota, like its views."""

AGY_FRESH_SECONDS = 300
AGY_MAX_INPUT = 128 * 1024
AGY_VERIFIED_VERSIONS = {"1.5.17", "1.5.18"}
AGY_RECENT_SNAPSHOTS = 32
AGY_GROUPS = {"gemini": "Gemini", "3p": "Claude/GPT"}
AGY_WINDOWS = {"weekly": 7 * 86400, "5h": 5 * 3600}


def agy_executable() -> str:
  return os.environ.get("AGY_BIN", "agy")


def antigravity_source() -> str:
  configured = os.environ.get("AGENT_QUOTA_ANTIGRAVITY_SOURCE")
  if configured is not None:
    return configured
  return "statusline" if statusline_cache_path().is_file() else "cli"


def antigravity_source_matches(service: Any) -> bool:
  return (isinstance(service, dict)
          and service.get("source_mode") == antigravity_source())


def visible_antigravity(document: dict[str, Any], provider: str) -> dict[str, Any]:
  services = document.get("services", {})
  service = services.get("antigravity")
  if not isinstance(service, dict):
    return document
  if provider in ("all", "antigravity") and antigravity_source_matches(service):
    return document
  if provider == "antigravity" and service.get("source_mode") is None:
    return document  # Keep the explicit provider's disabled-source diagnostic.
  return dict(document, services={name: value for name, value in services.items()
                                  if name != "antigravity"})


def antigravity_enabled(previous_services: dict[str, Any]) -> bool:
  if antigravity_source() == "statusline":
    return True
  source = os.environ.get("AGENT_QUOTA_ANTIGRAVITY_SOURCE")
  if source is not None:
    return source == "cli"
  previous = previous_services.get("antigravity")
  return (isinstance(previous, dict) and previous.get("source_mode") == "cli"
          and shutil.which(agy_executable()) is not None)


def agy_run(executable: str, arguments: list[str], timeout: int) -> str:
  result = subprocess.run(
    [executable, *arguments], stdin=subprocess.DEVNULL,
    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
    text=True, encoding="utf-8", timeout=timeout, check=False,
    cwd=tempfile.gettempdir(),
    env=dict(os.environ, AGY_CLI_DISABLE_AUTO_UPDATE="true"),
  )
  if result.returncode != 0 or len(result.stdout) > AGY_MAX_INPUT:
    raise ValueError("Antigravity CLI failed")
  return result.stdout


@contextmanager
def pinned_agy(executable: str) -> Iterator[str]:
  # An atomic update at the installed pathname must not change the executable
  # between the version check and the local slash commands. A private hard
  # link pins it without copying the binary. Try HOME for tmpfs installations.
  installed = Path(executable).resolve(strict=True)
  for parent in (tempfile.gettempdir(), str(Path.home())):
    try:
      directory = tempfile.TemporaryDirectory(prefix="agy-quota-", dir=parent)
    except OSError:
      continue
    with directory as scratch:
      pinned = Path(scratch) / "agy"
      try:
        os.link(installed, pinned)
      except OSError:
        continue
      yield str(pinned)
      return
  raise OSError("Unable to pin Antigravity CLI for quota collection")


def agy_command(executable: str, command: str) -> dict[str, Any]:
  raw = json.loads(agy_run(executable, [
    "--log-file", os.devnull, "--settings", '{"disableSlashCommands":false}',
    "-p", f"/{command}", "--print-timeout", "10s", "--output-format", "json",
  ], 15))
  response = raw.get("command") if isinstance(raw, dict) else None
  usage = raw.get("usage") if isinstance(raw, dict) else None
  if (not isinstance(response, dict) or response.get("name") != command
      or raw.get("status") != "SUCCESS" or type(raw.get("num_turns")) is not int
      or raw["num_turns"] != 0 or not isinstance(usage, dict)
      or type(usage.get("total_tokens")) is not int or usage["total_tokens"] != 0
      or not isinstance(response.get("data"), dict)):
    raise ValueError("Unexpected Antigravity CLI response")
  return response["data"]


def agy_commands(executable: str) -> set[str]:
  # Print mode can send an unrecognized slash command to a model. Only probe
  # versions verified to handle /help locally, then require its registry to
  # advertise each requested handler.
  version = agy_run(executable, ["--version"], 5).strip()
  if version not in AGY_VERIFIED_VERSIONS:
    return set()
  commands = agy_command(executable, "help").get("commands")
  if not isinstance(commands, list):
    return set()
  return {item["name"] for item in commands
          if isinstance(item, dict) and isinstance(item.get("name"), str)}


def agy_buckets(records: list[tuple[Any, Any]]) -> tuple[list[dict], bool]:
  """Keep only the measured fields for the recognized model group windows."""
  buckets = {}
  seen = set()
  partial = False
  for native_id, record in records:
    if not isinstance(native_id, str) or not isinstance(record, dict):
      partial = True
      continue
    group, _, window = native_id.rpartition("-")
    if group not in AGY_GROUPS or window not in AGY_WINDOWS:
      partial = True
      continue
    if native_id in seen:
      buckets.pop(native_id, None)
      partial = True
      continue
    seen.add(native_id)
    if record.get("disabled") is True:
      continue
    fraction = record.get("remaining_fraction")
    if (type(fraction) not in (int, float) or not 0 <= fraction <= 1
        or record.get("window", window) != window):
      partial = True
      continue
    raw_reset = record.get("reset_time")
    reset = parse_timestamp(raw_reset)
    if raw_reset is not None and reset is None:
      partial = True
    if reset == datetime.fromtimestamp(0, timezone.utc):
      reset = None
    buckets[native_id] = {
      "group": group, "window": window,
      "remaining_fraction": float(fraction) + 0.0,
      "reset_time": iso_utc(reset) if reset is not None else None,
    }
  return list(buckets.values()), partial


def agy_usage(data: dict[str, Any]) -> tuple[list[dict], bool]:
  groups = data.get("groups")
  if not isinstance(groups, list):
    return [], True
  records = []
  partial = False
  for group in groups:
    buckets = group.get("buckets") if isinstance(group, dict) else None
    if not isinstance(buckets, list):
      partial = True
      continue
    for item in buckets:
      records.append((item.get("id"), item) if isinstance(item, dict)
                     else (None, None))
  result, invalid = agy_buckets(records)
  return result, partial or invalid


def agy_service(buckets: list[dict], partial: bool, source: str,
                observed: datetime, now: datetime) -> dict[str, Any]:
  service = base_service("google", "antigravity", "Antigravity", now)
  service["account"] = None
  service["identity_status"] = "unverified"
  service["source_mode"] = "statusline" if source == "agy.statusLine" else "cli"
  service["refresh"] = refresh_record(
    "succeeded" if buckets else "failed", now, observed if buckets else None,
    None if buckets else {"code": "antigravity_quota_unavailable",
                          "message": "No recognized Antigravity quota windows"},
  )
  service["data_status"] = (
    ("partial" if partial else "complete") if buckets else "unavailable"
  )
  if partial:
    service["warnings"].append(warning(
      "antigravity_invalid_window", "Some Antigravity quota windows were ignored"))
  for item in buckets:
    group, window = item["group"], item["window"]
    service["limits"].append({
      "limit_id": f"antigravity:{group}:{window}",
      "bucket": {
        "id": group, "name": AGY_GROUPS[group], "scope_kind": "provider_defined",
      },
      "window": {"label": window, "duration_seconds": AGY_WINDOWS[window]},
      "non_additive": True, "availability": None,
      "last_observation": make_observation(
        (1 - item["remaining_fraction"]) * 100,
        observed, parse_timestamp(item["reset_time"]), AGY_FRESH_SECONDS,
        source, "documented" if source == "agy.statusLine" else "undocumented",
        now,
      ),
    })
  return service


def agy_credit(data: dict[str, Any], observed: datetime) -> dict | None:
  raw = data.get("remaining_credits")
  if type(raw) not in (int, float):
    return None
  balance = str(raw)
  if credit_balance(balance) is None:
    return None
  return {
    "bucket": {"id": "antigravity", "name": "Antigravity",
               "scope_kind": "account"},
    "balance": balance, "observed_at": iso_utc(observed),
    "fresh_until": iso_utc(observed + timedelta(seconds=AGY_FRESH_SECONDS)),
    "has_credits": raw > 0, "unlimited": None,
    "source": {"id": "agy.print.credits", "documentation_status": "undocumented"},
  }


def statusline_cache_path() -> Path:
  return default_cache_path().with_name("antigravity-statusline-v1.json")


def agy_statusline(payload: Any) -> dict[str, Any] | None:
  if not isinstance(payload, dict) or payload.get("product") != "antigravity":
    return None
  if payload.get("agent_state") in ("authenticating", "initializing", "error"):
    return None
  email = payload.get("email")
  quota = payload.get("quota")
  if (not isinstance(email, str) or not isinstance(quota, dict)
      or re.fullmatch(r"[A-Za-z0-9.!%_+~-]{1,128}@"
                      r"[A-Za-z0-9.-]{1,128}", email) is None):
    return None
  buckets, partial = agy_buckets(list(quota.items()))
  if not buckets:
    return None
  plan = payload.get("plan_tier")
  return {
    "product": "antigravity", "email": email,
    "plan_tier": plan if isinstance(plan, str)
    and re.fullmatch(r"[A-Za-z0-9 ._-]{1,64}", plan) else None,
    "quota": {
      f'{b["group"]}-{b["window"]}': {
        "remaining_fraction": b["remaining_fraction"], "reset_time": b["reset_time"],
      } for b in buckets
    },
    "partial": partial or payload.get("partial") is True,
  }


def read_statusline(path: Path) -> dict[str, Any] | None:
  try:
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as stream:
      meta = os.fstat(stream.fileno())
      if (not stat.S_ISREG(meta.st_mode) or meta.st_uid != os.getuid()
          or meta.st_mode & 0o077):
        return None
      raw = stream.read(AGY_MAX_INPUT + 1)
    if len(raw) <= AGY_MAX_INPUT:
      value = json.loads(raw)
      if isinstance(value, dict):
        return value
  except (OSError, ValueError, RecursionError):
    pass
  return None


def record_antigravity_statusline(stream: Any) -> None:
  if os.environ.get("AGENT_QUOTA_ANTIGRAVITY_SOURCE") not in (None, "statusline"):
    return
  raw = stream.read(AGY_MAX_INPUT + 1)
  try:
    observed = agy_statusline(json.loads(raw)) if len(raw) <= AGY_MAX_INPUT else None
  except (ValueError, RecursionError):
    return
  if observed is None:
    return
  path = statusline_cache_path()
  with cache_lock(path) as acquired:
    if acquired:
      previous = read_statusline(path) or {}
      if previous.get("observation") == observed:
        return
      raw_seen = previous.get("recent_snapshots")
      recent = raw_seen if isinstance(raw_seen, dict) else {}
      quota_evidence = [observed["email"].casefold(), observed["quota"]]
      fingerprint = hashlib.sha256(json.dumps(
        quota_evidence, sort_keys=True, separators=(",", ":"),
      ).encode()).hexdigest()
      now = utc_now()
      original = parse_timestamp(recent.pop(fingerprint, None))
      observed_at = iso_utc(original if original is not None
                            and original <= now else now)
      recent[fingerprint] = observed_at
      write_cache(path, {
        "observed_at": observed_at, "observation": observed,
        "recent_snapshots": dict(list(recent.items())[-AGY_RECENT_SNAPSHOTS:]),
      })


def get_antigravity(*, now: datetime | None = None,
                    executable: str | None = None) -> dict[str, Any]:
  started = now or utc_now()
  source = antigravity_source()

  def unavailable(code: str, message: str) -> dict[str, Any]:
    service = unavailable_service(
      "google", "antigravity", "Antigravity", code, message, started,
    )
    service["account"] = None
    service["identity_status"] = "unverified"
    service["source_mode"] = source if source in ("cli", "statusline") else None
    return service

  if source not in ("cli", "statusline"):
    return unavailable("antigravity_source_disabled",
                       "Antigravity quota source is disabled or invalid")
  if source == "statusline":
    saved = read_statusline(statusline_cache_path()) or {}
    observation = agy_statusline(saved.get("observation"))
    observed = parse_timestamp(saved.get("observed_at"))
    if observation is None or observed is None or observed > started:
      return unavailable("antigravity_statusline_missing",
                         "No usable Antigravity statusLine observation")
    buckets, invalid = agy_buckets(list(observation["quota"].items()))
    service = agy_service(
      buckets, observation["partial"] or invalid, "agy.statusLine", observed,
      started,
    )
    email = observation["email"]
    key = hashlib.sha256(("antigravity:" + email.casefold()).encode()).hexdigest()
    service["account"] = {
      "key": key, "label": email, "plan": observation["plan_tier"] or "unknown",
      "source": "agy.statusLine", "observed_at": iso_utc(observed),
    }
    service["identity_status"] = "last_observed"
    service["refresh"] = refresh_record("not_needed", None, observed)
    return service

  executable = executable if executable is not None else agy_executable()
  located = shutil.which(executable)
  if located is None:
    return unavailable("antigravity_cli_missing", "Antigravity CLI is unavailable")
  try:
    with pinned_agy(located) as pinned:
      return collect_headless_agy(pinned, started, now, unavailable)
  except OSError:
    return unavailable("antigravity_cli_pin_failed",
                       "Unable to pin Antigravity CLI for a safe quota query")


def collect_headless_agy(executable: str, started: datetime, now: datetime | None,
                         unavailable) -> dict[str, Any]:
  try:
    commands = agy_commands(executable)
  except (OSError, ValueError, RecursionError, subprocess.SubprocessError):
    return unavailable("antigravity_registry_failed",
                       "Antigravity CLI command registry query failed")
  service = unavailable("antigravity_usage_unsupported",
                        "Antigravity CLI does not provide a supported quota command")
  if "usage" in commands:
    try:
      buckets, partial = agy_usage(agy_command(executable, "usage"))
      observed = now or utc_now()
      service = agy_service(buckets, partial, "agy.print.usage", observed, started)
    except (OSError, ValueError, RecursionError, subprocess.SubprocessError):
      service = unavailable("antigravity_usage_failed",
                            "Antigravity quota query failed")
  service["credits"] = []
  if "credits" in commands:
    try:
      data = agy_command(executable, "credits")
      credit = agy_credit(data, now or utc_now())
      if credit is not None:
        service["credits"].append(credit)
    except (OSError, ValueError, RecursionError, subprocess.SubprocessError):
      pass
  return service
