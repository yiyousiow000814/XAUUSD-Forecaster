# Health refresh subscription fix

The shell may win the singleton status-baseline request before HealthView mounts.
HealthView previously read the cache once and missed subsequent HTTP/push updates.
Subscribe using the existing resource API and synchronously read after subscribing.
The resource cache remains authoritative; no new requests, timers, stores or states.
Subscription cleanup follows view unmount. Initial missing/partial state shows
loading or the existing error notice, not healthy zero counts. Existing complete
state remains visible during refresh failures. API and stored data are unchanged.

Trace: HTTP baseline / live broadcast -> resource cache notification -> HealthView
state -> component/source/incident rendering. Verify shell-first loading, reload,
late mount, subsequent push and error/recovery; desktop and two mobile sizes.
Non-production automatic builds are disabled by the user's main-only deployment
choice; use a native immutable version Preview if available, not another Worker.
