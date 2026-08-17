# Input and Provider Reference

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
The v15 generation converts EIA and BEA candidates into immutable structured
release packets and distinct Ridge features. Similar FRED and EIA WTI series
are deliberately preserved separately; OOS learning decides their value.
Changing this feature set requires a complete generation handover.
Collection alone does not silently replace the active generation.

Official and discovery source cadence is success-based. An `OK` poll starts the
source's normal interval; `ERROR` and `PARTIAL` polls instead enter durable
bounded recovery at 5, 15, and then 30 minutes. HTTP 429 uses a slower bounded
rate-limit schedule unless a valid provider `Retry-After` supplies the bounded
delay. The HTTP status and delay seconds are stored without URLs, bodies, or
credentials so restart preserves the same retry. Only explicitly credentialed
transports classify HTTP 401/403 as operator-actionable authentication failures;
public endpoints classify them as remote access rejection and recover through
freshness escalation. A `PARTIAL` poll never advances normal cadence, but its
usable evidence may be the newest health-freshness reference while recovery of
failed siblings remains active. Recovery reads only the four newest indexed poll
receipts; lifetime history remains append-only audit evidence.

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
