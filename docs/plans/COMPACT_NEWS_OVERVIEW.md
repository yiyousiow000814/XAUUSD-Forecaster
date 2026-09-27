# Compact news overview

## Change and ownership

Replace the oversized quote, counters and source-health panel on the overview
with a compact quote, three brief headlines and three current-event headlines.
The existing audit pages retain full detail and the global health link retains
operational diagnostics. This is a read-only consumer change, with no new API,
schema, model work, ingest authority or persisted state.

The path is existing brief/event producers -> existing synchronized D1 resources
-> `/api/audit-briefs` and `/api/news-evidence?mode=eligible&page=1&limit=3`
-> the shared browser resource cache -> the overview. Brief selection uses the
latest available date and revision, displays that date and preserves source
headlines. Event eligibility and ordering remain owned by the existing API.

## Composition and failure review

Each optional resource has a separate subscription and refresh lease using the
existing dashboard scheduler. Initial reads share the resource cache; visible
current-data refresh runs every five minutes. Preview keeps its existing frozen
detail-resource behavior, with a visible snapshot label and no detail polling.
The existing status owner keeps its refresh policy. No new persistent actor,
credential, database writer or cross-version protocol is introduced.

Loading, successful empty data and failed reads are distinct. A failed refresh
retains accepted data with an explicit stale notice. Retry and subsequent
scheduled refresh can recover the same resource; failure in one card does not
block the other or the quote. Unmount removes subscriptions and timers; late
shared-cache results cannot set unmounted component state. A reload reconstructs
the presentation from existing resources. No historical brief is called today.

The main risks are accidental full-detail text on phones, stale content without
a date, duplicate polling, malformed envelopes and breaking navigation. Existing
brief envelope validation is reused; event envelopes are validated before
rendering. Transport limits remain owned by the existing endpoints; display is
limited to three rows per section. This adds two bounded initial reads, then at
most two detail refreshes per five minutes while visible.

## Verification and rollout

Test actual rendered cards for row limits, date/revision selection, empty/error
states and retained detail links. Exercise the shared cache's rejection and
recovery contracts. Build and run web gates; inspect final callers and lifecycle.
Verify desktop, 390x844 and 360x800 on an immutable branch Preview, including
navigation to full briefs/events and back, all panel edges, no overflow and
44px controls. Local reads established the brief envelope. The local raw event feed does not
apply the browser pagination contract; the overview requires the existing web
route's eligible mode, snapshot identity and eligible rows. Preview acceptance
verifies that real route separately.
Release through protected main after review and checks. Recovery is a forward
fix through the same release owner; authoritative data is never replaced.


## Local evidence

The final local web gate passed 397 tests, with six existing conditional skips.
The actual effect harness covers both overview resources, current polling,
Preview no-poll behavior, rejected reads and recovery on the same URLs, and
cleanup. Render coverage verifies the latest date/revision, six headline limit,
no summary prose, original-data preservation, empty/error distinction and links.
Architecture source generation/check and document-link checks passed. Generated
index artifacts changed because the indexed rendering test changed.
