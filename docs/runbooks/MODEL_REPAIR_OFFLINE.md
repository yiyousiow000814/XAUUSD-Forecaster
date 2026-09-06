# Offline model repair reproduction

This is research-only. It does not initialize a ledger, call a provider, activate
a generation or trade. All output directories must be new and outside the source
and input trees. No candidate in the initial retrospective run qualified.

CLI filesystem authority is limited to the immutable research checkout,
`~/Documents/Codex`, and the OS temporary directory. Paths are normalized and
real-link-resolved before containment checks; CLI arguments cannot expand that
allowlist. Do not run elevated or concurrently replace its directories. This
is a local-user research utility, not a sandbox against malicious same-user
filesystem races. Reparse/symlink escapes are rejected. Production runtime
directories outside these roots are never accepted as output destinations.
The canonical directory-prefix check includes the separator (so a similarly
named sibling does not pass), following the
[CodeQL path-validation guidance](https://codeql.github.com/codeql-query-help/python/py-path-injection/).
No alert suppression or custom sanitizer model is used.

Use the preserved online-backup identity in `facts.json`, `followup_input.json`
and exact copied historical artifacts, not a live database or latest model.
The extractor checks the backup's recorded file/WAL identity and requires an
empty WAL before opening `mode=ro&immutable=1`. The recorded historical backup
hash is provenance, not a newly recomputed 7 GB hash. A changed or nonempty-WAL
input requires a separately verified online snapshot; do not copy only its main
file. The frozen artifact inventory binds the original exact path, canonical
hash and copied bytes; unresolved paths never fall back to production.

From a clean research checkout (PowerShell; replace input locations with their
preserved local paths):

```powershell
$evidence = 'C:\Users\yiyou\Documents\Codex\2026-09-06\model-loss-triage'
$replay = "$evidence\experiment\replay"
$panel = "$evidence\experiment\exact-source-panel"
$result = "$evidence\experiment\exact-source-results"
$snapshot = 'C:\Users\yiyou\Documents\Codex\2026-09-05\collector-atomicity\production-online.sqlite3'
$env:OPENBLAS_NUM_THREADS = '1'
$env:OMP_NUM_THREADS = '1'
python scripts/build_model_repair_panel.py --input-dir $evidence --snapshot $snapshot --input-sha256 1277e34220d09428829db00435510e7b06013562ebc7fe5fa75d23bd98485dc8 --artifact-root "$replay\artifacts" --artifact-inventory "$replay\artifact_inventory.json" --source-root . --plan docs/plans/MODEL_REPAIR_OFFLINE.json --selected-inputs "$replay\selected_inputs.json" --selected-sha256 3a25c5ea7892f2ae2e1fdd782506b39c918c4a4a37c00d966020d27b6d16e46b --output-dir $panel
python scripts/run_model_repair_offline.py --panel "$panel\panel.json" --plan docs/plans/MODEL_REPAIR_OFFLINE.json --output $result
python scripts/report_model_repair_offline.py --results-dir $result
```

Omit both selected-input options only when that small selected-column cache is
unavailable. This executes four bounded queries against the frozen copy, not a
full-file hash or production payload request. An existing nonempty output is
rejected rather than overwritten. Store the commands and `execution.json` with
the results; never relabel an old report as a new source run.

The plan fixes four consecutive retrospective test blocks, inner maturity-based
validation, three affine penalties, three cost margins, and at most six optional
market Ridge controls (alpha 10/100/1000, expanding/14-day window). The pure
Ridge owner uses unit sample weights and summed loss. No News model is retrained,
so no full-sample residual target is introduced. There is no search expansion.

Primary simulation applies one open position across folds and generations,
actual retained entry/exit times and common input availability. The latter is a
conservative persistence bound, not evidence of the exact inference completion
instant. Most retained entry prices precede it. Clock-time counterfactual
results are reported separately and cannot prove executability. Session and News
event-time provenance that were not retained remain UNKNOWN. Missing exposure
is never assigned a zero return. No SL/TP, capital sizing or dollar P&L is inferred.

`results.json` contains all parameter trials, day/version/generation/fold and
composition contributions, ranking/error measures and cost stress. Each strategy
has per-opportunity records and a separate counterfactual ledger. `artifacts/`
contains every fitted fold artifact, including rejected experiments. These are
not production-compatible activation instructions. `future_validation.json`
explicitly remains NOT_RUN when no untouched data or acceptable candidate exists.

Tests:

```powershell
python -m pytest tests/test_offline_model_repair.py tests/test_model_repair_replay.py tests/test_execution_costs.py -q
```

No production runtime behavior changes. Recovery work and original PR-intent
closure remain independent; this research result cannot satisfy their gates.
