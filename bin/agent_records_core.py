"""Shared storage, git, locking and parsing for agent records."""

from __future__ import annotations

import base64
import contextvars
import fcntl
import hashlib
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import traceback
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path


EXIT_TEXT = (
  "Exit codes: 0 success; 1 refused with records unchanged; 2 usage or "
  "configuration; 3 committed locally but push failed; 4 watch timeout; "
  "5 recovery needed."
)
FORCE_TEXT = (
  "For use only on the explicit instruction of the human owner; REASON "
  "must quote or cite that instruction. It never overrides close "
  "preconditions; choose cancelled or superseded with a reason."
)
ID_RE = re.compile(r"[A-Za-z0-9._@:-]{1,64}\Z")
TIME_RE = re.compile(r"\d{4}-\d\d-\d\d \d\d:\d\d(?::\d\d)? [+-]\d{4}\Z")
LOCK_WAIT = 400.0
GIT_TIMEOUT = 120.0
PUSH_TIMEOUT = 60.0
HELD_LOCKS = contextvars.ContextVar("records_lock_fds", default=())


class RecordsError(Exception):
  def __init__(self, message, code=1):
    super().__init__(message)
    self.code = code


def now():
  return datetime.now().astimezone()


def stamp(value=None):
  return (value or now()).strftime("%Y-%m-%d %H:%M:%S %z")


def parse_time(value):
  if not TIME_RE.fullmatch(value):
    raise RecordsError("invalid timestamp: " + value, 2)
  for fmt in ("%Y-%m-%d %H:%M:%S %z", "%Y-%m-%d %H:%M %z"):
    try:
      return datetime.strptime(value, fmt)
    except ValueError:
      pass
  raise RecordsError("invalid timestamp: " + value, 2)


ANNOTATED_DATE_RE = re.compile(
  r"(?P<time>\d{4}-\d{2}-\d{2}(?: [^()]*?)?)(?: \([^()\n]+\))?")


def parse_record_date(value):
  """Parse a changelog date, which may carry a trailing (note) annotation.

  Hand-written records mark reconstructed times, for example
  `2026-01-01 09:00 -0800 (inferred)`, and older ones give only a date.
  Task timestamps stay strict; see parse_time.
  """
  match = ANNOTATED_DATE_RE.fullmatch(value)
  if not match:
    raise RecordsError("invalid timestamp: " + value, 2)
  text = match.group("time")
  if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
    try:
      return datetime.strptime(text, "%Y-%m-%d")
    except ValueError:
      raise RecordsError("invalid timestamp: " + value, 2) from None
  return parse_time(text)


def one_line(value, name="text"):
  if not isinstance(value, str):
    raise RecordsError(name + " must be text", 2)
  if "\n" in value or "\r" in value:
    raise RecordsError(name + " must be one line", 2)
  return value


def duration(value, name="duration"):
  try:
    number = float(value)
  except (TypeError, ValueError):
    raise RecordsError(name + " must be positive and finite", 2)
  if not 0 < number < float("inf"):
    raise RecordsError(name + " must be positive and finite", 2)
  return number


def config():
  root = Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config")))
  path = root / "agent-kit" / "records.json"
  try:
    data = json.loads(path.read_text(encoding="utf-8"))
  except FileNotFoundError:
    return {}
  except (OSError, ValueError) as exc:
    raise RecordsError("invalid config: " + str(exc), 2)
  if not isinstance(data, dict):
    raise RecordsError("config must be a JSON object", 2)
  if data.get("estimate_policy", "off") not in ("off", "warn", "require"):
    raise RecordsError("estimate_policy must be off, warn or require", 2)
  for key in ("tasks_dir", "changelog_dir", "machine"):
    if key in data and not isinstance(data[key], str):
      raise RecordsError(key + " must be text", 2)
  if "push" in data:
    value = data["push"]
    if isinstance(value, dict):
      if any(key not in ("tasks", "changelog") or
             not isinstance(flag, bool) for key, flag in value.items()):
        raise RecordsError(
          "push must be true, false, or an object mapping tasks and "
          "changelog to true or false", 2)
    elif not isinstance(value, bool):
      raise RecordsError(
        "push must be true, false, or an object mapping tasks and "
        "changelog to true or false", 2)
  if "repos" in data:
    repos = data["repos"]
    if not isinstance(repos, dict) or any(
        not isinstance(path, str) or not isinstance(key, str)
        for path, key in repos.items()):
      raise RecordsError("repos must map paths to keys", 2)
  return data


def directory(kind, explicit=None, cfg=None):
  cfg = cfg if cfg is not None else config()
  env = "AGENT_TASKS_DIR" if kind == "tasks" else "AGENT_CHANGELOG_DIR"
  key = "tasks_dir" if kind == "tasks" else "changelog_dir"
  value = explicit or os.environ.get(env) or cfg.get(key)
  return Path(value).expanduser().resolve() if value else None


def machine(cfg=None):
  cfg = cfg if cfg is not None else config()
  if os.environ.get("AGENT_MACHINE"):
    return one_line(os.environ["AGENT_MACHINE"], "machine"), "environment"
  if cfg.get("machine"):
    return one_line(cfg["machine"], "machine"), "config"
  return socket.gethostname().split(".")[0], "hostname -s"


def require_agent(value):
  value = value or os.environ.get("AGENT_ID")
  if not value or not ID_RE.fullmatch(value):
    raise RecordsError("mutations require --agent ID or AGENT_ID", 2)
  return value


def session_id():
  for key, label in (("CLAUDE_CODE_SESSION_ID", "claude"),
                     ("CODEX_THREAD_ID", "codex"),
                     ("CODEX_SESSION_ID", "codex")):
    if os.environ.get(key):
      value = os.environ[key]
      return label + "-" + hashlib.sha256(value.encode()).hexdigest()[:16], key
  return None, None


def state_path(name):
  root = Path(os.environ.get(
    "XDG_STATE_HOME", str(Path.home() / ".local/state")))
  return root / "agent-kit" / name


def private_append(path, line):
  path.parent.mkdir(parents=True, exist_ok=True)
  fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
  try:
    fcntl.flock(fd, fcntl.LOCK_EX)
    os.fchmod(fd, 0o600)
    data = line.encode("utf-8")
    while data:
      data = data[os.write(fd, data):]
    os.fsync(fd)
  finally:
    fcntl.flock(fd, fcntl.LOCK_UN)
    os.close(fd)


