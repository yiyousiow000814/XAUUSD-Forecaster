# Repository Working Rules

## Before Adding Code

1. Find the existing source of truth.
2. Reuse existing abstractions.
3. Avoid duplicate logic.
4. Identify obsolete code.
5. Add or update tests.

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

## Documentation Language

- Write repository documentation in English. This includes the root README,
  Markdown files under `docs/`, developer guides, and pull-request descriptions.
- Keep developer-facing explanations concise and understandable without private
  project context. Move implementation detail into the relevant contract or
  reference document instead of expanding the README.
- Chinese remains appropriate for the product UI, user-provided content, and
  immutable audit evidence; those surfaces are not repository documentation.

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
- Add or update automated coverage, and record the Preview URL and responsive
  checks in the pull request before calling the change complete.

## Browser Automation Lifecycle

- Treat every Playwright or browser-control session as a resource that must be
  explicitly closed. Persistent browser sessions must never be left running
  after inspection because they can keep polling deployed pages and consume
  production request quotas.
- Before browser automation, audit existing Playwright browser processes and
  record the baseline. Reuse one named session for the complete verification
  flow; do not open a new browser for each viewport, route, or assertion.
- Close the named session on every completion path, including failed commands,
  timeouts, interrupted checks, and task switches. A failed verification does
  not waive cleanup.
- After closing, audit Playwright browser processes again. Browser verification
  is incomplete until no process created by the task remains. Report the final
  process count in the pull request when Preview verification was performed.
- Prefer branch Previews with immutable code artifacts for UI checks. Do not
  leave a production page open in an automated browser, and never rely on
  background throttling to limit its requests.

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
