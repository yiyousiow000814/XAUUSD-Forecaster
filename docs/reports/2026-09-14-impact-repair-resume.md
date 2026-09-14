# Resume impact repair without repeating initial generation

The live job reached repair but repeatedly exhausted TPM after paying for initial
generation. The existing job lease and annotator remain the sole workflow owners.
Add one immutable checkpoint per annotation, source hash, model, impact prompt
and repair wire contract. It contains only the bounded request context, rejected
initial result, original model identity and rejection reason. No source body,
secret or additional queue state is stored. Capture before repair; retry and
process restart reuse that checkpoint and issue only the repair request.

The existing assessment remains the completion authority. Checkpoints never
mean success and are ignored for a changed identity. Completed jobs are already
excluded by the scheduler. No cleanup actor, lock or new service is introduced.
Append-only checkpoints survive HTTP errors, quota deferral and invalid repair;
original identity validation remains mandatory. Additive SQLite schema is
compatible with the previous runtime, which ignores the table. Main-only forward
update owns activation. Storage is bounded per identity and retains audit facts.

Verify a real gateway response followed by quota deferral, close/reopen storage,
and successful repair with zero repeated initial requests. Verify identity
mismatch, tampering, existing job completion and ordinary first-pass success.
Then verify the original production job and assessment after exact-head CI and
merge. External provider availability is separate from correct resumption.
