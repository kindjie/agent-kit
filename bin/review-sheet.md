# Review sheet reference

`review-sheet` builds a local, single-file page and validates downloaded
answers. It uses Python 3.9+ and the standard library. It does not upload
media or write project catalogues.

```sh
./bin/review-sheet schema
./bin/review-sheet build description.json --output ignored/review.html
./bin/review-sheet import review-results.json \
  --resolved ignored/review.resolved.json --check-current
./bin/review-sheet status review-results.json
```

`build` also writes `<page>.resolved.json`. Preserve that exact file with
its page until results are imported; rebuilding the same output replaces it.
`import` requires `--resolved` and interprets every packet from that frozen
file alone. A different resolved-file hash is stale. `--check-current`
separately reports missing or changed description, preset, component, and
media inputs without changing answer states. A project adapter must still
check caller evidence against its own current records before applying results.

## Description and decisions

```json
{
  "review": "sample-batch-01",
  "title": "Sample variants",
  "extends": ["image-variants"],
  "instructions": "Choose the clearer variant.",
  "caveats": ["Visual preference only."],
  "decisions": {
    "verdict": {"kind": "choice", "required": true,
                "options": ["keep", "revise"], "keys": "12"},
    "notes": {"kind": "text"}
  },
  "groups": [{"id": "set-one", "items": [
    {"id": "variant-a", "media": [{"kind": "image", "src": "a.png"}]},
    {"id": "variant-b", "media": [{"kind": "image", "src": "b.png"}]}
  ]}]
}
```

Items may also be top-level. `decisions` applies to items;
`decisions_review`, group `decisions`, and item `decisions` set other scopes.
Decision kinds are `choice`, `boolean`, `flags`, `text`, and `number` (with
optional `min`, `max`, and `unit`). Group `pick` chooses an ordered item ID;
synchronized comparisons also permit `tie` and `none`. Export remains
available when required answers are missing. `authority: true` requires
explicit confirmation, has no hotkey, and forces embedded media. A visual
preference can use embedded media with `authority: false`.

Built-in media kinds are `image`, `animated`, `video`, `audio`, `text`, `link`,
and `frame-sequence`. Local media can set `mode: "embed"`; text and frame
sequences are always embedded. Every embedded byte is browser-hashed before
it enables decisions. Browser-computed hashes accompany each answered
scope and are checked on import independently of `authority`. Reference
media is unverified. Local paths stay inside the description directory;
symlinks and `..` are refused. Links are inert text.

The page uses `j/k` for items, `J/K` for groups, `n` for next required
unanswered, `f` for focus mode, `/` to filter, `u` to undo, and `?` for a
legend. Choice hotkeys are declared; flags use numbered keys by default.
Component keys yield to global and decision keys. Local storage is a guarded
draft; the downloaded JSON is the handoff. Re-import shows differing valid
answers for manual resolution.

## Components and trust

A component directory contains `component.json`, `component.js`, and
optional `README.md` and manifest-declared worker or wasm assets. Its
manifest declares `kind`, SemVer `version`, integer `api: 1`, `workers`,
and `wasm`. Kinds are portable directory names: letters, digits, hyphens and
underscores, starting with a letter or digit. They cannot escape the component
lookup roots.
Its classic script registers exactly once:

```js
ReviewSheet.register({
  kind: 'example', version: '1.0.0', api: 1,
  keys: {p: 'play or pause'},
  render(media, api) {
    const element = document.createElement('audio');
    element.src = api.url;
    return element;
  },
  blur(element) { element.pause(); },
  dispose(element) { element.pause(); element.removeAttribute('src'); }
});
```

`render` returns an element or a promise of one. Optional `focus`, `blur`,
`key`, `showFrame`, and `dispose` hooks receive that element and API. `blur`
stops playback; `dispose` releases listeners and nodes. The host catches
failures, blocks affected decisions, ignores late results after disposal,
and cleans up tracked workers and blob URLs. Media state is keyed by item ID
and media index.

The read-only per-media API has `url` (verified blob URL or sibling file URL),
`bytes`
(a copy of embedded bytes, else `null`), `settings`, and `group` (ordered
public members and focused ID). `api.media(itemId, index)` awaits a sibling
handle in the same group and rejects missing, failed, or disposed entries.
It exposes bytes, URL, play and pause, never answers. Avoid awaiting mutually
dependent siblings inside `render`. `api.ready()` marks non-native playback
ready, `api.fail(text)` blocks it, and `api.note(text)` shows escaped text.
`api.worker(name)` creates a tracked Worker from declared inlined source;
`api.wasm(name, imports)` instantiates declared inlined bytes. Both reject
undeclared names. Components never fetch extra paths.

`settings` includes `gain`, `gainCap`, `privacy`, `reducedMotion`, and
`darkMode`. For frame sequences, `api.frame(index, signal)` returns a verified
frame URL, integer index, source frame and phase. `showFrame(index, element,
api, signal)` returns `{frame, commit()}` after decoding without displaying
it. The host aborts obsolete requests and commits both exact-index responses
only for the current generation; components must observe the abort signal.

Components run in the page realm and can read answers: this API is **not a
sandbox**. Built-ins run by default. Review an external component's code
before trusting its exact bundle digest:

```sh
./bin/review-sheet component trust path/to/component
./bin/review-sheet component list
./bin/review-sheet component untrust SHA256
```

