# Live OOS retirement specification

Live OOS learning curves, all six forecast models, training, prediction,
scoring, and decision/outcome history are retired. The collector no longer
starts a training owner or produces or settles forecast decisions. The Web
and local API no longer serve learning, learning-history, chart, or
audit-decisions resources.

Current usable news events remain a retained product: collection, annotation,
source filtering, event identity, evidence, daily briefs and storylines retain
their existing behavior. Market collection and candle history also remain.
Neither retained service requires forecast models or trained artifacts.

Physical deletion is an explicit operational step after producer retirement;
see [the cleanup runbook](../runbooks/LIVE_OOS_CLEANUP.md). Existing schemas can
remain empty to support shared database initialization. They do not authorize
reintroducing model writers. Historical research documents describe the former
system and do not require the retired runtime to remain executable.
