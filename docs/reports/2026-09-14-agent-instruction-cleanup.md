# Agent Instruction Cleanup Report

Date: 2026-09-14. Baseline: `62100753`.

## Repository changes

The root instructions now route material changes to existing authorities rather
than reproducing their full procedures. AGENTS.md decreased from 2,963 to about
1,360 whitespace-delimited words. The change-safety skill is a short protocol
entrypoint. No runtime, deployment configuration, or test behavior changed.

| Previous instruction family | Retained authority |
| --- | --- |
| Actor/state record, safety/liveness, safe recovery | Safety Composition contract; explicit actor enumeration retained there |
| External assurance, evidence reuse, latency, small-blocker autonomy, real runtime rehearsal | Change Safety Protocol |
| Final production-path adversarial review and boundary acceptance | Change Safety Protocol, required by AGENTS.md |
| Baseline/delta semantics, ownership, growth, failure isolation | Hosting Boundaries contract |
| Regression families, test consolidation, critical coverage | AGENTS.md testing section |
| Web/mobile, browser cleanup, documentation, deployment, Preview, generation handover | Existing AGENTS.md sections retained |

Completion now explicitly includes implementation, affected verification,
review, and repair within authorization. Passed checks are repeated only when
new evidence or an explicit gate requires them. Opening a PR does not authorize
merge or production activation. The baseline's main-based deployment rule is
preserved; earlier conversational advice about mandatory Promote was stale.

## Local skill changes outside this PR's tracked files

The user's global skill directory was updated in the same task. These changes
are local configuration, not delivered to other contributors by this PR:

- Removed `karpathy-guidelines`; its generic coding guidance was redundant.
- Shortened discovery descriptions for codex-deepwiki, commit-push-pr-workflow,
  figma, frontend-design, gh-address-comments, github-pr-edit, hatch-pet,
  playwright, playwright-interactive, release-notes-format,
  rmg-strategy-port-parity, screenshot, and ui-check-framework.
- Simplified the three Git/PR workflows: repository-owned language and checks,
  isolated worktrees for unrelated work, existing authorization and permission
  profiles, and separate creation/editing/review responsibilities.
- Moved the optional PR REST fallback into a linked reference and corrected the
  PR editing helper's relative path. No helper scripts were changed.
- Plugin caches and system skills were not edited. Original copies of the 13
  modified local skills are outside discovery in the local skill-backups folder.

## Validation and limits

Repository policy and architecture documentation checks passed. The existing
repository policy and architecture documentation tests passed (16 tests).
All 13 modified local skills and the repository skill passed quick_validate;
Windows validation of UTF-8 documents uses Python's `-X utf8` option.
Karpathy has no remaining references in repository guidance or local skills.

Manual routing review (instruction inspection, not a model performance eval):

| Request | Result under revised instructions |
| --- | --- |
| Fix a README typo | Scoped edit and relevant documentation checks; no material architecture workflow |
| Repair a delta that erases richer state | Change Safety plus Hosting Boundaries, real producer/consumer coverage, final review |
| Recover a degraded service | Preserve evidence, use supported recovery authority, then permanent correction and rehearsal |
| Open a PR while unrelated files are dirty | Isolated branch/worktree; push is included, merge is not |
| Address all requested review findings | Process applicable findings without asking for a second selection; messages still require authorization |

The final review checked removed requirements against the authority map above,
retained all six later AGENTS.md policy sections, and verified local skill links.
No production actions or automated browser sessions were needed. Runtime speed
or quality improvements require observing subsequent real tasks; no measured
model-performance gain is claimed.

## Follow-up: workflow bodies and forward testing

The second pass examined local skill entrypoints and their workflow boundaries,
not only descriptions. Changes remain local configuration outside this PR:

| Skill family | Correction |
| --- | --- |
| hatch-pet | 923-line entrypoint becomes a 40-line stage router; generation, alpha/geometry, identity, direction, blind review, and packaging requirements remain in linked references |
| playwright-interactive | 693-line entrypoint becomes a surface/stage router; removes automatic sandbox disabling, config edits, and package initialization; cleanup applies on failure/interruption too |
| screenshot | Platform-specific capture instructions load only for the selected OS |
| rmg-strategy-port-parity | Historical Current031 paths/results and contradictory fill examples are explicitly historical; current source and actual execution govern reuse |
| playwright | Existing tool/runtime first; no forced global install or CLI-only test policy; Windows launcher and cleanup requirements explicit |
| frontend-design | Preserve existing product design; remove universal font bans and forced dramatic styling |
| codex-deepwiki | Source and official documentation govern current behavior; DeepWiki is secondary explanation |
| figma | Implementation flow no longer applies to metadata/setup-only questions; tool prerequisites remain required |
| ui-check-framework | Preserve project selectors/layout; do not impose qa: tags, global hooks, or a new framework on unrelated work |
| release-notes-format | Template is a default subordinate to user/repository requirements; drafting is distinct from publishing |
| Git/PR workflows | Previous pass's creation/edit/review separation retained after body review |

Existing helper scripts were not modified. Detailed technical procedures were
preserved in references rather than rewritten as shorter unverified commands.
Global originals are backed up outside discovery under the phase2 skill-backups
folder. A local diff artifact records the changes for review.

The repository protocol now keeps common change/review requirements in its core
and routes runtime composition and provider/verification evidence to separate
procedures. The release contract and actual main service entrypoint were checked:
current recovery is forward correction, so generic safety guidance must not
require the retired Stable/Candidate controller. Historical hosting projection
requirements remain intact and explicitly separate from deployment mechanics.

An independent agent executed two bounded fixture tasks: a README typo fix and
a dictionary delta repair. The delta test failed before repair and passed after
repair (one unittest with seven contract subcases: preservation, replacement,
addition, explicit deletion, missing deletion, empty delta, and input immutability).
The fixture had no external provider, persisted storage, or production resource;
this is instruction-routing and local behavior evidence, not a production rehearsal.

That run exposed overbroad material-state routing and possible typo-test
interpretation. The final rules distinguish pure-function repair with no changed
external/persistent/lifecycle contract from material boundary work, and distinguish
behavioral regressions from text-only corrections. No safety gate for actual
cross-boundary changes was removed.
