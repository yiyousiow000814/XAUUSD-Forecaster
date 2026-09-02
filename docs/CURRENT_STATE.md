# Current State

This page is a navigation aid, not a release receipt. The exact source identity
is always the checked Git revision (`git rev-parse HEAD`); this document moves
with that immutable revision.

## Topology

- Cloudflare Workers owns the public Web and Worker runtime.
- The local Windows runtime owns quote, collector, annotator, Dashboard API,
  Dashboard Sync, and optional live-broadcast processes.
- `scripts/xauusd_control_center.ps1` is the stable command facade. Canonical
  implementations live in the owner files declared by
  `scripts/control-center-owners.json`.
- Mutable runtime state remains under the explicit runtime root. Moving the code
  checkout does not move production state.

## Release authority

- Active is the observed production identity; Committed is the verified Stable
  identity; Last Known Good is the rollback authority.
- Transactions use `NORMAL` or the bounded `RECOVERY_HOTFIX` mode.
- Promotion consumes the authoritative 15-node Evidence DAG. There is no second
  receipt universe.
- Future history events use a bounded versioned projection; existing history is
  retained read-only.

## Installed state

Repository merge does not install the Control Plane and does not change Worker
traffic. The production-installed Control Plane therefore remains unchanged
until a separate, explicit installation task verifies and installs an exact
bundle.

## Current operational boundary

- Assistant is **PAUSED**.
- No Control Plane installation, Promote, Reverse, production data mutation, or
  service restart is authorized by this code campaign.

## Retained debt and next step

- The Python 14-module import SCC and the News detail/generation owner remain.
- The SQLite estate still requires a fresh measurement.
- The real production release waterfall has not run for this source revision.
- Stale pre-campaign Draft pull requests remain administratively out of scope.
- The next production step is an external final-main audit followed by a
  separately authorized exact-bundle Control Plane installation.