def atomic(path, data, mode=None):
  path = Path(path)
  path.parent.mkdir(parents=True, exist_ok=True)
  fd, name = tempfile.mkstemp(prefix=".records-tmp-", dir=path.parent)
  try:
    if mode is not None:
      os.fchmod(fd, mode)
    with os.fdopen(fd, "wb") as stream:
      stream.write(data)
      stream.flush()
      os.fsync(stream.fileno())
    os.replace(name, path)
    dir_fd = os.open(path.parent, os.O_RDONLY)
    try:
      os.fsync(dir_fd)
    finally:
      os.close(dir_fd)
  finally:
    if os.path.exists(name):
      os.unlink(name)


def create_exclusive(path, data):
  path = Path(path)
  path.parent.mkdir(parents=True, exist_ok=True)
  fd, name = tempfile.mkstemp(prefix=".records-tmp-", dir=path.parent)
  try:
    with os.fdopen(fd, "wb") as stream:
      stream.write(data)
      stream.flush()
      os.fsync(stream.fileno())
    os.link(name, path)
  finally:
    os.unlink(name)


def git(root, *args, timeout=GIT_TIMEOUT, check=True):
  if not shutil.which("git"):
    raise RecordsError("git is required", 2)
  if timeout == GIT_TIMEOUT and os.environ.get("AGENT_RECORDS_GIT_TIMEOUT"):
    timeout = duration(os.environ["AGENT_RECORDS_GIT_TIMEOUT"], "git timeout")
  if timeout == PUSH_TIMEOUT and os.environ.get("AGENT_RECORDS_PUSH_TIMEOUT"):
    timeout = duration(os.environ["AGENT_RECORDS_PUSH_TIMEOUT"],
                       "push timeout")
  writes_git_state = args[0] in (
    "add", "rm", "reset", "commit", "rebase", "push", "pull", "init")
  proc = subprocess.Popen(
    ["git", "-C", str(root), "-c", "core.fsmonitor=false", "-c",
     "gc.auto=0", *args], stdout=subprocess.PIPE,
    stderr=subprocess.PIPE, start_new_session=True,
    pass_fds=HELD_LOCKS.get() if writes_git_state else (),
  )
  try:
    out, err = proc.communicate(timeout=timeout)
  except subprocess.TimeoutExpired:
    os.killpg(proc.pid, signal.SIGKILL)
    proc.communicate()
    raise RecordsError("git timed out: " + " ".join(args), 5)
  if check and proc.returncode:
    raise RecordsError("git " + " ".join(args) + ": " +
                       err.decode(errors="replace").strip(), 1)
  return (proc.returncode, out.decode(errors="replace"),
          err.decode(errors="replace"))


def git_root(path):
  code, out, _ = git(path, "rev-parse", "--show-toplevel", check=False)
  return Path(out.strip()).resolve() if code == 0 else None


def validate_root(root, kind):
  if root is None:
    raise RecordsError(kind + " directory is not configured", 2)
  if not root.is_dir():
    raise RecordsError("records directory does not exist: " + str(root), 2)
  code, output, _ = git(
    root, "rev-parse", "--show-toplevel", "--path-format=absolute",
    "--git-common-dir", "--absolute-git-dir", check=False)
  if code:
    raise RecordsError("records directory is not a git repository: " +
                       str(root), 2)
  top, common_path, gitdir_path = output.splitlines()
  if Path(top).resolve() != root:
    raise RecordsError("records directory must be its git repository root", 2)
  common = Path(common_path).resolve()
  gitdir = Path(gitdir_path)
  if common != gitdir.resolve():
    raise RecordsError("records directory must be a main checkout", 2)
  marker = root / ".agent-records"
  try:
    data = json.loads(marker.read_text())
  except (OSError, ValueError):
    raise RecordsError("missing or invalid .agent-records marker", 2)
  if data.get("kind") != kind:
    raise RecordsError("wrong .agent-records kind", 2)
  return data


def allowed_path(path, kind):
  if path in ("README.md", ".agent-records", ".gitignore"):
    return True
  if kind == "tasks":
    return path == ".next-id" or bool(re.fullmatch(
      r"(?:archive/)?T-\d{4,}-[a-z0-9-]+\.md", path))
  return bool(re.fullmatch(
    r"entries/[0-9-]+-[a-z0-9-]+\.md|mistakes/[0-9-]+-[a-z0-9-]+\.md",
    path))


def lint_layout(root, kind):
  marker = json.loads((root / ".agent-records").read_text())
  allowed = marker.get("allow", [])
  errors = []
  for path in git(root, "ls-files")[1].splitlines():
    if not allowed_path(path, kind) and not any(
        path == item or path.startswith(item.rstrip("/") + "/")
        for item in allowed):
      errors.append("unexpected tracked path: " + path)
  return errors


