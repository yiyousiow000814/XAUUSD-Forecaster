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

Configure one Cloudflare Access self-hosted application for these production
human paths only:

- `/assistant`
- `/api/assistant-chat`
- `/api/assistant-conversations`
- `/api/news-questions`
- `/api/operator-retry`

Allow only the configured owner identity and set `CF_ACCESS_TEAM_DOMAIN`,
`CF_ACCESS_AUD`, and at least one of
`DASHBOARD_OPERATOR_OWNER_SUBJECTS` or `DASHBOARD_OPERATOR_OWNER_EMAILS` to
match that application. Keep the Access cookie path restriction disabled so
Assistant and System API paths share one application session. If Google is the
chosen identity provider, enable it on this same application and optionally use
instant authentication; the owner allowlist remains mandatory authorization.
Do not create a retry-specific Access application or login state.

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
   allowlist without removing the existing Assistant path.
2. Set the new `DASHBOARD_OPERATOR_OWNER_*` secret(s). Legacy
   `ASSISTANT_OWNER_*` values may remain during one cutover but do not broaden a
   configured shared allowlist.
3. Apply D1 migration `0020_operator_retry_scheduling.sql`.
4. Deploy one Worker revision containing both `/api/operator-retry` and
   `/api/operator-retry-worker`.
5. Configure `DASHBOARD_OPERATOR_BRIDGE_TOKEN`, then restart both the local
   Dashboard API and `Dashboard Mirrors`.
6. Verify anonymous Dashboard reads still work, anonymous privileged reads and
   writes fail, one Access login works across Assistant and System, the machine
   route rejects human credentials, and the localhost bridge rejects missing or
   wrong credentials.
7. Submit one bounded safe retry command and observe `PENDING` -> `APPLYING` ->
   a terminal result, followed by the updated retry-job mirror and `SYNC OK` at
   the exact deployed main revision.

Verify the deployed Worker, required API routes, and dashboard synchronization
before describing the deployment as recovered. A running process or accepted
cloud command is not sufficient; the final scheduler mirror must reflect the
applied result.

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
