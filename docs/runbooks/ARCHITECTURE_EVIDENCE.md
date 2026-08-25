# Architecture Evidence Runbook

## Regenerate and verify

```text
python scripts/compile_architecture.py --root .
python scripts/compile_architecture.py --root . --check
python scripts/verify_architecture_evidence.py --root .
python scripts/check_architecture_imports.py --root .
python scripts/architecture_diff.py --root . --base <parent-ref>
```

Run the compiler twice after changing an extractor and confirm the second run
is byte-clean. Review observed imports separately from allowed policy. To add a
semantic owner, add the smallest declaration, bind it to exact source facts,
regenerate, and update the authoritative architecture document in the same PR.

If a selector is stale, repair the declaration or the owning source boundary;
do not weaken cardinality. If an import or writer is unexpected, identify its
real owner before updating policy. Reverting the compiler/declaration commit
restores the prior static artifacts without runtime data migration.

