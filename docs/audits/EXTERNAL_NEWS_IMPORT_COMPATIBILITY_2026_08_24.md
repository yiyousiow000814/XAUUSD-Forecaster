# External News Import Compatibility Audit — 2026-08-24

## Scope

The read-only audit searched tracked Python, notebook, PowerShell, and shell
files for exact legacy namespace imports:

```python
from xauusd_forecaster.news import ...
import xauusd_forecaster.news
```

It covered this repository, every checkout reported by `git worktree list`,
and the bounded adjacent repositories `automated-trading`,
`XAUUSD-Calendar-Agent`, and `xauusd-calendar-automation`. Git metadata,
virtual environments, `node_modules`, caches, generated output, `.local`,
secrets, and historical datasets were excluded.

## Result

| Location | Exact import | Classification | Required migration |
|---|---|---|---|
| Current repaired stack | None | Active repository | None; active source and tests use concrete News owner packages. |
| Other Forecaster Git worktrees | `from xauusd_forecaster.news import ...` in one or more `tests/` modules per legacy checkout | Test-only, branch-local historical state | Rebase each still-open branch onto the modularization campaign before merge; do not use the namespace facade as a mutable owner. |
| `C:/Users/yiyou/automated-trading/src/XAUUSD-Forecaster/tests/test_forward_only.py` | `from xauusd_forecaster.news import (...)` | Test-only retained embedded Forecaster snapshot | No change in this campaign. The standalone Forecaster repository is authoritative; migrate only if that retained snapshot is independently revived. |
| `C:/Users/yiyou/automated-trading/src/XAUUSD-Forecaster/tests/test_news_revision_quality.py` | `from xauusd_forecaster.news import collect_direct_full_text_html_news` | Test-only retained embedded Forecaster snapshot | Same as above. |
| `XAUUSD-Calendar-Agent` | None | Adjacent active repository | None. |
| `xauusd-calendar-automation` | None | Adjacent active repository | None. |

No accessible active runtime caller uses the legacy namespace import, so no
compatibility facade or merge blocker is required. Unknown inaccessible
external consumers cannot be proven absent. This audit did not modify any
external checkout.
