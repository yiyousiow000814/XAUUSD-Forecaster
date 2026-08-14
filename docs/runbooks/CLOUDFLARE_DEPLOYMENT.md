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

After changing a Worker secret, restart `Dashboard Mirrors` in the Control
Center so the child process receives the current user-level environment.

Verify the deployed Worker, required API routes, and dashboard synchronization
before describing the deployment as recovered.

GitHub checks validate the branch; they do not deploy it. Do not add a workflow
`environment:` key or call GitHub's Deployments API. When inspecting GitHub API
resources with `gh api`, pass `--method GET` explicitly whenever field flags are
also present: `-f` otherwise changes the default request into a write.

If an accidental GitHub Deployment is created, mark it inactive, delete the
deployment, then delete the unused environment. Confirm both list endpoints are
empty with explicit GET requests.
