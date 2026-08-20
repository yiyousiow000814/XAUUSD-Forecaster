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

- vinext and the Cloudflare Vite plugin compile the web application and
  prerender the public and Admin shells.
- Cloudflare Static Assets serves the distinct canonical `/`, `/health`, and
  `/audit` prerendered HTML shells, the favicon, and immutable client assets
  without invoking the Worker. Their raw HTML preserves page identity before
  hydration and when JavaScript is unavailable.
- a minimal Worker router owns API and ingest requests. It dispatches directly
  to route-specific modules; only an unmatched dynamic request can load the
  vinext application router.
- D1 stores the latest dashboard, learning, market-chart, and news resources.
- `run_dashboard_sync.py` writes both mirrors with independent state files.
- `xauusd_control_center.ps1` loads user-level URLs and tokens when starting the
  mirror process.

The two targets have independent synchronization state and failure reporting.
Growing resources use bounded snapshots or paged D1 records rather than an
ever-growing dashboard payload.

The synchronizer assigns the critical status heartbeat, bounded control work,
and heavy optional work to separate single-owner lanes. Optional resources have
target-specific durable cadence and exponential backoff state. A heavy lane
admits at most one resource at a time, while paged learning, market, news, and
evidence mirrors advance by bounded cursors. A 60--90 second optional build
therefore cannot delay the next heartbeat, and a process restart cannot recreate
a multi-resource CPU burst at the Worker.

Operator retry processing is bounded at 10 commands per 30-second control
cycle. The product drain SLA for an already queued batch is 30 seconds for 10
commands, 150 seconds for 50, and 300 seconds for 100. External transport or
local scheduler failures remain explicit SLA violations rather than reasons to
silently extend this envelope.

Worker responses and structured invocation logs identify the Git commit,
Cloudflare version, route, resource, correlation ID, wall duration, controlled
D1 operation count, byte counts, and failure stage. The Cloudflare Version
Metadata binding is runtime authority for the deployed version ID; the Git SHA
is embedded from the build revision.
