#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.9"
# dependencies = [
#   "cmarkgfm==2025.10.22",
#   "nh3==0.3.6",
# ]
# ///

"""Serve a private, local GitHub-style Markdown preview."""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import urllib.error
import urllib.request
import webbrowser
from copy import deepcopy
from dataclasses import dataclass
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import quote, urlsplit

import cmarkgfm
import nh3
from cmarkgfm.cmark import Options


VERSION = "0.2.0"

# Directories never served as assets, at any root.
EXCLUDED_ASSET_DIRS = frozenset({".git", ".hg", ".svn"})
CSS_VERSION = "5.9.0"
CSS_BASE_URL = (
  "https://raw.githubusercontent.com/sindresorhus/"
  f"github-markdown-css/v{CSS_VERSION}"
)
MAX_STYLESHEET_BYTES = 1024 * 1024
PREVIEW_PREFIX = "/__md_preview__/"


class PreviewError(RuntimeError):
  """An expected command failure with a user-facing message."""


@dataclass(frozen=True)
class StylesheetSpec:
  """Pinned public stylesheet metadata."""

  filename: str
  url: str
  sha256: str


STYLESHEETS = {
  "auto": StylesheetSpec(
    filename=f"github-markdown-{CSS_VERSION}.css",
    url=f"{CSS_BASE_URL}/github-markdown.css",
    sha256=(
      "6112686f954db5d3806fb96116d2ab20"
      "ad3018469ab1015c587fd8efe7d25cf4"
    ),
  ),
  "light": StylesheetSpec(
    filename=f"github-markdown-light-{CSS_VERSION}.css",
    url=f"{CSS_BASE_URL}/github-markdown-light.css",
    sha256=(
      "de2d14b5290b8cf2af74c95e92560d9"
      "c00642ae72de0b856cece3e4eddb2d885"
    ),
  ),
  "dark": StylesheetSpec(
    filename=f"github-markdown-dark-{CSS_VERSION}.css",
    url=f"{CSS_BASE_URL}/github-markdown-dark.css",
    sha256=(
      "b45ead2db01f5856c4eb378f21f47da6"
      "3f6b0ecf3be5d06385472164b7283df6"
    ),
  ),
}


ALLOWED_TAGS = set(nh3.ALLOWED_TAGS) | {"input", "picture", "source"}
ALLOWED_ATTRIBUTES = deepcopy(nh3.ALLOWED_ATTRIBUTES)
ALLOWED_ATTRIBUTES.setdefault("div", set()).add("align")
ALLOWED_ATTRIBUTES.setdefault("input", set()).update(
  {"checked", "disabled"}
)
ALLOWED_ATTRIBUTES.setdefault("p", set()).add("align")
ALLOWED_ATTRIBUTES.setdefault("source", set()).update(
  {"media", "src", "srcset", "type"}
)
TAG_ATTRIBUTE_VALUES = {"input": {"type": {"checkbox"}}}


RELOAD_JAVASCRIPT = """\
(() => {
  let lastModified = null;

  async function checkForChanges() {
    try {
      const response = await fetch(
        "/__md_preview__/state.json",
        {cache: "no-store"}
      );
      if (response.ok) {
        const state = await response.json();
        if (lastModified !== null && state.mtime_ns !== lastModified) {
          window.location.reload();
          return;
        }
        lastModified = state.mtime_ns;
      }
    } catch (_error) {
      // The foreground preview may be stopping; retry without noisy logs.
    }
    window.setTimeout(checkForChanges, 750);
  }

  checkForChanges();
})();
"""


def positive_port(value: str) -> int:
  """Parse a TCP port, allowing zero for an operating-system choice."""
  try:
    port = int(value)
  except ValueError as error:
    raise argparse.ArgumentTypeError("must be an integer") from error
  if port < 0 or port > 65535:
    raise argparse.ArgumentTypeError("must be between 0 and 65535")
  return port


def theme_name(value: str) -> str:
  """Parse a supported preview theme."""
  if value not in STYLESHEETS:
    raise argparse.ArgumentTypeError(
      "must be one of: " + ", ".join(STYLESHEETS)
    )
  return value


