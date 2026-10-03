#!/usr/bin/env python3
# Adapted from kindjie/tess; Copyright (c) 2026 tess contributors.
# MIT licensed; see docs/site-tooling.md for attribution.
"""Fetch the pinned Mermaid runtime the documentation site self-hosts.

The site serves Mermaid from its own origin instead of Material for
MkDocs' unpkg.com fallback, but the minified bundle is far too large for
the repository's tracked-file token budget, so it is downloaded at build
time from the npm registry and verified against pinned SHA-256 digests —
the same shape as the pinned Doxygen install in the Pages workflow.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import sys
import tarfile
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

MERMAID_VERSION = "11.16.1"
TARBALL_URL = (
  "https://registry.npmjs.org/mermaid/-/mermaid-"
  f"{MERMAID_VERSION}.tgz"
)
TARBALL_SHA256 = (
  "ebd9885111092c78cefc79a76f6c1dc34ed5b834b02ae8f338227ce79c003de4"
)
DIST_MEMBER = "package/dist/mermaid.min.js"
DIST_SHA256 = (
  "18327bef70d96fb505fe7287d9f6a7362ebf07ff6576ddfaffb1a06f3e1a2954"
)
DEFAULT_DEST = REPO_ROOT / "docs" / "assets" / "javascripts" / "mermaid.min.js"


def sha256_hex(data: bytes) -> str:
  return hashlib.sha256(data).hexdigest()


def verify_digest(data: bytes, expected: str, label: str) -> list[str]:
  """Return failures when data does not match the pinned digest."""
  actual = sha256_hex(data)
  if actual != expected:
    return [f"{label}: SHA-256 {actual} does not match pinned {expected}"]
  return []


def extract_dist(tarball: bytes) -> tuple[bytes | None, list[str]]:
  """Return the distributed bundle from the registry tarball bytes."""
  with tarfile.open(fileobj=io.BytesIO(tarball), mode="r:gz") as archive:
    try:
      member = archive.getmember(DIST_MEMBER)
    except KeyError:
      return None, [f"tarball does not contain {DIST_MEMBER}"]
    extracted = archive.extractfile(member)
    if extracted is None:
      return None, [f"{DIST_MEMBER} is not a regular file"]
    return extracted.read(), []


def fetch(dest: Path, url: str = TARBALL_URL) -> list[str]:
  """Place the verified Mermaid bundle at dest; return failures."""
  if dest.exists() and not verify_digest(
    dest.read_bytes(), DIST_SHA256, str(dest)
  ):
    print(f"{dest} already matches Mermaid {MERMAID_VERSION}")
    return []
  with urllib.request.urlopen(url) as response:
    tarball = response.read()
  failures = verify_digest(tarball, TARBALL_SHA256, url)
  if failures:
    return failures
  dist, failures = extract_dist(tarball)
  if failures or dist is None:
    return failures
  failures = verify_digest(dist, DIST_SHA256, DIST_MEMBER)
  if failures:
    return failures
  dest.parent.mkdir(parents=True, exist_ok=True)
  dest.write_bytes(dist)
  print(f"Fetched Mermaid {MERMAID_VERSION} to {dest}")
  return []


def main(argv: list[str] | None = None) -> int:
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument(
    "--dest",
    type=Path,
    default=DEFAULT_DEST,
    help="destination path for the verified bundle",
  )
  args = parser.parse_args(argv)
  failures = fetch(args.dest)
  for failure in failures:
    print(f"error: {failure}", file=sys.stderr)
  return 1 if failures else 0


if __name__ == "__main__":
  raise SystemExit(main())
