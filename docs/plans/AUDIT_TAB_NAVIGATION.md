# Audit tab navigation repair

## Change record

The browser DashboardApp remains the single URL/history and room-load owner.
Audit subviews already share one mounted AuditView; switching between them must
commit synchronously without hiding that owner or loading the same room again.
Cross-room imports retain their pending skeleton and retry path. Every intent,
including same-room and popstate intents, invalidates earlier async completions.
The global operational banner and its polling component are removed; health
routes, status data, authentication and operator evidence remain unchanged.

Actors are link/picker clicks, popstate, lazy imports and React commit/effect
cleanup. State is browser-only: selected location, sequence, pending import,
error and scroll target. There is no schema, API, storage or runtime-service
change; deployed assets and existing data remain compatible. On refresh the
URL restores the selected view. An import rejection restores the previous view
with retry; superseded success/failure has no authority. Returning to a loaded
room completes immediately even while an older import is unresolved.

Pre-mortem: a same-room fast path could fail to invalidate a pending import,
leave the page hidden, or lose history/filter state. Test actual DashboardApp
handlers with deferred imports, success/rejection and browser history events.
Preview rehearsal covers six tabs, history, filters, expansion and room return
at desktop, 390x844 and 360x800, including overflow and target geometry.

Baseline is main b33d0549d7088632e6a51b6c48ad8ed00bbcb577. Normal reviewed main
publication remains the sole activation path; repair forward through the same
pipeline if needed, without changing data or service ownership. No bridges,
new timers or cleanup workers are introduced. Final review and Preview evidence
will be recorded in the pull request.