def parse_args(argv=None):
  """Parse command-line arguments."""
  default_theme = os.environ.get("MD_PREVIEW_THEME", "auto")
  parser = argparse.ArgumentParser(
    # Invoked through the md-preview wrapper, so name it for the caller
    # rather than letting argparse read argv[0].
    prog="md-preview",
    description=(
      "Preview GitHub-flavored Markdown locally without uploading it."
    )
  )
  parser.add_argument(
    "file",
    nargs="?",
    help="Markdown file or directory (default: README.md)",
  )
  parser.add_argument(
    "--theme",
    type=theme_name,
    choices=tuple(STYLESHEETS),
    default=default_theme,
    help="GitHub Markdown color theme (default: %(default)s)",
  )
  parser.add_argument(
    "--port",
    type=positive_port,
    default=0,
    help="loopback port; zero selects an available port (default: 0)",
  )
  parser.add_argument(
    "--no-open",
    action="store_true",
    help="do not open the preview in the default browser",
  )
  parser.add_argument(
    "--repository-assets",
    action="store_true",
    help=(
      "serve the whole enclosing repository, not just the document's "
      "directory; every sibling file becomes readable by local clients"
    ),
  )
  parser.add_argument(
    "--refresh-css",
    action="store_true",
    help="redownload the pinned public stylesheet",
  )
  parser.add_argument(
    "--version", action="version", version=f"%(prog)s {VERSION}"
  )
  return parser.parse_args(argv)


def resolve_markdown_path(
  argument: Optional[str], cwd: Optional[Path] = None
) -> Path:
  """Resolve a Markdown file, accepting a directory as README shorthand."""
  cwd = (cwd or Path.cwd()).resolve()
  path = Path(argument).expanduser() if argument else Path("README.md")
  if not path.is_absolute():
    path = cwd / path

  if path.is_dir():
    for name in ("README.md", "Readme.md", "readme.md"):
      candidate = path / name
      if candidate.is_file():
        return candidate.resolve()

  if not path.is_file():
    raise PreviewError(f"Markdown file not found: {path}")
  return path.resolve()


def is_excluded_asset(relative: Path) -> bool:
  """Whether a path below the asset root must never be served.

  A repository's history and hooks are never a Markdown asset, and serving
  them leaks remotes, worktrees and anything staged.
  """
  return any(part in EXCLUDED_ASSET_DIRS for part in relative.parts)


def find_asset_root(markdown_path: Path, repository: bool) -> Path:
  """Return the directory whose files the preview may serve.

  Everything under the root is reachable by any local client for as long as
  the server runs, so the default is the document's own directory. Serving
  the enclosing repository exposes every sibling -- including .git and any
  untracked secrets -- and is therefore opt-in through --repository-assets.
  """
  if not repository:
    return markdown_path.parent.resolve()
  return find_repository_root(markdown_path)


def find_repository_root(markdown_path: Path) -> Path:
  """Return the containing Git root, or the Markdown file's directory."""
  try:
    result = subprocess.run(
      [
        "git",
        "-C",
        str(markdown_path.parent),
        "rev-parse",
        "--show-toplevel",
      ],
      check=False,
      capture_output=True,
      text=True,
    )
  except OSError:
    return markdown_path.parent

  if result.returncode != 0:
    return markdown_path.parent

  root = Path(result.stdout.strip()).resolve()
  try:
    markdown_path.relative_to(root)
  except ValueError:
    return markdown_path.parent
  return root


def cache_directory() -> Path:
  """Return the cross-platform user cache directory for this command."""
  xdg_cache = os.environ.get("XDG_CACHE_HOME")
  base = Path(xdg_cache).expanduser() if xdg_cache else Path.home() / ".cache"
  return base / "md-preview"


def file_sha256(path: Path) -> str:
  """Return a file's SHA-256 digest."""
  digest = hashlib.sha256()
  with path.open("rb") as stream:
    for chunk in iter(lambda: stream.read(64 * 1024), b""):
      digest.update(chunk)
  return digest.hexdigest()


def ensure_stylesheet(
  cache: Path,
  theme: str,
  refresh: bool = False,
  opener: Callable = urllib.request.urlopen,
) -> Path:
  """Return a checksum-verified cached GitHub Markdown stylesheet."""
  spec = STYLESHEETS[theme]
  target = cache / spec.filename
  if not refresh and target.is_file():
    if file_sha256(target) == spec.sha256:
      return target

  cache.mkdir(parents=True, exist_ok=True)
  request = urllib.request.Request(
    spec.url,
    headers={"User-Agent": f"md-preview/{VERSION}"},
  )
  try:
    with opener(request, timeout=15) as response:
      content = response.read(MAX_STYLESHEET_BYTES + 1)
  except (OSError, urllib.error.URLError) as error:
    raise PreviewError(
      f"Could not download the public GitHub Markdown CSS: {error}"
    ) from error

  if len(content) > MAX_STYLESHEET_BYTES:
    raise PreviewError("Downloaded GitHub Markdown CSS is unexpectedly large")

  actual = hashlib.sha256(content).hexdigest()
  if actual != spec.sha256:
    raise PreviewError(
      "Downloaded GitHub Markdown CSS checksum mismatch; refusing to use it"
    )

  temporary_path = None
  try:
    with tempfile.NamedTemporaryFile(
      mode="wb", dir=cache, prefix=f".{spec.filename}.", delete=False
    ) as temporary:
      temporary.write(content)
      temporary_path = Path(temporary.name)
    os.replace(temporary_path, target)
  finally:
    if temporary_path is not None and temporary_path.exists():
      temporary_path.unlink()
  return target


