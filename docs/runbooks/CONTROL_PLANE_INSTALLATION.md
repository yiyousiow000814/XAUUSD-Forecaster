# Main Runtime Installation

Production source lives in `C:/Users/yiyou/xauusd-news-runtime`; configuration
continues to live in `C:/Users/yiyou/xauusd-news`. PR worktrees are development
only. The sole entrypoint is `scripts/run_main_services.ps1`.

Run Install once from the accepted main checkout with explicit RuntimeRoot and
RepositoryRoot. It registers xauusd-news-main for the current interactive
user. Windows Task Scheduler launches hidden wscript at login and checks once a
minute; IgnoreNew and the runtime mutex admit only one owner. Install does not
start or stop business processes. Start persists running intent; Stop persists
stopped intent; StatusJson reads the actual owner heartbeat and source identity.

The main owner fetches every five minutes, resolves one main SHA, refuses dirty
source, stops its owned service set, checks out that SHA and installs changed
Python dependencies. Its launcher reloads the new source. Failed updates remain
visible and retry forward. No retained code slot or rollback is selected.

For initial takeover, disable and export the old exact Autostart and Guard tasks,
verify the new source and registry, and update the runtime only in the authorized
maintenance scope. Preserve current data and configuration. Install and start the
new owner, verify local/public identity, business health and strict ACK, then
unregister the old tasks and delete their installed scripts/shortcuts. Preserve
historical logs/receipts separately. Never delete the authoritative forward root.

When the backup directory is carried from the retired
`XAUUSD-Forecaster-runtime` root into `xauusd-news-runtime`, the signed
stale-temporary-reclaim receipt retains its original absolute root. The
maintenance owner accepts only this known same-home relocation, after checking
the receipt and confirming the retired root is absent. It preserves the old
receipt unchanged and writes a separate current-root reclaim state linked by
digest. An unknown root, surviving old root, changed receipt, or old-root plan
fails closed. Retention resumes from the current-root state; no historical
snapshot or receipt is rewritten to make relocation pass.