def init_repo(root, kind, adopt=False, allow=()):
  root = Path(root).expanduser().resolve()
  if root.exists() and not root.is_dir():
    raise RecordsError("directory path is not a directory", 2)
  if any(Path(value).is_absolute() or ".." in Path(value).parts
         for value in allow):
    raise RecordsError("--allow paths must be relative to the records root", 2)
  existing = git_root(root if root.exists() else root.parent)
  staging = None
  if adopt:
    if existing != root:
      raise RecordsError("--adopt requires an existing repository root", 2)
    common = Path(git(root, "rev-parse", "--path-format=absolute",
                      "--git-common-dir")[1].strip()).resolve()
    gitdir = Path(git(root, "rev-parse", "--absolute-git-dir")[1].strip())
    if common != gitdir.resolve():
      raise RecordsError("--adopt requires a main checkout", 2)
    tracked = git(root, "ls-files")[1].splitlines()
    bad = [p for p in tracked if not allowed_path(p, kind) and
           not any(p == a or p.startswith(a.rstrip("/") + "/")
                   for a in allow)]
    if bad:
      raise RecordsError("non-record tracked files: " + ", ".join(bad), 1)
    operation_state(root)
    if active_hook(root):
      raise RecordsError("active git hook: " + active_hook(root), 2)
  else:
    if existing:
      raise RecordsError("cannot initialize inside a repository", 2)
    if root.exists() and any(root.iterdir()):
      raise RecordsError("init requires an empty directory", 2)
    root.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".records-init-", dir=root.parent))
  work = staging or root
  if (work / ".agent-records").exists():
    raise RecordsError("records directory already initialized", 1)
  try:
    if staging:
      git(work, "init", "-q")
      hook = active_hook(work)
      if hook:
        raise RecordsError("active git hook: " + hook, 2)
    marker = {"kind": kind, "allow": list(allow)}
    atomic(work / ".agent-records", (json.dumps(marker) + "\n").encode())
    ignores = (".records.lock", ".records-journal.json", ".records-tmp-*")
    prior_ignore = ((work / ".gitignore").read_text()
                    if (work / ".gitignore").exists() else "")
    added_ignore = "".join(value + "\n" for value in ignores
                           if value not in prior_ignore.splitlines())
    if added_ignore:
      atomic(work / ".gitignore", (prior_ignore + added_ignore).encode())
    if kind == "tasks":
      (work / "archive").mkdir(exist_ok=True)
      if not (work / ".next-id").exists():
        highest = max([int(p.name.split("-")[1]) for p in
                       list(work.glob("T-*-*.md")) +
                       list((work / "archive").glob("T-*-*.md"))] or [0])
        atomic(work / ".next-id", (str(highest + 1) + "\n").encode())
      readme = (
        "# Tasks\n\n"
        "One `T-0001-example-task.md` per live task; closed tasks move "
        "to `archive/`.\n\n"
        "Headers use `key: value` lines, followed by `## Known`, "
        "`## Plan`, `## Done when` and `## Log`.\n"
        "Example fields: `id: T-0001`, `status: open`, "
        "`owner: none`, `repos: example-repo`.\n"
        "`.next-id` is the next allocation candidate.\n")
    else:
      (work / "entries").mkdir(exist_ok=True)
      (work / "mistakes").mkdir(exist_ok=True)
      readme = (
        "# Changelog\n\n"
        "One entry such as "
        "`entries/2026-01-01-0900-example-repo-scratch.md` per "
        "persistent change; incidents go under `mistakes/`.\n\n"
        "Headers use `key: value` lines. Example fields: "
        "`status: open`, `repos: example-repo`, `tasks: T-0001`.\n"
        "An open record documents cleanup but never authorizes it.\n")
    if not (work / "README.md").exists():
      atomic(work / "README.md", readme.encode())
    paths = [".agent-records", ".gitignore", "README.md"]
    if kind == "tasks":
      paths.append(".next-id")
    git(work, "add", "--", *paths)
    git(work, "commit", "-m", "Initialize " + kind + " records",
        "--", *paths)
    if staging:
      os.replace(staging, root)
  except (RecordsError, OSError) as exc:
    if staging:
      try:
        shutil.rmtree(staging)
      except OSError as cleanup_error:
        raise RecordsError("init failed and scratch cleanup failed: " +
                           str(cleanup_error), 5)
      raise RecordsError(str(exc), exc.code if isinstance(
        exc, RecordsError) else 1)
    raise RecordsError(str(exc), 5)
  return root


def git_path(root, name):
  return Path(git(root, "rev-parse", "--path-format=absolute",
                  "--git-path", name)[1].strip())


def operation_state(root):
  for name in ("rebase-merge", "rebase-apply", "MERGE_HEAD",
               "CHERRY_PICK_HEAD", "BISECT_LOG"):
    if git_path(root, name).exists():
      raise RecordsError(name + " in progress; finish or abort it", 5)
  if git(root, "symbolic-ref", "-q", "HEAD", check=False)[0]:
    raise RecordsError("detached HEAD; check out a branch", 5)


HOOK_NAMES = {
  "pre-commit", "prepare-commit-msg", "commit-msg", "post-commit",
  "pre-rebase", "post-rewrite", "pre-push", "reference-transaction",
  "post-checkout", "post-merge", "pre-merge-commit",
  "pre-applypatch", "applypatch-msg", "post-applypatch",
  "post-index-change", "pre-auto-gc",
}


def active_hook(root):
  code, value, _ = git(root, "config", "--path", "--get", "core.hooksPath",
                       check=False)
  if code == 0:
    hooks = Path(value.strip())
    if not hooks.is_absolute():
      hooks = root / hooks
  else:
    hooks = git_path(root, "hooks")
  for name in HOOK_NAMES:
    path = hooks / name
    if path.is_file() and os.access(path, os.X_OK):
      return str(path)
  return None


@contextmanager
def locks(roots, writes=(), wait=LOCK_WAIT, agent="reader", operation="read",
          unlocked=False):
  held = []
  token = None
  try:
    for root in roots:
      if root is None:
        continue
      path = root / ".records.lock"
      try:
        fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
      except OSError as exc:
        if unlocked and root not in writes:
          yield False
          return
        raise RecordsError("cannot open lock; add both records directories "
                           "to sandbox writable roots: " + str(exc), 1)
      mode = fcntl.LOCK_EX if root in writes else fcntl.LOCK_SH
      deadline = time.monotonic() + wait
      while True:
        try:
          fcntl.flock(fd, mode | fcntl.LOCK_NB)
          break
        except BlockingIOError:
          if time.monotonic() >= deadline:
            os.lseek(fd, 0, os.SEEK_SET)
            holder = os.read(fd, 4096).decode(errors="replace").strip()
            os.close(fd)
            try:
              info = json.loads(holder)
              held_for = int((now() - parse_time(
                info["started"])).total_seconds())
              holder = (str(info["pid"]) + " " + info["agent"] + " " +
                        info["operation"] + " for " + str(held_for) + "s")
            except (ValueError, KeyError, TypeError, RecordsError):
              holder = holder or "another reader or writer"
            raise RecordsError("lock held by " + holder + "; retry", 1)
          time.sleep(min(0.05, deadline - time.monotonic()))
      if root in writes:
        os.ftruncate(fd, 0)
        os.write(fd, (json.dumps({"pid": os.getpid(), "agent": agent,
                                  "operation": operation,
                                  "started": stamp()}) + "\n").encode())
        os.fsync(fd)
      held.append(fd)
    token = HELD_LOCKS.set(HELD_LOCKS.get() + tuple(held))
    yield True
  finally:
    if token is not None:
      HELD_LOCKS.reset(token)
    for fd in reversed(held):
      fcntl.flock(fd, fcntl.LOCK_UN)
      os.close(fd)


def status(root):
  return git(root, "status", "--porcelain", "--untracked-files=all")[1]


def file_bytes(path):
  try:
    return Path(path).read_bytes()
  except FileNotFoundError:
    return None


def state_encode(value):
  return None if value is None else base64.b64encode(value).decode("ascii")


def state_decode(value):
  return None if value is None else base64.b64decode(value)


