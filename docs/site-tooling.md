# Documentation maintenance for agents

The repository Markdown is the maintained source. The site stages those
files into ignored `build/docs-source`, rewrites links to repository-only
source files to GitHub, and renders one current site. No copied release
version trees or API generator are needed.

## Build and preview

The documentation build uses Python 3.12; runtime command requirements are
separate. From the repository root:

```sh
python3.12 -m venv .venv-docs
.venv-docs/bin/python -m pip install --require-hashes -r requirements-docs.txt
python3 tools/fetch_mermaid.py
python3 tools/stage_docs.py
.venv-docs/bin/python -m mkdocs build --strict
python3 tools/check_docs_links.py build/site
python3 tools/check_mermaid.py --docs-root build/docs-source
.venv-docs/bin/python -m mkdocs serve --dev-addr 127.0.0.1:8000
```

Re-run staging after changing Markdown or assets. Never add generated
output, virtual environments, private transcripts, provider caches, or font
files to Git. The site serves only its generated documentation on loopback.
The GitHub-style README preview is separate: `md-preview README.md --no-open`.

The lockfile is generated with:

```sh
uv pip compile requirements-docs.in --python-version 3.12 --universal \
  --generate-hashes -o requirements-docs.txt
```

[Material for MkDocs](https://squidfunk.github.io/mkdocs-material/) provides
navigation, local search, themes and code copy buttons. Mermaid is fetched
at build time with pinned SHA-256 checks and served from the site itself.
Fonts use the system stack; screenshots do not distribute Berkeley Mono.

## Publication boundary

Preview and review before publishing. The intended address is
`agent-kit.owx.dev`; a local preview does not establish that the domain or
hosting is configured. Use the agent-kit repository's own GitHub Pages
settings and deployment, independently of Tess. Configure and verify the
custom domain before its DNS record; use GitHub's actual verification value.
Never guess that value or change live DNS while preparing a preview.

## Reused tooling and attribution

`tools/check_docs_links.py`, `tools/check_mermaid.py`,
`tools/fetch_mermaid.py`, and the self-hosted Mermaid template are adapted
from [tess](https://github.com/kindjie/tess), under its MIT licence:

Copyright (c) 2026 tess contributors

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
