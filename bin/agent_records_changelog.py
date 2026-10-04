"""Changelog entries and mistake records."""

from __future__ import annotations

import hashlib
import json
import re
import sys
import time
from pathlib import Path

from agent_records_core import (
  RecordsError, comma, duration, joined, machine, mutate, mutate_cross,
  one_line,
  parse_document, parse_record_date, record_locks, render_document,
  repo_key, slug,
  stamp, lint_layout,
)


ENTRY_ORDER = ("date", "machine", "agent", "kind", "status", "location",
               "why", "cleanup-when", "cleanup-how", "notes", "repos",
               "tasks", "closed")
MISTAKE_ORDER = ("date", "machine", "agent", "severity", "status", "scope",
                 "summary", "impact", "cause", "detection",
                 "cleanup-options", "cleanup-done", "prevention", "repos",
                 "tasks")
KINDS = ("worktree", "stash", "branch", "scratch", "asset", "backup",
         "tool", "service", "other")


def record_path(root, value):
  path = (root / value).resolve()
  if (root not in path.parents or path.suffix != ".md" or
      path.parent.name not in ("entries", "mistakes")):
    raise RecordsError("entry path must be under entries/ or mistakes/", 2)
  if not path.is_file():
    raise RecordsError("record not found: " + value, 1)
  return path


def read_record(path):
  return parse_document(path.read_bytes())


def put_record(fields, order, body, mistake=False):
  canonical = MISTAKE_ORDER if mistake else ENTRY_ORDER
  keys = list(canonical) + [key for key in order if key not in canonical]
  return render_document(fields, keys, body)


def task_ids(root, values):
  if values and root is None:
    raise RecordsError("tasks directory is not configured", 2)
  if values:
    from agent_records_tasks import task_path, read_task, CLOSED
    for value in values:
      fields, _, _ = read_task(task_path(root, value))
      if fields["status"] in CLOSED:
        raise RecordsError("task is archived: " + value, 1)
  return joined(values)


def default_repos(extra):
  current = repo_key()
  return joined(([current] if current else []) + list(extra or []))


def new_entry(root, tasks_root, args, agent, push, mistake=False):
  local = stamp()
  for label in ("slug", "scope") if not mistake else ("slug",):
    if not re.fullmatch(r"[a-z0-9-]+", getattr(args, label)):
      raise RecordsError(label + " must contain only a-z, 0-9 or -", 2)
  if mistake:
    name = local[:10] + "-" + slug(args.slug) + ".md"
    path = root / "mistakes" / name
    fields = {"date": local, "machine": machine()[0], "agent": agent,
              "severity": args.severity, "status": args.status,
              "scope": one_line(args.scope), "summary": one_line(args.summary),
              "impact": one_line(args.impact), "cause": one_line(args.cause),
              "detection": one_line(args.detection),
              "cleanup-options": one_line(args.cleanup_options),
              "cleanup-done": "", "prevention": one_line(args.prevention),
              "repos": default_repos(args.repo),
              "tasks": task_ids(tasks_root, args.task or [])}
    order = MISTAKE_ORDER
  else:
    for kind in args.kind.split(" + "):
      if kind not in KINDS:
        raise RecordsError("invalid kind: " + kind, 2)
    name = (local[:10] + "-" + local[11:13] + local[14:16] + "-" +
            slug(args.scope) + "-" + slug(args.slug) + ".md")
    path = root / "entries" / name
    notes = args.notes or ""
    body_notes = sys.stdin.read() if notes == "-" else ""
    fields = {"date": local, "machine": machine()[0], "agent": agent,
              "kind": args.kind, "status": "open",
              "location": one_line(args.location), "why": one_line(args.why),
              "cleanup-when": one_line(args.cleanup_when),
              "cleanup-how": one_line(args.cleanup_how),
              "notes": one_line(notes) if notes != "-" else "",
              "repos": default_repos(args.repo),
              "tasks": task_ids(tasks_root, args.task or []), "closed": ""}
    order = ENTRY_ORDER
  if path.exists():
    raise RecordsError("record already exists: " + str(path), 1)
  body = ""
  if not mistake and body_notes:
    body = "## Notes\n\n" + body_notes + "\n"
  relative = str(path.relative_to(root))
  mutate(root, {relative: put_record(fields, order, body, mistake)},
         "Create " + relative, agent, push)
  print(relative)