The owner file is `$XDG_CONFIG_HOME/review-sheet/trusted-components.json`
or `~/.config/review-sheet/trusted-components.json`, mode `0600`. A bundle
digest is SHA-256 of canonical JSON mapping each declared relative filename,
including `README.md` when present, to its byte SHA-256. Trust pins bytes,
not a changing path. Lookup order: explicit `components` path relative to
the description, nearest repository `.review-sheet/components/`, user config,
then built-ins. A found untrusted or invalid override fails the build. The
frozen file records selected manifests, file hashes, and bundle digests.
The page uses a hash CSP, enables wasm evaluation only for a selected
component declaring wasm, and blocks network connections.

## Presets

`extends` applies presets in order, then the description. A preset can
extend another; cycles fail. Bare names search repository
`.review-sheet/presets/`, user config, then shipped presets. A path resolves
from its referring JSON file. Scalars and arrays replace. `decisions` merges
by name, each definition replacing whole. `null` deletes a key or decision.
Presets cannot inherit `groups` or `items`. One `group_defaults` object is
replaced whole and fills absent fields of explicitly supplied groups.
The frozen file records every preset path and byte hash.

```sh
./bin/review-sheet preset list
./bin/review-sheet build description.json --output ignored/review.html \
  --explain
./bin/review-sheet preset save reusable --from description.json
./bin/review-sheet preset save reusable --from description.json \
  --write --scope repo
```

`--explain` reports origins and deletions, including deleted paths and earlier
origins. `preset save` prints only reusable decisions, group defaults,
instructions, caveats, and allowlisted layout/media/gain/control settings.
It omits review ID, title, context, privacy, groups, items, components, and
evidence. `--write` saves atomically in repo or user scope; `--overwrite`
is required to replace an existing preset. Shipped presets: `verdict`,
`image-variants`, `audio-takes`, `video-clips`, `owner-decision`, and
`synchronized-comparison`.

## Synchronized comparison

`layout: "synchronized-comparison"` gives a group one reference and ordered
candidate IDs. Each item supplies one `frame-sequence` media entry per view.
Its `src` names a JSON index with `width`, `height`, and `frames`; each frame
has `index`, `source_frame`, `phase`, relative PNG `src`, and `sha256`.
The builder checks dimensions, contiguous display indices, shared source
frame maps, hold aliases, hashes, and size limits. The page embeds each
unique image once per sequence. It never seeks a video timestamp for an
exact frame.

```json
{
  "id": "case-one", "layout": "synchronized-comparison",
  "pick": {"kind": "best", "required": false},
  "comparison": {
    "reference": "reference-id", "candidates": ["a-id", "b-id"],
    "views": ["front", "side"], "fps": [60, 1],
    "motion_frames": 44, "hold_start": 15, "hold_end": 15,
    "frame_count": 74, "key_frames": [0, 22, 43],
    "crops": {"full": [0, 0, 400, 360],
              "detail": [0, 180, 400, 180]}
  },
  "items": [{"id": "reference-id", "media": [
    {"kind": "frame-sequence", "view": "front",
     "src": "frames/reference/front/index.json"}
  ]}]
}
```

The abbreviated example shows one media entry; a valid group supplies every
view for every listed item. The page shows reference beside the selected
candidate, labels candidates A/B/…, and shares one integer frame index.
Controls cover play/pause, scrub, exact step, 0.125–1× speed, half-open loop
range, angle, crop, next supplied key, case navigation, and expand. Paired
frames commit only when both decodes match the current generation. A missing
frame stops that case and disables its decisions. At most 32 decoded frame
URLs are cached; case changes release hidden frame sources too. Generic flags
and notes are group decisions. Best may be a
candidate ID, `tie`, or `none`; export keeps the opaque ID behind its label.

Reveal is optional and records an immutable first-reveal event and monotonic
best-decision revision. Export includes `revealed`, `first_reveal`,
`decision_revision`, and derived `revealed_before_decision`. Undo and
re-import advance revisions; reload hides identities but retains history.
Import rejects impossible sequences and conflicting histories across packets.
Draft reveal histories also carry the scope digest; a stale history is
preserved in storage and requires reconfirming the best decision.
Anonymity is an interface guard, not secrecy: a page with a reveal mapping
can contain recoverable identities.

## Results, privacy, and tests

Each answer is `answered`, `unanswered`, `inherited`, `stale`, or `invalid`;
normalized import may also report `conflict`. Answered entries preserve
`evidence: verified` for embedded bytes, otherwise `unverified`. `false`,
`0`, and `""` are valid. Different answered values conflict; timestamps
and reviewer names never choose a winner. Identical answers from different
reviewers merge. `--require-complete` fails when a required answer is not
valid. Exit codes: 0 clean, 2 invalid, 3 stale, conflicting, or required
incomplete. Orphaned answers exit 2.

By default `build` refuses output in a Git worktree unless ignored;
`--allow-tracked` overrides. Import warns about Git or synced results paths.
Keep private pages and media local. The page blocks connections and forms and
shows errors when reference media cannot load. Current Chrome is the browser
gate. Firefox is a known gap on this macOS host; Safari is best effort.

Default limits: 5,000 items; 2 MiB per component file and 8 MiB per
component; 16 MiB per embedded file, 128 MiB embedded total, 96 KiB per
comparison PNG, 256 MiB page; 64 KiB per text answer and 16 MiB results.
`build` and `import` accept corresponding `--max-*` flags except the fixed
96 KiB frame ceiling. Real review media needs separate fidelity, page size,
and browser memory checks before delivery.

Install the pinned harness with `npm ci --prefix tests`. Run
`PYTHONDONTWRITEBYTECODE=1 python3 -m unittest tests.test_review_sheet
tests.test_review_sheet_stage2 tests.test_review_sheet_browser` outside the
sandbox so system Chrome can launch. It falls back to bundled Chromium only
if Chrome is absent and reports each browser and skip explicitly.
