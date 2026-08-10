# Repository Working Rules

## Before Adding Code

1. Find the existing source of truth.
2. Reuse existing abstractions.
3. Avoid duplicate logic.
4. Identify obsolete code.
5. Add or update tests.

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
