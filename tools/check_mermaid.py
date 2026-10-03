#!/usr/bin/env python3
# Adapted from kindjie/tess; Copyright (c) 2026 tess contributors.
# MIT licensed; see docs/site-tooling.md for attribution.
"""Validate every Mermaid fence in the documentation against the pinned
runtime the site actually serves.

Mermaid parse failures are invisible to `mkdocs build --strict` — the
page ships with raw diagram source and only a browser console error —
so this check extracts each ```mermaid fence, loads the self-hosted
bundle fetched by tools/fetch_mermaid.py into headless Chrome, and calls
`mermaid.parse()` on every diagram. Excluded documentation under docs/
is checked too: diagrams stay valid wherever they live.

Known limit: fences indented four or more spaces (for example inside
list items) are not recognized and are silently skipped.
"""

from __future__ import annotations

import argparse
import html
import json
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

import fetch_mermaid

REPO_ROOT = Path(__file__).resolve().parents[1]
DOCS_ROOT = REPO_ROOT / "build" / "docs-source"

# Superfences grammar: three-plus backticks or tildes, up to three
# leading spaces, and an info string that names the fence either bare
# ("mermaid") or as an attribute list ("{.mermaid #id}").
FENCE_OPEN = re.compile(
  r"^(?P<indent> {0,3})(?P<fence>`{3,}|~{3,})\s*(?P<info>.*?)\s*$"
)
MERMAID_INFO = re.compile(
  r"^(mermaid(\s.*)?|\{[^}]*\.mermaid[^}]*\})$"
)
RESULTS_RE = re.compile(
  r'<pre id="tess-mermaid-results">(.*?)</pre>', re.DOTALL
)
DONE_MARKER = 'data-tess-mermaid-check="done"'

BROWSER_CANDIDATES = (
  "google-chrome",
  "chromium",
  "chromium-browser",
  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
)

HARNESS_TEMPLATE = """<!doctype html>
<html>
<head><meta charset="utf-8"><script src="mermaid.min.js"></script></head>
<body>
<pre id="tess-mermaid-results"></pre>
<script>
const diagrams = __DIAGRAMS__;
(async () => {
  const results = [];
  for (const diagram of diagrams) {
    try {
      await mermaid.parse(diagram.source);
      results.push({id: diagram.id, ok: true});
    } catch (error) {
      results.push({
        id: diagram.id,
        ok: false,
        error: String((error && error.message) || error),
      });
    }
  }
  document.getElementById("tess-mermaid-results").textContent =
    JSON.stringify(results);
  document.documentElement.dataset.tessMermaidCheck = "done";
})();
</script>
</body>
</html>
"""


@dataclass(frozen=True)
class Fence:
  """One ```mermaid fence with its source location."""

  path: Path
  line: int
  source: str


def _closes(line: str, fence: str) -> bool:
  match = FENCE_OPEN.match(line)
  return (
    match is not None
    and not match.group("info")
    and match.group("fence")[0] == fence[0]
    and len(match.group("fence")) >= len(fence)
  )


def extract_fences(text: str, path: Path) -> tuple[list[Fence], list[str]]:
  """Return the Mermaid fences in one document plus scan failures.

  Every fence is tracked so a literal ```mermaid inside a longer
  enclosing fence (a Markdown example) is not mistaken for a diagram.
  """
  fences: list[Fence] = []
  failures: list[str] = []
  lines = text.splitlines()
  index = 0
  while index < len(lines):
    opened = FENCE_OPEN.match(lines[index])
    if opened is None:
      index += 1
      continue
    is_mermaid = MERMAID_INFO.match(opened.group("info")) is not None
    start = index + 1
    indent = len(opened.group("indent"))
    body: list[str] = []
    index += 1
    while index < len(lines) and not _closes(
      lines[index], opened.group("fence")
    ):
      stripped = lines[index]
      if stripped[:indent].isspace() or not stripped[:indent]:
        stripped = stripped[indent:]
      body.append(stripped)
      index += 1
    if index >= len(lines):
      if is_mermaid:
        failures.append(f"{path}:{start}: unterminated mermaid fence")
      break
    if is_mermaid:
      fences.append(Fence(path, start, "\n".join(body)))
    index += 1
  return fences, failures


def collect_fences(docs_root: Path) -> tuple[list[Fence], list[str]]:
  fences: list[Fence] = []
  failures: list[str] = []
  for path in sorted(docs_root.rglob("*.md")):
    found, scan_failures = extract_fences(
      path.read_text(encoding="utf-8"), path
    )
    fences.extend(found)
    failures.extend(scan_failures)
  return fences, failures


