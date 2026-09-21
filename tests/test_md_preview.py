"""Tests for the local GitHub-style Markdown preview command."""

from __future__ import annotations

import hashlib
import importlib.machinery
import importlib.util
import io
import json
import subprocess
import sys
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "bin" / "md-preview.py"


# md-preview declares its dependencies in PEP 723 metadata and is run
# through uv, which resolves them per invocation. Importing it here uses
# whichever interpreter runs the suite, so the dependencies are only present
# if that interpreter happens to have them. Skip rather than fail: a missing
# cmarkgfm says nothing about the command, which uv would satisfy on its own.
_DEPS = ("cmarkgfm", "nh3")
MISSING = [m for m in _DEPS if importlib.util.find_spec(m) is None]
REASON = (
  f"md-preview dependencies not importable here: {', '.join(MISSING)}. "
  "The command itself supplies them through uv; install them into this "
  "interpreter to exercise these tests."
)


def load_command():
  """Load the extensionless command as a Python module."""
  loader = importlib.machinery.SourceFileLoader("md_preview", str(SCRIPT))
  spec = importlib.util.spec_from_loader(loader.name, loader)
  assert spec is not None
  module = importlib.util.module_from_spec(spec)
  sys.modules[spec.name] = module
  previous = sys.dont_write_bytecode
  try:
    sys.dont_write_bytecode = True
    loader.exec_module(module)
  finally:
    sys.dont_write_bytecode = previous
  return module


class FakeResponse(io.BytesIO):
  """Minimal urlopen response for stylesheet cache tests."""

  def __enter__(self):
    return self

  def __exit__(self, *_args):
    self.close()


