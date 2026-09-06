# Causal signal-time execution replay plan

## Frozen experiment and change boundary

This is execution research on PR #457's fixed `b501da8a` predictions/actions,
not another calibration, threshold search or training run. Preserve all old
outputs. The 3,646 identities in every strategy file are the opportunity
authority. Output is a new directory outside source. No runtime imports this
opt-in replay; no database, model activation, provider or broker mutation occurs.

The old label owner targets **old label entry receipt + 30 minutes**. Preserve
that target and its recorded exit, not new entry + 30 minutes. A missing old
entry/exit is not an invented target or zero return. Reconcile retained exit
prices against the old labels before using them for new entries.

## Timing and scenarios (freeze before reading new returns)

Audit writer and caller semantics before assigning any retained timestamp an
availability meaning. Batch start, feature recomputation and prediction creation
are not inherently consumer visibility. A historical post-prediction completion
bound may support a conservative scenario, but not exact inference latency or
broker order time. Absent such evidence the evidence-only replay is UNKNOWN.
Market-only dependencies exclude News; batch publication latency, if shared,
is distinct from a News input dependency. New fitted methods are hypothetical.

Report the old common maximum marker unchanged as a baseline. A separate
dependency-marker scenario assumes computation/publication completes 100 ms
after the latest required marker; it does not assert that this happened. Use
only existing dependency inputs: originals use their own prediction, Market
calibrators use Market, Broad calibrators use Broad Full, combination uses Market
and Broad residual, Ridge uses market features, past mean uses current cost/U5
market inputs and its frozen past fit. No numerical-zero coefficient pruning.

Freeze execution delays at 0, 1,000 and 5,000 ms. Zero means the first strictly
later quote, not a same-timestamp fill. Freeze cost stress at commission x1,
x1.5 and x2 with respectively 0, 0.5 and 1 log-bps assumed round-trip slippage.
Report every scenario; never select one by its return. Missing publication times
remain a separate evidence failure even if conditional scenarios make money.

## Event and quote contracts

Use archived real XAUUSD Bid/Ask and the existing `parse_quote_line` and
`MarketObservation` owners. Only bounded, closed historical daily archives are
read; copy and hash their exact bytes into research output, with before/after
source identity checks. No production credential or active quote stream is read.
All fills require finite positive non-crossed quotes, event/receipt skew <=20 s,
strict receipt order after order time, and a 20-second order expiry. Same-receipt
conflicting quotes are ambiguous and cannot fill. No interpolation.

One strategy has one pending order or position, across folds/generations. Sort
by actual hypothetical order time; reserve ownership through pending expiry or
exit. A tied order timestamp has deterministic decision-ID priority, disclosed
as an assumed policy. Release at exit only for strictly later orders (unknown
same-timestamp ordering is not optimistic). Unknown exit holds exposure to the
end, rather than silently reopening capacity. No signal after the old target.

Every opportunity has one terminal classification and retained intermediate
facts (including whether signal was later than old label entry). WAIT is not a
trade; missing evidence/quotes is NOT_EVALUABLE, never zero P&L. Retain unknown
session and News PIT status. This cannot establish live/broker alignment.

## Safety, compatibility and verification

Impact: frozen files -> read-only quote extract -> pure event replay -> separate
research ledgers/report. Production authority is unchanged. Old consumers and
outputs are untouched; new output uses its own schema. Failure before/after
partial output leaves an incomplete directory, not PASS; retry uses a new
output directory or verified frozen inputs. No production rollback is needed.
Input/output roots use the existing offline research path owner. No new durable
runtime state, receipt family or platform is introduced.

Tests cover strict after-signal quotes, fixed old target, quality/ambiguity,
Market independent of News, missing timing, pending/held ownership across
generation and fold boundaries, absent exits, every opportunity accounted once,
cost once, no fitting, and the real CLI boundary. Rehearsal is the full frozen
cohort against byte-bound archived quotes, with independent count/return checks.
Prediction metrics are retained unchanged; closed-trade metrics, incomplete
exposure, evidence gaps and sample-size limits are reported separately. Results
are retrospective simulations, never account P&L or untouched validation.
