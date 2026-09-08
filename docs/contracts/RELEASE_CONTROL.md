# Release Control Contract

Protected main is the only production source. Pull requests validate changes;
they cannot activate local services. Native Cloudflare Workers Builds builds
main and deploys one version at 100 percent traffic. GitHub Actions validates
code and must not create Deployments or Environments.

The local main service entrypoint is `scripts/run_main_services.ps1`. It owns one
runtime checkout and one service set. Only its root-specific machine owner may
stop, update and start those services. Updates resolve origin/main to one commit,
preserve dirty work and persistent data, and never select a PR branch. Updates
may interrupt service. Failure is visible and corrected forward; no code backup,
Stable/Candidate coordination or automatic rollback is part of this design.

Authentication, Collector atomicity, source-first processing, strict ACK,
append-only facts, source identity and database compatibility remain required.
Assistant stays PAUSED. Updating code must not replace latest authoritative data
with historical data. Historical release receipts remain audit only.

Before installed takeover, disable the old exact scheduled writers, verify the
replacement entrypoint and preserve supervision of live business processes.
Production acceptance requires actual deployed source and input identity,
business health and synchronization. Local tests do not establish activation.
