# News provider recovery and alert severity

## Change contract

### Updated provider choice (user direction, 2026-09-13)

The authorized list is OpenRouter `google/gemma-4-31b-it:free` and Groq
`qwen/qwen3.8-27b`, then `qwen/qwen3.6-27b`. Nemotron is retired from this
unmerged proposal; its earlier rehearsal remains audit evidence only.
Both Google Gemma and Gemini recovery try OpenRouter Gemma, then Groq 3.8,
then Groq 3.6. Each route is attempted at most once per failed generation. Recoverable HTTP
500/502/503, timeout/connection failure, and Gemma capacity denial/429 can
enter recovery. Invalid outputs and authentication failures are not retried
through a different model. Exhausted routes leave work in the existing queue.

Both Groq keys and OpenRouter keys remain optional Windows user settings passed
only to the annotator. The existing SQLite admission transaction owns per-model
Groq limits: 30 RPM, 1,000 RPD, 8,000 TPM and 200,000 tokens in a trailing 24-hour
window. All lanes/keys share each model's account budget. No new tables or timers.
Input is never shortened. Groq reserves a conservative estimate of the complete
converted prompt plus up to 2,048 output tokens; oversized requests skip that
route. Failed attempts retain their reservations. Provider Retry-After remains
durable and independent per model. The request ledger already retains 24 hours,
which is the complete baseline for the daily token window. Model availability
is external; authenticated model discovery confirmed both requested IDs active.
Tests must cover the ordered chain, capacity/HTTP recovery, oversized input,
token admission under concurrency/restart, complete source preservation, actual
model identity and the real Windows credential handoff.

Baseline: protected main 9aaec7d7d3093945472bfa193965d81540aa0996.
The existing news scheduler remains the only owner. A Google generation HTTP
500/502/503 may invoke the optional, ordered backup routes above with the same
prompt and decoder. No extra successful-path request, model training, Assistant
activation, broker operation, new queue or database table is introduced.

The Windows user environment owns OPENROUTER_API_KEY and GROQ_API_KEY. Only the
annotator receives it through the existing runtime launcher. Secrets never enter
Git, diagnostics, request URLs or public snapshots. The fixed endpoint and fixed
OpenRouter `:free` model plus zero maximum provider prices prohibit paid routing.
Groq uses only the two models and free account quotas supplied by the user.

## Actors, transitions and growth

`main runtime -> annotator -> scheduler claim -> Google gateway -> optional
ordered backup gateways -> existing decoder/semantic validation -> evidence -> ACK`.
Google success returns immediately. The triggers are listed above; malformed
output and authentication errors do not trigger another provider. Backup
exhaustion returns to the existing fair queue; accepted earlier
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

The free OpenRouter Gemma endpoint returned HTTP 429 and its published backend
was Google AI Studio. An earlier alternative-model probe succeeded, but the user
rejected that alternative; it has no active route. Authenticated Groq discovery
confirmed both Qwen IDs. A real Groq 3.8 title request completed the existing
prompt and decoder after a controlled Google 503. The first urllib attempt
returned 403; the same authenticated API worked with PowerShell. Supplying the
application User-Agent fixed the actual Python transport, verified by a real
successful request. Original failed receipts remain in the local audit folder.
This proves transport/contract recovery, not future semantic correctness.
No production news was changed by these rehearsals.

The final ordered chain was rehearsed with injected Google 503, real OpenRouter
429, and real Groq 3.8 success. A second rehearsal injected both Gemma failures
and Groq 3.8 exhaustion; real Groq 3.6 then completed the same title contract.
Groq 3.6 initially exhausted its small title output on default reasoning and
returned JSON validation 400. Both Qwen routes now explicitly use the documented
`reasoning_effort=none`; the successful 3.6 rehearsal recorded 223 total tokens.
Original failed responses remain audit evidence. This keeps short news tasks
within their output budget without adding a repair request.

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

Updated related Python and real Windows runtime family: 332 passed. Full Web:
395 passed, 6 existing skips, zero failures; build passed. Exact-source architecture
check and import policy passed. Remote CI, deployed Preview and main activation
are subsequent acceptance steps and are not claimed by these local results.

The initial CI run found the new fallback test missing from the explicit shard
manifest. The manifest now includes it, and the complete-assignment contract
passes. New test files must be registered in that existing authority; no gate
was removed. The expanded fallback plus CI contract family has 52 passing tests.