def head_bytes(root, relative):
  if not shutil.which("git"):
    raise RecordsError("git is required", 2)
  proc = subprocess.Popen(
    ["git", "-C", str(root), "show", "HEAD:" + relative],
    stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True,
    pass_fds=HELD_LOCKS.get())
  try:
    out, _ = proc.communicate(timeout=GIT_TIMEOUT)
  except subprocess.TimeoutExpired:
    os.killpg(proc.pid, signal.SIGKILL)
    proc.communicate()
    raise RecordsError("git show timed out", 5)
  return out if proc.returncode == 0 else None


def journal_path(root):
  return root / ".records-journal.json"


def write_journal(root, data):
  atomic(journal_path(root), (json.dumps(data, sort_keys=True) + "\n").encode())


def read_journal(root):
  path = journal_path(root)
  return json.loads(path.read_text()) if path.exists() else None


def unexpected_error(exc):
  if os.environ.get("AGENT_RECORDS_DEBUG") == "1":
    traceback.print_exc()
  else:
    message = " ".join(str(exc).splitlines())
    print("unexpected records error: " + message, file=sys.stderr)
  roots = []
  for kind in ("tasks", "changelog"):
    try:
      root = directory(kind)
      if root:
        roots.append(root)
    except Exception:
      pass
  args = sys.argv[1:]
  if "--dir" in args:
    try:
      roots.append(Path(args[args.index("--dir") + 1]))
    except IndexError:
      pass
  roots.extend(Path(arg.partition("=")[2]) for arg in args
               if arg.startswith("--dir="))
  return 5 if any(journal_path(root).exists() for root in roots) else 1


def snapshot(root):
  return status(root)


def dirty_paths(root, relatives):
  output = git(root, "status", "--porcelain", "--untracked-files=all",
               "--", *relatives)[1]
  return [line[3:] for line in output.splitlines()]


def warn_other_dirty(status_text, touched):
  for line in status_text.splitlines():
    path = line[3:]
    if path not in touched:
      print("warning: unrelated dirty path excluded: " + path,
            file=sys.stderr)


def restore(path, data):
  if data is None:
    if path.exists():
      path.unlink()
  else:
    atomic(path, data)


def check_index_lock(root):
  if git_path(root, "index.lock").exists():
    raise RecordsError("index.lock exists; inspect the git process and "
                       "remove a stale lock before recovery", 5)


def stage_paths(root, states):
  deleted = [path for path, value in states.items() if value is None]
  present = [path for path, value in states.items() if value is not None]
  if deleted:
    git(root, "rm", "-q", "--cached", "--ignore-unmatch", "--", *deleted)
  if present:
    git(root, "add", "--", *present)


def recover_single(root, journal=None, keep=(), discard=(), validator=None):
  try:
    return _recover_single(root, journal, keep, discard, validator)
  except RecordsError as exc:
    if exc.code == 1:
      raise RecordsError(str(exc), 5)
    raise


def _recover_single(root, journal, keep, discard, validator):
  journal = journal or read_journal(root)
  if not journal:
    return
  if journal.get("cross"):
    raise RecordsError("cross-directory recovery needs both directories", 5)
  check_index_lock(root)
  paths = journal["paths"]
  unknown = (set(keep) | set(discard)) - set(paths)
  if unknown:
    raise RecordsError("path not in journal: " + ", ".join(sorted(unknown)), 2)
  if single_commit_exists(root, journal):
    for relative, values in paths.items():
      before = state_decode(values["pre"])
      after = state_decode(values["post"])
      current = file_bytes(root / relative)
      if current not in (before, after):
        raise RecordsError("conflicting edit during recovery: " +
                           relative, 5)
      restore(root / relative, after)
      git(root, "reset", "-q", "--", relative)
    journal_path(root).unlink()
    return
  for relative, values in paths.items():
    path = root / relative
    before = state_decode(values["pre"])
    after = state_decode(values["post"])
    current = file_bytes(path)
    if relative in keep:
      if not path.exists():
        raise RecordsError("cannot keep absent path " + relative, 5)
      continue
    if current not in (before, after) and relative not in discard:
      raise RecordsError("conflicting edit during recovery: " + relative, 5)
    restore(path, before)
    git(root, "reset", "-q", "--", relative)
  if keep:
    if validator:
      errors = validator()
      if errors:
        raise RecordsError("cannot keep invalid record: " +
                           "; ".join(errors), 5)
    stage_paths(root, {path: file_bytes(root / path) for path in keep})
    git(root, "commit", "-m", "Recover records: " + journal["operation"],
        "--", *keep)
  current = snapshot(root)
  if current != journal["status"]:
    raise RecordsError("unjournaled changes appeared during recovery", 5)
  journal_path(root).unlink()


def single_commit_exists(root, journal):
  old = journal["head"]
  current = git(root, "rev-parse", "HEAD")[1].strip()
  if current == old:
    return False
  if git(root, "merge-base", "--is-ancestor", old, current,
         check=False)[0]:
    raise RecordsError("HEAD diverged during mutation", 5)
  output = git(root, "log", old + ".." + current, "--format=%B%x00",
               "--fixed-strings", "--grep=Records-Journal: " +
               journal["id"])[1]
  matches = [message for message in output.split("\x00")
             if any(line == "Records-Journal: " + journal["id"]
                    for line in message.splitlines())]
  if len(matches) != 1:
    raise RecordsError("cannot verify mutation commit", 5)
  if any(head_bytes(root, path) != state_decode(states["post"])
         for path, states in journal["paths"].items()):
    raise RecordsError("mutation commit differs from journal post-state", 5)
  return True


def push_policy(cfg, tasks=None, changes=None):
  """Resolve the config's push setting to a value push_enabled accepts."""
  value = cfg.get("push", False)
  if isinstance(value, bool):
    return value
  policy = {}
  for kind, root in (("tasks", tasks), ("changelog", changes)):
    if root is not None:
      policy[str(Path(root).resolve())] = value.get(kind, False)
  return policy


def push_enabled(push, root):
  """Whether to push root: push is a bool or a map of resolved roots."""
  if isinstance(push, dict):
    return bool(push.get(str(Path(root).resolve()), False))
  return bool(push)


