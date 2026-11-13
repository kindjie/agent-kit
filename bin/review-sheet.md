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
media inputs, plus revoked or changed owner component trust, without changing
answer states. Import refuses a frozen component whose owner trust has been
revoked or changed, whether or not `--check-current` is selected. A project
adapter must still
check caller evidence against its own current records before applying results.

## Opaque project editors (local prototype)

This prototype carries one review-scoped project editor separately
from primitive answers and media components. It does not migrate an existing
project UI or establish browser, fidelity, scale or canonical acceptance.
Descriptions without an editor retain the ordinary v1 contract.

An explicit top-level `editor` has `kind`, relative `path`, `source_identity`,
`parameters` (an object), and `initial_state` (opaque UTF-8 text). Editor state
cannot be inherited or saved through presets, or mixed with primitive
decisions, picks or synchronized comparisons. Hidden historical records may
remain in the initial text independently of visible items. The frozen resolved
file keeps the complete source description. The browser embeds one authoritative
resolved editor configuration; owner code receives it through `create(config)`.

The folder contains `editor.json` with `role: "editor"`, a simple `kind`,
SemVer `version`, `api: 1` (state only) or `api: 2` (one owner view),
and an ordered, nonempty `scripts` array. Scripts
are ordinary trusted code under the page CSP, with no additional network,
worker, wasm or media authority. Review their complete inventory before
`review-sheet editor trust PATH --yes`. Editor trust lives separately from
component trust; `editor list` and `editor untrust SHA256` inspect/revoke it.
Existing component file/total limits also bound editor bundles.

API2 may also declare an ordered `styles` array of relative `.css` files.
Styles share those exact file/total bounds, safe-path checks and trusted bundle
inventory. The host appends them in declaration order to its existing stylesheet
before computing its single CSP hash; no inline-style permission or new origin
is added. CRLF/lone CR normalize to LF for emitted text and its CSP hash;
NUL is refused. The original file bytes remain the trust inventory.
Keep project CSS scoped to the dedicated owner roots. Closing-style
markup is refused. API1 cannot declare nonempty styles; an absent style list
leaves existing state-only behavior and bindings unchanged. Dynamic style tags
inside owner HTML are not authorized by this contract.

Scripts register `ReviewSheet.registerEditor({kind, version, api: 1, create})`.
`create(config, {changed})` receives copied initial text, parameters, source
identity and visible IDs. It returns synchronous `snapshot()` and
`prepare(raw)` methods. Preparation must preserve live state and undo history.
The returned transaction has synchronous `snapshot()`, `commit()` and
`rollback()` methods; the owner validates lifecycle and staleness before
writes. Rollback must restore the full prior state even when commit throws
after swapping it. Thenables are refused and their rejections handled; pending
work or a failed rollback leaves state explicitly unrecovered, with normal
export disabled. This API is not an asynchronous transaction framework.

The host freezes source, bundle, parameters, exact baseline bytes and visible
IDs into an editor digest. Editor pages, drafts and results use v2 so an older
v1 importer cannot silently drop state. Results contain `answers: {}` and
`project_state: {schema_version: 1, editor_sha256, data}`. The core never parses
the domain data, mints owner IDs or counts inherited state as new answers.
CLI import validates one bound packet and labels project validation `not-run`;
it refuses merging and `--require-complete`. The owning canonical importer
still decides validation, conflicts and catalogue writes.

The complete candidate result is bounded before owner commit. Persistence is
suppressed throughout prepare/commit/rollback, then attempted after a verified
commit. Quota failure retains accepted state in memory and the old draft on
disk, with an explicit unsaved status and recovery input. A refused import
retains its original text and prior draft; another import is not consumed while
recovery is pending. Export that recovery input before explicitly discarding
it. The factory receives one isolated configuration snapshot. Its digest is captured
before the factory runs, so caller or factory mutation cannot retag state.

### API2 owner view

API2 keeps the same opaque state transaction and adds one synchronous
`session.mount({root, items, host, signal})`. Mount follows saved-state
restoration. `root` is a dedicated global owner root; each frozen `items`
entry contains its bound `id`, dedicated owner `root` and explicit host
`media` container. The view may arrange that container inside its owner root.
The host retains the outer wrapper, item identity/order, verified media
children, readiness, focus and disposal. Do not access incidental host DOM
selectors, replace verified children or create parallel media decoders.

