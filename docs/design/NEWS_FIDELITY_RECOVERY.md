# News fidelity and incomplete-work recovery

## Evidence and scope

The September 12 source audit found numeric scale, modality and historical-time
ambiguity in accepted prose. Strengthen the existing Gemini/Gemma request using
the same complete selected source; do not add a reviewer request or language veto.
Whole-page annotation must use the existing exact-source anchor repair too.

At September 12, 15:59 MYT an indexed read-only runtime snapshot found 343
non-retired terminal jobs updated since September 1. Of these, 273 ended with
HTTP 500/503. These are task counts, not unique articles or a corpus error rate.
One preserved attempt history contains four HTTP 500/503 failures followed by
the first long-evidence validation failure, which became terminal because both
shared the total attempt count. JSON, source-anchor and field errors also stopped
work after two or three failures. Source fetching additionally stopped on access,
redirect, certificate or extraction errors. An empty scheduler candidate read was
incorrectly treated as proof of obsolescence after two leases.

## Actors, authority and transitions

- The existing annotation worker owns source selection, semantic validation,
  the single Gemma display review and append-only failure evidence. Anchor repair
  uses only the selected immutable article; semantic fields and selection IDs
  stay fixed. Accepted stages are reused. Invalid output never grants completion
  or model eligibility.
- The existing scheduler owns leases, admission, timing and recovery. Annotation,
  title and impact failures remain BACKING_OFF at 15, 60, 360 and then 720 minutes.
  A pending-reader miss releases its lease with the same capped delay. Current
  source/version/completion reconciliation alone establishes retirement. There
  is no new queue, fallback provider, retry mode or recurring owner.
- Versioned recovery receipts reopen legacy annotation/title/impact failures in
  pages of at most 200 grants per task family. Receipt and job updates share one
  transaction. The receipt identity prevents restart from repeatedly granting
  recovery. Later attempts have their own next retry time. Old source-ineligible
  retirement flags are not blindly revived or asserted to have been incorrect.
- The existing collector retries source-fetch failures with the same cadence.
  Historical stopped fetches become eligible twelve hours after their failure
  clock. Access denial stays visible; retry never bypasses authentication or
  treats an unreadable page as usable content. An individually inaccessible page
  does not pause the shared hydration lane for other publishers. Source revisions and original
  failure receipts remain immutable.
- The archive and public reader expose COMPLETED and PROCESSING only. Pending
  work includes failures and unavailable sources, with specific diagnostics.
  Generic news isolation is retired. Quote validity and historical training
  exclusion are separate invariants and are unchanged.

## Persistence, publication and recovery

Worker -> append-only evidence -> scheduler -> archive/mirror -> D1 -> public
reader retains the existing source identity and strict synchronization ACK.
Recovery clocks already participate in incremental archive invalidation. Old
failure and retirement enum values remain readable audit evidence, not a new
policy for permanently stopping news.

Forward migration 0037 replaces four expression indexes with the same two-state
classification used by the reader. Apply it before activating the new reader.
No article rewrite or full archive replay is required. Old activated-generation
ISOLATED counts fold into PROCESSING until normal publication replaces them;
category counts and keyset membership must agree throughout that interval.
Index creation is one-time work, distinct from normal bounded page reads.

A crash before the recovery transaction commits changes nothing. After commit,
normal scheduling resumes. A failed attempt stays incomplete with a future retry.
Quota admission, current eligibility and model visibility still apply. Historical
accepted prose is not mass-rewritten or backdated. Reverting code preserves facts
but restores the old stopping policy; production uses the main-only fix-forward
entrypoint, not the retired blue/green control plane.

## Execution and verification

| Boundary | Executed evidence |
| --- | --- |
| Python worker to immutable failure rows | Mixed provider/validation attempts remain retryable; no annotation success is fabricated |
| Scheduler lease to candidate reader | Repeated missing prerequisites remain pending; later availability can complete the same job |
| Recovery receipt to annotation/title/impact reader | Old failure remains immutable; one versioned grant requeues current work; retired jobs stay retired |
| Collector failure clock to later source fetch | Old terminal source receipt does not prevent later successful hydration |
| SQL migration to public page | Both persisted index families preserve counts, category filtering, forward/reverse keyset traversal and indexed seeks |
| Built Worker to web readers | Full web build/tests, two public categories and old-count compatibility |

Final local checks: 401 related Python tests passed before the final composition
corrections. Afterwards, all 134 scheduler and 154 forward/source tests passed;
architecture contracts passed 177 tests with 4 declared skips. Full web validation
passed 393 tests with 6 declared skips. Generated architecture and import/repository
policy checks passed. Author verification is not independent
review, deployed Preview verification or production acceptance.

A real Gemma display rehearsal of immutable annotation
`afd8a749-bae8-5ee2-b05f-67d7d26b2b37` returned HTTP 500 at September 12, 15:27 MYT.
Both bounded attempts used the existing scheduler quota accountant. Only request
and quota receipts were written; no source, annotation or job was changed. This
is a provider-availability result, not a model-accuracy pass.

Remaining gates: independent PR review; immutable deployed Preview checks on
desktop, 390x844 and 360x800; migration and main publication; actual backlog
recovery and synchronization; real-provider fidelity evaluation and reviewed
append-only correction of existing accepted prose. Non-production builds are
currently disabled in the documented Cloudflare deployment configuration. No
Preview pass or production recovery is claimed, and no browser session was opened.
