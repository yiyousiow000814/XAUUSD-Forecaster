# Cloudflare Deployment

The sole production application is `aurum-signal-room`. Workers Builds connects
to `yiyousiow000814/XAUUSD-Forecaster`, production branch `main`, root `/web`.
Non-production builds are disabled. Build: `npm ci && npm test`.
Deploy: `npx wrangler deploy --message "main:$WORKERS_CI_COMMIT_SHA"`.

Merge only after required checks and review. Check current production schema and
input compatibility before main activation. Native deployment serves one version
at 100 percent traffic. Verify deployed source identity, real business API
responses and strict sync ACK. A failed deployment is a failure to fix forward.
Do not restore an old database or fabricate health/ACK evidence.

On 2026-09-08 the settings above were saved and obsolete Workers `agents`,
`aurum-signal-room-preview` and inactive `aurum-live-broadcast` were deleted.
The dashboard inventory was refreshed and showed exactly one application.
Required production storage, secrets and Access were preserved.

Production currently needs the reviewed 0031 learning-history schema checked
against measured D1 quota headroom before current main is activated. Configuration
completion does not establish migration or deployment completion.