def force_picture_theme(rendered: str, theme: str) -> str:
  """Make picture color-scheme sources agree with a forced theme."""
  if theme == "auto":
    return rendered

  replacements = {
    "dark": "all" if theme == "dark" else "not all",
    "light": "all" if theme == "light" else "not all",
  }
  for color, media in replacements.items():
    pattern = (
      r'media="\(prefers-color-scheme:\s*' + re.escape(color) + r'\)"'
    )
    rendered = re.sub(pattern, f'media="{media}"', rendered)
  return rendered


def render_markdown(markdown: str, theme: str) -> str:
  """Render GFM locally, preserving safe README-oriented raw HTML."""
  raw = cmarkgfm.github_flavored_markdown_to_html(
    markdown,
    options=Options.CMARK_OPT_UNSAFE,
  )
  sanitized = nh3.clean(
    raw,
    tags=ALLOWED_TAGS,
    attributes=ALLOWED_ATTRIBUTES,
    tag_attribute_values=TAG_ATTRIBUTE_VALUES,
    link_rel="noopener noreferrer",
  )
  return force_picture_theme(sanitized, theme)


def document_base(markdown_path: Path, root: Path) -> str:
  """Return the server URL base for document-relative links and images."""
  relative_parent = markdown_path.parent.relative_to(root)
  if relative_parent == Path("."):
    return "/"
  return f"/{quote(relative_parent.as_posix(), safe='/')}/"


def frame_styles(theme: str) -> str:
  """Return the minimal repository README frame around Markdown content."""
  common = """\
html {
  color-scheme: light dark;
}
body {
  margin: 0;
  min-width: 320px;
}
.markdown-body {
  box-sizing: border-box;
  min-width: 200px;
  max-width: 1012px;
  margin: 32px auto;
  padding: 45px;
  border: 1px solid;
  border-radius: 6px;
}
@media (max-width: 767px) {
  .markdown-body {
    margin: 0;
    padding: 15px;
    border: 0;
    border-radius: 0;
  }
}
"""
  light = """\
body { background: #ffffff; }
.markdown-body { border-color: #d0d7de; }
"""
  dark = """\
body { background: #0d1117; }
.markdown-body { border-color: #3d444d; }
"""
  if theme == "light":
    return common + "html { color-scheme: light; }\n" + light
  if theme == "dark":
    return common + "html { color-scheme: dark; }\n" + dark
  return (
    common
    + light
    + "@media (prefers-color-scheme: dark) {\n"
    + "  body { background: #0d1117; }\n"
    + "  .markdown-body { border-color: #3d444d; }\n"
    + "}\n"
  )


def render_page(markdown_path: Path, root: Path, theme: str) -> str:
  """Render a complete local preview page."""
  try:
    markdown = markdown_path.read_text(encoding="utf-8")
  except OSError as error:
    raise PreviewError(f"Could not read {markdown_path}: {error}") from error

  content = render_markdown(markdown, theme)
  base = html.escape(document_base(markdown_path, root), quote=True)
  title = html.escape(markdown_path.name)
  return f"""\
<!doctype html>
<html lang="en" data-color-mode="{theme}">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="referrer" content="no-referrer">
  <base href="{base}">
  <title>{title} — Markdown preview</title>
  <link rel="stylesheet"
        href="{PREVIEW_PREFIX}github-markdown.css">
  <link rel="stylesheet" href="{PREVIEW_PREFIX}frame.css">
  <script defer src="{PREVIEW_PREFIX}reload.js"></script>
</head>
<body>
  <article class="markdown-body entry-content container-lg">
{content}  </article>
</body>
</html>
"""


class PreviewServer(ThreadingHTTPServer):
  """HTTP server carrying immutable preview configuration."""

  daemon_threads = True

  def __init__(
    self,
    address,
    handler,
    markdown_path: Path,
    root: Path,
    stylesheet_path: Path,
    theme: str,
  ):
    self.markdown_path = markdown_path
    self.root = root
    self.stylesheet_path = stylesheet_path
    self.theme = theme
    super().__init__(address, handler)