@unittest.skipIf(MISSING, REASON)
class MarkdownPreviewTests(unittest.TestCase):
  @classmethod
  def setUpClass(cls):
    cls.preview = load_command()

  def test_asset_root_defaults_to_the_document_directory(self):
    with TemporaryDirectory() as raw:
      root = Path(raw).resolve()
      (root / "docs").mkdir()
      doc = root / "docs" / "README.md"
      doc.write_text("# x", encoding="utf-8")
      self.assertEqual(
        self.preview.find_asset_root(doc, False), root / "docs"
      )

  def test_asset_root_widens_to_the_repository_when_requested(self):
    with TemporaryDirectory() as raw:
      root = Path(raw).resolve()
      subprocess.run(
        ["git", "init", "-q", str(root)], check=True, capture_output=True
      )
      (root / "docs").mkdir()
      doc = root / "docs" / "README.md"
      doc.write_text("# x", encoding="utf-8")
      self.assertEqual(self.preview.find_asset_root(doc, False), root / "docs")
      self.assertEqual(self.preview.find_asset_root(doc, True), root)

  def test_version_control_directories_are_never_assets(self):
    """Serving .git would leak remotes, worktrees and staged content."""
    for candidate in (".git/config", "sub/.git/config", ".hg/hgrc"):
      with self.subTest(candidate=candidate):
        self.assertTrue(
          self.preview.is_excluded_asset(Path(candidate))
        )

  def test_ordinary_assets_are_not_excluded(self):
    for candidate in ("logo.png", "img/diagram.svg", "a/b/c.md"):
      with self.subTest(candidate=candidate):
        self.assertFalse(
          self.preview.is_excluded_asset(Path(candidate))
        )

  def test_resolve_markdown_defaults_to_readme(self):
    with TemporaryDirectory() as temp:
      root = Path(temp)
      readme = root / "README.md"
      readme.write_text("# Example\n", encoding="utf-8")

      self.assertEqual(
        self.preview.resolve_markdown_path(None, cwd=root), readme.resolve()
      )
      self.assertEqual(
        self.preview.resolve_markdown_path(str(root), cwd=Path("/")),
        readme.resolve(),
      )

  def test_resolve_markdown_rejects_missing_file(self):
    with TemporaryDirectory() as temp:
      with self.assertRaisesRegex(
        self.preview.PreviewError, "Markdown file not found"
      ):
        self.preview.resolve_markdown_path(
          "missing.md", cwd=Path(temp)
        )

  def test_parse_args_rejects_invalid_environment_theme(self):
    with mock.patch.dict(
      self.preview.os.environ,
      {"MD_PREVIEW_THEME": "bogus"},
    ):
      self.assertEqual(
        self.preview.parse_args(["--theme", "dark"]).theme,
        "dark",
      )
      with self.assertRaises(SystemExit), mock.patch(
        "sys.stderr", new_callable=io.StringIO
      ) as stderr:
        self.preview.parse_args([])

    self.assertIn("must be one of", stderr.getvalue())

  def test_render_sanitizes_html_but_preserves_readme_markup(self):
    markdown = """\
<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="dark.svg">
    <img src="light.svg" alt="logo" width="520" onerror="alert(1)">
  </picture>
</p>

<script>alert("unsafe")</script>

- [x] complete
"""
    rendered = self.preview.render_markdown(markdown, theme="auto")

    self.assertIn('<p align="center">', rendered)
    self.assertIn("<picture>", rendered)
    self.assertIn('srcset="dark.svg"', rendered)
    self.assertIn('type="checkbox"', rendered)
    self.assertIn("checked", rendered)
    self.assertNotIn("onerror", rendered)
    self.assertNotIn("<script", rendered)

  def test_forced_theme_selects_matching_picture_source(self):
    markdown = """\
<picture>
  <source media="(prefers-color-scheme: dark)" srcset="dark.svg">
  <img src="light.svg" alt="logo">
</picture>
"""

    dark = self.preview.render_markdown(markdown, theme="dark")
    light = self.preview.render_markdown(markdown, theme="light")

    self.assertIn('media="all"', dark)
    self.assertIn('media="not all"', light)

  def test_stylesheet_cache_verifies_download(self):
    content = b".markdown-body { color: inherit; }\n"
    digest = hashlib.sha256(content).hexdigest()
    spec = self.preview.StylesheetSpec(
      filename="fixture.css",
      url="https://example.invalid/fixture.css",
      sha256=digest,
    )

    with TemporaryDirectory() as temp:
      cache = Path(temp)

      def opener(_request, timeout):
        self.assertEqual(timeout, 15)
        return FakeResponse(content)

      with mock.patch.dict(self.preview.STYLESHEETS, {"dark": spec}):
        path = self.preview.ensure_stylesheet(
          cache, "dark", opener=opener
        )
        self.assertEqual(path.read_bytes(), content)

        def fail_if_called(_request, timeout):
          raise AssertionError(f"unexpected download with timeout {timeout}")

        self.assertEqual(
          self.preview.ensure_stylesheet(
            cache, "dark", opener=fail_if_called
          ),
          path,
        )

  def test_stylesheet_cache_rejects_wrong_checksum(self):
    spec = self.preview.StylesheetSpec(
      filename="fixture.css",
      url="https://example.invalid/fixture.css",
      sha256="0" * 64,
    )

    with TemporaryDirectory() as temp:
      cache = Path(temp)
      with mock.patch.dict(self.preview.STYLESHEETS, {"auto": spec}):
        with self.assertRaisesRegex(
          self.preview.PreviewError, "checksum mismatch"
        ):
          self.preview.ensure_stylesheet(
            cache,
            "auto",
            opener=lambda _request, timeout: FakeResponse(b"wrong"),
          )
      self.assertFalse((cache / spec.filename).exists())

  def test_server_renders_nested_file_and_static_assets(self):
    with TemporaryDirectory() as temp:
      root = Path(temp) / "repo"
      root.mkdir()
      docs = root / "docs"
      docs.mkdir()
      markdown = docs / "guide.md"
      markdown.write_text(
        "# Guide\n\n![diagram](diagram.svg)\n", encoding="utf-8"
      )
      (docs / "diagram.svg").write_text("<svg/>\n", encoding="utf-8")
      outside = Path(temp) / "outside.txt"
      outside.write_text("private\n", encoding="utf-8")
      (docs / "outside.txt").symlink_to(outside)
      stylesheet = root / "github-markdown.css"
      stylesheet.write_text(
        ".markdown-body { color: inherit; }\n", encoding="utf-8"
      )

      server = self.preview.create_server(
        markdown,
        root,
        stylesheet,
        theme="dark",
        host="127.0.0.1",
        port=0,
      )
      thread = threading.Thread(target=server.serve_forever, daemon=True)
      thread.start()
      host, port = server.server_address

      try:
        with urllib.request.urlopen(
          f"http://{host}:{port}/__md_preview__/", timeout=5
        ) as response:
          page = response.read().decode("utf-8")
          self.assertEqual(response.status, 200)
          self.assertIn("default-src 'none'",
                        response.headers["Content-Security-Policy"])

        self.assertIn('<base href="/docs/">', page)
        self.assertIn("<h1>Guide</h1>", page)
        self.assertIn("github-markdown.css", page)

        with urllib.request.urlopen(
          f"http://{host}:{port}/docs/diagram.svg", timeout=5
        ) as response:
          self.assertEqual(response.read(), b"<svg/>\n")

        for unsafe_path in ("/", "/docs/outside.txt"):
          with self.assertRaises(urllib.error.HTTPError) as context:
            urllib.request.urlopen(
              f"http://{host}:{port}{unsafe_path}", timeout=5
            )
          self.assertEqual(context.exception.code, 404)

        with urllib.request.urlopen(
          f"http://{host}:{port}/__md_preview__/state.json",
          timeout=5,
        ) as response:
          state = json.load(response)
          self.assertEqual(state["mtime_ns"], markdown.stat().st_mtime_ns)
      finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


if __name__ == "__main__":
  unittest.main()