def mutate(root, changes, operation, agent, push=False, git_timeout=GIT_TIMEOUT,
           push_timeout=PUSH_TIMEOUT):
  """Commit a mapping of repository-relative paths to bytes or None."""
  operation_state(root)
  hook = active_hook(root)
  if hook:
    raise RecordsError("active git hook: " + hook, 2)
  dirty = dirty_paths(root, [".next-id", *changes])
  if dirty:
    raise RecordsError(dirty[0] + " is dirty; commit or recover --discard", 1)
  before_status = snapshot(root)
  warn_other_dirty(before_status, changes)
  paths = {p: {"pre": state_encode(file_bytes(root / p)),
               "post": state_encode(v)} for p, v in changes.items()}
  ident = str(uuid.uuid4())
  journal = {"id": ident, "operation": operation, "agent": agent,
             "head": git(root, "rev-parse", "HEAD")[1].strip(),
             "status": before_status, "paths": paths}
  try:
    write_journal(root, journal)
  except OSError as exc:
    raise RecordsError("cannot write journal: " + str(exc), 1)
  try:
    for relative, data in changes.items():
      if paths[relative]["pre"] is None and data is not None:
        create_exclusive(root / relative, data)
      else:
        restore(root / relative, data)
    git(root, "add", "-A", "--", *changes, timeout=git_timeout)
    git(root, "commit", "-m", operation + " by " + agent +
        "\n\nRecords-Journal: " + ident, "--", *changes,
        timeout=git_timeout)
    if any(head_bytes(root, path) != data for path, data in changes.items()):
      raise RecordsError("commit differs from journal post-state", 5)
  except (RecordsError, OSError) as exc:
    try:
      committed = single_commit_exists(root, journal)
      recover_single(root, journal)
    except (RecordsError, OSError) as recovery_error:
      raise RecordsError(str(recovery_error), 5)
    if not committed:
      if journal_path(root).exists() or snapshot(root) != before_status:
        raise RecordsError("rollback could not be verified", 5)
      raise RecordsError(str(exc), 1)
    if snapshot(root) != before_status:
      raise RecordsError("unjournaled changes appeared after commit", 5)
  else:
    journal_path(root).unlink()
  if push_enabled(push, root):
    try:
      git(root, "push", timeout=push_timeout)
    except RecordsError as exc:
      raise RecordsError(str(exc), 3)


def sync(root, push=False, git_timeout=GIT_TIMEOUT, push_timeout=PUSH_TIMEOUT):
  operation_state(root)
  if active_hook(root):
    raise RecordsError("active git hook: " + active_hook(root), 2)
  dirty = [line for line in status(root).splitlines()
           if not line[3:].startswith((".records.lock",
                                       ".records-journal.json"))]
  ignored = git(root, "ls-files", "--others", "--ignored",
                "--exclude-standard")[1].splitlines()
  dirty.extend(path for path in ignored
               if path not in (".records.lock", ".records-journal.json"))
  if dirty:
    raise RecordsError("sync requires a clean tree", 1)
  if git(root, "rev-parse", "--abbrev-ref", "--symbolic-full-name",
         "@{upstream}", check=False)[0]:
    raise RecordsError("sync requires an upstream", 2)
  old_head = git(root, "rev-parse", "HEAD")[1].strip()
  count = int(git(root, "rev-list", "--count", "@{upstream}..HEAD")[1])
  try:
    git(root, "pull", "--rebase", timeout=git_timeout + 30 * count)
  except (RecordsError, OSError) as exc:
    started = any(git_path(root, name).exists()
                  for name in ("rebase-merge", "rebase-apply"))
    if started:
      try:
        git(root, "rebase", "--abort")
      except RecordsError as abort_error:
        raise RecordsError("rebase abort failed: " + str(abort_error), 5)
    if git(root, "rev-parse", "HEAD")[1].strip() != old_head or status(root):
      raise RecordsError("rebase failed and abort did not restore tree", 5)
    if not started:
      raise RecordsError(str(exc), 1)
    raise RecordsError("rebase failed and was aborted: " + str(exc), 5)
  if push_enabled(push, root):
    try:
      git(root, "push", timeout=push_timeout)
    except RecordsError as exc:
      raise RecordsError(str(exc), 3)


def parse_document(raw):
  text = raw.decode("utf-8")
  header, sep, body = text.partition("\n\n")
  fields = ParsedFields()
  order = []
  previous = None
  for line in header.splitlines():
    if line.startswith("  ") and previous:
      fields[previous] += "\n" + line
      fields.raw[previous] += "\n" + line
      continue
    if ":" not in line:
      raise RecordsError("invalid header line: " + line, 2)
    key, value = line.split(":", 1)
    if key in fields:
      raise RecordsError("duplicate header: " + key, 2)
    fields[key] = value.lstrip(" ")
    fields.raw[key] = line
    order.append(key)
    previous = key
  fields.original = dict(fields)
  return fields, order, body if sep else ""


class ParsedFields(dict):
  def __init__(self):
    super().__init__()
    self.raw = {}
    self.original = {}


KNOWN_FIELDS = {
  "id", "title", "status", "owner", "expires", "helpers", "priority",
  "severity", "repos", "produces-changes", "created", "links", "related",
  "depends-on", "estimates", "storypoints", "execution-model",
  "estimate-model-unknown", "helper-models",
  "review", "blocked-on-owner", "closed", "date", "machine", "agent",
  "kind", "location", "why", "cleanup-when", "cleanup-how", "notes",
  "tasks", "scope", "summary", "impact", "cause", "detection",
  "cleanup-options", "cleanup-done", "prevention",
}


def render_document(fields, order, body=""):
  lines = []
  for key in order:
    if key not in fields:
      continue
    if (key not in KNOWN_FIELDS and isinstance(fields, ParsedFields) and
        fields.original.get(key) == fields[key] and key in fields.raw):
      lines.append(fields.raw[key])
    else:
      lines.append(key + ": " + fields[key])
  return ("\n".join(lines) + "\n\n" + body).encode("utf-8")


def comma(value):
  return [x.strip() for x in value.split(",") if x.strip()]


def joined(items):
  values = []
  for item in items:
    one_line(item)
    if "," in item:
      raise RecordsError("list item cannot contain a comma", 2)
    values.append(item)
  return ", ".join(dict.fromkeys(values))


def slug(value):
  value = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")[:40]
  return value.strip("-") or "record"


def read_registry(path):
  try:
    data = json.loads(path.read_text())
  except FileNotFoundError:
    return {"keys": {}, "events": []}
  except (OSError, ValueError) as exc:
    raise RecordsError("invalid repository key registry: " + str(exc), 2)
  if not isinstance(data, dict):
    raise RecordsError("invalid repository key registry", 2)
  if "keys" not in data:
    return {"keys": data, "events": []}
  if not isinstance(data["keys"], dict) or not isinstance(
      data.get("events"), list):
    raise RecordsError("invalid repository key registry", 2)
  return data


