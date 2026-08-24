# Repository Working Rules

## Architecture Change Gate

Before changing runtime, data flow, state, API, storage, a scheduler, a
background owner, or deployment, read
`docs/design/SYSTEM_ARCHITECTURE.md`, `docs/reference/CODEBASE_MAP.md`, and
`docs/contracts/ARCHITECTURE_RULES.md`, then record:

```text
Owner:
Authoritative state/store:
Execution boundary:
Critical or optional:
Maximum work per operation:
Incremental cursor/revision/checkpoint:
Failure domain:
Last-good/recovery behavior:
Architecture documents affected:
```

- Update the maps when adding a process, thread, Worker, Durable Object, store,
  API resource, or long-running cycle.
- Never add growing-history work to a critical or request path.
- Keep missing, not loaded, empty, stale, and unavailable distinct.
- Repair state only through its owner. Do not hide an architecture violation by
  raising a timeout, limit, threshold, or quota.
- Move code only in a separate behavior-preserving PR. One PR may change only
  one architecture boundary.
- Every abstraction must name the real dependency or boundary it hides.
- After changing a subsystem boundary, update its detailed map and the Codebase
  Map. See the architecture rules contract for evidence requirements.
- Any architecture, owner, boundary, dependency, or canonical path change must
  update `architecture/manifest.json` and the relevant architecture document in
  the same PR. Validate it with `scripts/check_architecture_manifest.py`.

## Before Adding Code

1. Find the existing source of truth.
2. Reuse existing abstractions.
3. Avoid duplicate logic.
4. Identify obsolete code.
5. Add or update tests.

Before changing a cross-boundary data flow, follow
`docs/contracts/HOSTING_BOUNDARIES.md`: identify the authoritative owner,
classify the path as critical or optional, classify accumulated growth, keep
critical work and transport bounded independently, and isolate optional failure
domains. Distinguish display limits from serialized transport bounds. Prefer
repairing ownership or transport architecture over raising limits or deleting
authoritative data.

## Problem Resolution Standard

- Treat fail-closed behavior, error visibility, and audit evidence as safety
  requirements, not as substitutes for fixing the failed workflow.
- When the user asks to resolve a failure, completion requires a corrective path
  that can produce the intended valid result and evidence that the path succeeds.
  Do not redefine success as displaying, isolating, suppressing, or permanently
  stopping at the failure state unless the underlying input is genuinely
  impossible or the user explicitly requests that behavior.
- A retry must use the prior rejection reason and preserve already-valid work.
  Do not blindly repeat the same request or recompute an accepted stage when a
  narrower failed stage can be repaired independently.
- Verify recovery with a representative end-to-end fixture and, when safe and
  available, one production-shaped or real-provider rehearsal. Report remaining
  external availability limits separately from correctness.

## Testing Discipline

- Tests must protect durable behavior, system contracts, and invariants. Do not add a test merely because code changed.
- Every bug fix must leave durable regression coverage, but this does not require a new standalone test for every bug.
- Before adding a new regression test:
  1. Identify the invariant or contract that the bug violated.
  2. Identify sibling implementations governed by the same rule.
  3. Search for an existing test or contract that already represents that rule.
  4. Prefer extending or parameterizing the existing contract over adding another case-specific test.
- A bug found in implementation B must trigger a review of equivalent implementations A, C, and other siblings. Do not protect only the instance that happened to fail when the underlying rule applies to a family.
- Prefer family-level contract coverage for shared behavior such as collectors, model generations, transport payloads, evidence stores, API routes, schedulers, and runtime workers.
- Keep a specific regression test only when it represents a distinct failure mode that is not clearly covered by a broader contract.
- Once a broader contract fully subsumes an older regression test, consolidate or remove the redundant test.
- Tests should assert externally meaningful behavior, persisted state, public contracts, safety properties, or required architecture boundaries. Avoid pinning incidental implementation details, private function names, exact source layout, dynamic copy, or temporary representations unless those details are themselves an explicit contract.
- A refactor that preserves behavior should not require widespread test rewrites. If many tests fail only because implementation structure changed, review whether those tests are coupled to implementation rather than behavior.
- Do not preserve stale tests for historical reasons. Update, consolidate, or remove tests whose original requirement no longer exists.
- Do not optimize for test count. More tests are not automatically safer, and fewer tests are not automatically cleaner. Optimize for meaningful coverage with minimal duplication.
- Shared test setup, builders, factories, fixtures, and assertions should be extracted when repetition becomes material, but do not hide the business meaning of a test behind a generic test framework.
- Split oversized test modules by responsibility when a file spans unrelated domains or no longer has one clear contract.
- Contract and invariant tests should remain explicit and easy to locate. Test organization should make it obvious which system rule is being protected.
- When changing a test suite, preserve critical coverage for point-in-time correctness, causality, append-only evidence, immutable historical records, execution semantics, credential secrecy, fail-closed behavior, and production/Preview isolation.
- Before deleting or consolidating a test, prove that its behavior is covered elsewhere or that the underlying requirement is obsolete.
- A change is not complete merely because the full suite passes. Review whether the new or modified tests cover the correct abstraction level and whether equivalent sibling paths remain untested.

