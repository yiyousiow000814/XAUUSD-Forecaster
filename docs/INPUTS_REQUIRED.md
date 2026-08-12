# Confirmed Phase 2F Inputs

These owner decisions configure evidence collection only. They do not grant
trading authority. Only one secret remains to enable optional LLM annotation.

## 1. Live XAUUSD Bid/Ask bridge

Confirmed: the repository-local `XauusdForwardQuoteBridge` Algo runs through
cTrader CLI and appends UTC event/receipt times and executable Bid/Ask quotes.
The bridge contains no order API. The collector fails closed if the feed is
missing, stale, crossed, malformed, or for an unexpected symbol.

## 2. LLM annotation provider

Confirmed pilot: Google Gemini API with `gemini-3.5-flash-lite` and the frozen
structured-output schema. The API key is stored outside source control in the
local user environment. The LLM process is isolated from the five-minute
decision clock, structures news only, and cannot select Long, Short, or Wait.

## 3. BLS access route

Confirmed current behavior: use the official BLS Public Data API because it is
reachable even though the BLS RSS pages return HTTP 403. Payrolls, earnings,
unemployment, headline/core CPI, and JOLTS values are stored with first-seen
time and later revisions. Without a free BLS registration key the collector
polls within the public 25-query daily limit; a free key permits five-minute
polling around the clock. Store the registration key only in the user-level
`BLS_API_KEY` environment variable; the Control Center injects it into the
collector process without writing it to source control or logs. A
`bls.gov`-restricted Google News lane supplies a
conservative release-page discovery fallback. Its later Google/local receipt
time is retained; it never backdates visibility to the publisher timestamp.

The Control Center also recognizes `BEA_API_KEY`, `FRED_API_KEY`, and
`EIA_API_KEY`. Official-data keys may
be backed up in the ignored local file
`.local/secrets/collector-keys.json`; user-level environment variables take
precedence. Secret values are never returned by the dashboard or written to
collector logs. The BEA Data API is separate from BEA news-release pages and
uses two hourly NIPA requests for quarterly real-GDP growth, the GDP price
index, and the PCE price index. The collector records these as point-in-time
Forward candidates and does not assign model permission. A versioned generation
contract decides whether and how a model consumes them.

With a registered FRED key, the hourly background collector uses the official
JSON observations API for its six configured series instead of the public graph
CSV transport. The EIA adapter makes at most one request per hour and stores the
latest two official daily WTI observations as point-in-time Forward candidates.
Collection does not silently replace the active feature source or generation.

## 4. Optional synchronized symbols

Confirmed: collect XAUUSD only. The market-provider and symbol-validation
boundaries remain extensible, but no other symbol is configured or silently
treated as available.

## 5. Operating destination and retention

Confirmed: retain evidence permanently for the pilot. Completed UTC quote days
are losslessly compressed with a checksum receipt, while SQLite uses its online
backup API and passes an integrity check. Both remain under the ignored local
Forward tree for now. This protects against file corruption or an accidental
working-file change, but same-disk backup does not protect against disk loss.
VPS, Web UI, API, Telegram, and cTrader display integration remain later
surfaces. Champion promotion is manual and owned by the user.
