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

Before enabling the private Assistant, configure one Cloudflare Access
self-hosted application for these production paths only:

- `/assistant`
- `/api/assistant-chat`
- `/api/assistant-conversations`
- `/api/news-questions`
- `/api/operator-retry`

Allow only the configured owner identity and set `CF_ACCESS_TEAM_DOMAIN`,
`CF_ACCESS_AUD`, and at least one owner allowlist secret to match that
application. Do not add `/api/assistant-worker/*` to the Access application.
Do not add `/api/operator-retry-worker` either.
The Windows synchronizer reaches that separate control plane with
`INGEST_TOKEN`; unauthenticated requests to it must receive `401` from the
Worker.

After changing a Worker secret, restart `Dashboard Mirrors` in the Control
Center so the child process receives the current user-level environment.

Verify the deployed Worker, required API routes, and dashboard synchronization
before describing the deployment as recovered. Verification includes an Access
login through `/assistant`, an authenticated human API request, an
unauthenticated machine-route rejection, a successful Windows claim cycle, and
`SYNC OK` at the exact deployed main revision.

GitHub checks validate the branch; they do not deploy it. Do not add a workflow
`environment:` key or call GitHub's Deployments API. When inspecting GitHub API
resources with `gh api`, pass `--method GET` explicitly whenever field flags are
also present: `-f` otherwise changes the default request into a write.

If an accidental GitHub Deployment is created, mark it inactive, delete the
deployment, then delete the unused environment. Confirm both list endpoints are
empty with explicit GET requests.
