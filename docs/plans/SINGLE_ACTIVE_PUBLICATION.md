# Single-active maintenance publication replacement

## Current authority

The owner's latest instruction on 2026-09-08 supersedes the earlier automatic
main/no-rollback draft. Retire custom Stable/Candidate coordination. Use one
active version, a controlled maintenance window, direct deployment and an
explicit, verified code recovery target. Main movement alone must not stop the
Server or deploy production traffic. No old NORMAL Switch/Observe prerequisite
applies to implementation of this replacement.

Preserve WIP, accepted business corrections, original databases, source-first
processing, strict ACK, Collector atomicity, authenticated boundaries, version
identity and single process ownership. Assistant remains PAUSED. No broker,
research activation, secrets/Access changes or paid services are authorized.

## Smallest replacement flow

Fixed source and Worker artifact -> build/security/business tests -> data and
interface compatibility -> authorized maintenance window -> one-version deploy
-> real business health and sync confirmation -> end maintenance or recover.

Workers Builds can prepare the immutable main artifact. The maintenance entry
uses Cloudflare's native deployment at 100 percent traffic for one version; no
Candidate discovery, supersession or qualification graph is involved. GitHub
continues to validate code and does not become a deployment plane.

Recovery deploys the recorded prior compatible Worker and local code. It never
restores an old database over current authoritative data. Before entering
maintenance, verify the recovery code exists and can read the current data and
interface contracts. Failed recovery is reported as failed, never as healthy.

## Owners and states

| Actor | Authority |
|---|---|
| Protected main and CI | Source artifact and build/security/business checks |
| Workers Builds | Fixed Worker artifact; no production traffic mutation |
| Maintenance entry | Sole publication writer after takeover; records actual mutations |
| Single runtime supervisor | One owner of the existing service registry and desired running/stopped state |
| Business services | Existing authoritative data, migrations, ACK and authentication |
| Operator | Existing maintenance authorization, scope and any required independent approval |

Routine states are running, maintenance and failed. The supervisor cannot
update source or undo an intentional maintenance stop. Runtime and Worker
source identities and maintenance outcome are observable. Audit history is
retained but does not grant present publication authority.

## Takeover and compatibility

Build and verify the new entry alongside the still-running old supervisor.
Inventory old scheduled tasks, launchers and publication writers; isolate their
write authority during the authorized takeover before enabling the new owner.
Do not delete old supervision first or blindly kill business processes.
New entry adoption is tested before removing the old exclusive implementation.

Old controller/new publication ownership is unsupported after takeover. Old
historical release files remain untouched and unused. New code must read
existing data without destructive reset. Local and Worker update ordering must
preserve actual HTTP/data compatibility. A partial code update or process crash
must leave a usable explicit recovery route and current evidence intact.

## Failure and verification matrix

- Before maintenance: unavailable artifact, dirty checkout, failed tests or
  unknown compatibility reject publication without stopping business.
- During maintenance: stop only owned processes, preserve all current data,
  deploy fixed identities, and record each successful or failed mutation.
- Startup/provider/sync failure: remain in maintenance and execute explicit
  compatible code recovery; validate business and ACK again.
- Controller/machine restart: preserve intentional stop, report unfinished
  maintenance, and permit explicit recovery without the failed condition.
- Duplicate owner: reject the second writer without corrupting the first
  owner's status. No PID-only termination or cross-checkout process killing.

Execute real Windows entrypoint, quoting, configuration/environment inheritance,
process start/stop, duplicate owner, maintenance interruption and recovery tests.
Use a local Git remote and isolated processes without copying production data
or reading production credentials. Test both success and failure paths before
real provider rehearsal. Author review is not independent review. Required
independent review, CI, actual deployment identity and real business acceptance
remain outstanding until recorded.

## Removal mapping

For each retired module, UI control, task and test, map the responsibility to
retired policy or replacement coverage. Preserve mixed business/process/data
properties before deleting old suites. Update contracts, CI, operating docs and
generated architecture. The final system has one formal publication entry.

## Separate remaining acceptance

CF capacity and news are not completed by release cleanup. Update only changed
budget inputs: normal business, initial catch-up, migration, retry and cleanup.
Measure real rows_read, rows_written, storage and spare capacity. Reconcile the
original news backlog item by item and verify new records on the actual page.
All required historical data must remain accessible; truncation or hiding rows
is not acceptance. Original PR intentions and bounded research remain in scope
without blocking minimal production recovery.

## Native provider boundary verified

Cloudflare documents native single-version deployment and explicit version
rollback. Linked resources are not reverted by Worker rollback, and data or
binding incompatibility can prevent safe use of older code. The maintenance
flow must inspect the actual retained recovery version before interruption.

- https://developers.cloudflare.com/workers/versions-and-deployments/
- https://developers.cloudflare.com/workers/wrangler/commands/workers/
- https://developers.cloudflare.com/workers/versions-and-deployments/rollbacks/

## Implementation evidence so far (not acceptance)

The unpublished supervisor replacement runs alongside retained old source in
this worktree. Production supervision and scheduled tasks are unchanged.
Real Windows fixtures exercise native argument binding, process deduplication
and stop, exact Git identity checkout, dirty-WIP rejection and code recovery
while newer data is retained. The public-health owner now checks the actual
Worker headers, local/source identities, data epoch, post-maintenance freshness,
Collector business status and the existing sync owner's heartbeat ACK. These
checks do not close separate news or CF-capacity acceptance.

Remaining: maintenance orchestration and takeover, failure/recovery rehearsal,
independent review, retired-responsibility mapping, final CI/contracts/docs and
architecture, actual deployment, business verification and separate CF/news.

The prepared replacement source-index view is not yet activated. Its additional
source/test groups exceed the existing per-source transport part count while
the old installation view is retained. Do not raise the part cap, discard tests
or claim the architecture replacement complete. Complete the retained graph's
transport/retirement step with all required facts present after validated
takeover; this is not a prerequisite for building the minimum runtime entry.

The publisher and operator state writes share the existing publication byte
lock across Python and PowerShell. This prevents Start from racing the stop
acknowledgement before maintenance is persisted. The lock is process-scoped;
a crashed publisher releases it, while persisted maintenance still rejects
ordinary Start/Stop until explicit recovery. Real Windows cross-runtime tests
cover contention, maintenance rejection and later normal writes. The fixed
publication source also pins the installed Wrangler version before provider
calls, including explicit recovery.

The trusted-main repository policy requires staged admission: first merge the
checker accepting the two exact immutable-upload contracts while retaining the
existing v1 build contract, then switch to v2 after the new entry takes over.
Both forms forbid build-triggered traffic changes. The old annotation does not
require Candidate validation in the replacement publisher. Remove v1 admission
with old release retirement. This prevents changing a trusted gate through the
same pull request whose new contract it is meant to validate.

## Current verification and required review

The real Windows publication shard passed after the bounded file-sharing fix;
its earlier failed result is retained outside the repository. Later launcher
and cross-runtime publication-lock contracts passed (17 tests), and the final
compatibility evidence family passed (11 tests). The existing complete web gate
passed (381 passed, 6 skipped). Repository policy, source import boundaries and
the retained generated architecture check passed. No independent review,
production takeover, provider deployment or real business acceptance is implied.

Final independent review must trace the installed controller files through the
actual Windows registry, configuration roots, retained business processes,
maintenance ownership, native Worker version assignment, API identity and sync
ACK. Verify crash recovery without overwriting newer facts and reconcile the
old task/shortcut writers before enabling the replacement. The author cannot
supply this independent approval. Full retirement and separate CF/news remain
open after minimum-entry review.
