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
