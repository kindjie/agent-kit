"""Task records and command implementation."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
from datetime import timedelta
from pathlib import Path

from agent_records_core import (
  EXIT_TEXT, FORCE_TEXT, RecordsError, comma, config, directory, doctor,
  duration, file_bytes, git, init_repo, joined, locks, machine, mutate,
  mutate_cross, now, one_line, parse_document, parse_time, record_locks,
  render_document, repo_key, require_agent, slug, stamp, sync,
  lint_layout,
)


ORDER = ("id", "title", "status", "owner", "expires", "helpers", "priority",
         "severity", "repos", "produces-changes", "created", "links",
         "related", "review", "blocked-on-owner", "closed")
FIXED = (
  ("merged", "merged on the default branch, its CI green"),
  ("cleanup", "worktrees, branches, stashes and scratch removed"),
  ("docs", "documentation updated"),
  ("work-reviewed", "the work was reviewed"),
)
LIVE = ("open", "in-progress", "in-review", "blocked")
CLOSED = ("done", "cancelled", "abandoned", "superseded")


def task_files(root, archived=False):
  valid = re.compile(r"T-\d{4,}-[a-z0-9-]+\.md\Z")
  paths = [p for p in root.glob("T-*-*.md") if valid.fullmatch(p.name)]
  if archived:
    paths += [p for p in (root / "archive").glob("T-*-*.md")
              if valid.fullmatch(p.name)]
  return sorted(paths)


def task_path(root, ident, archived=True):
  if not re.fullmatch(r"T-\d{4,}", ident):
    raise RecordsError("invalid task ID", 2)
  matches = [p for p in task_files(root, archived)
             if p.name.startswith(ident + "-")]
  if len(matches) != 1:
    raise RecordsError("task not found or duplicate: " + ident, 1)
  return matches[0]


def read_task(path, raw=None):
  fields, order, body = parse_document(
    path.read_bytes() if raw is None else raw)
  filename_id = "-".join(path.name.split("-")[:2])
  if fields.get("id") != filename_id:
    raise RecordsError("task ID and filename disagree: " + str(path), 2)
  return fields, order, body


def put_task(fields, order, body):
  keys = list(ORDER) + [key for key in order if key not in ORDER]
  return render_document(fields, keys, body)


def log_lines(body):
  match = re.search(r"(?m)^## Log\s*$", body)
  if not match:
    raise RecordsError("task has no Log section", 2)
  return [line for line in body[match.end():].splitlines()
          if line.startswith("- ")]


def append_log(body, actor, text, to=None):
  one_line(text, "log text")
  if not text:
    raise RecordsError("log text must not be empty", 2)
  line = "- " + stamp() + " " + actor
  if to:
    recipients = list(dict.fromkeys(to))
    if not all(re.fullmatch(r"[A-Za-z0-9._@:-]{1,64}", value)
               for value in recipients):
      raise RecordsError("invalid message recipient", 2)
    line += " -> " + ",".join(recipients)
  line += " -- " + text
  return body + ("" if body.endswith("\n") else "\n") + line + "\n"


def cursor(lines):
  raw = "\n".join(lines).encode()
  return str(len(lines)) + ":" + hashlib.sha256(raw).hexdigest()[:16]


def addressed(line, ident):
  prefix = line.split(" -- ", 1)[0]
  if " -> " not in prefix:
    return False
  recipients = prefix.rsplit(" -> ", 1)[1].split(",")
  return ident in recipients or "all" in recipients


def live_owner(fields):
  owner = fields.get("owner", "none")
  expiry = fields.get("expires", "")
  if owner == "none" or not expiry:
    return None
  return owner if parse_time(expiry) > now() else None


def heartbeat(fields, agent):
  if live_owner(fields) != agent:
    return
  current = parse_time(fields["expires"])
  fields["expires"] = stamp(max(current, now() + timedelta(hours=2)))


def permission(fields, agent, operation, force=None, helper=False,
               unowned=False):
  owner = live_owner(fields)
  if owner == agent:
    return False
  if helper and owner and agent in comma(fields.get("helpers", "")):
    return False
  if unowned and owner is None:
    return False
  if owner:
    if force:
      return True
    raise RecordsError("task claimed by " + owner + "; --force requires "
                       "owner instruction", 1)
  if force:
    raise RecordsError("claim task before " + operation +
                       "; --force cannot create a claim", 1)
  raise RecordsError("claim task before " + operation, 1)


def forced(body, agent, reason):
  return append_log(body, agent, "FORCED by " + agent + ": " + reason)


def default_repos(extra):
  current = repo_key()
  return joined(([current] if current else []) + list(extra or []))


def new_task(root, args, agent, push):
  title = one_line(args.title, "title")
  if args.review and (not args.from_design or not re.fullmatch(
      r"passed .+", args.review)):
    raise RecordsError("--review requires --from-design and passed REF", 2)
  highest = max([int(p.name.split("-")[1])
                 for p in task_files(root, True)] or [0])
  try:
    counter = int((root / ".next-id").read_text().strip())
  except (OSError, ValueError):
    raise RecordsError("invalid .next-id", 2)
  number = max(counter, highest + 1)
  ident = "T-" + str(number).zfill(4)
  relative = ident + "-" + slug(title) + ".md"
  if (root / relative).exists():
    raise RecordsError("task path already exists", 1)
  fields = dict.fromkeys(ORDER, "")
  fields.update({"id": ident, "title": title, "status": "open",
                 "owner": "none", "priority": args.priority or "unset",
                 "severity": args.severity or "unset",
                 "repos": default_repos(args.repo),
                 "produces-changes": "no" if args.no_changes else "yes",
                 "created": stamp() + " by " + agent,
                 "review": one_line(args.review, "review") if args.review else
                 ("required" if args.from_design else "n/a"),
                 "blocked-on-owner": "no"})
  checklist = "\n".join("- [ ] " + key + ": " + text
                        for key, text in FIXED)
  for check in args.check or []:
    checklist += "\n- [ ] " + one_line(check, "check")
  known = args.known or ""
  plan = args.plan or ""
  if known == "-":
    known = sys.stdin.read()
  else:
    one_line(known, "known")
  if plan == "-":
    plan = sys.stdin.read()
  else:
    one_line(plan, "plan")
  for value in (known, plan):
    if any(line.startswith("## ") for line in value.splitlines()):
      raise RecordsError("section text cannot contain headings", 2)
  body = ("## Known\n\n" + known + "\n\n## Plan\n\n" + plan +
          "\n\n## Done when\n\n" + checklist +
          "\n\n## Log\n")
  if args.from_design:
    fields["links"] = "doc:" + one_line(args.from_design)
  body = append_log(body, agent, "Created task")
  edits = {relative: put_task(fields, ORDER, body),
           ".next-id": (str(number + 1) + "\n").encode()}
  validate_task_post(root, edits)
  mutate(root, edits,
         "Create " + ident, agent, push)
  print(ident)


def associations(root, ident):
  if root is None:
    raise RecordsError("changelog directory is not configured", 2)
  found = []
  for path in sorted((root / "entries").glob("*.md")) + sorted(
      (root / "mistakes").glob("*.md")):
    fields, _, _ = parse_document(path.read_bytes())
    if ident in comma(fields.get("tasks", "")):
      found.append((path, fields))
  return found


def checklist(body):
  match = re.search(r"(?s)## Done when\n(.*?)\n## Log", body)
  if not match:
    raise RecordsError("missing completion checklist", 2)
  return match, [line for line in match.group(1).splitlines()
                 if line.startswith("- [")]


def close_ready(fields, body, changes, ident, state):
  if state == "done":
    _, lines = checklist(body)
    if len(lines) < 4:
      raise RecordsError("fixed checklist is incomplete", 1)
    for index, (key, _) in enumerate(FIXED):
      if not lines[index].startswith("- [x] " + key + ":"):
        raise RecordsError("checklist item is not done: " + key, 1)
    for line in lines:
      if not line.startswith("- [x] ") or not (
          " -- evidence: " in line or ": n/a -- " in line):
        raise RecordsError("checklist needs evidence or n/a reason", 1)
    if fields.get("produces-changes") == "yes":
      for index in (0, 3):
        if ": n/a -- " in lines[index]:
          raise RecordsError("merged and work-reviewed need evidence", 1)
    if fields.get("review") not in ("n/a",) and not fields.get(
        "review", "").startswith("passed "):
      raise RecordsError("review is not passed", 1)
    if fields.get("blocked-on-owner") != "no":
      raise RecordsError("blocked on owner", 1)
  open_records = [(p, f) for p, f in associations(changes, ident)
                  if f.get("status") not in ("closed", "mitigated", "resolved")]
  return open_records


def alter_task(root, args, agent, push, changes=None):
  path = task_path(root, args.task)
  fields, order, body = read_task(path)
  original = dict(fields)
  command = args.command
  force = getattr(args, "force", None)
  forced_action = False
  if command != "reopen" and fields["status"] in CLOSED:
    raise RecordsError("task is closed; reopen first", 1)
  if command == "claim":
    owner = live_owner(fields)
    if owner and owner != agent:
      forced_action = permission(fields, agent, command, force)
    old = fields.get("owner", "none")
    old_expiry = fields.get("expires", "")
    hours = duration(args.hours, "hours")
    if hours > 24:
      raise RecordsError("claim is limited to 24 hours", 2)
    fields["owner"] = agent
    fields["expires"] = stamp(max(
      parse_time(old_expiry) if owner == agent else now(),
      now() + timedelta(hours=hours)))
    if owner != agent:
      fields["helpers"] = ""
    if fields["status"] in ("open", "blocked") and owner is None:
      fields["status"] = "in-progress"
    takeover = old != "none" and (owner is None or forced_action)
    body = append_log(body, agent, "Claimed task" +
                      (" from " + old + " (expiry " + old_expiry + ")"
                       if takeover else ""))
  elif command == "release":
    forced_action = permission(fields, agent, command, force)
    fields["status"] = args.status or "open"
    fields["owner"] = "none"
    fields["expires"] = ""
    fields["helpers"] = ""
    body = append_log(body, agent, "Released task: " + one_line(args.note))
  elif command == "handoff":
    if fields["owner"] == "none":
      raise RecordsError("claim before handoff", 1)
    if not re.fullmatch(r"[A-Za-z0-9._@:-]{1,64}", args.to):
      raise RecordsError("invalid handoff agent ID", 2)
    if live_owner(fields):
      forced_action = permission(fields, agent, command, force)
    old = fields["owner"]
    fields["owner"] = one_line(args.to)
    fields["expires"] = stamp(now() + timedelta(hours=2))
    fields["helpers"] = ""
    body = append_log(body, agent, "Handed off from " + old + " to " +
                      args.to + ": " + one_line(args.note))
  elif command == "helper":
    if not re.fullmatch(r"[A-Za-z0-9._@:-]{1,64}", args.helper_id):
      raise RecordsError("invalid helper ID", 2)
    forced_action = permission(fields, agent, command, force)
    helpers = comma(fields.get("helpers", ""))
    if args.action == "add":
      helpers.append(args.helper_id)
    else:
      helpers = [x for x in helpers if x != args.helper_id]
    fields["helpers"] = joined(helpers)
    body = append_log(body, agent, args.action + " helper " + args.helper_id)
  elif command == "status":
    old = fields["status"]
    new = args.state
    if new in CLOSED:
      raise RecordsError("use close for a closed status", 1)
    if old == "open" and new != "blocked":
      raise RecordsError("claim before in-progress or in-review", 1)
    if old == "open" and new == "open":
      raise RecordsError("open task is already open", 1)
    if old == "blocked" and live_owner(fields) is None and new != "open":
      raise RecordsError("claim before in-progress", 1)
    if old == "blocked" and live_owner(fields) and new == "open":
      raise RecordsError("use release to open an owned task", 1)
    if old == "blocked" and live_owner(fields) is None and new == "blocked":
      raise RecordsError("blocked task is already blocked", 1)
    if old in ("in-progress", "in-review") and live_owner(fields) is None:
      raise RecordsError("claim expired task before changing status", 1)
    if old in ("in-progress", "in-review") and new == "open":
      raise RecordsError("use release", 1)
    if live_owner(fields):
      forced_action = permission(fields, agent, command, force, helper=True)
    if new in ("blocked", "open") and not args.reason:
      raise RecordsError("--reason is required", 2)
    fields["status"] = new
    if new == "open":
      fields["owner"] = "none"
      fields["expires"] = ""
      fields["helpers"] = ""
    body = append_log(body, agent, "Status " + old + " -> " + new +
                      (": " + one_line(args.reason) if args.reason else ""))
  elif command == "set":
    no_claim = live_owner(fields) is None
    forced_action = permission(fields, agent, command, force, helper=True,
                               unowned=True)
    if args.review is not None or args.blocked_on_owner == "yes":
      if no_claim and not force:
        raise RecordsError("unowned review or owner block needs --force", 1)
      if no_claim and force:
        forced_action = True
    restricted = any(x is not None for x in (
      args.title, args.priority, args.severity, args.review,
      args.blocked_on_owner, args.produces_changes, args.add_repo,
      args.remove_repo, args.add_check, args.remove_check))
    if restricted and live_owner(fields) != agent and not no_claim:
      forced_action = permission(fields, agent, command, force)
    for key, value in (("title", args.title), ("priority", args.priority),
                       ("severity", args.severity), ("review", args.review),
                       ("produces-changes", args.produces_changes)):
      if value is not None:
        fields[key] = one_line(value, key)
    if args.review is not None and args.review not in (
        "n/a", "required", "pending") and not re.fullmatch(
          r"(?:passed|failed) .+", args.review):
      raise RecordsError("invalid review value", 2)
    if args.produces_changes == "no":
      if any(x.startswith("pr:") for x in comma(fields.get("links", ""))):
        raise RecordsError("demote PR links before no-changes", 1)
      if not args.reason:
        raise RecordsError("--reason required for no-changes", 2)
    if args.blocked_on_owner:
      fields["blocked-on-owner"] = (
        "no" if args.blocked_on_owner == "no" else
        "yes -- verified: " + one_line(args.verified or ""))
      if args.blocked_on_owner == "yes" and not args.verified:
        raise RecordsError("--verified required", 2)
    repos = comma(fields.get("repos", ""))
    if args.add_repo:
      repos.append(args.add_repo)
    if args.remove_repo:
      repos = [x for x in repos if x != args.remove_repo]
    fields["repos"] = joined(repos)
    for heading, value in (("Known", args.known), ("Plan", args.plan)):
      if value is not None:
        if value == "-":
          value = sys.stdin.read()
        else:
          one_line(value, heading.lower())
        if any(line.startswith("## ") for line in value.splitlines()):
          raise RecordsError("section text cannot contain headings", 2)
        marker = "## " + heading + "\n"
        if marker not in body:
          raise RecordsError("missing section " + heading, 2)
        start = body.index(marker) + len(marker)
        end = body.find("\n## ", start)
        if end < 0:
          raise RecordsError("missing section after " + heading, 2)
        section = body[start:end].strip("\n")
        body = (body[:start] + "\n" + section +
                ("\n\n" if section and value else "") + value +
                "\n" + body[end:])
    if args.add_check:
      body = body.replace("\n## Log", "\n- [ ] " +
                          one_line(args.add_check) + "\n\n## Log", 1)
    if args.remove_check:
      match, lines = checklist(body)
      index = args.remove_check - 1
      if index < 4 or index >= len(lines):
        raise RecordsError("cannot remove fixed or missing check", 1)
      body = body.replace(lines[index] + "\n", "", 1)
    body = append_log(body, agent, "Set task fields" +
                      (": " + args.reason if args.reason else ""))
  elif command == "check":
    forced_action = permission(fields, agent, command, force, helper=True)
    match, lines = checklist(body)
    index = (int(args.item) - 1 if args.item.isdigit() else next(
      (i for i, line in enumerate(lines) if line.startswith(
        "- [ ] " + args.item + ":") or line.startswith(
        "- [x] " + args.item + ":")), -1))
    if not 0 <= index < len(lines):
      raise RecordsError("checklist item not found", 1)
    if bool(args.evidence) == bool(args.na):
      raise RecordsError("give --evidence or --na", 2)
    if args.na and fields.get("produces-changes") == "yes" and index in (0, 3):
      raise RecordsError("this item needs evidence", 1)
    line = lines[index]
    content = line[6:]
    fixed = next(((key, label) for key, label in FIXED
                  if content.startswith(key + ":")), None)
    key = (fixed[0] if fixed else
           content.split(" -- evidence: ", 1)[0].split(": n/a -- ", 1)[0])
    if args.na:
      replacement = "- [x] " + key + ": n/a -- " + one_line(args.na)
    else:
      base = (key + ": " + fixed[1] if fixed else key)
      replacement = ("- [x] " + base + " -- evidence: " +
                     one_line(args.evidence))
    body = body.replace(line, replacement, 1)
    body = append_log(body, agent, "Checked " + key)
  elif command == "link":
    forced_action = permission(fields, agent, command, force, helper=True,
                               unowned=True)
    links = comma(fields.get("links", ""))
    if args.pr:
      links.append("pr:" + one_line(args.pr))
      fields["produces-changes"] = "yes"
    elif args.ref:
      links.append("ref:" + one_line(args.ref))
    elif args.doc:
      links.append("doc:" + one_line(args.doc))
    elif args.demote:
      if not args.reason:
        raise RecordsError("--reason required", 2)
      if "pr:" + args.demote not in links:
        raise RecordsError("PR link not found", 1)
      links.remove("pr:" + args.demote)
      links.append("ref:" + args.demote)
    elif args.remove:
      if not args.reason:
        raise RecordsError("--reason required", 2)
      if args.remove in comma(fields.get("related", "")):
        other = task_path(root, args.remove)
        ofields, oorder, obody = read_task(other)
        other_forced = False
        if ofields["status"] in LIVE:
          other_forced = permission(ofields, agent, command, force,
                                    helper=True, unowned=True)
        fields["related"] = joined(
          x for x in comma(fields["related"]) if x != args.remove)
        ofields["related"] = joined(
          x for x in comma(ofields.get("related", "")) if x != args.task)
        body = append_log(body, agent, "Removed relation to " +
                          args.remove + ": " + args.reason)
        obody = append_log(obody, agent, "Removed relation to " +
                           args.task + ": " + args.reason)
        if force and forced_action:
          body = forced(body, agent, force)
        if force and other_forced:
          obody = forced(obody, agent, force)
        if force and not (forced_action or other_forced):
          raise RecordsError("--force does not apply to this action", 2)
        heartbeat(fields, agent)
        edits = {
          str(path.relative_to(root)): put_task(fields, order, body),
          str(other.relative_to(root)): put_task(ofields, oorder, obody)}
        validate_task_post(root, edits)
        mutate(root, edits, "Unlink " + args.task, agent, push)
        return
      if not any(x.split(":", 1)[-1] == args.remove for x in links):
        raise RecordsError("link not found", 1)
      links = [x for x in links if x.split(":", 1)[-1] != args.remove]
    elif args.related:
      if args.related == args.task:
        raise RecordsError("task cannot relate to itself", 2)
      other = task_path(root, args.related)
      ofields, oorder, obody = read_task(other)
      other_forced = False
      if ofields["status"] in LIVE:
        other_forced = permission(ofields, agent, command, force,
                                  helper=True, unowned=True)
      fields["related"] = joined(comma(fields.get("related", "")) +
                                 [args.related])
      ofields["related"] = joined(comma(ofields.get("related", "")) +
                                  [args.task])
      obody = append_log(obody, agent, "Related to " + args.task)
      body = append_log(body, agent, "Related to " + args.related)
      heartbeat(fields, agent)
      if force and forced_action:
        body = forced(body, agent, force)
      if force and other_forced:
        obody = forced(obody, agent, force)
      if force and not (forced_action or other_forced):
        raise RecordsError("--force does not apply to this action", 2)
      edits = {str(path.relative_to(root)): put_task(fields, order, body),
               str(other.relative_to(root)):
               put_task(ofields, oorder, obody)}
      validate_task_post(root, edits)
      mutate(root, edits, "Link " + args.task, agent, push)
      return
    fields["links"] = joined(links)
    body = append_log(body, agent, "Updated links" +
                      (": " + args.reason if args.reason else ""))
  elif command == "log":
    text = args.text
    if args.request == "helper":
      text = "Request helper: " + text
    owner = live_owner(fields)
    if ((owner and owner != agent and agent not in comma(
        fields.get("helpers", ""))) or
        (owner is None and fields.get("owner") != "none")):
      text += " (not owner)"
    recipients = (comma(args.to) if args.to else
                  [owner] if args.request == "helper" and owner else None)
    body = append_log(body, agent, text, recipients)
  elif command == "reopen":
    if fields["status"] not in CLOSED:
      raise RecordsError("task is already live", 1)
    old = fields["closed"]
    fields["status"] = "open"
    fields["owner"] = "none"
    fields["expires"] = ""
    fields["helpers"] = ""
    fields["closed"] = ""
    body = append_log(body, agent, "Reopened from " + old + ": " +
                      one_line(args.reason))
  else:
    raise RecordsError("unsupported task operation", 2)
  if force and forced_action:
    body = forced(body, agent, force)
  elif force:
    raise RecordsError("--force does not apply to this action", 2)
  if command not in ("release", "handoff", "close", "reopen"):
    heartbeat(fields, agent)
  relative = str(path.relative_to(root))
  edits = {relative: None} if command == "reopen" else {}
  target = path.name if command == "reopen" else relative
  edits[target] = put_task(fields, order, body)
  validate_task_post(root, edits)
  mutate(root, edits, command.capitalize() + " " + args.task, agent, push)


def close_task(root, changes, args, agent, push):
  path = task_path(root, args.task)
  fields, order, body = read_task(path)
  if fields["status"] in CLOSED:
    raise RecordsError("task is already closed", 1)
  if args.state not in CLOSED:
    raise RecordsError("invalid close state", 2)
  if args.state != "done" and not args.reason:
    raise RecordsError("--reason required", 2)
  if args.state == "superseded" and not args.by:
    raise RecordsError("superseded requires --by", 2)
  if args.state == "done" and args.force:
    raise RecordsError("done cannot be forced; use cancelled or superseded "
                       "with a reason", 2)
  overridden = permission(fields, agent, "close", args.force)
  if args.force and not overridden:
    raise RecordsError("--force does not apply to this action", 2)
  open_records = close_ready(fields, body, changes, args.task, args.state)
  target_id = args.transfer or args.by
  if open_records and args.state == "done":
    raise RecordsError("open associated records: " +
                       ", ".join(p.name for p, _ in open_records), 1)
  if open_records and not target_id:
    raise RecordsError("open records require --transfer or closure", 1)
  task_edits = {}
  change_edits = {}
  if target_id:
    if target_id == args.task:
      raise RecordsError("cannot transfer to same task", 2)
    target_path = task_path(root, target_id, archived=False)
    target, target_order, target_body = read_task(target_path)
    if target["status"] in CLOSED:
      raise RecordsError("transfer target is archived", 1)
    transferred = []
    from agent_records_changelog import ENTRY_ORDER, MISTAKE_ORDER
    for record_path, record in open_records:
      record_fields, record_order, record_body = parse_document(
        record_path.read_bytes())
      record_fields["tasks"] = joined(
        target_id if value == args.task else value
        for value in comma(record_fields.get("tasks", "")))
      relative = str(record_path.relative_to(changes))
      canonical = (ENTRY_ORDER if record_path.parent.name == "entries"
                   else MISTAKE_ORDER)
      change_edits[relative] = render_document(
        record_fields, list(canonical) + [k for k in record_order
                                          if k not in canonical], record_body)
      transferred.append(relative)
    if transferred:
      target_body = append_log(target_body, agent,
                               "Transferred from " + args.task + ": " +
                               ", ".join(transferred) + "; reason: " +
                               (args.reason or "superseded"))
      task_edits[str(target_path.relative_to(root))] = put_task(
        target, target_order, target_body)
  fields["status"] = args.state
  fields["owner"] = "none"
  fields["expires"] = ""
  fields["helpers"] = ""
  fields["closed"] = (stamp() + " by " + agent + " -- " + args.state +
                      ": " + one_line(args.reason or "completed"))
  body = append_log(body, agent, "Closed " + args.state + ": " +
                    (args.reason or "completed"))
  if overridden:
    body = forced(body, agent, args.force)
  task_edits[str(path.relative_to(root))] = None
  task_edits["archive/" + path.name] = put_task(fields, order, body)
  validate_task_post(root, task_edits)
  mutate_cross(root, changes, task_edits, change_edits,
               "Close " + args.task, agent, push)


def format_task(fields):
  remaining = ""
  if fields.get("owner") != "none" and fields.get("expires"):
    expiry = parse_time(fields["expires"])
    remaining = (" EXPIRED" if expiry <= now() else
                 " " + str(int((expiry - now()).total_seconds() // 60)) + "m")
  return (fields["id"] + " " + fields["priority"] + " " + fields["status"] +
          " " + fields["owner"] + remaining + " " + fields["title"])


def task_read(root, changes, args, authoritative=True):
  command = args.command
  if command in ("show", "watch"):
    path = task_path(root, args.task)
    fields, _, body = read_task(path)
    related = associations(changes, args.task) if command == "show" else []
    if command == "show":
      lines = log_lines(body)
      if args.after:
        if not re.fullmatch(r"\d+:[0-9a-f]{16}", args.after):
          raise RecordsError("invalid cursor", 2)
        n = int(args.after.split(":", 1)[0])
        if cursor(lines[:n]) != args.after:
          raise RecordsError("log rewritten since cursor", 5)
        if args.no_messages:
          body = body[:body.index("## Log")]
        else:
          body = (body[:body.index("## Log")] + "## Log\n" +
                  "\n".join(lines[n:]) + "\n")
      elif args.no_messages:
        body = body[:body.index("## Log")]
      if args.json:
        print(json.dumps({"fields": fields, "body": body,
                          "changes": [{"path": str(p.relative_to(changes)),
                                       "status": f.get("status")}
                                      for p, f in related],
                          "cursor": cursor(lines),
                          "authoritative": authoritative}))
      else:
        prefix = "" if authoritative else "UNVERIFIED "
        for line in (put_task(fields, ORDER, body).decode().rstrip() +
                     "\n" + "\n".join(str(p.relative_to(changes)) +
                     " " + f.get("status", "") for p, f in related) +
                     "\ncursor: " + cursor(lines)).splitlines():
          print(prefix + line)
    else:
      return fields, body
  elif command in ("list", "next"):
    repos = list(getattr(args, "repo", None) or [])
    if getattr(args, "here", False):
      here = repo_key()
      if not here:
        raise RecordsError("--here requires a git checkout", 2)
      repos.append(here)
    rows = []
    for path in task_files(root, args.archived or args.all if command == "list"
                           else False):
      fields, _, _ = read_task(path)
      if command == "list":
        if args.status and fields["status"] != args.status:
          continue
        if args.owner and fields["owner"] != args.owner:
          continue
        if args.unowned and live_owner(fields):
          continue
        if args.archived and path.parent.name != "archive":
          continue
      else:
        if live_owner(fields) or fields["status"] == "blocked" or fields[
            "review"].split(" ")[0] in ("required", "pending", "failed"):
          continue
      record_repos = comma(fields.get("repos", ""))
      if repos and not any((r == "none" and not record_repos) or
                           r in record_repos for r in repos):
        continue
      rows.append(fields)
    rows.sort(key=lambda f: (("P0", "P1", "P2", "P3", "unset").index(
      f.get("priority", "unset")), f["id"]))
    if command == "next":
      rows = rows[:1]
    if getattr(args, "json", False):
      print(json.dumps({"authoritative": authoritative, "tasks": rows}))
    else:
      for fields in rows:
        print(("" if authoritative else "UNVERIFIED ") + format_task(fields))
        if getattr(args, "with_changes", False):
          for path, change in associations(changes, fields["id"]):
            print(("" if authoritative else "UNVERIFIED ") + "  " +
                  str(path.relative_to(changes)) + " " + change["status"])


def watch_task(root, changes, args):
  timeout = duration(args.timeout, "timeout")
  interval = duration(args.interval, "interval")
  if interval < 1:
    raise RecordsError("interval must be at least 1 second", 2)
  if args.after and not re.fullmatch(r"\d+:[0-9a-f]{16}", args.after):
    raise RecordsError("invalid cursor", 2)
  if args.until and not (
      args.until in ("closed", "message", "change") or
      re.fullmatch(r"status=(?:open|in-progress|in-review|blocked|done|"
                   r"cancelled|abandoned|superseded)", args.until) or
      re.fullmatch(r"owner=[A-Za-z0-9._@:-]{1,64}", args.until)):
    raise RecordsError("invalid --until condition", 2)
  if args.until == "message" and not args.for_id:
    raise RecordsError("message requires --for", 2)
  deadline = time.monotonic() + timeout
  previous = args.after
  previous_fields = None
  while True:
    with record_locks(root, changes, wait=args.wait,
                      unlocked=args.unlocked) as authoritative:
      fields, body = task_read(root, changes, args, authoritative)
      lines = log_lines(body)
      printed_cursor = False
      if previous:
        count = int(previous.split(":", 1)[0])
        if cursor(lines[:count]) != previous:
          prefix = "" if authoritative else "UNVERIFIED "
          print(prefix + "log rewritten since cursor")
          for line in lines:
            print(prefix + line)
          print(prefix + "cursor: " + cursor(lines))
          return 5
      else:
        count = len(lines)
        print(("" if authoritative else "UNVERIFIED ") +
              "cursor: " + cursor(lines))
        printed_cursor = True
      new_lines = lines[count:]
      changed = []
      if previous_fields is not None:
        changed = [key for key in fields if fields.get(key) !=
                   previous_fields.get(key)]
        for key in changed:
          if not args.for_id or key in ("status", "owner", "helpers", "closed"):
            print(("" if authoritative else "UNVERIFIED ") + args.task +
                  " " + key + ": " + fields.get(key, ""))
      for line in new_lines:
        if not args.for_id or addressed(line, args.for_id):
          print(("" if authoritative else "UNVERIFIED ") +
                args.task + " " + line)
      current = cursor(lines)
      until = args.until
      met = ((until == "closed" and fields["status"] in CLOSED) or
             (until and until.startswith("status=") and
              fields["status"] == until[7:]) or
             (until and until.startswith("owner=") and
              fields["owner"] == until[6:]) or
             (until == "change" and bool(new_lines or changed)) or
             (until == "message" and any(
               addressed(line, args.for_id)
               for line in new_lines)))
      if met and (authoritative or until in ("change", "message")):
        if not printed_cursor:
          print(("" if authoritative else "UNVERIFIED ") +
                "cursor: " + current)
        return 0
      previous = current
      previous_fields = dict(fields)
    if time.monotonic() >= deadline:
      print(("" if authoritative else "UNVERIFIED ") +
            "cursor: " + previous)
      return 4
    time.sleep(min(interval, deadline - time.monotonic()))


def validate_task_post(root, edits):
  errors = lint_tasks(root, overrides=edits)
  if errors:
    raise RecordsError("task post-state fails lint: " +
                       "; ".join(errors), 1)


def lint_tasks(root, changes=None, overrides=None):
  overrides = overrides or {}
  errors = lint_layout(root, "tasks")
  highest = 0
  paths = {p.relative_to(root).as_posix(): p
           for p in task_files(root, True)}
  for relative, data in overrides.items():
    if relative == ".next-id":
      continue
    if data is None:
      paths.pop(relative, None)
    else:
      paths[relative] = root / relative
  virtual = list(paths.values())
  def read_current(path):
    relative = path.relative_to(root).as_posix()
    return read_task(path, overrides.get(relative))

  for path in sorted(virtual):
    try:
      fields, _, body = read_current(path)
      for key in ORDER:
        if key not in fields:
          errors.append(str(path) + ": missing " + key)
      highest = max(highest, int(fields["id"][2:]))
      if fields.get("status") not in LIVE + CLOSED:
        errors.append(str(path) + ": invalid status")
      if fields["status"] in CLOSED and path.parent.name != "archive":
        errors.append(str(path) + ": closed task is not archived")
      if fields["status"] in LIVE and path.parent.name == "archive":
        errors.append(str(path) + ": live task is archived")
      if fields.get("priority") not in ("unset", "P0", "P1", "P2", "P3"):
        errors.append(str(path) + ": invalid priority")
      if fields.get("severity") not in (
          "unset", "n/a", "S1", "S2", "S3", "S4"):
        errors.append(str(path) + ": invalid severity")
      if fields.get("produces-changes") not in ("yes", "no"):
        errors.append(str(path) + ": invalid produces-changes")
      review = fields.get("review", "")
      if review not in ("n/a", "required", "pending") and not re.fullmatch(
          r"(?:passed|failed) .+", review):
        errors.append(str(path) + ": invalid review")
      block = fields.get("blocked-on-owner", "")
      if block != "no" and not re.fullmatch(
          r"yes -- verified: .+", block):
        errors.append(str(path) + ": invalid blocked-on-owner")
      if fields["status"] == "open" and fields["owner"] != "none":
        errors.append(str(path) + ": open task has owner")
      if fields["owner"] == "none" and (fields.get("expires") or
                                        fields.get("helpers")):
        errors.append(str(path) + ": unowned task has claim fields")
      if fields["owner"] != "none" and not fields.get("expires"):
        errors.append(str(path) + ": owned task lacks expiry")
      if fields["owner"] != "none" and not re.fullmatch(
          r"[A-Za-z0-9._@:-]{1,64}", fields["owner"]):
        errors.append(str(path) + ": invalid owner")
      if any(not re.fullmatch(r"[A-Za-z0-9._@:-]{1,64}", helper)
             for helper in comma(fields.get("helpers", ""))):
        errors.append(str(path) + ": invalid helper")
      if fields["status"] in ("in-progress", "in-review") and fields[
          "owner"] == "none":
        errors.append(str(path) + ": owned status lacks owner")
      if fields["status"] in CLOSED and not fields.get("closed"):
        errors.append(str(path) + ": missing closed field")
      if fields["status"] in LIVE and fields.get("closed"):
        errors.append(str(path) + ": live task has closed field")
      _, checks = checklist(body)
      for index, (key, label) in enumerate(FIXED):
        if index >= len(checks) or not re.match(
            r"- \[[ x]\] " + re.escape(key) + r": ", checks[index]):
          errors.append(str(path) + ": missing fixed check " + key)
      if fields.get("expires"):
        parse_time(fields["expires"])
      if fields.get("created"):
        parse_time(fields["created"].split(" by ", 1)[0])
      for link in comma(fields.get("links", "")):
        if not re.fullmatch(r"(?:pr|ref|doc):.+", link):
          errors.append(str(path) + ": invalid link")
      for line in log_lines(body):
        prefix, separator, message = line[2:].partition(" -- ")
        match = re.fullmatch(
          r"(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d [+-]\d{4}) "
          r"[A-Za-z0-9._@:-]{1,64}"
          r"(?: -> [A-Za-z0-9._@:-]{1,64}"
          r"(?:,[A-Za-z0-9._@:-]{1,64})*)?", prefix)
        if not separator or not message or not match:
          errors.append(str(path) + ": invalid log line")
        else:
          try:
            parse_time(match.group(1))
          except RecordsError:
            errors.append(str(path) + ": invalid log timestamp")
      for related in comma(fields.get("related", "")):
        try:
          matches = [p for p in virtual
                     if p.name.startswith(related + "-")]
          if len(matches) != 1:
            raise RecordsError("missing related task", 1)
          other, _, _ = read_current(matches[0])
          if fields["id"] not in comma(other.get("related", "")):
            errors.append(str(path) + ": asymmetric related")
        except RecordsError:
          errors.append(str(path) + ": missing related task")
    except (RecordsError, KeyError) as exc:
      errors.append(str(path) + ": " + str(exc))
  try:
    counter_raw = overrides.get(".next-id")
    counter = int((counter_raw.decode() if counter_raw is not None else
                   (root / ".next-id").read_text()).strip())
    if counter <= highest:
      errors.append(".next-id must exceed every task ID")
  except (OSError, ValueError):
    errors.append("invalid .next-id")
  return errors
