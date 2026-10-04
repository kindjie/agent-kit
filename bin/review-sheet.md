# Review sheet reference

`review-sheet` builds a local, single-file review page and validates the
downloaded answers. It uses Python 3.9+ and the standard library. It does not
upload media or write project records.

```sh
./bin/review-sheet schema > review-sheet.schema.json
./bin/review-sheet build description.json --output ignored/review.html
./bin/review-sheet import review-results.json \
  --description description.json --resolved ignored/review.resolved.json \
  --require-complete
./bin/review-sheet status review-results.json
```

`build` also writes `<page>.resolved.json` beside the page. Preserve both
until the downloaded results are imported. Keep a separate copy if rebuilding
the same page before importing an older export. The resolved file records the
description, scope digests, and hashes of built-in component files. Passing
`--resolved` verifies that exported answers came from that frozen build.
Import recomputes current media hashes and scope digests from the description;
changed media, fields, evidence, questions, options, layout, membership or
components make a former answer stale. A digest proves consistency with the
current files, not that caller-supplied evidence is still authoritative.
Project adapters must check that against their current records.
The digest includes every resolved media presentation field, including `fps`
and the derived MIME type. It also includes the SHA-256 of
`review_sheet_page.js` and `review_sheet_page.css`. A page
core change therefore makes old answers stale. Stage 1 permits import without
`--resolved`; it will be required once presets exist so an import is bound to
the exact frozen preset and component versions used by the page.

## Description

```json
{
  "review": "sample-batch-01",
  "title": "Sample variants",
  "instructions": "Choose the clearer variant.",
  "caveats": ["This is a visual preference, not release approval."],
  "privacy": "private",
  "decisions": {
    "verdict": {"kind": "choice", "required": true,
                "options": ["keep", "revise"], "keys": "12"},
    "notes": {"kind": "text"}
  },
  "groups": [{
    "id": "set-one", "layout": "grid",
    "pick": {"kind": "best", "required": true},
    "items": [
      {"id": "variant-a", "media": [{"kind": "image", "src": "a.png"}]},
      {"id": "variant-b", "media": [{"kind": "image", "src": "b.png"}]}
    ]
  }]
}
```

Items can also be top-level in `items`. Item decisions inherit `decisions`
and can override them by name. `decisions_review` and a group's `decisions`
define decisions at those scopes. A group `pick` creates a choice over its
ordered item IDs. Supported decision kinds are `choice`, `boolean`, `flags`,
`text`, and `number` (`min`, `max`, and `unit`). `authority: true` requires an
explicit confirmation and embeds the scope's media. It has no shortcut.
Other decisions reference image, animated image, video and audio files in
place. Text is embedded so it works under the page's CSP on `file://`. Link
entries are inert text, and private reviews omit the copy button.

Every media path must stay within the description directory; `..` and
symlink escapes fail. `extends`, custom components, ratings, tags, marks,
ranking and richer layouts are deferred. Stage 1 includes only built-in
components. Their files live in `review_sheet_components/` and are bundled
into the page. `review-sheet schema` prints the published description schema.

## Page and results

Use `j/k` for item navigation, `J/K` for groups, `n` for the next required
unanswered item, `f` for focus mode, `/` to filter, `u` to undo, and `?` for
the legend. Choice hotkeys are declared in the description; flags use
numbered keys by default. Export remains available while incomplete and
reports the number of required answers left. The downloaded JSON is the
handoff; localStorage is only a guarded draft. Re-import into the page shows
different valid answers side by side for manual resolution. The page rejects
bad values, stale digests and unmatched authority media hashes. An authority
answer re-imported from a file remains inherited until the person confirms it
on the page. Unavailable draft storage and unreadable stored records have
separate warnings. The discard button removes stale or unmatched draft keys
only after explicit confirmation. Two open tabs can overwrite each other's
draft; export before switching tabs or use one tab at a time.

Each result entry has `answered`, `unanswered`, `inherited`, `stale`,
`conflict`, or `invalid` state. Conflicts have no value and appear in the
`conflicts` list with each side's state, reviewer and value. Answered entries
preserve `evidence` (`verified` for
validated authority media, `unverified` otherwise) and `media_hashes`.
`false`, `0`, and the empty string are valid answers for
their respective kinds. `import` prints normalized JSON without changing a
project catalogue. It exits 0 for clean input, 2 for invalid input, or 3
for stale or conflicting input. With `--require-complete`, required incomplete
input also exits 3. Multiple results files with different answers
to one decision conflict; browser timestamps never resolve the conflict.
An orphaned answer exits 2 and appears in `orphaned`.
Files from different reviewers are never merged automatically; affected
entries become conflicts. A `--resolved` hash mismatch exits 3 as stale,
including when the packet has no answers.

By default `build` refuses output in a Git worktree unless the output path
is ignored. `--allow-tracked` is an explicit override. Import warns about
Git worktree results paths. Private reviews should stay local and never be
hosted. Content Security Policy blocks connections and forms; description
text is escaped and no external scripts load. Authoritative media is
embedded, hashed in the browser with `crypto.subtle`, displayed from a
`blob:` URL, and its browser-computed hashes accompany answers. Reference
media is marked unverified. If a browser rejects sibling media on `file://`,
the page shows a load failure and disables its decisions. Chromium and
Firefox are tested; Safari is best effort.

## Limits and tests

Default limits: 5,000 items, 2 MiB per component file, 8 MiB per component,
16 MiB per embedded file, 128 MiB total embedded, 256 MiB page, 64 KiB per
text answer, 16 MiB results. `build` and `import` accept matching `--max-*`
flags to raise limits. Stage 1's built-in components are below their caps.

The browser harness is pinned by `tests/package-lock.json`. Install it with
`npm ci --prefix tests`, then `tests/node_modules/.bin/playwright install
chromium firefox`. The full Python suite runs the browser harness when Node,
Playwright and a browser are present. It uses system Google Chrome by default
and falls back to bundled Chromium only when Chrome is absent. It tries bundled
Firefox when installed. Each run prints the browser version and reports PASS
or SKIP with a reason; the wrapper skips only when no browser can run. Run it
outside the sandbox so Chrome can launch. Record unavailable browsers as
validation deviations.
