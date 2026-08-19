# Cloudflare Deployment Runbook

The hosting architecture is described in
[`CLOUDFLARE_HOSTING.md`](../design/CLOUDFLARE_HOSTING.md). Run these commands
from `web/`:

```powershell
npm run lint
npm test
npx wrangler d1 migrations apply aurum-signal-room --remote
npx wrangler deploy
```

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
