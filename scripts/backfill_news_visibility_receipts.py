#!/usr/bin/env python
"""Reconstruct append-only model/news visibility receipts at frozen decision times."""

from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path


MODULE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MODULE_ROOT))

from xauusd_forecaster.forward_ledger import ForwardLedger  # noqa: E402
from xauusd_forecaster.live_v2 import _append_news_visibility_receipts  # noqa: E402
from xauusd_forecaster.legacy_news_features_v2 import (  # noqa: E402
    LEGACY_BROAD_ELIGIBILITY_VERSION,
    LEGACY_ELIGIBILITY_VERSION,
    aggregate_legacy_news_features_v2,
)
from xauusd_forecaster.legacy_news_features_v3 import (  # noqa: E402
    LEGACY_V3_BROAD_ELIGIBILITY_VERSION,
    LEGACY_V3_ELIGIBILITY_VERSION,
    aggregate_legacy_news_features_v3,
)
from xauusd_forecaster.evidence_v2 import ELIGIBILITY_VERSION  # noqa: E402
from xauusd_forecaster.news_evidence import EVIDENCE_POLICY_VERSION  # noqa: E402
from xauusd_forecaster.news_features_v2 import aggregate_news_features_v2  # noqa: E402


DEFAULT_DATABASE = MODULE_ROOT / ".local" / "forward" / "forward-evidence.sqlite3"
UTC = timezone.utc


def backfill(database: Path) -> tuple[int, int]:
    ledger = ForwardLedger(database)
    try:
        rows = ledger.connection.execute(
            """SELECT p.source_decision_id,p.decision_time,p.model_identity,
                      p.model_version,p.prediction_status,u.eligibility_version
               FROM predictions_v2 p
               JOIN model_updates_v2 u ON u.model_version=p.model_version
               WHERE p.model_identity IN (
                   'NEWS_RESIDUAL','FULL','BROAD_NEWS_RESIDUAL','BROAD_FULL')
                 AND p.prediction_status!='DATA_UNHEALTHY'
               ORDER BY p.decision_time,p.model_identity"""
        ).fetchall()
        grouped: dict[tuple[str, str], list[dict]] = defaultdict(list)
        for row in rows:
            grouped[(row["source_decision_id"], row["decision_time"])].append({
                "model_identity": row["model_identity"],
                "model_version": row["model_version"],
                "eligibility_version": row["eligibility_version"],
            })
        inserted = 0
        replayed = 0
        recorded_at = datetime.now(UTC)
        for (decision_id, decision_time_text), predictions in grouped.items():
            decision_time = datetime.fromisoformat(decision_time_text)
            news = aggregate_news_features_v2(ledger, decision_time)
            legacy_v2 = aggregate_legacy_news_features_v2(ledger, decision_time)
            legacy_v3 = aggregate_legacy_news_features_v3(ledger, decision_time)
            with ledger.connection:
                inserted += _append_news_visibility_receipts(
                    ledger.connection,
                    decision_id=decision_id,
                    decision_time=decision_time,
                    recorded_at=recorded_at,
                    predictions=predictions,
                    news_by_eligibility={
                        ELIGIBILITY_VERSION: news,
                        f"{ELIGIBILITY_VERSION}+{EVIDENCE_POLICY_VERSION}": news,
                        LEGACY_ELIGIBILITY_VERSION: legacy_v2,
                        LEGACY_BROAD_ELIGIBILITY_VERSION: legacy_v2,
                        LEGACY_V3_ELIGIBILITY_VERSION: legacy_v3,
                        LEGACY_V3_BROAD_ELIGIBILITY_VERSION: legacy_v3,
                    },
                    origin="POINT_IN_TIME_REPLAY",
                )
            replayed += 1
        return replayed, inserted
    finally:
        ledger.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    args = parser.parse_args()
    replayed, inserted = backfill(args.database)
    print(f"replayed_decisions={replayed} inserted_receipts={inserted}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