Mount returns `refresh()`, `focus(id|null)`, `key(event)` and `dispose()`.
All hooks are synchronous; `key` returns a boolean. The single host dispatcher
calls owner keys before generic defaults; true consumes the key. Modifiers
are excluded, and only Escape is offered while editing text. The view must
preserve its project's intended keyboard meanings without adding another
document dispatcher. Refresh reads the current live owner after edits,
imports or rollback; it must not mutate project state. Domain undo is handled
by the API2 owner's `key` hook, with `beforeChange` before mutation. There is
no generic editor-session undo hook; API1 declares only snapshot/prepare.
Ordinary v1 primitive-answer undo is unchanged.

The frozen host interface provides `currentId()`, `select(id, scroll=true)`,
`setVisible(ids)`, `note(text,error)`, `beforeChange()`, `projectState()`,
`importProject(raw)` and read-only `canImport`. Selection accepts currently
visible bound IDs; visibility accepts only bound IDs. These operations never
create answers or hidden-history pseudoitems. `projectState()` returns validated current opaque text and
refuses transport-unrecovered state. `importProject` uses the same bounded
prepare/commit/rollback path and retains the original input text in recovery,
not a synthetic wrapper. File controls check `canImport` before reading,
again after an awaited read, and honor a refused import without consuming
another pending input. Project UI can offer its canonical direct download
and importer alongside the explicit generic v2 state envelope; the core does
not parse domain data or silently replace that canonical flow. The generic
**Save editor state / Import editor state** controls carry the v2 envelope;
they are distinct from the owner's canonical feedback file controls.
Browser v2 downloads use the same compact serialization as the admission
and storage size check, preserving opaque text including whitespace/escapes.
The CLI bounds the actual transport packet before adding its diagnostic
`project_validation: "not-run"` annotation. CLI import output is a report,
not a reimportable transport packet; retain the original downloaded input.

API2 `create` also receives `beforeChange()` beside `changed()`. Cooperative
trusted adapters check it before every owner mutation or ID creation. The
host compares the exact current draft text with its last observed value;
changed or unreadable storage blocks edits/import before mutation. Persistence
rechecks before writing. A race after the precheck can leave a valid local
edit unsaved; retain/export that state and the competing draft, then reload
to reconcile. This is not atomic WebStorage compare-and-swap or containment
of arbitrary owner code. API1 behavior remains separate. Project adapters
must also preserve and explicitly reconcile their own older storage keys;
this host does not overwrite or interpret those keys.

A hook failure aborts the view signal, calls dispose at most once, detaches
owner roots while retaining host media, and leaves a persistent warning.
Valid bound state remains exportable as explicitly unverified recovery state;
a visual failure does not invent transport rollback. Transport-unrecovered
state still refuses normal export and keeps its original recovery input.
The view registers cleanup before risky work and observes the supplied abort
signal. Its own event callbacks retain explicit error handling: hook wrappers
do not contain arbitrary asynchronous work. Host media cleanup runs separately
even if view disposal fails.

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

Key declarations use one literal character or a single-character JavaScript
escape: backslash, either quote, slash, `\b`, `\f`, `\n`, `\r`, `\t`,
`\v`, `\0`, `\xNN`, or `\uNNNN`. They are decoded without executing script
before duplicate and hotkey checks. Multi-character keys, surrogate code units,
and other escape forms are unsupported.

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
before trusting its exact bundle digest. The first command prints the file
inventory and hashes and refuses to write; pass `--yes` after reviewing it:

```sh
./bin/review-sheet component trust path/to/component
./bin/review-sheet component trust path/to/component --yes
./bin/review-sheet component list
./bin/review-sheet component untrust SHA256
```

The owner file is `$XDG_CONFIG_HOME/review-sheet/trusted-components.json`
or `~/.config/review-sheet/trusted-components.json`, mode `0600`. A bundle
digest is SHA-256 of canonical JSON mapping each declared relative filename,
including `README.md` when present, to its byte SHA-256. Trust records the
bundle kind, version, digest, and path. Registration requires exactly one
literal `ReviewSheet.register({...})` call with bare top-level `kind`,
`version`, and `api` fields whose values are literal strings and `1`.
Comments and nested objects cannot supply those fields. Lookup order:
explicit `components` path relative to
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
It refuses instructions or caveats containing absolute or home-directory
paths, including POSIX, Windows drive, and common home-variable forms.
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
Import rejects impossible sequences. Reveal histories are keyed by reviewer;
a later monotone history for the same reviewer supersedes its prefix, while
divergent histories for that reviewer conflict and exit 2. Independent
reviewers retain separate histories. A packet without `reviewer` uses the
empty-string key.
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
