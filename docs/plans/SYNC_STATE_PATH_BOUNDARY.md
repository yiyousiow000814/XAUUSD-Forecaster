# Sync state write boundary

The existing Sync writer receives an explicit runtime authority from its
configuration or the bootstrap CLI's production authority. A destination cannot
authorize its own parent. Before temporary-file creation and every atomic
replacement, validate the normalized direct-child JSON path and reject existing
directory symlinks, junctions, or other reparse points in the authority chain.
Terminal file links retain replace-entry semantics; outside linked content is
never opened for writing. No new state owner or receipt is introduced.

The runtime tree is private to its existing Windows owner. These checks detect
pre-existing redirection and changes visible at each operation boundary; they
do not claim protection from a concurrently malicious equally privileged actor
able to rename directories between a check and an OS call. Such an actor is
outside the private-directory permission assumption. Permission denial fails
closed; the existing bounded Windows sharing retry does not replay HTTP work.

Coverage includes normal and normalized paths, escape rejection, terminal file
links, real directory junctions, redirection after configuration and at replace
retry, denied access, retained prior state, and owned temporary-file cleanup.
Tests use explicit isolated authorities, not production paths. Production
preflight remains separate evidence; no production mutation is part of this fix.
