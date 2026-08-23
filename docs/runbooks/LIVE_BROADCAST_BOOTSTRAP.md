# Live Broadcast Bootstrap Runbook

## Safety boundary

This is a one-time coordinated bootstrap for `aurum-live-broadcast`. It is not
part of the normal `aurum-signal-room` `wrangler versions upload` flow. Never
apply these commands to `aurum-signal-room` or `aurum-signal-room-preview`.

Repository CI, Preview validation, and Candidate dry-runs stop before platform
mutation. They must not create the service, namespace, secret, route, or state.

## Preflight inspection

From an exact clean main checkout:

```powershell
git fetch origin
git rev-parse HEAD
git rev-parse origin/main
git status --short
Set-Location broadcast
npm ci
npx wrangler whoami
npx wrangler deployments list --name aurum-live-broadcast
npx wrangler deploy --dry-run --outdir .wrangler-dry-run
```

Record the exact main SHA, Wrangler version, account, current service lookup,
dry-run output, and confirmation that the config names only `LiveHub` on
`aurum-live-broadcast`. Inspect the Cloudflare dashboard/API for an existing
service and namespace before proceeding. A surprising existing resource stops
the bootstrap for review.

## One-time lifecycle bootstrap

After explicit operator approval, the isolated lifecycle command is:

```powershell
npx wrangler deploy --name aurum-live-broadcast --var AURUM_GIT_COMMIT_SHA:<exact-main-sha>
```

This is intentionally `deploy`, not `versions upload`: the initial
`new_sqlite_classes` migration must create the SQLite-backed `LiveHub` namespace.
Expected resources are one Worker service, one `LIVE_HUB` binding, and one
SQLite-backed `LiveHub` namespace. No website route, D1 database, existing
Durable Object, or production traffic setting may change.

Do not run the command from this PR preparation task.

## Secret and verification

Create a unique high-entropy token outside source control, then set it only on
the isolated service:

```powershell
npx wrangler secret put LIVE_BROADCAST_PUBLISH_TOKEN --name aurum-live-broadcast
```

Verify `/health` contains the intended code revision, `PUBLIC_LIVE_V1`, a ready
binding, and no secret. Run one authenticated `dry_run=true` publish and prove
that `latest_available` and subscriber delivery did not change. Then run an
approved local/external WebSocket smoke against `/subscribe`, followed by one
real compact state publish from a non-Preview operator fixture. Confirm the new
subscriber immediately receives `FULL_STATE`, multiple subscribers receive the
next update, and application writes are rejected.

## Coordinated website and Windows cutover

1. Record exact main and broadcast revisions.
2. Bootstrap and verify the isolated service and secret.
3. Keep the website Candidate at 0%.
4. Configure `VITE_LIVE_BROADCAST_URL` for the intended main Candidate.
5. Configure Release Control health URL and, only when needed, the explicitly
   compatible broadcast revision.
6. Run Candidate validation and record broadcast readiness separately from HTTP
   fallback.
7. Verify Candidate WebSocket, stale handling, and HTTP fallback.
8. Promote the website only after all existing gates pass.
9. Activate the matching Windows publisher during the normal runtime cutover;
   Preview and validation continue to use dry-run.
10. Keep OBSERVING until normal decision cycles complete.

## Failure and rollback

If bootstrap fails, stop website cutover. Do not retry blindly; preserve the
error and inspect the service, binding, migration tag, and account. Do not
delete a partially created namespace until Cloudflare state and recovery impact
are understood.

If broadcast later fails, leave the website on bounded `/api/status` fallback
and stop publisher activation. Reversing the website to old Stable requires no
broadcast rollback. Do not delete the Worker namespace or latest-state record;
the isolated service may remain deployed but unused.
