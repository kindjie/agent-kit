# Image component

Version 1.1.0 shows a fitted local image. Click or press `z` to toggle full
size and scroll to pan, including when comparing. Compare cycles through
the item's other images, beginning with the next, skipping non-image media.
Each image has its `alt` caption or image position; both use the same pixel
scale and retain their aspect ratios. Close comparison restores one image.

For a single-image item, Compare uses another image-bearing group member.
Groups with explicit `comparison` configuration always compare members.
The button is absent when there is no alternative. The host supplies frozen
item/group media metadata and verified handles through `api.media`; embedded
media uses verified `blob:` URLs. Failed comparisons raise a visible error.
Disposal disconnects resize observation and ignores pending comparisons.

The manifest and registration version changed together. A build freezes the
new bundle digest. External copies need review and trust for that exact
digest; existing owner trust records are never silently updated.
