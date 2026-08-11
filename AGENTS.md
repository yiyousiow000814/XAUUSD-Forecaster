# Repository Working Rules

## Before Adding Code

1. Find the existing source of truth.
2. Reuse existing abstractions.
3. Avoid duplicate logic.
4. Identify obsolete code.
5. Add or update tests.

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
- Prefer immutable branch Previews for UI checks. Do not leave a production page
  open in an automated browser, and never rely on background throttling to limit
  its requests.

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
