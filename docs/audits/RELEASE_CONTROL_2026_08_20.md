# Release Control Ownership Audit — 2026-08-20

This point-in-time audit records the bootstrap boundary before release-control
implementation. It is evidence, not the normative release contract.

## Current ownership

| Boundary | Current owner and behavior | Risk |
| --- | --- | --- |
| Git | `origin/main` is the accepted source revision. | Main movement is treated as an activation signal by downstream automation. |
| Cloudflare build | Workers Builds watches `main`, runs `npm ci && npm test` from `/web`, then runs `npx wrangler deploy`. Non-production branches use `npx wrangler versions upload`. | A successful main build creates a version and immediately assigns production traffic. |
| Cloudflare traffic | Version `76d314fc-e484-4f50-8ace-3689e0896709` owns 100%; accepted PR #268 version `dd823aa4-20f0-47e1-9255-1b785a4c17b0` is present at 0%. | Traffic is currently correct, but the production build command can replace it implicitly. |
| Windows revision discovery | The watchdog polls the deployed dashboard revision and `origin/main` every five minutes. | A matching newer revision is automatically checked out into the production runtime. |
| Windows activation | The watchdog runs production-shaped preflight, force-checks out the revision, reloads collector/annotator/API/sync, and enters `OBSERVING`. | Candidate validation and production activation are coupled; a staged revision can become the sole production owner without an operator promotion. |
| Windows rollback | `previous_revision`, runtime code state, two decision-cycle observation, and automatic checkout/service rollback are durable local mechanisms. | These useful mechanisms are revision-only and do not coordinate a Cloudflare Worker version. |
| D1 and SQLite | D1 and local evidence remain outside code rollback. | No release record currently expresses compatibility across Worker, Windows, and additive storage evolution. |

## Target ownership

| Boundary | Target owner and behavior |
| --- | --- |
| Git | Push and main movement may discover a release but never change Stable. |
| Cloudflare build | Every branch, including `main`, uploads an immutable Worker Version. Builds never assign production traffic. |
| Candidate | One durable release record binds the exact Git SHA, Worker Version ID, staged Windows revision, compatibility state, and validation evidence. Replacement never inherits prior evidence. |
| Validation | The local release controller classifies changed boundaries and automatically runs the required repository, isolated Windows, and directed 0% Worker gates. |
| Stable | One durable release record owns the active Worker version and the one Windows production owner. It changes only through the local Control Center's explicit Promote transaction. |
| Previous Stable | Promotion records the complete prior Stable release. Reverse restores its Worker version and Windows revision without rolling back D1 or deleting evidence. |
| Transaction owner | A durable local lock serializes Promote and Reverse. Incomplete phases reconcile observed Worker and Windows identity after restart and fail closed on drift. |
| Existing runtime mechanisms | Preflight, service process management, `OBSERVING`, decision-cycle evidence, `previous_revision`, and rollback are reused as transaction phases rather than duplicated. |

## Bootstrap requirement

Before this branch can be merged, the Cloudflare production deploy command must
be changed from `npx wrangler deploy` to `npx wrangler versions upload` while
the current Stable remains at 100%. The Windows watchdog must also stop treating
`origin/main` or the deployed Git revision as authorization to checkout or
activate production code. These two controls are independent and both are
required.

The Cloudflare Settings > Build bootstrap was applied after the current values
above were recorded. Both production **Deploy command** and non-production
**Version command** now use:

```text
npx wrangler versions upload --message "release:$WORKERS_CI_COMMIT_SHA branch:$WORKERS_CI_BRANCH"
```

Cloudflare's injected commit and branch variables make the immutable Version
discoverable as one release identity. A deployment-status read immediately
after saving showed the same deployment ID and the same Stable 100% / accepted
#268 Candidate 0% split; saving the build configuration assigned no traffic.
The configuration rollback is to restore the recorded former production
command `npx wrangler deploy`; it is documented for emergency control-plane
recovery and intentionally not executed because it re-enables implicit Stable
promotion.

The post-bootstrap Windows read remained on applied revision
`783d25314b090dd7fbbf124777c3b8de517d2b85`. Collector, annotator, dashboard
API, and dashboard sync each had one matching process; the local status surface
reported RUNNING/RUNNING/API OK/SYNC OK. No production runtime checkout,
service restart, Promote, or Reverse was executed by this work.

The accepted PR #268 head and 0% Candidate are reference evidence only. This
work must not modify, merge, promote, replace, or relabel them.

At 2026-08-20 20:11 MYT, a fresh deployment read still showed Stable
`76d314fc-e484-4f50-8ace-3689e0896709` at 100% and the accepted PR #268
Candidate `dd823aa4-20f0-47e1-9255-1b785a4c17b0` at 0%. Exact-version Workers
Observability over the preceding hour reported 56 invocations, maximum and p99
CPU of 11 ms, zero `exceededCpu`, and zero 5xx responses. The 11 ms observation
is recorded rather than relabeled as proof of a strict 10 ms ceiling; Cloudflare
documents limited execution flexibility, while `exceededCpu` is the platform
termination outcome.

When PR #268 is later rebased onto release-control `main`, its stale statement
that the audit first page carries three Daily Briefs must be reconciled with its
accepted split-audit contract: `/api/audit` is the fixed summary and
`/api/audit-briefs` is bounded detail.
