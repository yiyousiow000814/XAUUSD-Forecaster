# News publication cost and quote recovery

## Finding

The September 14 inspection found repeated full News publication despite small
source changes. In two captured generations with 4,877 active rows each, 4,875
common rows were unchanged, one changed eligibility, one arrived and one left.
The four-index/eight-detail batch protocol nevertheless replayed 1,830 batches.
The earlier capacity estimate incorrectly treated full publication as bootstrap
only. Its recurring workload assumption cannot establish daily quota compliance.

A separate complete JSONL record contained a partial quote concatenated with the
next bridge session. Its byte offset was 10,354,561, length 338, SHA-256
`9b6fd5fcc707dc66e1eea0130338d2fc101a8788397dbaabffb0a3e3f407bc56`.
The reader raised on that line on each process restart. A current supervisor
heartbeat did not prove that forward decisions were advancing. Old decision-time
semantic checks were also being displayed as current news-processing failures.

## Corrective paths and evidence

- Quote reader: preserve malformed bytes and log offset/length/hash, omit that
  record, then accept later valid quotes. Partial live tails still wait for a
  delimiter; wrong symbols and semantic quote errors remain fail closed.
- Writer: on opening a nonempty daily file, preserve its bytes and add a missing
  record delimiter before appending. The exact production IO methods were
  compiled and executed with the real Windows .NET runtime against five tail
  conditions. No cTrader trading process was launched.
- News: a complete local fingerprint inventory identifies a bounded sparse patch.
  The Worker rechecks the acknowledged base and publishes all changes in one
  transaction. Additive migration0038 retains deletion fences. Full replay remains
  bootstrap/recovery and runs on each changed source day to own retention cleanup.
- Health: an expired decision-input check retains historical reasons/counts,
  identifies the stale decision collector, and does not present old failure counts
  as current news failures. The stale condition remains visible.

The patched reader was also exercised read-only against the actual live quote
file. It identified the exact damaged record above and accepted 6,781 observations
in the selected rolling window, through September 14 at 13:03:18 MYT. Source bytes,
the live SQLite ledger, runtime processes and deployed code were not changed.

The reproducible 4,877-row SQLite/D1-interface rehearsal used the observed change
shape, with synthetic article bodies. It performed one delta POST, 14 transaction
statements and 11 direct SQLite row changes. Exactly three index identities were
touched: changed, added and withdrawn. The other 4,875 old rows were untouched.
This is not a Cloudflare billed-row measurement: index maintenance and provider
accounting differ. Daily full replay, backlog, ordinary traffic and other sync
resources still consume capacity. Observe equal post-deployment windows before
claiming an account-wide reduction or Free-plan compliance.

Cloudflare documents whole-batch rollback on statement failure in its
[D1 batch contract](https://developers.cloudflare.com/d1/worker-api/d1-database/#batch).
Use the [D1 pricing definition](https://developers.cloudflare.com/d1/platform/pricing/)
for billed reads/writes rather than treating SQLite changes as the bill.

## Verification scope and release

Local evidence includes Python reader/gzip/restart and authenticated HTTP API
tests, actual Python JSON serialization through the built production Worker,
SQLite transaction rollback, lost ACK, wrong baselines, missing details, duplicate
membership, empty targets, bounded metadata, full replay recovery and legacy
writer fences. Full Web tests/build and lint pass. Windows CI owns the real
writer filesystem test; the ordinary Python shards also own the recovery family.

The production path is: frozen local generation -> authenticated inventory/batches
-> Dashboard Sync private acknowledgement state -> authenticated `/api/news-index`
-> D1 batch -> generation-fenced public pages and immutable detail reads.
Old local APIs or Workers use full replay. Missing migration0038 prevents sparse
admission, not normal full publication. Losing a response preserves the old local
baseline and retries the identical digest. Transaction failure preserves the old
publication. A changed remote base returns to the existing full-recovery owner.

Apply migration0038 before enabling sparse publication. The supported main-only
release updates the Python runtime and Worker; rebuild/restart the quote bridge
through its existing owner to activate the writer change. Reader recovery also
works with the previous writer. This PR does not activate production or alter
historical evidence. The repository runbook disables non-production Cloudflare
builds, so deployed branch Preview desktop/phone verification remains unavailable;
do not represent local/built-route checks as a deployed Preview check.

Final review found and corrected the interaction with existing CURRENT write
fences; tests now exercise the real triggers rather than assuming successful SQL
means rows changed. Review also added migration admission, a staging race at the
transaction boundary, daily full-replay retention ownership, and the production
sync caller's persisted acknowledgement path.

Validation before PR publication: the affected Python batch passed 568 tests
(one pre-existing skip); the operational suite passed 38; the Windows runtime
shard passed all 47 including real C# IO; the final Web build/suite passed 411
(six Preview-only skips). CI ownership and architecture compiler checks pass.
An optional standalone `tsc --noEmit` still reports the same 95 diagnostics on
the base and this branch; it is not a passing check. The required Web build/test
gate is separate. No live browser sessions were created.
