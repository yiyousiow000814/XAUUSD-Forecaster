# Current health authority correction

The local main runtime owns main-runtime-status.json; dashboard API reads it and
publishes the existing runtime_update_failure field. Retired blue/green state
is immutable audit only. No file is removed or rewritten by this change.

Impact: main runtime status -> local API -> status sync -> public dashboard and
operational alerts. Existing field shape remains compatible; current failure
labels replace obsolete rollback claims. Running clears an earlier failure;
failed/update_failed remain visible. Missing status does not invent an update
failure; independent process, quote and deployment detectors remain active.
Malformed present state remains visible as unavailable. No activation authority,
scheduler, secret, raw data or recovery procedure changes.

Sync alerts retain unresolved failures, attach the latest resource observation,
and say recovery is unconfirmed. They never claim an old result is this cycle.
A newer successful observation supersedes a stale degraded entry; unknown or
failed observations cannot clear it. Producer retains normal retry ownership.

Tests execute real temporary status files through the production Python API,
including retired state, current failure and successful recovery. Existing
operational and web contracts cover presentation and retained failures. Validate
current-head CI, then observe the normal main update and public status. Failure
before deployment leaves the active runtime untouched; subsequent correction
uses normal main publication. No new rollback mechanism or durable state.