def write_registry(path, data):
  atomic(path, (json.dumps(data, sort_keys=True) + "\n").encode(), 0o600)


def repo_key(path=None, cfg=None):
  cfg = cfg if cfg is not None else config()
  path = Path(path or os.getcwd()).resolve()
  root = git_root(path)
  if root is None:
    return None
  common = Path(git(root, "rev-parse", "--path-format=absolute",
                    "--git-common-dir")[1].strip()).resolve()
  main = common.parent if common.name == ".git" else root
  mapping = cfg.get("repos", {})
  if not isinstance(mapping, dict):
    raise RecordsError("repos config must be an object", 2)
  mapping = {str(Path(p).expanduser().resolve()): key
             for p, key in mapping.items()}
  if len(set(mapping.values())) != len(mapping):
    raise RecordsError("one repository key names two paths", 2)
  key = one_line(mapping.get(str(main), main.name), "repository key")
  registry = state_path("repo-keys.json")
  registry.parent.mkdir(parents=True, exist_ok=True)
  with open(str(registry) + ".lock", "a+b") as lock:
    os.fchmod(lock.fileno(), 0o600)
    fcntl.flock(lock, fcntl.LOCK_EX)
    state = read_registry(registry)
    data = state["keys"]
    for mapped_path, mapped_key in mapping.items():
      if mapped_key in data and data[mapped_key] != mapped_path:
        raise RecordsError("repos config conflicts with key registry", 2)
    if key in data and data[key] != str(main):
      raise RecordsError("repository key already belongs to another "
                         "checkout; configure repos or repo-rebind", 2)
    if key not in data:
      data[key] = str(main)
      write_registry(registry, state)
  return key


def repo_keys():
  path = state_path("repo-keys.json")
  path.parent.mkdir(parents=True, exist_ok=True)
  with open(str(path) + ".lock", "a+b") as lock:
    os.fchmod(lock.fileno(), 0o600)
    fcntl.flock(lock, fcntl.LOCK_EX)
    return read_registry(path)["keys"]


def repo_rebind(key, path, agent, force=None):
  destination = Path(path).resolve()
  if git_root(destination) != destination:
    raise RecordsError("destination must be a main checkout", 2)
  common = Path(git(destination, "rev-parse", "--path-format=absolute",
                    "--git-common-dir")[1].strip()).resolve()
  if common.parent != destination:
    raise RecordsError("destination is a linked worktree", 2)
  for configured_path, configured_key in config().get("repos", {}).items():
    configured = Path(configured_path).expanduser().resolve()
    if configured_key == key and configured != destination:
      raise RecordsError("repos config conflicts with rebind destination", 2)
  registry = state_path("repo-keys.json")
  registry.parent.mkdir(parents=True, exist_ok=True)
  with open(str(registry) + ".lock", "a+b") as lock:
    os.fchmod(lock.fileno(), 0o600)
    fcntl.flock(lock, fcntl.LOCK_EX)
    state = read_registry(registry)
    data = state["keys"]
    if key not in data:
      raise RecordsError("unknown repository key", 2)
    if any(k != key and p == str(destination) for k, p in data.items()):
      raise RecordsError("destination is already registered", 2)
    old_path = Path(data[key])
    old_exists = git_root(old_path) == old_path
    if old_exists and not force:
      raise RecordsError("old checkout still exists; --force requires "
                         "owner instruction", 1)
    if force and not old_exists:
      raise RecordsError("--force does not apply when old checkout is gone", 2)
    old = data[key]
    data[key] = str(destination)
    state["events"].append({"time": stamp(), "agent": agent, "key": key,
                            "old": old, "new": str(destination),
                            "forced": bool(force), "reason": force or ""})
    write_registry(registry, state)


def commit_exists(root, journal, side):
  info = journal[side]
  if info.get("skipped"):
    return True
  old = info["head"]
  if git(root, "merge-base", "--is-ancestor", old, "HEAD",
         check=False)[0]:
    raise RecordsError("HEAD diverged during cross recovery", 5)
  _, output, _ = git(root, "log", old + "..HEAD", "--format=%H%x00%B%x00",
                     "--fixed-strings", "--grep=Records-Journal: " +
                     journal["id"])
  hits = []
  parts = output.split("\x00")
  for index in range(0, len(parts) - 1, 2):
    commit, message = parts[index].strip(), parts[index + 1]
    if any(line == "Records-Journal: " + journal["id"]
           for line in message.splitlines()):
      hits.append(commit)
  if len(hits) > 1:
    raise RecordsError("multiple cross commits match journal", 5)
  if not hits:
    return False
  if any(head_bytes(root, path) != state_decode(states["post"])
         for path, states in info["paths"].items()):
    raise RecordsError("cross commit tree differs from journal", 5)
  return True


def restore_side(root, info):
  for relative, states in info["paths"].items():
    path = root / relative
    current = file_bytes(path)
    before = state_decode(states["pre"])
    after = state_decode(states["post"])
    if current not in (before, after):
      raise RecordsError("conflicting edit during recovery: " + relative, 5)
    restore(path, before)
    git(root, "reset", "-q", "--", relative)
  if snapshot(root) != info["status"]:
    raise RecordsError("unjournaled changes appeared during recovery", 5)


def restore_committed_side(root, info):
  if info.get("skipped"):
    return
  for relative, states in info["paths"].items():
    current = file_bytes(root / relative)
    before = state_decode(states["pre"])
    after = state_decode(states["post"])
    if current not in (before, after):
      raise RecordsError("conflicting edit during recovery: " + relative, 5)
    restore(root / relative, after)
    git(root, "reset", "-q", "--", relative)


def cross_recover(tasks, changes):
  try:
    return _cross_recover(tasks, changes)
  except RecordsError as exc:
    if exc.code == 1:
      raise RecordsError(str(exc), 5)
    raise


