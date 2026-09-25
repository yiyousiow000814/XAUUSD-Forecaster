# Forecast model pause change record

The requested boundary is the local collector's model training and prediction
work. The authoritative operator state is the ignored
`.local/forward/forecast-model-activity.json` file. The local main runtime owner
remains the sole service supervisor. Cloudflare remains a reader of previously
published evidence and is not given training authority.

The collector reads the pause before opening its ledger. A valid pause bypasses
startup model reconciliation, the background training owner, periodic training
requests, and new decision grids. News collection, outcome settlement, quote
archiving, backups, and WAL maintenance continue in the same collector process.
The supervisor's normal main update stops the prior collector and starts the new
revision; the pause file is retained outside Git. On machine restart, the same
file is read before model work. A malformed control fails startup closed and is
visible as a service failure rather than silently resuming models.

The pause does not delete or alter historical facts or active artifacts. The
last already recorded decisions may still receive their due outcomes. Pending
training requests remain durable but unclaimed. No new model request or
prediction is admitted after the paused collector starts. A later resumption
needs a separately reviewed cursor transition so paused grids are never
backfilled as live predictions.

Verification uses a collector lifecycle fixture that starts the news and
maintenance owners while proving model startup, training request, and prediction
paths cannot run. Activation requires a main revision, followed by checking the
runtime source revision, collector restart, unchanged service ownership, and
stable counts of new predictions and model generations after a live grid.
