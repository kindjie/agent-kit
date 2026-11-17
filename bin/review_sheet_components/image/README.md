# Image component

Version 1.1.1 shows a fitted local image. Click or press `z` to toggle full
size and scroll to pan, including when comparing. Compare cycles through
the item's other images, beginning with the next, skipping non-image media.
Each image has its `alt` caption or image position; both use the same pixel
scale and retain their aspect ratios. Close comparison restores one image.

An ordinary group's single-image items have no Compare button. Only groups
with explicit `comparison` configuration compare across items, even when an
item has several images. The second caption includes its source item's title
(or ID when untitled), followed by its image caption when supplied.
The button is absent when there is no alternative. The host supplies frozen
item/group media metadata and verified handles through `api.media`; embedded
media uses verified `blob:` URLs. Failed comparisons raise a visible error.
Disposal disconnects resize observation and ignores pending comparisons.

The manifest and registration version changed together. Version 1.1.1 removes
implicit cross-item comparison. A build freezes the new bundle digest in its
resolved component records. External copies need review and trust for that
exact digest; existing owner trust records are never silently updated.
