# Main-only direct publication

The owner superseded custom blue-green and maintenance/rollback publication on
2026-09-08. Protected main is the only production source. PRs remain validation
inputs until merged. Cloudflare Workers Builds uses main, `npm ci && npm test`,
and `npx wrangler deploy --message "main:$WORKERS_CI_COMMIT_SHA"`.

## Local ownership and transitions

One `run_main_services.ps1` process owns the existing service registry under a
root-specific machine mutex. Start persists running intent; Stop persists stopped
intent. The hidden launcher reloads the controller after source movement. Every
five minutes the owner fetches origin/main, resolves one commit, refuses dirty
checkouts, stops its services, updates that single checkout, installs changed
dependencies and restarts. No other branch, version slot or rollback is selected.
Failures remain visible and retry forward after a bounded delay. Original data,
configuration, secrets and audit evidence are outside source replacement.

Old scheduled release writers are disabled before takeover. Existing business
processes must remain supervised until the new entrypoint is installed and
verified. Deleting repository code does not prove installed runtime takeover.

## Retired responsibility mapping

| Removed responsibility | Disposition |
| --- | --- |
| Control Panel, Candidate discovery, supersession, qualification graph | Retired by owner direction |
| Stable/Candidate Switch, Observe, Reverse, recovery hotfix | Retired; no dual versions or rollback |
| Controller installation generations and watchdog guard | Replaced by one main service owner and hidden launcher |
| Single-active maintenance publisher and publication lock | Retired; native Cloudflare main deploy and local main fetch |
| Service launch, quoting, single owner, stop, source identity, data preservation | Real Windows contracts in test_main_runtime.py |
| Collector atomicity, news source-first, ACK, authentication, database authority | Existing business contract suites remain required |
| Historical receipts and audit | Preserved; no publication authority |

## Acceptance

Six Windows runtime contracts pass in an isolated real Windows execution.
Production takeover, final independent review, all required CI and business
health remain pending until recorded against the final source. Cloudflare main
configuration and deletion of three obsolete Workers are real completed writes.
Production D1 compatibility must be established before activating current main.
