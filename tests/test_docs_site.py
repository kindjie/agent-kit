"""Documentation staging and generated link checks."""
import importlib.util
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def module(name):
  spec = importlib.util.spec_from_file_location(name, ROOT / 'tools' / (name + '.py'))
  result = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(result)
  return result


class SiteTest(unittest.TestCase):
  def test_stage_keeps_one_home_and_links_to_public_source(self):
    staging = module('stage_docs')
    with tempfile.TemporaryDirectory() as temporary:
      root = Path(temporary) / 'repo'
      root.mkdir()
      (root / 'docs').mkdir()
      (root / 'bin').mkdir()
      (root / 'README.md').write_text('[Guide](docs/guide.md)')
      (root / 'docs/guide.md').write_text('[Home](../README.md) [Command](../bin/tool)')
      (root / 'bin/tool').write_text('source')
      (root / '.secret').write_text('never publish')
      out = Path(temporary) / 'stage'
      staging.stage(root, out)
      self.assertTrue((out / 'index.md').is_file())
      self.assertFalse((out / 'README.md').exists())
      self.assertFalse((out / '.secret').exists())
      self.assertFalse((out / 'bin/tool').exists())
      text = (out / 'docs/guide.md').read_text()
      self.assertIn('../index.md', text)
      self.assertIn('https://github.com/kindjie/agent-kit/blob/main/bin/tool', text)

  def test_site_surfaces_preserve_portable_markdown_source(self):
    staging = module('stage_docs')
    with tempfile.TemporaryDirectory() as temporary:
      root = Path(temporary) / 'repo'
      root.mkdir()
      original = ('<!-- site:paper -->\n\n## Setup\n\n'
                  'Readable prose.\n\n<!-- /site:paper -->\n')
      (root / 'README.md').write_text(original)
      out = Path(temporary) / 'stage'
      staging.stage(root, out)
      text = (out / 'index.md').read_text()
      self.assertIn('<div class="ak-section ak-section--paper" markdown="1">', text)
      self.assertIn('## Setup', text)
      self.assertIn('</div>', text)
      self.assertEqual((root / 'README.md').read_text(), original)

  def test_checker_rejects_missing_file_anchor_and_escape(self):
    checker = module('check_docs_links')
    with tempfile.TemporaryDirectory() as temporary:
      root = Path(temporary)
      (root / 'index.html').write_text('<a href="guide/#missing">x</a><img src="absent.png"><a href="../outside">x</a>')
      (root / 'guide').mkdir()
      (root / 'guide/index.html').write_text('<h1 id="exists">Guide</h1>')
      failures = checker.check_site(root)
      self.assertEqual(len(failures), 3)
      self.assertTrue(any('missing anchor' in x for x in failures))
      self.assertTrue(any('escapes' in x for x in failures))
      self.assertTrue(any('missing local target' in x for x in failures))

  def test_checker_accepts_valid_local_links(self):
    checker = module('check_docs_links')
    with tempfile.TemporaryDirectory() as temporary:
      root = Path(temporary)
      (root / 'index.html').write_text('<h1 id="top">Home</h1><a href="#top">x</a><a href="https://example.com/">x</a>')
      self.assertEqual(checker.check_site(root), [])

  def test_stage_refuses_unrelated_output_directory(self):
    staging = module('stage_docs')
    with tempfile.TemporaryDirectory() as temporary:
      root = Path(temporary) / 'repo'
      root.mkdir()
      (root / 'README.md').write_text('Home')
      out = Path(temporary) / 'keep'
      out.mkdir()
      sentinel = out / 'keep.txt'
      sentinel.write_text('keep')
      with self.assertRaises(ValueError):
        staging.stage(root, out)
      self.assertEqual(sentinel.read_text(), 'keep')

  def test_github_heading_links_keep_double_hyphens(self):
    slug = module('site_slug')
    self.assertEqual(slug.slugify('Step 0 — is it even on-CPU?', '-'),
                     'step-0--is-it-even-on-cpu')

  def test_mermaid_default_checks_staged_home_and_references(self):
    import sys
    sys.path.insert(0, str(ROOT / 'tools'))
    import check_mermaid as checker
    self.assertEqual(checker.DOCS_ROOT, ROOT / 'build/docs-source')
    with tempfile.TemporaryDirectory() as temporary:
      root = Path(temporary)
      (root / 'bin').mkdir()
      (root / 'index.md').write_text('```mermaid\nflowchart LR\nA-->B\n```')
      (root / 'bin/tool.md').write_text('```mermaid\nunfinished')
      fences, failures = checker.collect_fences(root)
      self.assertEqual(len(fences), 1)
      self.assertEqual(len(failures), 1)
      self.assertIn('bin/tool.md', failures[0])

  def test_capture_palette_changes_colours_without_changing_terminal_text(self):
    captures = module('render_captures')
    source = '<svg><text fill="#c4c4c4">Working T-0001</text><text fill="#D3E561">Waiting</text><text fill="#31BB71">done</text></svg>'
    themed = captures.project_palette(source)
    self.assertIn('fill="#F3F5F7"', themed)
    self.assertIn('fill="#FF882B"', themed)
    self.assertIn('fill="#9BC995"', themed)
    import xml.etree.ElementTree as ET
    self.assertEqual(''.join(ET.fromstring(themed).itertext()),
                     ''.join(ET.fromstring(source).itertext()))

  def test_html_image_paths_account_for_nested_readme_index_pages(self):
    staging = module('stage_docs')
    with tempfile.TemporaryDirectory() as temporary:
      root = Path(temporary) / 'repo'
      (root / 'docs/demo').mkdir(parents=True)
      (root / 'README.md').write_text('Home')
      (root / 'docs/demo/README.md').write_text('<img src="../assets/example.png">')
      (root / 'docs/guide.md').write_text('<img src="assets/example.png">')
      out = Path(temporary) / 'stage'
      staging.stage(root, out)
      self.assertIn('src="../assets/example.png"',
                    (out / 'docs/demo/README.md').read_text())
      self.assertIn('src="../assets/example.png"',
                    (out / 'docs/guide.md').read_text())

  def test_site_home_uses_header_logo_instead_of_os_themed_wordmark(self):
    staging = module('stage_docs')
    with tempfile.TemporaryDirectory() as temporary:
      root = Path(temporary) / 'repo'
      root.mkdir()
      original = '<picture id="agent-kit-wordmark"><img src="logo.svg"></picture>\n# agent-kit'
      (root / 'README.md').write_text(original)
      out = Path(temporary) / 'stage'
      staging.stage(root, out)
      self.assertNotIn('<picture', (out / 'index.md').read_text())
      self.assertIn('# agent-kit', (out / 'index.md').read_text())
      self.assertEqual((root / 'README.md').read_text(), original)