## Cross-Boundary Change Discipline

- Before implementing a state machine, classify every non-success state as
  transient/pending, externally retryable, operator-reviewable, or terminal
  deterministic failure. An externally mutable condition for the same
  immutable identity must not become terminal merely because it is currently
  unavailable. A retryable state remains fail closed and non-promotable; test
  both rejection and recovery on the same identity. A terminal state requires
  an explicit reason why the same immutable input cannot legitimately succeed.
- A change to an API payload, serialized object, persisted snapshot, queue or
  event, WebSocket frame, sync payload, or database-derived projection requires
  review of the producer and every meaningful consumer. Verify names, shapes,
  units, optionality, ordering, and user-visible semantics. Producer validation
  or serialization coverage alone is insufficient; changed semantics require
  consumer-level behavioral coverage.
- For partial, compact, delta, or incremental transport, follow
  `docs/contracts/HOSTING_BOUNDARIES.md`. Identify the authoritative complete
  baseline, field ownership, merge and deletion rules, stale and sequence
  semantics, and reconnect/resync behavior. Prove that applying a delta
  preserves unrelated baseline fields and that a complete required state can
  be rebuilt.
- Every recurring responsibility has exactly one explicit production runtime
  owner. Identify who starts and supervises it, cadence, disabled or
  not-configured behavior, activation boundary, durable state, process and
  machine restart recovery, retry and failure isolation, rollback, and
  shutdown. A library, CLI, endpoint, scheduler definition, Worker, Durable
  Object, or fixture does not prove that recurring production work is owned.
- Wiring-sensitive integration tests must exercise the actual production route,
  entry point, configuration name, coordination key, serializer-to-consumer
  path, and service registry. When a helper is tested, also prove that its
  production caller supplies every semantics-controlling value. A test-only
  identifier may differ from production only when the difference is intentional
  and independently covered.

## Pre-Completion Adversarial Review

A non-trivial change is not complete merely because focused tests, the full
suite, lint, builds, or CI pass. After implementation is substantially finished,
independently review the final exact head as if another engineer wrote it.

For every changed cross-boundary workflow, trace the real production path:

`producer -> state generation/storage -> transport -> routing -> production entry point -> consumer -> externally observable behavior`

Use actual production call sites, configuration and route names, coordination
keys, schemas, ownership, and deployed entry points. A helper-level test does
not prove integration when production wiring can supply different values or
take another path. The review must establish:

- what invokes the workflow in production and who consumes every changed field;
- behavior at first start with no state, genuine empty/zero and partial state,
  stale state, process restart, machine restart, reconnect, dependency failure,
  and later recovery;
- compatibility with old Stable, activation ordering, Promote, Reverse Stable,
  and rollback where those boundaries apply;
- what grows, what bounds each operation and transport, whether optional
  failure is isolated, and whether every recurring responsibility has one owner;
- whether compact state can erase richer authority, mutable external state can
  become accidentally terminal, and the production entry point matches the
  tested assumptions; and
- whether the result works from observable contracts without relying on the
  implementation author's intended design.

If this review finds a defect, fix it, rerun affected focused tests, repeat the
review on the new exact head, and then run final exact-head validation. Before
reporting completion, inspect the final diff, production callers, consumers,
state transitions, lifecycle and restart paths, and tests independently. The
implementation plan, earlier reasoning, and green tests are not evidence of the
final implementation by themselves; completion reports must cite evidence from
the final exact head.

When human or code review finds a defect after implementation was considered
complete, ask which reusable review rule or authoritative contract failed to
catch that class. Update the appropriate rule or contract when the invariant
generalizes beyond the incident; do not create permanent rules for one-off
typos.

## Documentation Language

- Write repository documentation in English. This includes the root README,
  Markdown files under `docs/`, developer guides, and pull-request descriptions.
- Keep developer-facing explanations concise and understandable without private
  project context. Move implementation detail into the relevant contract or
  reference document instead of expanding the README.
- Chinese remains appropriate for the product UI, user-provided content, and
  immutable audit evidence; those surfaces are not repository documentation.

## Documentation Taxonomy

- Classify a document by purpose before creating it. Reserve CONTRACT for
  non-negotiable invariants and boundaries.
