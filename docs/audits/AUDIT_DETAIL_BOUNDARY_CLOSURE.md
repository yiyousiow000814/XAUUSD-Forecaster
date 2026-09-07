# Audit detail source boundary correction

## Change contract

The existing optional `audit` read-model owner builds one pinned SQLite snapshot.
It must retain the fixed landing summary and the three independently bounded
detail projections from that same build. HTTP GET selects one projection; it
does not rebuild the full audit payload or serialize the combined local bundle.
Dashboard Sync reads the corresponding resources, not a detail-free summary.
The local derived-model contract changes so an old summary-only cache rebuilds.
No new table, receipt, process, scheduler, or persistent authority is introduced.

The source DTO marker `audit-detail-source-v1` is emitted only after the required
array exists and is an array of records. Missing or malformed source detail is
not an empty collection. New marked empty arrays are authoritative; older
ambiguous empty split snapshots cannot displace a complete legacy snapshot.
Legacy fallback retains its source timestamp and bounded JSON1 projection.

## Actors, compatibility, failure, and recovery

SQLite is the source; the existing background read-model owner is the producer;
the existing serial Sync owner publishes D1 display snapshots; Worker and Preview
adapters validate and select them; the UI retains each resource's own time.
The critical status owner remains separate. Snapshot/transport size limits and
source-revision publication rules are unchanged. Optional failure retains the
last verified value and retries on the existing bounded schedule.

New code rebuilds the old local derived audit row. New consumers accept valid
nonempty old projections, while requiring explicit source evidence for empty
split arrays. Old Worker consumers ignore the additive marker. Rollback retains
the original SQLite authority and legacy D1 snapshot; new code never deletes
either. A missing/invalid local detail aborts publication rather than publishing
a fabricated empty snapshot. Deferred publication requires all selected source
timestamps to agree and satisfy the existing post-cutover freshness boundary.

Preview capture validates the actual public detail and its fallback independently.
Invalid HTTP 200 is unavailable, not ready. The TypeScript build/route/browser
consumer checks the actual renderable row contract. Snapshot coverage timestamps
are never refreshed by a live status heartbeat.

## Execution evidence plan

Run Python source projection, one pinned read-model build, real loopback HTTP
GET, normal/deferred Sync, exact generated UTF-8 bytes, Worker D1 JSON1 reader,
Preview capture/build admission, and browser rendering contracts. Cover nonempty,
explicit empty, missing/malformed, old split/legacy ordering, retry, and unchanged
source (no repeated full build). Local tests use temporary SQLite and loopback
only. Deployed immutable Preview is a separate read-only acceptance surface;
test fixtures are not production recovery evidence.
