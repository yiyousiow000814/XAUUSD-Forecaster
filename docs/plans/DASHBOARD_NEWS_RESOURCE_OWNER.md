# Dashboard News resource owner extraction

## Scope and source

Complete the remaining original PR #288 intent from current recovery source,
without replaying its historical patch. The separate recovery release remains
the prerequisite for production restoration, not this refactor. The API still
owns 29 News definitions, two cache/lock pairs and one projection builder.
Bootstrap imports five of those definitions from the API script.

## Ownership and composition

Move the definitions and their existing mutable globals together into
`xauusd_forecaster.dashboard.news_resources`. Keep function bodies, persisted
names, schemas, timing, counters, error states, locks and thread targets
unchanged. The API imports only its eight actual resource dependencies;
bootstrap imports its five functions directly from the same package owner.
Sync imports only the existing stdlib-only projection module's 60-day constant.
Do not add a wrapper service, new cache, compatibility implementation or thread.

The API process owns resource scheduling. HTTP authentication, parameter bounds,
status translation and response construction remain in its Handler. SQLite is
authoritative; immutable capture and generation artifacts retain their existing
owners. The evidence cache publisher and projection builder each keep their
existing lock. A request may start one builder, return the retained generation,
or report pending. A completed builder publishes its existing artifact before
making the generation visible; failure retains accepted data and sets the
existing retry deadline. New source advancement waits for the current snapshot's
activation. First start, empty data, cursor expiry, failed rebuild, later retry,
process restart and machine restart preserve those transitions. Importing the
module starts no work. Each running API process serves one configured database.

## Compatibility and verification

No serialized producer/consumer contract changes. Old Stable and new consumers
retain the same generation format, cursor ordering and remote ACK rules. The
existing installer copies the tracked package, so verify the new module is in
its real staged dependency closure. Reverse Stable restores the prior package
without rewriting history or captures. There is no new expiry or migration.

Move existing resource-level tests to the canonical owner and retain HTTP,
bootstrap, Sync and retained-capture integration tests. Do not emulate the old
API private namespace to make tests pass. Test one canonical cache/lock owner,
API-to-owner calls, bootstrap imports, failure/retry and persisted restart.
Test fixtures must isolate module lifetime without creating a second production
owner. Compare moved function ASTs, run the affected families, import-direction
checks and generated architecture check. Complete the independent exact-head
review and real required integration gates before this intent is accepted.

## Capture identity consumer discovered during verification

The first complete API/bootstrap run exposed an additional execution authority:
bootstrap validates the reader code filename and hashes before capture. Its
historical reader-segment proof also binds the API file and a narrowly reviewed
reader-only correction. Moving the function must not silently reinterpret those
immutable proof fields or let the new owner reuse an old producer identity.
Five bootstrap cases initially failed at that boundary. Current capture execution
now hashes the bootstrap, canonical News resource owner and projection module;
the real reader code filename must match that owner. The existing family covers
fresh capture, wrong executing hash, omitted transition and retained input/WAL
rejection. All 28 bootstrap cases pass after the correction.

Historical reader-segment schema and proof validation stay unchanged. The old
API-only equivalence proof cannot authorize the relocated producer. A partial
capture remains bound to its original producer or its existing explicitly
reviewed transition; retain that frozen producer for continuation. A completed
capture is consumed as immutable generation evidence by the existing retained
artifact path, without impersonating its producer. The installer stages the
exact tracked revision via Git worktree, including the new package module.
Final integration and independent review remain required.

## Test ownership completion

Eighteen resource-only test definitions (24 parameterized cases) now live in
`tests/test_dashboard_news_resources.py`. Remove only their unused API-module
initialization; retain all assertions and fixtures. The two annotation builders
and existing credential/cache lifetime fixture live in one shared test module.
Twenty-one moved definitions have identical ASTs after removing that unused
load. HTTP integration tests remain in the API suite. CI assigns the resource
suite to the same existing shard; no tests or timeout gates are removed. The
News architecture view adds the new resource test and shared fixture sources
to its existing selected test set. API/bootstrap integration still runs as a
separate gate; this does not claim all-repository graph coverage.
