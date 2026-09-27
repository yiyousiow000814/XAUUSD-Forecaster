# XAUUSD News Web

The web application for XAUUSD News: gold quotes, curated news events, daily
briefs, storylines, and operational health. The public site is branded 黄金资讯.
Cloudflare Workers is the only production hosting plane; D1 holds the published
read models, so visitors do not connect directly to the local Windows runtime.

## Development

Use Node.js `>=22.13.0` and the pinned lockfile:

```bash
npm ci
npm run dev
npm test
```

`npm test` verifies binding types, builds the Worker and prerendered pages, and
runs the Web regressions. `npm run lint` checks source. `npm run cf:types` refreshes
binding types after a configuration change. Python package installation and the
local service setup are documented in the [root README](../README.md).

## Production and Preview

Protected `main` in `yiyousiow000814/xauusd-news` is the only production source.
Native Cloudflare Workers Builds runs `npm ci && npm test` from `/web`, then
`npx wrangler deploy --message "main:$WORKERS_CI_COMMIT_SHA"`. The Worker is named
`xauusd-news`. Its D1 UUID and persisted data stay independent from the product
name. The exact build settings live in
[cloudflare-build-contract.json](cloudflare-build-contract.json).

Branch Previews are isolated, read-only artifacts. They must not activate local
services or mutate production resources. Follow the
[Preview behavior specification](../docs/specs/PREVIEW_BEHAVIOR.md) and
[isolation contract](../docs/contracts/PREVIEW_ISOLATION.md).

## Authentication and synchronization

Public reading routes are anonymous. Cloudflare Access protects `/admin*` and
the compatibility admin routes. The Worker also checks the configured operator
identity allowlists; Access identity alone does not grant operator authority.
Machine endpoints use the ingest credential and applicable lease checks.
Never commit secrets, owner identities, or production configuration.

The local synchronizer publishes authoritative snapshots and bounded deltas to
Cloudflare with strict acknowledgement. Its configured destination must match
the Worker hostname. Setup, Access policies, bindings, and recovery belong to the
[deployment runbook](../docs/runbooks/CLOUDFLARE_DEPLOYMENT.md) and
[release contract](../docs/contracts/RELEASE_CONTROL.md).