def edit_associations(fields, args, tasks_root, is_open):
  values = comma(fields.get("tasks", ""))
  if getattr(args, "from_task", None) and not getattr(args, "transfer", None):
    raise RecordsError("--from-task requires --transfer", 2)
  if getattr(args, "add_task", None):
    task_ids(tasks_root, args.add_task)
    values.extend(args.add_task)
  if getattr(args, "remove_task", None):
    if not args.reason:
      raise RecordsError("--reason required to remove task", 2)
    for task_id in args.remove_task:
      if task_id not in values:
        raise RecordsError("task is not associated: " + task_id, 1)
    values = [v for v in values if v not in args.remove_task]
    if is_open:
      if not values:
        raise RecordsError("open record needs another live task", 1)
      live = False
      for task_id in values:
        try:
          task_ids(tasks_root, [task_id])
          live = True
        except RecordsError as exc:
          if exc.code != 1:
            raise
      if not live:
        raise RecordsError("open record needs another live task", 1)
  if getattr(args, "transfer", None):
    if not args.reason:
      raise RecordsError("--reason required to transfer", 2)
    if not values:
      raise RecordsError("record has no task to transfer", 1)
    if len(values) > 1 and not args.from_task:
      raise RecordsError("--from-task required for multiple tasks", 2)
    old = args.from_task or values[0]
    if old not in values:
      raise RecordsError("source task not associated", 1)
    task_ids(tasks_root, [args.transfer])
    values = [args.transfer if v == old else v for v in values]
  fields["tasks"] = joined(values)


def update_record(root, tasks_root, args, agent, push, mistake=False):
  path = record_path(root, args.entry)
  if (path.parent.name == "mistakes") != mistake:
    raise RecordsError("wrong record kind", 2)
  fields, order, body = read_record(path)
  relative = str(path.relative_to(root))
  if args.command == "close":
    if fields["status"] != "open" and not args.force:
      raise RecordsError("entry already closed", 1)
    if fields["status"] == "open":
      if args.force:
        raise RecordsError("--force does not apply to an open entry", 2)
      fields["status"] = "closed"
      fields["closed"] = (stamp() + " by " + agent + " on " + machine()[0] +
                          " -- " + one_line(args.what))
    else:
      body += ("\n" + stamp() + " by " + agent + " -- correction: " +
               one_line(args.what) + "\nFORCED by " + agent + ": " +
               one_line(args.force) + "\n")
  else:
    for key in (("status", "cause", "prevention") if mistake else
                ("location", "why", "cleanup-when", "cleanup-how")):
      value = getattr(args, key.replace("-", "_"), None)
      if value is not None:
        fields[key] = one_line(value, key)
    if mistake and args.cleanup_done:
      fields["cleanup-done"] = (fields.get("cleanup-done", "") + "\n  " +
                                stamp() + " by " + agent + " -- " +
                                one_line(args.cleanup_done)).strip()
    if not mistake and args.notes:
      value = sys.stdin.read() if args.notes == "-" else args.notes
      body += "\n" + value + "\n"
    repos = comma(fields.get("repos", ""))
    if args.add_repo:
      repos.extend(args.add_repo)
    if args.remove_repo:
      repos = [r for r in repos if r not in args.remove_repo]
    fields["repos"] = joined(repos)
    edit_associations(
      fields, args, tasks_root,
      fields["status"] not in ("closed", "mitigated", "resolved"))
  change_edits = {relative: put_record(fields, order, body, mistake)}
  operation = ("Close " if args.command == "close" else "Update ") + relative
  if getattr(args, "transfer", None):
    from agent_records_tasks import (append_log, put_task, read_task,
                                     task_path)
    target_path = task_path(tasks_root, args.transfer, archived=False)
    target, target_order, target_body = read_task(target_path)
    target_body = append_log(target_body, agent, "Transferred " + relative +
                             " from " + (args.from_task or "prior task") +
                             ": " + args.reason)
    task_edits = {str(target_path.relative_to(tasks_root)):
                  put_task(target, target_order, target_body)}
    mutate_cross(tasks_root, root, task_edits, change_edits,
                 operation, agent, push)
  else:
    mutate(root, change_edits, operation, agent, push)


def list_records(root, args, authoritative=True):
  repos = list(args.repo or [])
  if args.here:
    here = repo_key()
    if not here:
      raise RecordsError("--here requires a git checkout", 2)
    repos.append(here)
  paths = sorted((root / "entries").glob("*.md"))
  if args.mistakes:
    paths += sorted((root / "mistakes").glob("*.md"))
  rows = []
  for path in paths:
    fields, _, _ = read_record(path)
    if args.open and fields.get("status") not in ("open",):
      continue
    if args.machine is not None and fields.get("machine") != (
        args.machine or machine()[0]):
      continue
    if args.kind and args.kind not in fields.get("kind", "").split(" + "):
      continue
    record_repos = comma(fields.get("repos", ""))
    if repos and not any((r == "none" and not record_repos) or
                         r in record_repos for r in repos):
      continue
    if args.task and args.task not in comma(fields.get("tasks", "")):
      continue
    rows.append((path, fields))
  if args.json:
    print(json.dumps({"authoritative": authoritative, "records": [
      {"path": str(p.relative_to(root)), "fields": f} for p, f in rows]}))
  else:
    for path, fields in rows:
      print(("" if authoritative else "UNVERIFIED ") +
            str(path.relative_to(root)) + " " + fields["status"] + " " +
            fields.get("summary", fields.get("why", "")))


