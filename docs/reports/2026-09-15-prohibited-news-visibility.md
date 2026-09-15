# Hide explicitly skipped news

## Change contract

The content-policy owner already retains a hash-keyed skip marker and stops
provider work. The reader omitted that marker, so retained source evidence was
misrepresented as a queue failure. User-visible news must exclude this content;
raw evidence and scheduler history remain unchanged.

Reuse existing news withdrawal transport, complete-generation inventory and
atomic remote activation. Source readers emit a withdrawal rather than silently
omitting a changed key. The skip classification advances all matching revision
keys, including a different canonical source with the same content hash. Local
archive, complete build, streaming capture and recent-status readers share this
visibility rule. A partial classification-time index bounds discovery. Existing
markers are picked up by the normal complete capture after the main update;
no cursor reset, wire-version change, cloud migration or destructive cleanup.

Actors: content-policy writer, scheduler, source capture, sync and remote reader.
Only classification owns exclusion; archive/projection are derived consumers.
Pending -> marked -> withdrawal -> acknowledged remote generation. A restart
retains the marker; failed transport retains the current generation and retries
through existing strict ACK. Concurrent in-flight captures remain immutable and
the next capture converges. Changed content remains independently eligible.
No other skipped/irrelevant semantics, provider routing or failure thresholds
change. Public and local totals exclude the same withdrawn identity. Remote
historical detail storage is not deletion authority and remains audit evidence.

Verify source fixture before/after marking, delta cursor advance, canonical copy,
unchanged body and same-hash skip after restart. Exercise complete and streaming
projection withdrawal, remote withdrawal/count contracts and changed-content
visibility. Gates: focused under 2 minutes, combined under 5 minutes. Production
main-only update and normal mirror ACK must remove the named film article.
No browser layout changes; deployment has no branch Preview (runbook disables
non-production builds). Verify actual public data and desktop/phone list absence
on the deployed main when access is available, closing the browser afterwards.

## Verification and adversarial review

Read-only replay of the named film article returns a withdrawal for key
229352f8d439290af60dc8942f65e0dae75579a15080094ce1a6d4b98971ded4.
No production data was changed. Full and streaming readers share the exclusion;
incremental discovery still emits the hidden key and advances its cursor. Tests
mark a noncanonical copy and verify the canonical copy disappears, then reopen
the database and prove a changed-content revision can become visible again.
The recent-status production entrypoint also excludes the same item.

Review traced complete source inventory -> sparse removed keys -> existing
atomic remote membership update -> ACTIVE_NEWS_SQL -> list and verified totals.
No new remote shape or UI condition is needed. Retained remote details are not
active list membership. Added indexes bound classification-time and content-hash
joins; they are additive and owned by normal ledger initialization.

Web build and all 36 remote projection-store tests passed, including the actual
Worker receiving Python sparse transport, removal membership, count integrity,
strict ACK, authentication and stale-delta failure. No acceptance gate changed.

Final Python regression: 408 passed, 1 skipped in 61.91 seconds.
