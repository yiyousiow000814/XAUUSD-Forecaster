# Cloudflare Hosting Design

This document explains the hosting architecture and failure boundaries. The
non-negotiable Preview safety boundary is defined in
[`PREVIEW_ISOLATION.md`](../contracts/PREVIEW_ISOLATION.md). Deployment commands
belong in [`CLOUDFLARE_DEPLOYMENT.md`](../runbooks/CLOUDFLARE_DEPLOYMENT.md).
Production hosting security and target-isolation guarantees are defined in
[`HOSTING_BOUNDARIES.md`](../contracts/HOSTING_BOUNDARIES.md).

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

The two targets have independent synchronization state and failure reporting.
Growing resources use bounded snapshots or paged D1 records rather than an
ever-growing dashboard payload.
