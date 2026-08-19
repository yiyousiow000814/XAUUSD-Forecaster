"""Offline evaluation only: run the frozen benchmark through Gemma manually."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import tempfile
import time
import urllib.error
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from xauusd_forecaster.annotation import (  # noqa: E402
    DEFAULT_GEMMA_MODEL,
    generate_metered_response,
)
from xauusd_forecaster.named_reference_benchmark import (  # noqa: E402
    REVIEW_CONTRACT_VERSION,
    benchmark_manifest_sha256,
    decode_named_reference_review,
    load_named_reference_benchmark,
    named_reference_review_payload,
    score_named_reference_runs,
)
from xauusd_forecaster.news_scheduler import (  # noqa: E402
    configured_api_credentials,
    install_scheduler_schema,
)
from xauusd_forecaster.scheduler_model_gateway import (  # noqa: E402
    SchedulerModelAccountant,
)


DEFAULT_FIXTURE = ROOT / "tests" / "fixtures" / "news_named_reference_benchmark.json"


def _batches(values: tuple, size: int) -> tuple[tuple, ...]:
    return tuple(values[index:index + size] for index in range(0, len(values), size))


def run_benchmark(
    *,
    runs: int,
    batch_size: int,
) -> dict[str, object]:
    if runs not in {2, 3}:
        raise ValueError("benchmark runs must be 2 or 3")
    if not 1 <= batch_size <= 60:
        raise ValueError("benchmark batch size must be between 1 and 60")
    manifest = load_named_reference_benchmark(DEFAULT_FIXTURE)
    cases = manifest["review_cases"]
    credentials = configured_api_credentials()
    if not credentials:
        raise RuntimeError("Gemini credentials are not configured")

    all_runs: list[dict[str, str]] = []
    exact_models: set[str] = set()
    request_count = 0
    with tempfile.TemporaryDirectory(prefix="named-reference-benchmark-") as directory:
        database = Path(directory) / "scheduler.sqlite3"
        connection = sqlite3.connect(database)
        connection.row_factory = sqlite3.Row
        install_scheduler_schema(connection)
        try:
            for run_index in range(runs):
                decisions: dict[str, str] = {}
                for batch_index, batch in enumerate(_batches(cases, batch_size)):
                    credential = credentials[
                        (run_index * len(_batches(cases, batch_size)) + batch_index)
                        % len(credentials)
                    ]
                    accountant = SchedulerModelAccountant(
                        connection, credential, urgent=False,
                        work_lane="LIVE",
                    )
                    expected_ids = tuple(case.case_id for case in batch)
                    payload = named_reference_review_payload(batch)
                    try:
                        result, exact_model = generate_metered_response(
                            credential.api_key,
                            model=DEFAULT_GEMMA_MODEL,
                            purpose="named-reference-benchmark",
                            prompt_contract=REVIEW_CONTRACT_VERSION,
                            payload=payload,
                            decode=lambda envelope, ids=expected_ids: (
                                decode_named_reference_review(envelope, ids)
                            ),
                            request_accountant=accountant,
                        )
                    except urllib.error.HTTPError as error:
                        detail = error.read(1000).decode("utf-8", errors="replace")
                        raise RuntimeError(
                            f"reviewer provider HTTP {error.code}: {detail}"
                        ) from error
                    decisions.update(result)
                    exact_models.add(exact_model)
                    request_count += 1
                    # Preserve shared provider pacing even across accounts.
                    time.sleep(0.3)
                all_runs.append(decisions)
            usage = connection.execute(
                """SELECT count(*) AS requests,
                          count(DISTINCT account_id) AS accounts,
                          COALESCE(sum(provider_prompt_token_count),0) AS prompt_tokens
                   FROM news_ai_account_request_usage_v1
                   WHERE provider_outcome='PROVIDER_SUCCEEDED'"""
            ).fetchone()
        finally:
            connection.close()

    metrics = score_named_reference_runs(cases, tuple(all_runs))
    return {
        "schema_version": "news-named-reference-benchmark-report.v1",
        "benchmark_schema_version": manifest["schema_version"],
        "benchmark_manifest_sha256": benchmark_manifest_sha256(manifest),
        "review_contract_version": REVIEW_CONTRACT_VERSION,
        "requested_model": DEFAULT_GEMMA_MODEL,
        "provider_model_versions": sorted(exact_models),
        "runs": runs,
        "batch_size": batch_size,
        "provider_request_count": request_count,
        "provider_account_count": int(usage["accounts"]),
        "provider_prompt_tokens": int(usage["prompt_tokens"]),
        "hard_guard_cases_not_sent_to_reviewer": len(manifest["hard_guard_cases"]),
        **metrics,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--batch-size", type=int, default=60)
    args = parser.parse_args()
    report = run_benchmark(
        runs=args.runs, batch_size=args.batch_size,
    )
    serialized = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(serialized)
    return 0 if report["gate"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