class PreviewRequestHandler(SimpleHTTPRequestHandler):
  """Serve generated preview routes and repository-relative assets."""

  server: PreviewServer

  def log_message(self, _format, *_args):
    """Suppress live-reload request noise."""

  def send_content(
    self,
    content: bytes,
    content_type: str,
    content_security_policy: Optional[str] = None,
  ):
    """Send one uncached generated response."""
    self.send_response(200)
    self.send_header("Content-Type", content_type)
    self.send_header("Content-Length", str(len(content)))
    self.send_header("Cache-Control", "no-store")
    self.send_header("X-Content-Type-Options", "nosniff")
    if content_security_policy is not None:
      self.send_header(
        "Content-Security-Policy", content_security_policy
      )
    self.end_headers()
    self.wfile.write(content)

  def repository_asset_path(self) -> Optional[Path]:
    """Resolve a regular-file request without escaping the asset root."""
    candidate = Path(super().translate_path(self.path)).resolve()
    root = self.server.root.resolve()
    try:
      relative = candidate.relative_to(root)
    except ValueError:
      return None
    if is_excluded_asset(relative):
      return None
    return candidate if candidate.is_file() else None

  def do_GET(self):
    """Serve preview internals, falling back to repository assets."""
    path = urlsplit(self.path).path
    if path == PREVIEW_PREFIX:
      try:
        page = render_page(
          self.server.markdown_path,
          self.server.root,
          self.server.theme,
        ).encode("utf-8")
      except PreviewError as error:
        self.send_error(500, str(error))
        return
      policy = (
        "default-src 'none'; "
        "script-src 'self'; style-src 'self'; "
        "img-src 'self' data: https:; font-src 'self' data: https:; "
        "connect-src 'self'; object-src 'none'; frame-src 'none'; "
        "form-action 'none'; base-uri 'self'"
      )
      self.send_content(page, "text/html; charset=utf-8", policy)
      return

    if path == f"{PREVIEW_PREFIX}github-markdown.css":
      try:
        css = self.server.stylesheet_path.read_bytes()
      except OSError as error:
        self.send_error(500, str(error))
        return
      self.send_content(css, "text/css; charset=utf-8")
      return

    if path == f"{PREVIEW_PREFIX}frame.css":
      css = frame_styles(self.server.theme).encode("utf-8")
      self.send_content(css, "text/css; charset=utf-8")
      return

    if path == f"{PREVIEW_PREFIX}reload.js":
      self.send_content(
        RELOAD_JAVASCRIPT.encode("utf-8"),
        "text/javascript; charset=utf-8",
      )
      return

    if path == f"{PREVIEW_PREFIX}state.json":
      try:
        modified = self.server.markdown_path.stat().st_mtime_ns
      except OSError as error:
        self.send_error(500, str(error))
        return
      state = json.dumps({"mtime_ns": modified}).encode("utf-8")
      self.send_content(state, "application/json; charset=utf-8")
      return

    if self.repository_asset_path() is None:
      self.send_error(404, "File not found")
      return

    super().do_GET()


def create_server(
  markdown_path: Path,
  root: Path,
  stylesheet_path: Path,
  theme: str,
  host: str = "127.0.0.1",
  port: int = 0,
) -> PreviewServer:
  """Create a loopback preview server without starting it."""
  handler = partial(PreviewRequestHandler, directory=str(root))
  try:
    return PreviewServer(
      (host, port),
      handler,
      markdown_path,
      root,
      stylesheet_path,
      theme,
    )
  except OSError as error:
    raise PreviewError(
      f"Could not bind local preview on {host}:{port}: {error}"
    ) from error


def serve_preview(server: PreviewServer, open_browser: bool) -> int:
  """Serve until interrupted, optionally opening the default browser."""
  host, port = server.server_address[:2]
  url = f"http://{host}:{port}{PREVIEW_PREFIX}"
  print(f"Previewing {server.markdown_path}")
  print(url)
  print("Press Ctrl-C to stop.")
  # Callers capture the URL from a pipe while the server keeps running, so
  # the banner cannot wait for the block buffer to fill at exit.
  sys.stdout.flush()

  if open_browser:
    timer = threading.Timer(0.2, webbrowser.open, args=(url,))
    timer.daemon = True
    timer.start()

  try:
    server.serve_forever()
  except KeyboardInterrupt:
    print("\nPreview stopped.")
  finally:
    server.server_close()
  return 0


def main(argv=None) -> int:
  """Run the command and return a process exit status."""
  try:
    args = parse_args(argv)
    markdown_path = resolve_markdown_path(args.file)
    root = find_asset_root(markdown_path, args.repository_assets)
    stylesheet = ensure_stylesheet(
      cache_directory(),
      args.theme,
      refresh=args.refresh_css,
    )
    server = create_server(
      markdown_path,
      root,
      stylesheet,
      theme=args.theme,
      port=args.port,
    )
    return serve_preview(server, open_browser=not args.no_open)
  except PreviewError as error:
    print(f"md-preview: {error}", file=sys.stderr)
    return 1


if __name__ == "__main__":
  raise SystemExit(main())
