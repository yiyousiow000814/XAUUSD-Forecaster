# Cloudflare Deployment Runbook

The hosting architecture is described in
[`CLOUDFLARE_HOSTING.md`](../design/CLOUDFLARE_HOSTING.md). Run these commands
from `web/`:

```powershell
npm run lint
npm test
npx wrangler versions upload --message "release:<full-main-git-sha> branch:main artifact_kind:PRODUCTION_CANDIDATE"
```

Both production and non-production Workers Builds commands upload immutable
Versions. The production command MUST NOT be `wrangler deploy`. Its configured
command is:

```text
Production:     npx wrangler versions upload --message "release:$WORKERS_CI_COMMIT_SHA branch:$WORKERS_CI_BRANCH artifact_kind:PRODUCTION_CANDIDATE"
Non-production: npm run cf:preview-upload -- --message "release:$WORKERS_CI_COMMIT_SHA branch:$WORKERS_CI_BRANCH artifact_kind:PREVIEW"
```

The local Control Center discovers the exact release identity, keeps Candidate
at 0%, and runs boundary-appropriate automatic validation. A successful build
or merge does not change Stable. After Candidate is PASSED, the operator uses
**Promote Candidate**, confirms the exact Git/Worker/Windows identities, and
waits for the existing decision-cycle observation. **Reverse Stable** restores
the recorded Previous Stable identities without rolling back D1 or deleting
SQLite evidence. See [`RELEASE_CONTROL.md`](../contracts/RELEASE_CONTROL.md).

Bootstrap a previously unmanaged runtime only after the Cloudflare production
build command above is saved and the active Worker deployment is rechecked at
100%. Use **Bootstrap Release Control** in the Control Center once, verify the
recorded Stable Worker and Windows identities, then allow Candidate discovery.
Do not hand-edit `release-control-state.json` or copy validation evidence from a
different Worker Version ID or Git SHA.

The non-production script passes an explicit `--name aurum-signal-room-preview`
target. Do not replace this with a named environment on the production service:
Cloudflare service environments share that service's Version history. The
separate Preview Worker must never upload a Version into the production
`aurum-signal-room` history. The commands deliberately emit different immutable
artifact kinds; a production candidate additionally requires `branch:main` and
Git reachability from `origin/main`.

Normal builds and Candidate staging never apply D1 migrations or provision
bindings. Schema, migration, provisioning, or destructive storage changes are
`COORDINATED_STORAGE_MIGRATION_REQUIRED` and have no simple UI override.
Non-destructive Worker configuration or binding metadata changes are
`PLATFORM_CONFIG_REVIEW_REQUIRED`; after the exact Candidate provenance,
required checks, and existing production resource identities are verified, the
operator may approve that exact Version+SHA with **Approve Compatibility**.
Approval is audited and never carries to another Candidate. Missing resources
remain blocked; do not use Wrangler auto-configuration as recovery.

Worker-changing Candidates require a Cloudflare API token limited to read-only
Workers Observability query access. Store it only in the Windows user
environment as `CLOUDFLARE_RELEASE_OBSERVABILITY_TOKEN`; never pass it on a
command line or write it to repository/runtime state. Without that protected
credential the controller deliberately leaves the Candidate in TESTING with
`PLATFORM_CPU_EVIDENCE_REQUIRED`. The queried evidence is bound to the exact
Worker Version ID and must satisfy the CPU/headroom policy in the release
contract, including exact probe counts and zero `exceededCpu`, 1102, or 5xx.
The controller reads the candidate revision's Worker validation manifest,
builds deterministic production-shaped fixtures in an isolated candidate
worktree, runs excluded warm-ups, and then gathers repeated global and
route-family platform samples. Dry-run writes exercise the normal bounded
transport and read-only D1 validation but stop before authoritative mutation.
Do not substitute `{}` fixtures or treat one invocation per route as CPU
acceptance.

Normal release operation uses only the confirmed Control Center actions:
**Open Candidate**, **Approve Compatibility** when narrowly eligible,
**Promote Candidate**, and **Reverse Stable**. Hidden PowerShell actions are not
the operator workflow.

