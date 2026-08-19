#!/usr/bin/env python
"""Backfill immutable quota-accounted embeddings for news identity."""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
import json
from pathlib import Path
import sys


MODULE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MODULE_ROOT))

from xauusd_forecaster.forward_ledger import ForwardLedger  # noqa: E402
from xauusd_forecaster.news_impact import (  # noqa: E402
    IDENTITY_CANDIDATE_UNIVERSE_LIMIT,
    load_identity_candidate_universe,
)
from xauusd_forecaster.news_retrieval import (  # noqa: E402
    NEWS_EMBEDDING_MODEL,
    append_missing_embeddings,
    load_embeddings,
)
from xauusd_forecaster.gemini_embeddings import GeminiEmbeddingClient  # noqa: E402
from xauusd_forecaster.news_scheduler import (  # noqa: E402
    CONTRACT_BACKFILL_WORKLOAD,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--batch-size", type=int, default=32)
    args = parser.parse_args()
    if not 1 <= args.batch_size <= 128:
        raise ValueError("embedding batch size must be between 1 and 128")
    ledger = ForwardLedger(args.database.resolve())
    try:
        latest = ledger.connection.execute(
            "SELECT max(collector_first_seen_time) FROM news_revisions"
        ).fetchone()[0]
        if not latest:
            print(json.dumps({"model": NEWS_EMBEDDING_MODEL, "rows": 0}))
            return 0
        universe = load_identity_candidate_universe(
            ledger.connection,
            max_first_seen=str(latest),
            limit=IDENTITY_CANDIDATE_UNIVERSE_LIMIT,
        )
        client = GeminiEmbeddingClient(
            ledger.connection, dispatch_task="NEWS_EMBEDDING_BACKFILL",
            workload_class=CONTRACT_BACKFILL_WORKLOAD,
        )
        total = 0
        profile = client.profile()
        while True:
            profile, appended = append_missing_embeddings(
                ledger.connection, universe, client, limit=args.batch_size,
            )
            total += appended
            if appended == 0:
                break
            print(json.dumps({
                "event": "NEWS_EMBEDDING_BACKFILL_PROGRESS",
                "appended": total,
                "universe": len(universe),
                "at": datetime.now(UTC).isoformat(timespec="seconds"),
            }), flush=True)
        vectors = load_embeddings(
            ledger.connection, profile, expected_rows=universe,
        )
        missing = {
            str(row["candidate_id"]) for row in universe
        } - set(vectors)
        if missing:
            raise ValueError(f"embedding backfill incomplete: {len(missing)} missing")
        print(json.dumps({
            "model": profile.model_name,
            "model_digest": profile.model_digest,
            "dimensions": profile.dimensions,
            "universe": len(universe),
            "appended": total,
            "status": "COMPLETE",
        }))
        return 0
    finally:
        ledger.close()


if __name__ == "__main__":
    raise SystemExit(main())