def _cross_recover(tasks, changes):
  task_journal = read_journal(tasks) if tasks else None
  change_journal = read_journal(changes) if changes else None
  if not task_journal and not change_journal:
    return
  for root in (tasks, changes):
    if root and read_journal(root):
      check_index_lock(root)
  if change_journal and change_journal.get("cross_stub") and not tasks:
    raise RecordsError("cross recovery needs tasks directory", 5)
  if task_journal and not task_journal.get("cross"):
    recover_single(tasks, task_journal)
    task_journal = None
  if change_journal and not change_journal.get("cross_stub"):
    recover_single(changes, change_journal)
    change_journal = None
  if not task_journal and not change_journal:
    return
  if task_journal and task_journal.get("cross") and not change_journal:
    if not changes:
      raise RecordsError("cross recovery needs changelog directory", 5)
    task_done = commit_exists(tasks, task_journal, "tasks")
    change_done = commit_exists(changes, task_journal, "changelog")
    if task_done and change_done:
      restore_committed_side(changes, task_journal["changelog"])
      restore_committed_side(tasks, task_journal["tasks"])
      journal_path(tasks).unlink()
      return
    if not task_done and not change_done:
      settled = True
      for root, side in ((tasks, "tasks"), (changes, "changelog")):
        info = task_journal[side]
        settled &= all(file_bytes(root / path) == state_decode(
          values["pre"]) for path, values in info["paths"].items())
        settled &= snapshot(root) == info["status"]
      if settled:
        journal_path(tasks).unlink()
        return
    raise RecordsError("cross stub missing before both commits", 5)
  if not task_journal or not change_journal:
    raise RecordsError("incomplete cross journal", 5)
  if task_journal["id"] != change_journal["id"]:
    raise RecordsError("cross journal IDs disagree", 5)
  task_done = commit_exists(tasks, task_journal, "tasks")
  change_done = commit_exists(changes, task_journal, "changelog")
  if task_done and change_done:
    restore_committed_side(changes, task_journal["changelog"])
    restore_committed_side(tasks, task_journal["tasks"])
    journal_path(changes).unlink()
    journal_path(tasks).unlink()
    return
  if task_done and not change_done:
    raise RecordsError("tasks committed but changelog missing for journal " +
                       task_journal["id"], 5)
  if change_done:
    restore_committed_side(changes, task_journal["changelog"])
    info = task_journal["tasks"]
    if not info.get("skipped"):
      for relative, states in info["paths"].items():
        current = file_bytes(tasks / relative)
        before = state_decode(states["pre"])
        after = state_decode(states["post"])
        if current not in (before, after):
          raise RecordsError("conflicting task edit: " + relative, 5)
        restore(tasks / relative, after)
      stage_paths(tasks, {path: state_decode(value["post"])
                          for path, value in info["paths"].items()})
      git(tasks, "commit", "-m", task_journal["operation"] + " by " +
          task_journal["agent"] + "\n\nRecords-Journal: " +
          task_journal["id"], "--", *info["paths"])
    journal_path(changes).unlink()
    journal_path(tasks).unlink()
    return
  restore_side(changes, task_journal["changelog"])
  restore_side(tasks, task_journal["tasks"])
  journal_path(changes).unlink()
  journal_path(tasks).unlink()


def resolve_cross(tasks, changes, keep=(), discard=(), validators=None):
  if not keep and not discard:
    cross_recover(tasks, changes)
    return
  if not tasks or not changes:
    raise RecordsError("cross recovery needs both directories", 5)
  journal = read_journal(tasks)
  stub = read_journal(changes)
  if not journal or not journal.get("cross") or not stub:
    raise RecordsError("complete cross journals are required", 5)
  if journal["id"] != stub.get("id"):
    raise RecordsError("cross journal IDs disagree", 5)
  task_done = commit_exists(tasks, journal, "tasks")
  change_done = commit_exists(changes, journal, "changelog")
  if task_done and not change_done:
    raise RecordsError("tasks committed but changelog missing", 5)
  if task_done and change_done:
    raise RecordsError("both sides already committed; omit resolutions", 2)

  def locate(name):
    if name.startswith("tasks:"):
      side, relative = "tasks", name[6:]
    elif name.startswith("changelog:"):
      side, relative = "changelog", name[10:]
    else:
      candidates = [side for side in ("tasks", "changelog")
                    if name in journal[side]["paths"]]
      if len(candidates) != 1:
        raise RecordsError("ambiguous or unknown journal path: " + name, 2)
      side, relative = candidates[0], name
    if relative not in journal[side]["paths"]:
      raise RecordsError("path not in journal: " + name, 2)
    if (side == "tasks" and task_done) or (
        side == "changelog" and change_done):
      raise RecordsError("cannot resolve a committed side: " + name, 5)
    return side, relative

  chosen = {}
  for action, names in (("keep", keep), ("discard", discard)):
    for name in names:
      side, relative = locate(name)
      key = (side, relative)
      if key in chosen:
        raise RecordsError("duplicate resolution: " + name, 2)
      chosen[key] = action
  roots = {"tasks": tasks, "changelog": changes}
  for (side, relative), action in chosen.items():
    root = roots[side]
    states = journal[side]["paths"][relative]
    if action == "discard":
      restore(root / relative, state_decode(states["pre"]))
      git(root, "reset", "-q", "--", relative)
    else:
      current = file_bytes(root / relative)
      if current is None:
        raise RecordsError("cannot keep absent path: " + relative, 5)
      states["post"] = state_encode(current)
  write_journal(tasks, journal)
  stub["changelog"] = journal["changelog"]
  write_journal(changes, stub)
  if not keep:
    cross_recover(tasks, changes)
    return

  if change_done:
    info = journal["tasks"]
    for relative, states in info["paths"].items():
      current = file_bytes(tasks / relative)
      before = state_decode(states["pre"])
      after = state_decode(states["post"])
      if current not in (before, after):
        raise RecordsError("unresolved cross edit: " + relative, 5)
      restore(tasks / relative, after)
    if validators and validators.get("tasks"):
      errors = validators["tasks"]()
      if errors:
        raise RecordsError("cannot keep invalid task record: " +
                           "; ".join(errors), 5)
    cross_recover(tasks, changes)
    return

  for side in ("changelog", "tasks"):
    root = roots[side]
    info = journal[side]
    for relative, states in info["paths"].items():
      current = file_bytes(root / relative)
      before = state_decode(states["pre"])
      after = state_decode(states["post"])
      if current not in (before, after):
        raise RecordsError("unresolved cross edit: " + relative, 5)
    try:
      for relative, states in info["paths"].items():
        restore(root / relative, state_decode(states["post"]))
      if validators and validators.get(side):
        errors = validators[side]()
        if errors:
          raise RecordsError("cannot keep invalid " + side + " record: " +
                             "; ".join(errors), 5)
      stage_paths(root, {path: state_decode(value["post"])
                         for path, value in info["paths"].items()})
      git(root, "commit", "-m", journal["operation"] + " by " +
          journal["agent"] + "\n\nRecords-Journal: " + journal["id"],
          "--", *info["paths"])
      if any(head_bytes(root, relative) != state_decode(states["post"])
             for relative, states in info["paths"].items()):
        raise RecordsError("recovery commit differs from post-state", 5)
    except (RecordsError, OSError) as exc:
      raise RecordsError("cross recovery needs attention: " + str(exc), 5)
  journal_path(changes).unlink()
  journal_path(tasks).unlink()


