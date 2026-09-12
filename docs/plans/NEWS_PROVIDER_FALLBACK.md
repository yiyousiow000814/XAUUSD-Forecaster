# News provider recovery and alert severity

## Change contract

Baseline: protected main 9aaec7d7d3093945472bfa193965d81540aa0996.
The existing news scheduler remains the only owner. A Google generation HTTP
500/502/503 may invoke one optional OpenRouter free Nemotron request with the same
prompt and decoder. No extra successful-path request, model training, Assistant
activation, broker operation, new queue or database table is introduced.

The Windows user environment owns the supplied OPENROUTER_API_KEY. Only the
annotator receives it through the existing runtime launcher. Secrets never enter
Git, diagnostics, request URLs or public snapshots. The fixed endpoint and fixed
`:free` model plus zero maximum provider prices prohibit paid routing.

## Actors, transitions and growth

`main runtime -> annotator -> scheduler claim -> Google gateway -> optional
OpenRouter gateway -> existing decoder/semantic validation -> evidence -> ACK`.
Google success returns immediately. Only the specified HTTP statuses trigger
fallback; quota denial, malformed output, authentication and other failures do
not. OpenRouter failure returns to the existing fair queue; accepted earlier
stages remain intact. No recursive fallback occurs. Historical source and model
evidence remain immutable; successful fallback records its actual provider/model.

Each provider attempt is reserved in the existing SQLite request ledger before
transport, including failed attempts. All Google lanes share one OpenRouter
account budget: 50 requests per UTC day and 20 per rolling minute. This matches
the verified free-tier account and is below the user's 200/day ceiling. Existing
ledger retention bounds history. The OpenRouter provider scope owns only its
own Retry-After deadline; it must neither inherit nor clear Google's cooldown.
Atomic SQLite admission prevents concurrent lanes exceeding the budget. A crash
after admission consumes that slot conservatively; restart cannot reset usage.
Missing credentials disable only fallback. Backfill does not consume this reserve.

Retry-loop health distinguishes exclusively transient HTTP failures from mixed
or deterministic errors. Automatic provider retry remains a nonblocking warning
in system details. Current whole-pipeline no-progress/SLA failures, authentication
and deterministic loops remain visible globally. A later provider error cannot
hide earlier deterministic errors in the same unresolved streak.

## Compatibility, failure and recovery

| Boundary | Result |
|---|---|
| New code / old data | Same schema; optional provider scope and usage rows. |
| Old code / new data | Existing columns remain readable; no fallback dispatch. |
| Google succeeds | No OpenRouter request or reservation. |
| Backup absent/exhausted/unavailable | Preserve failure and use existing queue; no invented success. |
| Partial model work | Same contract validation; preserve accepted stages. |
| Process/machine restart | Existing leases recover; durable quota/deadlines survive. |
| Revert | Main-only code correction; remove optional key to disable fallback; never restore an older database. |

Provider capacity/delivery is external and not guaranteed. Official documentation
and GET /models plus authenticated GET /key establish protocol, free pricing and
free-tier identity, not future availability. Rehearse one bounded free-provider
request and test Google failure -> backup decode -> persisted exact identity.

## Verification

Extend real SQLite scheduler/gateway tests for sibling news tasks, success,
nontrigger failures, malformed envelopes, two-provider failure, quota exhaustion,
concurrent admission, UTC reset/restart and independent provider deadlines.
Exercise the real PowerShell runtime environment boundary. Existing health tests
must prove transient retry stays local while mixed failures and stalls still
reach the global alert consumer. Validate required CI on the final head, deployed
Preview at desktop/390x844/360x800 for changed presentation behavior, then confirm
main runtime identity and provider usage. No real failure is rewritten as PASS.

## Rehearsal and review evidence

The free Gemma endpoint returned HTTP 429 and its published backend was Google
AI Studio, so it was rejected as the independent backup. The selected free
Nemotron endpoint reports Nvidia as its backend. One real OpenRouter request
completed the existing title prompt and decoder after a controlled Google 503;
the ledger recorded `openrouter/nvidia/nemotron-3-super-120b-a12b:free`. This is
transport/contract evidence, not a claim that future model interpretations are
always correct. No production news was changed by this rehearsal.

Concurrent quota testing exposed a pre-lock clock race: waiting callers could
exclude newer committed admissions from their trailing window. Admission now
reads its live clock after acquiring the SQLite write lock, for all providers.
The existing transaction also commits an independent provider's outcome and
Retry-After deadline together. No new durable coordination state was needed.

Final review traced scheduler activation separately from other accountant users:
only the actual news job caller enables backup, and backfill remains excluded.
Real entrypoint testing persists a title with the backup model and original
source hash. Health tests cover sibling tasks, mixed errors, a separate more
severe job, and pipeline stagnation; UI tests verify global/detail placement.
Windows subprocess tests cover configured/missing optional credentials, parent
environment restoration, and zero remaining fixture processes.

Local related Python family: 309 passed. Windows runtime: 9 passed. Full Web:
395 passed, 6 existing skips, zero failures; build passed. Exact-source architecture
check and import policy passed. Remote CI, deployed Preview and main activation
are subsequent acceptance steps and are not claimed by these local results.
