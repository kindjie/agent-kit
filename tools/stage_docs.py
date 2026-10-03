#!/usr/bin/env python3
"""Stage authored public documentation without copying runtime data."""
import argparse
import re
import shutil
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
PUBLIC_SOURCE = 'https://github.com/kindjie/agent-kit/blob/main/'
SUFFIXES = {'.md', '.png', '.svg', '.css', '.js', '.woff2'}
LINK = re.compile(r'(\]\(\s*)(<?[^\s)]+>?)(\s*\))')


def stage(root, destination):
  root, destination = Path(root).resolve(), Path(destination).resolve()
  if destination == root or root.is_relative_to(destination):
    raise ValueError('staging must not replace the source repository')
  if destination.exists():
    if not (destination / '.agent-kit-docs-stage').is_file():
      raise ValueError('refusing to replace an unrelated directory')
    shutil.rmtree(destination)
  destination.mkdir(parents=True)
  (destination / '.agent-kit-docs-stage').touch()
  sources = [(root / 'README.md', Path('index.md'))]
  for directory in ('docs', 'skills', 'bin'):
    folder = root / directory
    if folder.exists():
      for source in sorted(folder.rglob('*')):
        if source.is_file() and not source.is_symlink():
          if source.suffix in SUFFIXES and (directory == 'docs' or source.suffix == '.md'):
            sources.append((source, source.relative_to(root)))
  selected = {source.resolve() for source, _ in sources}
  for source, relative in sources:
    target = destination / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    if source.suffix != '.md':
      shutil.copyfile(source, target)
      continue
    def rewrite(match):
      url = match[2].strip('<>')
      parsed = urlsplit(url)
      if parsed.scheme or parsed.netloc or not parsed.path:
        return match[0]
      linked = (source.parent / parsed.path).resolve()
      if linked == root / 'README.md':
        url = url.replace('README.md', 'index.md')
      elif linked not in selected and linked.exists() and linked.is_relative_to(root):
        url = PUBLIC_SOURCE + linked.relative_to(root).as_posix()
        if parsed.fragment:
          url += '#' + parsed.fragment
      return match[1] + url + match[3]
    text = LINK.sub(rewrite, source.read_text())
    if relative == Path('index.md'):
      # The site header supplies the logo. The GitHub wordmark follows OS
      # theme and cannot follow Material's manual theme toggle reliably.
      text = re.sub(r'<picture id="agent-kit-wordmark">.*?</picture>\s*',
                    '', text, flags=re.S)
    # MkDocs rewrites Markdown images, but raw HTML paths need the extra
    # directory introduced by pretty page URLs (except the homepage).
    if relative != Path('index.md') and relative.name != 'README.md':
      def html_image(match):
        url = match[2]
        if urlsplit(url).scheme or url.startswith('/'):
          return match[0]
        return match[1] + '../' + url + match[3]
      text = re.sub(r'(src=["\'])([^"\']+)(["\'])', html_image, text)
    target.write_text(text, encoding='utf-8')


if __name__ == '__main__':
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument('--output', type=Path, default=ROOT / 'build/docs-source')
  args = parser.parse_args()
  stage(ROOT, args.output)