def mutate_cross(tasks, changes, task_changes, change_changes, operation,
                 agent, push=False):
  if not task_changes:
    mutate(changes, change_changes, operation, agent, push=push)
    return
  if not change_changes:
    mutate(tasks, task_changes, operation, agent, push=push)
    return
  for root, edits in ((tasks, task_changes), (changes, change_changes)):
    operation_state(root)
    hook = active_hook(root)
    if hook:
      raise RecordsError("active git hook: " + hook, 2)
    dirty = dirty_paths(root, [".next-id", *edits])
    if dirty:
      raise RecordsError(dirty[0] + " is dirty", 1)
  ident = str(uuid.uuid4())
  journal = {"id": ident, "cross": True, "operation": operation,
             "agent": agent}
  for key, root, edits in (("tasks", tasks, task_changes),
                           ("changelog", changes, change_changes)):
    before_status = snapshot(root)
    warn_other_dirty(before_status, edits)
    journal[key] = {"head": git(root, "rev-parse", "HEAD")[1].strip(),
                    "status": before_status,
                    "paths": {p: {"pre": state_encode(file_bytes(root / p)),
                                  "post": state_encode(v)}
                              for p, v in edits.items()}}
  try:
    write_journal(tasks, journal)
    write_journal(changes, {"id": ident, "cross_stub": True,
                            "tasks": str(tasks),
                            "changelog": journal["changelog"]})
  except OSError as exc:
    try:
      journal_path(tasks).unlink(missing_ok=True)
      journal_path(changes).unlink(missing_ok=True)
    except OSError as cleanup_error:
      raise RecordsError("journal setup failed and cleanup failed: " +
                         str(cleanup_error), 5)
    raise RecordsError("cannot write cross journal: " + str(exc), 1)
  try:
    for key, root, edits in (("changelog", changes, change_changes),
                             ("tasks", tasks, task_changes)):
      for relative, data in edits.items():
        if journal[key]["paths"][relative]["pre"] is None and data is not None:
          create_exclusive(root / relative, data)
        else:
          restore(root / relative, data)
      stage_paths(root, edits)
      git(root, "commit", "-m", operation + " by " + agent +
          "\n\nRecords-Journal: " + ident, "--", *edits)
      if any(head_bytes(root, path) != data for path, data in edits.items()):
        raise RecordsError("cross commit differs from journal post-state", 5)
      journal[key]["commit"] = git(root, "rev-parse", "HEAD")[1].strip()
      write_journal(tasks, journal)
  except (RecordsError, OSError) as exc:
    try:
      change_committed = commit_exists(changes, journal, "changelog")
      cross_recover(tasks, changes)
    except (RecordsError, OSError) as recovery_error:
      raise RecordsError(str(recovery_error), 5)
    if not change_committed:
      raise RecordsError(str(exc), 5)
  else:
    journal_path(changes).unlink()
    journal_path(tasks).unlink()
  for root in (changes, tasks):
    if not push_enabled(push, root):
      continue
    try:
      git(root, "push", timeout=PUSH_TIMEOUT)
    except RecordsError as exc:
      raise RecordsError(str(exc), 3)


@contextmanager
def record_locks(tasks, changes, writes=(), wait=LOCK_WAIT, agent="reader",
                 operation="read", unlocked=False, auto_recover=True):
  roots = [r for r in (tasks, changes) if r]
  for root in roots:
    if root == tasks:
      validate_root(root, "tasks")
    else:
      validate_root(root, "changelog")
  while True:
    with locks(roots, writes, wait, agent, operation,
               unlocked) as authoritative:
      pending = any(read_journal(root) for root in roots)
      if not pending or not auto_recover:
        yield authoritative
        return
    try:
      with locks(roots, roots, wait, agent, "recover"):
        cross_recover(tasks, changes)
    except RecordsError as exc:
      if exc.code == 1 and str(exc).startswith("cannot open lock"):
        raise RecordsError("pending recovery needs writable locks: " +
                           str(exc), 5)
      raise


def doctor(root, kind, push=False, wait=LOCK_WAIT, other=None):
  validate_root(root, kind)
  if other:
    validate_root(other, "changelog" if kind == "tasks" else "tasks")
  operation_state(root)
  if other:
    operation_state(other)
  hook = active_hook(root)
  if hook:
    raise RecordsError("active git hook: " + hook, 2)
  ordered = ([root, other] if kind == "tasks" else [other, root])
  with locks(ordered, [root], wait, "doctor", "probe"):
    if any(read_journal(path) for path in ordered if path):
      raise RecordsError("pending journal needs recovery", 5)
    probe = root / (".records-probe-" + uuid.uuid4().hex)
    try:
      with open(probe, "xb") as stream:
        stream.write(b"probe\n")
    except OSError as exc:
      raise RecordsError("directory is not writable; add it to sandbox "
                         "writable roots: " + str(exc), 1)
    finally:
      probe.unlink(missing_ok=True)
  with tempfile.TemporaryDirectory() as temp:
    git(temp, "init", "-q")
    name = git(root, "config", "user.name")[1].strip()
    email = git(root, "config", "user.email")[1].strip()
    git(temp, "config", "user.name", name)
    git(temp, "config", "user.email", email)
    for key in ("commit.gpgsign", "gpg.program", "gpg.format",
                "gpg.ssh.program", "gpg.x509.program", "user.signingkey"):
      code, value, _ = git(root, "config", "--get", key, check=False)
      if code == 0:
        git(temp, "config", key, value.strip())
    atomic(Path(temp) / "probe", b"probe\n")
    git(temp, "add", "--", "probe")
    git(temp, "commit", "-m", "Signing probe", "--", "probe")
  if push_enabled(push, root):
    git(root, "ls-remote", timeout=PUSH_TIMEOUT)