After Candidate validation, confirm `/` and `/favicon.ico` are Static Asset responses and
do not create Worker invocations. Confirm `/health` and `/audit` are also Static
Assets and that their raw HTML contains `系统健康状态` and `证据台页面`
respectively. For API probes, record `X-Aurum-Git-SHA`,
`X-Aurum-Worker-Version`, `X-Aurum-Route`, `X-Aurum-Resource`, and
`X-Aurum-Request-Id`, then correlate them with Workers Logs. Inspect actual
Cloudflare CPU time for the route-family soak; local Windows process CPU timers
are not a substitute for the platform invocation measurement.

The local benchmark reports the same production-shaped route family with
diagnostic logging disabled and enabled. The difference estimates local logging
cost only; neither result proves the Free-plan CPU limit is safe or that a prior
1102 is resolved.

Candidate staging, directed validation, Promote, observation, and Reverse are
owned by Release Control. Operators must not substitute ad-hoc Wrangler deploy,
versions-deploy, rollback, or remote migration commands for that state machine.

## Historical PR #268 evidence

The pre-rebase PR #268 Candidate produced 104 distinct Cloudflare platform
samples: all 104 returned HTTP 200, CPU p50/p95/p99/max was 2/4/4/5 ms, and
there were zero 5xx, `exceededCpu`, or 1102 outcomes. Its `/api/audit` Candidate
payload was approximately 2,180 bytes. This is historical acceptance evidence
only. A rebased main `PRODUCTION_CANDIDATE` must repeat exact-Version+SHA Release
Control validation; this evidence cannot authorize promotion.

When a migration schedules a new version of work for a Windows consumer, use a
two-phase cutover. First deploy the claim-generation compatibility gate and
verify the old worker receives no new-generation item. Then apply the scheduling
migration and activate the matching runtime revision. A migration that exposes
new-generation jobs to an unversioned old claimant must not be applied.

## Shared Dashboard Operator authentication

Cloudflare Zero Trust must be activated before this boundary can be created.
Select the Zero Trust Free plan if it is appropriate for the account; Cloudflare
still requires the account owner to complete the plan and payment-details step.
This account subscription action is separate from a Worker deployment.

Configure Google under **Zero Trust -> Integrations -> Identity providers**.
The Google OAuth client uses
`https://<team-name>.cloudflareaccess.com` as its authorized JavaScript origin
and `https://<team-name>.cloudflareaccess.com/cdn-cgi/access/callback` as its
authorized redirect URI. Test the IdP connection before attaching it to the
application.

Configure one Cloudflare Access self-hosted application with these four
production destinations:

- `/admin*` (Admin pages and the canonical `/admin/api/*` browser APIs)
- `/assistant` (compatibility redirect)
- `/retry-jobs` (compatibility redirect)
- `/status` (compatibility redirect)

The canonical browser API aliases under `/admin/api/*` re-export the existing
fail-closed handlers. Do not use a broad `/api/*` Access destination: it would
capture anonymous research reads and the machine control plane. The legacy
`/api/admin-status`, `/api/assistant-health`, `/api/assistant-chat`,
`/api/assistant-conversations`, `/api/news-questions`, and
`/api/operator-retry` URLs remain server-authorized compatibility handlers,
but the Admin browser must use the protected aliases.

Allow only the configured owner identity and set `CF_ACCESS_TEAM_DOMAIN`,
`CF_ACCESS_AUD`, and at least one of
`DASHBOARD_OPERATOR_OWNER_SUBJECTS` or `DASHBOARD_OPERATOR_OWNER_EMAILS` to
match that application. Keep the Access cookie path restriction disabled so
all Admin pages and human API paths share one application session. If Google is the
chosen identity provider, enable it on this same application and optionally use
instant authentication; the owner allowlist remains mandatory authorization.
Do not create Assistant-, retry-, or usage-specific Access applications or
login state.

After saving the application, copy its exact audience tag to `CF_ACCESS_AUD`;
do not reuse an audience from an older or deleted application. List applications
and policies with the Cloudflare API before and after the change, and verify
that all protected entries belong to this one application and owner-only Allow
policy.

