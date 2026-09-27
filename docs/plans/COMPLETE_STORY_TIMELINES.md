# Complete retained story timelines

## Change and ownership

The story generator currently keeps only the last 20 core events, and the split
producer then keeps only the first/last three. Preserve every generated core
node for each admitted story instead. Keep the existing 120,000-byte endpoint
limit and at most 12 stories per family; when necessary, reduce secondary lists
and then the oldest whole story cards rather than silently sampling nodes.
No source evidence, event grouping, relation contract, schema, authentication or
new request owner changes. The existing optional audit producer and sync owner
continue to publish one replacement snapshot. The browser remains read-only.

Path: source events -> storyline generation -> optional audit read model ->
audit_stories_payload -> audit_stories_snapshot -> existing sync POST/D1 snapshot
-> existing GET -> StoryCard. Source selection already owns the event inventory;
the changed projection deep-copies only admitted rows. Packing has a fixed
number of candidate cards and a byte limit; it never clips an admitted chain.

## Compatibility, failure and recovery

Old/new Workers accept the same envelope below the unchanged byte limit. Old
local producers can temporarily supply sampled rows; the browser explicitly
labels a timeline whose length is below event_count. New local generation
replaces that row through normal sync. Preview keeps its existing source and
snapshot semantics. There is no migration or checkpoint mutation. Process or
machine restart reconstructs the projection through the current runtime owner.
Rejected or oversized detail preserves the last accepted snapshot and can retry
through the existing optional-resource owner. A single chain exceeding the
whole budget remains an explicit contract failure, never a fabricated complete
chain. Release/recovery uses protected main and forward repair.

## Evidence and verification

Production inspection found counts 10/17/40 paired with six delivered nodes.
Tests must prove generation past 20, complete source-to-serialized chains,
whole-card budget packing, unchanged source data, exact aggregate totals, and
old sampled-row disclosure. Rehearse the actual read model with the current
local database read-only, record serialized bytes and count/length pairs, then
verify deployed Preview and final public API after the runtime updates.

The selected overview direction is option C: cool light background, a compact
warm quote card and one white two-column news panel, split into independent
cards on phones. Retain the existing resources, authentication, navigation and
refresh owner. Decorative illustration, promotional copy and fake footer links
from the concept are intentionally omitted. This presentation change does not
alter data selection or the three-headline limit.

The actual read-only runtime database rehearsal produced 112,029 bytes with
12 current cards. Every event_count matched its timeline length:
6, 10, 17, 4, 5, 2, 3, 2, 40, 20, 2, 2. No card required shedding. This is
producer evidence, not proof of a subsequent remote sync. Dashboard/story
tests passed (481 passed, one skipped); web tests passed (398 passed, six
skipped), followed by 94 rendering tests after adding the border regression.
Architecture compilation and document links passed.

## Final adversarial review

Reviewed the final implementation independently from the original plan:
`status_resources._optional_resource_payload` invokes the bounded serializer;
`sync.resources._audit_projection_bytes` validates matching source times and
adds the producer revision; `_publish_audit_projection_bytes` uses the existing
authenticated snapshot POST. The Worker route accepts the unchanged envelope
under `AUDIT_DETAIL_SNAPSHOT_BYTES`, and its GET returns the retained payload
without resampling. `StoryCard` copies/sorts nodes and conditionally renders its
detail; the existing owner retains expansion state. No new recurring actor,
schema migration, credential, source-evidence write or cache owner was added.

Cold/invalid source still fails rather than publishing an empty replacement.
Genuine empty detail uses the existing projection marker. Oversize rejection
leaves the accepted remote snapshot intact and normal optional publication can
retry. New UI with old producer displays the partial notice; old UI with new
producer accepts the same fields. Restart reconstructs through the sole main
runtime owner. Packing is bounded by the admitted row inventory; removed rows
are display projections, not deleted source evidence. Long individual chains
can still exceed the fixed envelope and fail explicitly, a documented limit.

Deployed Preview at `ee0b50f9-aurum-signal-room.yiyousiow1234.workers.dev`
passed desktop, 390x844 and 360x800 navigation, scrolling, expansion and return
checks. No overflow or console warnings/errors; 44/48px targets. All five
story-card outer boundaries, including the incomplete desktop row, remained
intact. See the root design-qa.md for the selected C visual comparison.
Task-created browser sessions after cleanup: zero. Thirty GitHub checks passed
on implementation revision 31cff97d.