def build_harness(fences: list[Fence]) -> str:
  """Return harness HTML that parses every fence and reports as JSON."""
  diagrams = [
    {"id": index, "source": fence.source}
    for index, fence in enumerate(fences)
  ]
  # "</" would end the inline script early if a diagram ever contains it.
  payload = json.dumps(diagrams).replace("</", "<\\/")
  return HARNESS_TEMPLATE.replace("__DIAGRAMS__", payload)


def parse_results(
  dom: str,
) -> tuple[list[dict[str, object]] | None, list[str]]:
  """Return per-diagram results parsed from a dumped harness DOM."""
  if DONE_MARKER not in dom:
    return None, ["harness did not finish: completion marker missing"]
  match = RESULTS_RE.search(dom)
  if match is None:
    return None, ["harness did not finish: results element missing"]
  results: list[dict[str, object]] = json.loads(
    html.unescape(match.group(1))
  )
  return results, []


def find_browser(requested: str | None) -> str | None:
  candidates = (requested,) if requested else BROWSER_CANDIDATES
  for candidate in candidates:
    if candidate and (
      shutil.which(candidate) or Path(candidate).is_file()
    ):
      return candidate
  return None


def run_harness(
  browser: str, harness_dir: Path, timeout_seconds: int
) -> str:
  command = [
    browser,
    "--headless=new",
    "--no-sandbox",
    "--disable-gpu",
    "--virtual-time-budget=10000",
    "--dump-dom",
    (harness_dir / "harness.html").as_uri(),
  ]
  completed = subprocess.run(
    command,
    check=True,
    capture_output=True,
    text=True,
    timeout=timeout_seconds,
  )
  return completed.stdout


def check_fences(
  fences: list[Fence],
  mermaid_path: Path,
  browser: str,
  timeout_seconds: int,
) -> list[str]:
  """Parse every fence in headless Chrome; return failures."""
  with tempfile.TemporaryDirectory() as scratch:
    harness_dir = Path(scratch)
    shutil.copyfile(mermaid_path, harness_dir / "mermaid.min.js")
    harness = build_harness(fences)
    (harness_dir / "harness.html").write_text(harness, encoding="utf-8")
    dom = run_harness(browser, harness_dir, timeout_seconds)
  results, failures = parse_results(dom)
  if results is None:
    return failures
  if len(results) != len(fences):
    return [
      f"harness reported {len(results)} results for {len(fences)} diagrams"
    ]
  # The harness parses diagrams sequentially, so results pair with
  # fences by position.
  for fence, result in zip(fences, results):
    if not result.get("ok"):
      path = fence.path
      if path.is_relative_to(REPO_ROOT):
        path = path.relative_to(REPO_ROOT)
      location = f"{path}:{fence.line}"
      error = str(result.get("error", "unknown error")).strip()
      failures.append(f"{location}: mermaid parse failed: {error}")
  return failures


def main(argv: list[str] | None = None) -> int:
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument(
    "--mermaid",
    type=Path,
    default=fetch_mermaid.DEFAULT_DEST,
    help="self-hosted mermaid.min.js (from tools/fetch_mermaid.py)",
  )
  parser.add_argument(
    "--docs-root", type=Path, default=DOCS_ROOT, help="documentation root"
  )
  parser.add_argument(
    "--browser", help="Chrome binary (default: first available candidate)"
  )
  parser.add_argument(
    "--timeout", type=int, default=120, help="harness timeout in seconds"
  )
  args = parser.parse_args(argv)

  if not args.mermaid.is_file():
    print(
      f"error: {args.mermaid} is missing; run tools/fetch_mermaid.py",
      file=sys.stderr,
    )
    return 1
  failures = fetch_mermaid.verify_digest(
    args.mermaid.read_bytes(), fetch_mermaid.DIST_SHA256, str(args.mermaid)
  )
  browser = None
  if not failures:
    browser = find_browser(args.browser)
    if browser is None:
      failures = ["no Chrome binary found; pass --browser"]
  if not failures and browser is not None:
    fences, failures = collect_fences(args.docs_root)
    if not failures:
      if not fences:
        failures = [f"no mermaid fences found under {args.docs_root}"]
      else:
        failures = check_fences(
          fences, args.mermaid, browser, args.timeout
        )
        if not failures:
          print(
            f"all {len(fences)} mermaid diagrams parse with Mermaid "
            f"{fetch_mermaid.MERMAID_VERSION}"
          )
  for failure in failures:
    print(f"error: {failure}", file=sys.stderr)
  return 1 if failures else 0


if __name__ == "__main__":
  raise SystemExit(main())
