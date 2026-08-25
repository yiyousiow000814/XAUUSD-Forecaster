# Architecture Evidence Runbook

## Regenerate and verify

```text
python scripts/compile_architecture.py --root .
python scripts/compile_architecture.py --root . --check
python scripts/verify_architecture_evidence.py --root .
python scripts/collect_architecture_test_evidence.py --root . --run
python scripts/run_architecture_mutations.py --root . --profile smoke
python scripts/run_architecture_mutations.py --root . --profile full
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

The bounded collector executes only tests explicitly named in
`architecture/contracts/test_bindings.toml`. Regenerate afterward so execution
and runtime evidence use the same digest. An old receipt becomes `STALE`; never
edit its digest to make it current.

Mutation execution first proves the focused baseline, then uses one detached
temporary Git worktree per exact symbol mutation. `KILLED` is valid only when
the changed source remains syntactically valid and the designated failure
signature is observed. `SURVIVED`, `INVALID`, `TIMEOUT`, and `ERROR` stay
distinct. The runner removes every temporary worktree and proves the original
checkout status is byte-identical. Investigate a surviving CRITICAL mutant by
checking the selector, the stated contract, and the focused test assertion;
never hide it by deleting the mutation or weakening strict verification.

