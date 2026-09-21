---
name: preview-markdown
description: >-
  Serve and visually validate Markdown with the local GitHub-style browser
  preview. Use when asked to preview a README or Markdown file, show how
  Markdown will look on GitHub, provide a local preview URL, or inspect
  rendered headings, tables, code blocks, links, images, and relative assets.
---

# Preview Markdown

Use `md-preview` as the rendering authority. Do not duplicate its renderer or
replace it with an API-backed service.

If `md-preview` reports a missing prerequisite, relay what it asks for and
stop. Do not install it unprompted, substitute another renderer, or
hand-render the Markdown.

## Serve the document

1. Resolve the requested Markdown file. Use `README.md` when the user asks for
   a repository preview without naming a file.
2. Start `md-preview <file> --no-open` in a persistent foreground session.
   Add `--theme light` or `--theme dark` only when the user requests it; the
   default follows the browser preference.
3. Capture the loopback URL printed by the command. Keep the session alive
   while validation or user review is in progress.

Run `md-preview --help` for flags rather than restating its complete command
reference here.

## Validate the preview

Open the loopback URL with an available browser-control capability when the
request includes visual review or validation. Check the rendered features
that matter to the document, including:

- visible heading and section structure;
- code blocks, tables, lists, and task markers;
- logos, images, and repository-relative assets;
- light, dark, or narrow layouts when relevant to the request.

Report observed evidence rather than claiming pixel-identical GitHub output.
The preview uses GitHub's Markdown styling but does not reproduce the entire
GitHub page chrome.

## Hand off and stop

Provide the local URL after the preview is ready and any requested validation
passes. State that the server is being retained for review. Stop it when the
user finishes, when replacing it with another preview, or before ending work
unless the user still needs it.

Do not edit the Markdown merely to create a preview. If rendering exposes a
content problem, report it and edit only when requested.

## Privacy

`md-preview` renders and sanitizes Markdown locally; it does not upload the
document to a rendering API. The browser may still request remote images or
other assets explicitly referenced by the document. Preserve that distinction
when describing the preview's privacy.