- Use SPEC, PROTOCOL, DESIGN, PLAN, RUNBOOK, AUDIT, REPORT, REFERENCE, or ADR
  when those meanings fit better. Follow `docs/README.md` for placement.
- Do not introduce a durable rule only in a test, code comment, pull-request
  description, or plan when an authoritative document exists.
- Split a mixed document when its sections have materially different authority
  instead of choosing an inaccurate umbrella type.

## Web And Mobile Experience

- Treat desktop and mobile as separate acceptance surfaces. A desktop pass does
  not prove the phone experience works.
- Verify every user-facing web change on the deployed branch Preview, not only
  with a local build or source inspection.
- At minimum, check desktop plus 390x844 and 360x800 phone viewports. Confirm
  there is no unintended horizontal overflow, clipped content, blocked result,
  or unreachable control.
- Exercise the complete affected flow on a phone: open, navigate, scroll,
  select, paginate or expand, return, and close as applicable. Interactive
  targets should be at least 44x44 CSS pixels.
- For changes to grids, tables, or shared border selectors, inspect every outer
  edge and internal divider across the complete component and its sibling
  layouts, including spanning, incomplete, expanded, and collapsed rows. Follow
  `docs/specs/DASHBOARD_PRESENTATION.md`; do not validate only the edge named in
  the bug report.
- Render operator-facing timestamps as readable local date-times using the
  dashboard's fixed `Asia/Kuala_Lumpur` (UTC+8) zone. Do not expose raw ISO 8601
  strings in UI copy or diagnostic evidence; retain canonical UTC in stored
  records and APIs.
- Add or update automated coverage, and record the Preview URL and responsive
  checks in the pull request before calling the change complete.

## Browser Automation Lifecycle

- Playwright is permitted for repository verification only when its launcher
  and every child process are guaranteed not to open a visible Command Prompt,
  PowerShell, or terminal window on the user's Windows desktop.
- Prefer the in-app browser-control capability. When Playwright is required,
  use a verified hidden or no-window launch path; never invoke `playwright-cli`,
  `npx` Playwright packages, bundled wrappers, or scripts directly when that
  invocation can create a visible console window.
- Before launching browser automation, confirm that the selected launch method
  suppresses visible terminal windows. If that cannot be guaranteed, use the
  in-app browser or report browser verification as blocked instead of launching
  it visibly.
- Treat every permitted browser-control session as a resource that must be
  explicitly closed. Reuse one session for the complete verification flow and
  close it on every completion path, including failed commands, timeouts,
  interrupted checks, and task switches.
- After closing, confirm that no browser session created by the task remains.
  Report the final session count in the pull request when Preview verification
  was performed.
- Prefer branch Previews with immutable code artifacts for UI checks. Do not
  leave a production page open in an automated browser, and never rely on
  background throttling to limit its requests.

## Deployment Control Plane

- Cloudflare Workers is the repository's only deployment plane. GitHub Actions
  may validate code but must not create GitHub Deployments or Environments.
- Use an explicit read method for GitHub API inspection (`gh api --method GET`),
  especially when passing field flags that would otherwise imply a write.
- Follow `docs/contracts/HOSTING_BOUNDARIES.md` and
  `docs/runbooks/CLOUDFLARE_DEPLOYMENT.md`.
- Git push, pull-request merge, and `main` movement must never change Stable.
  Cloudflare builds upload immutable Versions only, and Windows may stage and
  test a newer revision but must not activate it from branch movement. Stable
  changes only through explicit local Control Center Promote; normal rollback
  is Reverse Stable. Follow `docs/contracts/RELEASE_CONTROL.md`.

## Preview Discipline

- Preview behavior follows `docs/specs/PREVIEW_BEHAVIOR.md`.
- Preview isolation guarantees follow `docs/contracts/PREVIEW_ISOLATION.md`.
- Do not introduce a new Preview mutability, isolation, authority, data-source,
  fallback, provenance, or freshness rule only inside a route, component, test,
  comment, or pull request.
- Update the relevant Preview source-of-truth document when such a rule changes.

## Version Handover

- A model-rule handover may keep the active and target implementations together
  only while the target generation is being built and verified.
- A generation must switch as one complete, verified set. Never mix members from
  different rule contracts.
- Once the target generation is active and verified, remove the superseded
  runtime code, compatibility branches, constants, and transition-only tests.
  Do not leave permanent `legacy` execution paths.
- Preserve immutable historical predictions, model metadata, evidence receipts,
  and schema needed to reproduce or audit past decisions. Historical records are
  audit evidence, not active compatibility code.
- A handover is not complete until tests prove the new generation is active and
  no obsolete runtime path remains.
