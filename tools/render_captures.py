#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = ["CairoSVG==2.9.1"]
# ///
"""Rasterize Freeze output using an installed font, without publishing fonts."""
import argparse
import html
import re
import shutil
import subprocess
import tempfile
from pathlib import Path


# Brand surface/text/accent from the owner-supplied logo palette. Supporting
# status colours keep the production green/cyan/magenta/red distinctions.
PALETTE = {
  '#c4c4c4': '#F3F5F7', '#d3e561': '#FF882B', '#31bb71': '#9BC995',
  '#04d7d7': '#8BC8C8', '#ed61d7': '#C3ADD9', '#d74e6f': '#F29B9B',
}


def project_palette(svg):
  """Change SVG colour attributes only, retaining text and geometry."""
  return re.sub(r'(?i)(fill|stroke)="(#[0-9a-f]{6})"',
    lambda match: match[1] + '="' +
      PALETTE.get(match[2].lower(), match[2]) + '"', svg)


def symbol_fallback(svg):
  """Keep Berkeley text; use installed symbol fonts for fold/spinner glyphs."""
  fonts = {}
  def replace(match):
    glyph = match[0]
    if glyph not in fonts:
      family = subprocess.check_output([
        'fc-match', '-f', '%{family}', ':charset=' + format(ord(glyph), 'x')],
        text=True).split(',')[0]
      fonts[glyph] = html.escape(family, quote=True)
    return '<tspan font-family="' + fonts[glyph] + '">' + glyph + '</tspan>'
  return re.sub(r'[▸▾\u2800-\u28ff]', replace, svg)


def main():
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument('ansi', type=Path)
  parser.add_argument('output', type=Path)
  parser.add_argument('--freeze', default=shutil.which('freeze'))
  parser.add_argument('--font-family', default='Berkeley Mono')
  args = parser.parse_args()
  if not args.freeze:
    parser.error('Freeze is required')
  if not shutil.which('fc-match'):
    parser.error('fontconfig fc-match is required to verify the installed font')
  matched = subprocess.check_output(['fc-match', '-f', '%{family}',
                                     args.font_family], text=True)
  if args.font_family.casefold() not in matched.casefold().split(','):
    parser.error('requested font is not installed; refusing a fallback')
  import cairosvg
  with tempfile.TemporaryDirectory(prefix='agent-kit-raster-') as temporary:
    svg = Path(temporary) / 'capture.svg'
    subprocess.run([args.freeze, '--execute',
      'cat ' + __import__('shlex').quote(str(args.ansi)),
      '--output', str(svg), '--background', '#202B36',
      '--font.family', args.font_family, '--font.size', '16',
      '--padding', '20', '--margin', '0', '--window=false'], check=True)
    svg.write_text(symbol_fallback(project_palette(svg.read_text())))
    cairosvg.svg2png(url=str(svg), write_to=str(args.output))


if __name__ == '__main__':
  main()
