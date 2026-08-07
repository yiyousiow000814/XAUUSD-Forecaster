# Cloudflare Hosting Contract

The public dashboard has two independent hosting targets:

1. ChatGPT Sites remains a fallback mirror.
2. Cloudflare Workers is the independent public mirror and API runtime.

The local machine is the only collector. It reads cTrader and news inputs,
builds the point-in-time snapshot, then sends bounded snapshots to each remote
target. Public visitors read D1 and never connect to localhost.

## Components

- vinext and the Cloudflare Vite plugin compile the web application.
- the Worker serves the pages and API routes.
- D1 stores the latest dashboard, learning, market-chart, and news resources.
- `run_dashboard_sync.py` writes both mirrors with independent state files.
- `xauusd_control_center.ps1` loads user-level URLs and tokens when starting the
  mirror process.

## Security

All public API reads are anonymous. Every ingest route requires the Worker
secret `INGEST_TOKEN`. The local token is stored as a Windows user environment
variable and is never written to Git. The ChatGPT Sites bypass header is sent
only to `*.chatgpt.site`; it is never forwarded to Cloudflare.

## Failure Semantics

A failed target is reported as degraded without stopping the other target.
Growing optional resources such as news details do not mark the live heartbeat
offline. If both targets reject the heartbeat, the synchronizer records an
error. Local collection continues regardless of public-hosting health.

## Deployment

Run from `src/XAUUSD-Forecaster/web`:

```powershell
npm run lint
npm test
npx wrangler d1 migrations apply aurum-signal-room --remote
npx wrangler deploy
```

After changing a Worker secret, restart `Dashboard Mirrors` in the Control
Center so the child process receives the current user-level environment.
