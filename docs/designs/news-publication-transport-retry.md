# Bounded news publication transport retry

## Change record

The existing dashboard sync owner retries immutable news projection requests
after connection resets or timeouts. A single transient disconnect currently
defers the entire resource until its next scheduled attempt, delaying publication
of article references and raw article bodies independently.

The change adds at most three attempts per replay-safe POST, with 0.5 and 1 second
delays. Each attempt sends the identical bytes to the identical target. Existing
30 second socket timeouts remain; this is not a total wall-clock deadline.
HTTP errors, invariant failures, invalid acknowledgements and non-transient
transport failures retain their existing error path. Cleanup is excluded.

The production path is the supervised main runtime, dashboard sync accumulated
resource lane, resources publisher, transport, Worker projection store, and
public reader. No process, configuration, secret, schema, cursor authority or
publication owner is added. Heartbeat and control lanes remain independent.
Local checkpoints still advance only after existing strict acknowledgement
validation. The remote generation, offset and receipt remain replay authority.

Before a write, retry sends the original request. After an accepted write whose
response is lost, the Worker recognizes the duplicate immutable operation.
After exhaustion, the existing scheduler and persisted checkpoint resume the
same generation. Process or machine restart uses the existing reconciliation
path; partial generations never become public. Current Worker and sender wire
contracts are unchanged in either deployment order.

Pre-mortem: blindly retrying arbitrary POSTs could duplicate side effects, and
retrying rejected acknowledgements could hide corruption. Scope retries to news
prepare, stage, activate, verify and delta requests, retaining validation outside
the retry loop. Evidence cleanup retains its original single-attempt behavior.

Validation exercises accepted-write/lost-response replay, exhaustion, error
classification, strict acknowledgements, existing sync callers and the actual
Worker store fixture. Real endpoint read-only inspection establishes current
publication state; it cannot prove recovery with the new sender before release.
Production mutation is reserved for the main-only release path and its existing
runtime owner. No parallel publisher or manual checkpoint modification is used.

Recovery is forward repair from main, preserving accepted remote progress and
local source evidence. The known starting revision is
81a25cc8fbde735fd68b1207875581c1804d2322. No temporary compatibility adapter is
introduced. Live acceptance requires both published event references and their
raw article reads; passing tests alone does not establish recovery.

## Review and evidence

The final caller trace confirms `run_dashboard_sync.py` imports `_sync_news`
and `_sync_news_evidence` from the changed resource owner. Existing resource
policy and the accumulated-resource lane invoke them. Generic heartbeat,
assistant, operator and audit POSTs do not use the new helper. Worker staging
returns the stored receipt for an identical batch; sparse delta returns its
original acknowledgement when the same generation is already CURRENT.
Acknowledgement validation remains outside the transport retry loop.

Focused sender, transport and progress tests pass (210 passed, one existing
skip). They exercise exhaustion without checkpoint advancement, recovery from
accepted writes with lost replies, activation replay, restart and malformed
acknowledgements. The actual Worker store and production-shaped route tests
pass (52 tests) after a fresh non-Preview local build. An initial run against
an old Preview build correctly rejected writes and was not acceptance evidence.

Read-only production inspection on 2026-09-28 found all 46 eligible events
carrying the new article reference after the existing publisher activated its
generation. Raw publication remained incomplete and newest article reads
returned 404. This runtime patch has not been activated in production; these
observations must not be attributed to the patch. One eligible canonical source
is independently absent from the raw reader's selected membership; immediate
transport retry does not change that admission decision.
