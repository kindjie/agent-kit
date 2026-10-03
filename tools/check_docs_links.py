#!/usr/bin/env python3
# Adapted from kindjie/tess; Copyright (c) 2026 tess contributors.
# MIT licensed; see docs/site-tooling.md for attribution.
"""Validate local links in the generated documentation site."""

from __future__ import annotations

import argparse
import sys
from functools import lru_cache
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit


class DocumentParser(HTMLParser):
  """Collect link targets and anchors from one HTML document."""

  def __init__(self) -> None:
    super().__init__(convert_charrefs=True)
    self.links: list[str] = []
    self.anchors: set[str] = set()

  def handle_starttag(
    self, tag: str, attrs: list[tuple[str, str | None]]
  ) -> None:
    values = dict(attrs)
    element_id = values.get("id")
    if element_id:
      self.anchors.add(element_id)
    if tag == "a" and values.get("name"):
      self.anchors.add(values["name"] or "")
    for attribute in ("href", "src"):
      target = values.get(attribute)
      if target:
        self.links.append(target)


@lru_cache(maxsize=None)
def parse_document(path: Path) -> DocumentParser:
  parser = DocumentParser()
  parser.feed(path.read_text(encoding="utf-8"))
  return parser


def resolve_target(site: Path, source: Path, raw_path: str) -> Path:
  decoded = unquote(raw_path)
  if not decoded:
    return source.resolve()
  target = site / decoded.lstrip("/") if decoded.startswith("/") else (
    source.parent / decoded
  )
  if target.is_dir():
    return (target / "index.html").resolve()
  if not target.exists() and not target.suffix:
    return (target / "index.html").resolve()
  return target.resolve()


def check_site(
  site: Path,
  *,
  ignored_missing_anchors: frozenset[tuple[str, str]] = frozenset(),
) -> list[str]:
  site = site.resolve()
  index = site / "index.html"
  if not index.is_file():
    return [f"documentation site has no index: {index}"]

  failures: list[str] = []
  for source in sorted(site.rglob("*.html")):
    source_label = source.relative_to(site).as_posix()
    for raw_target in parse_document(source).links:
      parsed = urlsplit(raw_target)
      if parsed.scheme or parsed.netloc:
        continue
      target = resolve_target(site, source, parsed.path)
      try:
        target.relative_to(site)
      except ValueError:
        failures.append(
          f"{source_label}: local target escapes the site: {raw_target}"
        )
        continue
      if not target.is_file():
        failures.append(
          f"{source_label}: missing local target: {raw_target}"
        )
        continue
      if parsed.fragment and target.suffix.lower() == ".html":
        fragment = unquote(parsed.fragment)
        if fragment not in parse_document(target).anchors:
          target_label = target.relative_to(site).as_posix()
          if (target_label, fragment) in ignored_missing_anchors:
            continue
          failures.append(
            f"{source_label}: missing anchor '{fragment}' in {target_label}"
          )
  return failures


def parse_missing_anchor(value: str) -> tuple[str, str]:
  """Parse a narrowly scoped missing-anchor exception."""
  path, separator, fragment = value.rpartition("#")
  if not separator or not path or not fragment:
    raise argparse.ArgumentTypeError("expected PATH#ANCHOR")
  return path, unquote(fragment)


def main(argv: list[str] | None = None) -> int:
  parser = argparse.ArgumentParser()
  parser.add_argument("site", nargs="?", type=Path, default=Path("build/site"))
  parser.add_argument(
    "--ignore-missing-anchor",
    action="append",
    default=[],
    metavar="PATH#ANCHOR",
    type=parse_missing_anchor,
  )
  args = parser.parse_args(argv)
  failures = check_site(
    args.site,
    ignored_missing_anchors=frozenset(args.ignore_missing_anchor),
  )
  for failure in failures:
    print(f"error: {failure}", file=sys.stderr)
  if failures:
    return 1
  print(f"validated generated documentation links in {args.site}")
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
