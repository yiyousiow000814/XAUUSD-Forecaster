# Explicit provider content rejection

## Change contract

Google's explicit `PROHIBITED_CONTENT` response currently enters the ordinary
model-output retry queue. The user's policy is to discard this work, not send
the identical content to another account or provider. A refusal does not reveal
the offending passage or establish an account sanction.

The annotation persistence boundary owns an append-only content classification
in the existing classification table. Its identity is the raw content hash,
independent of provider, prompt generation, source URL, and task stage. Readers
exclude that identity; explicit-record callers and the scheduler check it too.
No new table, timer, queue state, provider request, or operator workflow is added.

Impact graph: Google envelope -> typed failure -> annotation/title/impact
persistence -> content classification -> pending readers and scheduler dispatch
-> retired queue entry and historical failure evidence. The scheduler retains
DEAD_LETTER only as existing retirement bookkeeping, with a specific skip reason;
this is not an actionable failed task or successful semantic annotation.

## Actors and transitions

- A provider response with the exact rejection code durably records the skip
  before recording optional failure detail. Other response failures still retry.
- The classification writer atomically retires matching unleased queue work.
  A worker retains ownership of its lease and retires it through the normal
  conditional transition. Other already dispatched requests cannot be recalled.
- Discovery, direct-record execution, generation backfill, legacy failure
  recovery, and reconciliation must not submit a marked identity again.
- Restart observes the same marker; a crash after marking but before recording
  the attempt cannot cause another dispatch. Expired leases still use normal
  recovery, then the dispatch check retires them without a provider call.
- Changed content has a different identity and remains eligible. Healthy unrelated
  records progress normally. Historical raw content and accepted results survive.
- Administrative timing overrides for retired work become inactive. No automatic
  expiry or resume exists for this user-requested skip policy.

## Compatibility, rollout, and recovery

New code reads existing evidence without a table migration. The job-count
trigger owner detects the changed retirement predicate and rebuilds only its
disposable scheduler counts once; it does not rebuild historical news.

Production follows the current main-only release contract: merge qualified code
into protected main and let the existing machine owner update its runtime.
PR branches must not run production. The inspected prior runtime is f56d5a59.
Old code can read the classification but does not honor it, so old/new workers
must not overlap. Keep the existing incident's audited administrative hold until
the new runtime identity is confirmed; then convert it through the content-policy
owner and verify the retired job, inactive hold, and no further provider attempts.
Returning to old code requires restoring the administrative hold before restarting
old workers. No historical evidence deletion is part of recovery.

## Verification and evidence

The provider's exact block code is external authoritative evidence of rejection,
not evidence of its underlying trigger. Do not replay blocked content. Local
SQLite state and dispatch counts are controlled exact evidence. Rehearse actual
Python production entry points with recorded-shape provider envelopes, all three
news tasks, normal failure recovery, changed/duplicate content, a file-backed
restart, and the scheduler queue transition. Inspect the live incident read-only.
Focused gates should finish within five minutes; broaden only for affected
contracts. No shell, credential, or frontend interface changes are intended.

### Final local evidence

- `python -m pytest tests/ai tests/news -q --disable-warnings --maxfail=2`:
  833 passed, 2 filesystem-link permission skips, 130 seconds.
- `python -m pytest tests/runtime/test_operational_health.py tests/dashboard/test_dashboard_health_projection.py tests/dashboard/test_dashboard_news_resources.py -q --disable-warnings --maxfail=2`:
  86 passed, 8 seconds.
- Import-policy check: zero static violations; six pre-existing dynamic imports
  remain classified UNKNOWN. Architecture compiler check and diff whitespace pass.
- Final review traced annotation/title/impact rejection, nested contract repair,
  direct-record callers, discovery, overrides, lease retirement, legacy recovery,
  reconciliation, and trigger-backed health counts. Immutable accepted annotations
  remain available. Only pending work is removed from processing.
- Live read-only inspection at 2026-09-13 13:51 MYT: incident attempt count 364,
  unchanged since containment, with no active lease. The provider was not called
  again for diagnosis. Permanent skip policy has not been activated in production.