Do not add `/api/assistant-worker/*` or `/api/operator-retry-worker` to the
Access application. The Windows synchronizer reaches those separate machine
planes with `INGEST_TOKEN`; unauthenticated requests must receive `401` from the
Worker.

## Local operator bridge credential

Create a separate credential with at least 32 characters; 48 cryptographically
random bytes encoded as base64 is recommended. Store the same value in the
Windows user environment as `DASHBOARD_OPERATOR_BRIDGE_TOKEN` for both the
Dashboard API and Dashboard Mirrors processes. Never place it in
`dashboard-sync.json`, a URL, command-line argument, log, D1, SQLite evidence,
or source control. This token is independent from `INGEST_TOKEN` and Cloudflare
Access credentials.

After changing a Worker or bridge secret, restart the affected local Dashboard
API and `Dashboard Mirrors` processes in the Control Center so child processes
receive the current user-level environment.

## Operator retry production cutover

1. Verify the shared Access application paths, IdP, audience, and owner-only
   allowlist without removing the Admin and compatibility paths.
2. Set the new `DASHBOARD_OPERATOR_OWNER_*` secret(s). Legacy
   `ASSISTANT_OWNER_*` values may remain during one cutover but do not broaden a
   configured shared allowlist.
3. Apply D1 migration `0020_operator_retry_scheduling.sql`.
4. Deploy one Worker revision containing both `/api/operator-retry` and
   `/api/operator-retry-worker`.
5. Configure `DASHBOARD_OPERATOR_BRIDGE_TOKEN`, then restart both the local
   Dashboard API and `Dashboard Mirrors`.
6. Verify anonymous Dashboard reads still work, anonymous privileged reads and
   writes fail, one Access login works across every Admin destination, the machine
   route rejects human credentials, and the localhost bridge rejects missing or
   wrong credentials.
7. Submit one bounded safe retry command and observe `PENDING` -> `APPLYING` ->
   a terminal result, followed by the updated retry-job mirror and `SYNC OK` at
   the exact deployed main revision.

Verify the deployed Worker, required API routes, and dashboard synchronization
before describing the deployment as recovered. A running process or accepted
cloud command is not sufficient; the final scheduler mirror must reflect the
applied result.

Run the anonymous probes from the repository root:

```powershell
python scripts/check_public_health.py
python scripts/check_admin_access_boundary.py
```

The first command covers only genuinely public surfaces. The second requires
every human Admin path, including `/admin/auth-complete` and
`/admin/api/session`, to redirect to Cloudflare Access while public surfaces
remain anonymously reachable and machine-only endpoints continue to return the Worker's own
`401`, not an Access login redirect. Then complete one browser login at
`/admin`, verify `/admin/api/assistant-health` returns its current private schema, and
navigate through Assistant, Retry Jobs, and AI Model Usage without another
login. On a public page, confirm that the login action opens a popup, returns
to the original tab, changes the shared desktop and phone navigation to
`管理后台`, and enters `/admin`. Also verify popup blocking falls back to a full
page `/admin` handoff. Test a non-owner Google identity for denial, then visit both the
team-domain and application-domain `/cdn-cgi/access/logout` endpoints before
confirming that the next `/admin` navigation requires authentication again.
The team-domain endpoint clears the global Access session; the application-
domain endpoint clears the application cookie immediately.

Rollback disables retry mutation without rewriting evidence: disable its UI,
stop `Dashboard Mirrors` command consumption, and redeploy the prior Worker.
Do not delete D1 request/event rows or local append-only override evidence. The
local bridge remains fail-closed if its credential is removed. Migration 0020
tables may be retained because they are isolated control-plane evidence.

GitHub checks validate the branch; they do not deploy it. Do not add a workflow
`environment:` key or call GitHub's Deployments API. When inspecting GitHub API
resources with `gh api`, pass `--method GET` explicitly whenever field flags are
also present: `-f` otherwise changes the default request into a write.

If an accidental GitHub Deployment is created, mark it inactive, delete the
deployment, then delete the unused environment. Confirm both list endpoints are
empty with explicit GET requests.
