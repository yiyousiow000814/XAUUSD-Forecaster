# Repository Working Rules

## Scope and completion

Find the existing authority and reuse its abstractions. Keep changes scoped to
what the user requested, including necessary verification and repair. Remove
obsolete code involved in the change rather than adding parallel logic.

Continue through implementation, affected checks, final review, and repair of
change-caused failures within the authorized scope. Do not stop at a first
implementation. Routine reversible local work does not need repeated approval.
Report missing information, external blockers, remaining checks, and operations
outside the user's authorization explicitly. Opening a PR does not authorize
merging it or activating production.

## Read according to the changed boundary

Tiny local behavior-preserving edits do not require an architecture review.
For material boundary changes, use the repository
[change-safety skill](.agents/skills/change-safety/SKILL.md) and complete the
[Change Safety Protocol](docs/protocols/CHANGE_SAFETY.md). That protocol owns the
change record, evidence classification, runtime composition rehearsal,
verification latency, small-blocker autonomy, and final adversarial review.
Read other documents when their boundary applies:

- Non-trivial stateful, asynchronous, supervisory, persistent, or lifecycle changes:
  [Safety Composition](docs/contracts/SAFETY_COMPOSITION.md), including the
  pre-implementation actor/state record and safety plus liveness review.
- Cross-process, service, storage, API, or synchronization data flows:
  [Hosting Boundaries](docs/contracts/HOSTING_BOUNDARIES.md), including complete
  baseline/delta semantics, producer/consumer compatibility, single runtime
  ownership, independently bounded work/transport, and failure isolation.
- Deployment and recovery: the release contract and runbook linked below.
- User-facing web and Preview changes: the applicable sections below.

## Failure resolution

Restore the last-known-safe service through the supported recovery path while
preserving forensic evidence before permanent redesign; follow the recovery
requirements in [Safety Composition](docs/contracts/SAFETY_COMPOSITION.md).
Fail-closed behavior, diagnostics, and audit evidence do not substitute for a
corrective path that produces the intended valid result. Retry the deficient
stage using its rejection reason and retain accepted work. Verify recovery with
a representative end-to-end fixture and, when safe and available, a real-provider
or production-shaped rehearsal; report external availability limits separately.

## Testing and final review

- Every bug fix leaves durable coverage of the violated behavior or invariant.
  Inspect sibling implementations and extend an existing family contract where
  possible. Keep separate cases only for distinct failure modes.
- Assert observable behavior, persisted state, public contracts, and safety
  boundaries. Do not pin incidental names, source layout, or representations;
  widespread test rewrites during a behavior-preserving refactor warrant review.
- Consolidate redundant tests only after proving coverage elsewhere; remove
  obsolete tests only after establishing that the requirement no longer applies.
  Preserve point-in-time correctness, causality, append-only evidence, immutable
  history, execution semantics, credential secrecy, fail-closed behavior, and
  production/Preview isolation.
- Keep contract tests easy to locate. Split unrelated responsibilities and share
  material fixture duplication without hiding business meaning in a framework.
- Choose checks for the affected contracts and run all required gates for that
  scope. Fix failures, rerun affected checks, and validate the final revision.
  Repeat or broaden passed checks only for a new change, failure, unresolved
  concern, or explicit gate/freshness requirement.
- Non-trivial changes require independent inspection of the final diff and its
  actual callers, consumers, lifecycle, and tests. For material boundary changes,
  execute the protocol's final adversarial review. Plans and green tests alone
  do not prove integration; cite evidence from the final revision.

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
- Protected main is the only production source. Native Cloudflare Workers
  Builds builds and directly deploys main at 100 percent traffic. Local runtime
  uses `scripts/run_main_services.ps1` to update its single checkout from main.
  PR branches must never activate production. No Control Panel, blue-green
  coordination, retained code slots or automatic rollback is required.
  Preserve authoritative data, authentication, source-first and strict ACK.
  Follow `docs/contracts/RELEASE_CONTROL.md`.

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
