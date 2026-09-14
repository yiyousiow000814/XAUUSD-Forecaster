# Dependency Maintenance Change Record

## Change contract

Consolidate open dependency updates #503 and #519-#523 into #529 on main
825313b8. Package manifests and lockfiles own the install graph; Wrangler owns
its generated Worker types; Dependabot owns update proposal grouping. No
production state, data schema, model, route contract, or service owner changes.

The build path is npm ci -> Vite/Vinext + React/RSC -> static assets/Worker;
Wrangler type generation and Miniflare must consume the same installed graph.
React, React DOM, and React Server DOM Webpack must use one exact release;
matching types are updated together. Patched sharp and js-yaml remain resolved
in Web and Broadcast. The Java action stays pinned to the reviewed upstream SHA.

## Failure, recovery, and acceptance

The former isolated React updates failed during peer resolution or rendering;
Wrangler failed its generated-type freshness check. Repair the package family
and regenerate types rather than weakening install or validation checks.
Partial install/build output never qualifies. Restart uses clean npm ci from
committed manifests/locks; dependency download failures may retry unchanged
input. No service restarts, production migration, merge, or deployment is part
of this preparation. Main-only activation/recovery remains governed by Release
Control; the installed production graph stays unchanged until authorized merge.

Validate clean installs, exact React peers, both audits, generated types, full
Web tests/build, Broadcast tests/dry-run, actual React render and native sharp
execution, and remote checks on the final head. Verify branch Preview desktop
and phone navigation if the branch deployment is available. Inspect the final
diff for incidental graph changes and preserve unrelated code/data. Close
superseded PRs only after their complete changes are represented and validated.

Group the React runtime and type packages in Dependabot to prevent recurrence.
No compatibility shim, additional runtime state, or test timeout is required.

## Validation evidence

Clean Web install and full build/test passed: 396 tests passed, 6 skipped,
zero failures. Actual server rendering loaded React/DOM/RSC 19.3.0 together.
Web and Broadcast audits report zero vulnerabilities. Broadcast's unchanged
final graph already passed 10 tests, its Worker dry-run, and native sharp image
processing in this PR. The CI contract family passed 12 tests. Generated type
changes consist of the workerd version marker and its new Container/Tracing
members; configured bindings and compatibility date remain unchanged.

The current deployment runbook disables non-production builds and records the
old Preview Worker as deleted. Deployed branch browser checks are therefore
unavailable; no production or hosting setting was changed to bypass that limit.
Final remote checks must be read against the PR head, separately from these
local results. Existing peer/render and generated-type checks reproduced the
original dependency failures and passed with the repaired set; no redundant
case-specific test was added.