def show_record(root, value, authoritative=True):
  path = record_path(root, value)
  raw = path.read_text()
  for line in raw.splitlines():
    print(("" if authoritative else "UNVERIFIED ") + line)
  print(("" if authoritative else "UNVERIFIED ") +
        "cursor: h:" + hashlib.sha256(raw.encode()).hexdigest()[:16])


def watch_record(root, tasks, args):
  timeout = duration(args.timeout)
  interval = duration(args.interval)
  if interval < 1:
    raise RecordsError("interval must be at least 1 second", 2)
  if args.after and not re.fullmatch(r"h:[0-9a-f]{16}", args.after):
    raise RecordsError("invalid cursor", 2)
  deadline = time.monotonic() + timeout
  previous = args.after
  prior_fields = None
  while True:
    with record_locks(tasks, root, wait=args.wait,
                      unlocked=args.unlocked) as authoritative:
      path = record_path(root, args.entry)
      raw = path.read_bytes()
      fields, _, _ = parse_document(raw)
      current = "h:" + hashlib.sha256(raw).hexdigest()[:16]
      printed_cursor = False
      if previous and previous != current:
        prefix = "" if authoritative else "UNVERIFIED "
        if prior_fields is None:
          header = raw.decode().split("\n\n", 1)[0]
          for line in header.splitlines():
            print(prefix + line)
        else:
          for key, value in fields.items():
            if prior_fields.get(key) != value:
              print(prefix + key + ": " + value)
        print(prefix + "cursor: " + current)
        printed_cursor = True
      elif not previous:
        print(("" if authoritative else "UNVERIFIED ") + "cursor: " + current)
        printed_cursor = True
      met = ((args.until == "closed" and fields.get("status") in
              ("closed", "resolved", "mitigated")) or
             (args.until == "change" and
              bool(previous and previous != current)))
      if met and (authoritative or args.until == "change"):
        if not printed_cursor:
          print(("" if authoritative else "UNVERIFIED ") +
                "cursor: " + current)
        return 0
      previous = current
      prior_fields = dict(fields)
    if time.monotonic() >= deadline:
      print(("" if authoritative else "UNVERIFIED ") +
            "cursor: " + previous)
      return 4
    time.sleep(min(interval, deadline - time.monotonic()))


def migrate(root, args, agent, push):
  try:
    mapping = json.loads(Path(args.set_repos).read_text())
  except (OSError, ValueError) as exc:
    raise RecordsError("invalid migration map: " + str(exc), 2)
  edits = {}
  for relative, repos in mapping.items():
    path = record_path(root, relative)
    if path.parent.name != "entries" or not isinstance(repos, list):
      raise RecordsError("migration requires entry path and list", 2)
    fields, order, body = read_record(path)
    fields["repos"] = joined(repos)
    edits[relative] = put_record(fields, order, body)
    print(relative, "->", fields["repos"])
  if args.write and edits:
    mutate(root, edits, "Migrate entry repositories", agent, push)


def lint_records(root, tasks_root=None):
  errors = lint_layout(root, "changelog")
  for path in sorted((root / "entries").glob("*.md")) + sorted(
      (root / "mistakes").glob("*.md")):
    try:
      fields, _, _ = read_record(path)
      mistake = path.parent.name == "mistakes"
      keys = ((MISTAKE_ORDER[:11] + ("prevention",))
              if mistake else ENTRY_ORDER[:9])
      for key in keys:
        if key not in fields:
          errors.append(str(path) + ": missing " + key)
        elif not fields[key]:
          errors.append(str(path) + ": empty " + key)
      parse_record_date(fields["date"])
      if mistake:
        if fields["severity"] not in ("low", "medium", "high", "critical"):
          errors.append(str(path) + ": invalid severity")
        if fields["status"] not in ("open", "mitigated", "resolved"):
          errors.append(str(path) + ": invalid status")
      else:
        if fields["status"] not in ("open", "closed"):
          errors.append(str(path) + ": invalid status")
        if fields["status"] == "closed" and not fields.get("closed"):
          errors.append(str(path) + ": missing closed line")
        if fields.get("closed"):
          parse_record_date(fields["closed"].split(" by ", 1)[0])
        if any(k not in KINDS for k in fields["kind"].split(" + ")):
          errors.append(str(path) + ": invalid kind")
      if tasks_root:
        from agent_records_tasks import task_path, read_task, CLOSED
        for ident in comma(fields.get("tasks", "")):
          try:
            task, _, _ = read_task(task_path(tasks_root, ident))
            if task["status"] in CLOSED and fields["status"] == "open":
              errors.append(str(path) + ": open record names archived task")
          except RecordsError:
            errors.append(str(path) + ": missing task " + ident)
    except (RecordsError, KeyError) as exc:
      errors.append(str(path) + ": " + str(exc))
  return errors
