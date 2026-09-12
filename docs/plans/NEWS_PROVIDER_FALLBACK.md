# News provider recovery and alert severity

## Change contract

Baseline: protected main 9aaec7d7d3093945472bfa193965d81540aa0996.
The existing news scheduler is the sole owner. Google generation HTTP
500/502/503 or transport failure can try Groq Qwen 3.8, then Qwen 3.6.
Google Gemma capacity denial and 429 also qualify. Each backup is tried once
per failed generation. No successful-path extra request, new queue/table,
Assistant activation, model training or broker operation is introduced.

## Actors, transitions and growth

`main runtime -> annotator -> scheduler claim -> Google gateway -> Groq 3.8
-> Groq 3.6 -> existing decoder/semantic validation -> evidence -> ACK`.
Google success returns immediately. A backup succeeds only through the original
decoder; invalid output or authentication failure does not switch models.
Quota denial, transient transport failure, HTTP 404/429/500/502/503/504 can move
to the next backup. Exhaustion returns to the existing fair queue and preserves
accepted stages. No recursive provider retry or historical source mutation.

Windows user GROQ_API_KEY is passed only to the annotator through the existing
launcher. Secrets never enter Git, diagnostics, URLs or public projections.
Only the two explicit Groq model IDs and endpoint are allowed. The supplied
free account limits are enforced; no paid plan or service is enabled.

The existing SQLite admission transaction owns per-model limits shared by all
lanes and credentials: 30 RPM, 1,000 RPD, 8,000 TPM and 200,000 tokens in a
trailing 24-hour window. The request ledger already retains that complete
window. Complete converted prompt UTF-8 bytes plus up to 2,048 output tokens
are reserved conservatively; oversized requests skip the route, never truncate
source. Failed attempts remain charged. Successful actual tokens and identities
are retained. Atomic admission prevents races and restart cannot reset quotas.
Per-model Retry-After commits with the outcome, independent of Google.
Missing credentials disable only backup; historical backfill cannot spend it.

Backup inactivity timeouts are 15 seconds each; the ordinary single Google
request has 120 seconds within the existing 180-second job lease. No new timer
or lease extension. Qwen uses the documented non-thinking mode for bounded JSON.

Retry-loop health distinguishes exclusively transient HTTP failures from mixed
or deterministic errors. Automatic provider retry is a nonblocking warning in
system details. Whole-pipeline stalls, authentication and deterministic loops
still reach the global alert. One temporary error cannot hide another job's
more severe failure. Actual transport/model evidence remains intact.

## Compatibility and recovery

| Boundary | Result |
|---|---|
| New code / old data | Same tables; new independent provider scope rows. |
| Old code / new data | Existing columns readable; no backup execution. |
| Google success | No backup call or reservation. |
| Backup absent/exhausted | Existing fair queue; no invented success. |
| Partial work | Original decoder and source hash; accepted stages preserved. |
| Process/machine restart | Existing leases recover; durable quota/cooldown. |
| Revert | Main-only code correction or remove optional key; never restore old DB. |

## Verification and rehearsal evidence

Tests exercise all news task families, ordered recovery, nontrigger statuses,
bad outputs, oversized input, quota admission under concurrency, daily/minute
token exhaustion, restart, UTC/rolling reset and independent deadlines.
Production entrypoint testing persists a title with actual backup identity and
original source hash. Real PowerShell -> Python tests verify the credential
handoff, parent restoration and zero fixture processes. Health and Web consumer
tests verify detail/global placement and retain genuine stall warnings.

Authenticated Groq model discovery confirmed both IDs. Real title requests
passed the existing decoder on 3.8 and 3.6. The first Python transport got 403
while PowerShell worked; application User-Agent fixed the real Python call.
3.6 initially returned JSON validation 400 with default reasoning; explicitly
setting reasoning_effort=none fixed it, with 223 provider-reported total tokens.
Failed evidence remains in the ignored local audit directory.

OpenRouter Gemma repeatedly returned upstream Google AI Studio 429. The user
removed that provider from the design; its code, settings handoff and tests are
retired. Earlier alternative-model probes are audit evidence only, never active
routes. No production news was changed by these bounded rehearsals.

Concurrent admission exposed a pre-lock clock race; the current clock is now
read after acquiring SQLite's write lock. CI exposed a new test missing from
the explicit shard manifest; the file is registered and assignment coverage
passes. Existing quota and CI invariants were strengthened, not bypassed.

Exact-head CI, desktop/390x844/360x800 branch Preview and main/runtime identity
are separate acceptance gates. Local tests and provider rehearsals do not by
themselves establish production activation or future provider availability.
