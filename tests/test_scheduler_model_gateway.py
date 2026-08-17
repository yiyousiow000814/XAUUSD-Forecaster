from __future__ import annotations

import json
import math
import urllib.request
from datetime import UTC, datetime, timedelta

import pytest

from xauusd_forecaster.ai_provider_registry import AI_QUOTA_SURFACES
from xauusd_forecaster.annotation import DEFAULT_GEMMA_MODEL
from xauusd_forecaster.forward_ledger import ForwardLedger
from xauusd_forecaster.model_gateway import (
    GeminiModelGateway, ModelGatewayResponseInvalid, ModelRequestUsage,
)
from xauusd_forecaster.news_scheduler import (
    ApiCredential,
    calibrated_input_tokens,
    mark_account_request_attempted,
    record_account_request_outcome,
    reserve_account_request,
    reserve_provider_dispatch,
    rolling_account_usage,
)
from xauusd_forecaster.scheduler_model_gateway import SchedulerModelAccountant


NOW = datetime(2026, 8, 18, 1, 0, tzinfo=UTC)


def _initialize_provider_state(connection) -> None:
    assert reserve_provider_dispatch(
        connection, provider_task="ACTIVE_IMPACT",
        now=NOW - timedelta(seconds=1),
    )[0]


def _record_calibration_sample(
    connection,
    index: int,
    ratio: float,
    *,
    requested_model: str = "gemma-4-31b-it",
    purpose: str = "news-impact",
    prompt_contract: str = "impact-v1",
    estimator_version: str = "estimator-v1",
    provider_model_version: str = "gemma-exact-v1",
) -> None:
    usage_id = (
        f"sample-{requested_model}-{purpose}-{prompt_contract}-"
        f"{estimator_version}-{provider_model_version}-{index}"
    )
    base_tokens = 1_000
    instant = NOW + timedelta(seconds=index)
    assert reserve_account_request(
        connection,
        account_id="account",
        model_family=requested_model,
        daily_limit=1_000_000,
        requests_per_minute=1_000_000,
        input_tokens=base_tokens,
        input_tokens_per_minute=1_000_000_000,
        usage_id=usage_id,
        requested_model=requested_model,
        purpose=purpose,
        prompt_contract=prompt_contract,
        estimator_version=estimator_version,
        base_estimated_input_tokens=base_tokens,
        now=instant,
    )
    mark_account_request_attempted(connection, usage_id, now=instant)
    record_account_request_outcome(
        connection,
        usage_id,
        outcome="PROVIDER_SUCCEEDED",
        usage_metadata={"prompt_token_count": round(base_tokens * ratio)},
        provider_model_version=provider_model_version,
        now=instant + timedelta(milliseconds=100),
    )


@pytest.mark.parametrize(
    "model",
    [surface.model_families[0] for surface in AI_QUOTA_SURFACES],
)
def test_every_registered_model_is_accounted_under_the_model_sent(
    tmp_path, model: str,
) -> None:
    ledger = ForwardLedger(tmp_path / "forward.sqlite3")
    accountant = SchedulerModelAccountant(
        ledger.connection,
        ApiCredential("account", "PREEMPTIBLE", "secret", "credential"),
        urgent=False,
    )

    assert accountant.reserve(ModelRequestUsage(
        model=model, purpose="headline-translation", input_tokens=321,
    ))
    row = ledger.connection.execute(
        """SELECT model_family,request_count,input_token_count
           FROM news_ai_account_minute_usage_v1"""
    ).fetchone()

    assert dict(row) == {
        "model_family": model,
        "request_count": 1,
        "input_token_count": 321,
    }
    ledger.close()


def test_title_fallback_moves_accounting_to_the_actual_gemini_surface(tmp_path) -> None:
    ledger = ForwardLedger(tmp_path / "forward.sqlite3")
    accountant = SchedulerModelAccountant(
        ledger.connection,
        ApiCredential("account", "PREEMPTIBLE", "secret", "credential"),
        urgent=False,
    )

    assert accountant.reserve(ModelRequestUsage(
        model="gemini-3.1-flash-lite",
        purpose="headline-translation",
        input_tokens=123,
    ))
    families = [str(row["model_family"]) for row in ledger.connection.execute(
        "SELECT model_family FROM news_ai_account_daily_usage_v1"
    )]

    assert families == ["gemini-3.1-flash-lite"]
    ledger.close()


def test_successful_generation_persists_sanitized_provider_usage(tmp_path) -> None:
    ledger = ForwardLedger(tmp_path / "forward.sqlite3")
    accountant = SchedulerModelAccountant(
        ledger.connection,
        ApiCredential("account", "PREEMPTIBLE", "secret", "credential"),
        urgent=False,
    )
    gateway = GeminiModelGateway(
        ("secret",), requests_per_key=1, accountant=accountant,
    )
    gateway._post_json = lambda *args, **kwargs: {
        "value": 7,
        "modelVersion": "gemma-version",
        "usageMetadata": {
            "promptTokenCount": 123,
            "candidatesTokenCount": 45,
            "totalTokenCount": 168,
            "providerSpecificField": "not persisted",
        },
    }

    result, version = gateway.generate(
        0,
        model=DEFAULT_GEMMA_MODEL,
        purpose="news-impact",
        payload={"contents": []},
        input_tokens=321,
        decode=lambda envelope: int(envelope["value"]),
        retryable_http_codes=frozenset(),
    )
    row = ledger.connection.execute(
        """SELECT attempted_at,provider_outcome,provider_prompt_token_count,
                  provider_candidates_token_count,provider_total_token_count,
                  provider_model_version
           FROM news_ai_account_request_usage_v1"""
    ).fetchone()

    assert (result, version) == (7, "gemma-version")
    assert row["attempted_at"] is not None
    assert dict(row) == {
        "attempted_at": row["attempted_at"],
        "provider_outcome": "PROVIDER_SUCCEEDED",
        "provider_prompt_token_count": 123,
        "provider_candidates_token_count": 45,
        "provider_total_token_count": 168,
        "provider_model_version": "gemma-version",
    }
    ledger.close()


def test_calibration_is_cold_safe_bounded_and_long_lived(tmp_path) -> None:
    ledger = ForwardLedger(tmp_path / "forward.sqlite3")
    connection = ledger.connection
    _initialize_provider_state(connection)
    key = {
        "requested_model": "gemma-4-31b-it",
        "purpose": "news-impact",
        "prompt_contract": "impact-v1",
        "estimator_version": "estimator-v1",
    }

    assert calibrated_input_tokens(
        connection, base_estimated_input_tokens=1_000, **key,
    ) == (1_000, None, 1.0)
    _record_calibration_sample(connection, 0, 0.60)
    assert calibrated_input_tokens(
        connection, base_estimated_input_tokens=1_000, **key,
    )[0] >= 1_000
    _record_calibration_sample(connection, 1, 1.20)
    assert calibrated_input_tokens(
        connection, base_estimated_input_tokens=1_000, **key,
    )[0] >= 1_260

    for index in range(2, 202):
        _record_calibration_sample(connection, index, 0.80)
    stable = connection.execute(
        "SELECT * FROM news_ai_token_calibration_v1"
    ).fetchone()
    assert stable["lifetime_sample_count"] == 202
    assert stable["effective_sample_count"] == 128
    assert len(json.loads(stable["recent_ratio_window_json"])) == 128
    assert 0.84 <= stable["safe_ratio"] <= 0.86

    _record_calibration_sample(connection, 202, 1.40)
    _record_calibration_sample(connection, 203, 1.40)
    tailed = connection.execute(
        "SELECT safe_ratio FROM news_ai_token_calibration_v1"
    ).fetchone()[0]
    assert tailed >= 1.47

    for index in range(204, 462):
        _record_calibration_sample(connection, index, 0.70)
    adapted = connection.execute(
        "SELECT * FROM news_ai_token_calibration_v1"
    ).fetchone()
    window = json.loads(adapted["recent_ratio_window_json"])
    assert adapted["lifetime_sample_count"] == 462
    assert adapted["effective_sample_count"] == 128
    assert len(window) == 128
    assert set(window) == {0.7}
    assert 0.735 <= adapted["safe_ratio"] < 0.80
    ledger.close()


def test_one_new_extreme_ratio_immediately_protects_the_next_admission(
    tmp_path,
) -> None:
    ledger = ForwardLedger(tmp_path / "forward.sqlite3")
    connection = ledger.connection
    _initialize_provider_state(connection)
    key = {
        "requested_model": "gemma-4-31b-it",
        "purpose": "news-impact",
        "prompt_contract": "impact-v1",
        "estimator_version": "estimator-v1",
    }
    for index in range(128):
        _record_calibration_sample(connection, index, 0.80)
    stable = calibrated_input_tokens(
        connection, base_estimated_input_tokens=1_000, **key,
    )
    assert 840 <= stable[0] <= 841

    _record_calibration_sample(connection, 128, 1.40)
    protected = calibrated_input_tokens(
        connection, base_estimated_input_tokens=1_000, **key,
    )

    assert protected[0] >= 1_470
    assert protected[2] >= 1.47
    ledger.close()


def test_calibration_isolated_by_contract_estimator_and_provider_version(
    tmp_path,
) -> None:
    path = tmp_path / "forward.sqlite3"
    ledger = ForwardLedger(path)
    _initialize_provider_state(ledger.connection)
    _record_calibration_sample(ledger.connection, 0, 1.10)
    _record_calibration_sample(
        ledger.connection, 1, 0.75, prompt_contract="impact-v2",
    )
    _record_calibration_sample(
        ledger.connection, 2, 0.80, estimator_version="estimator-v2",
    )
    _record_calibration_sample(
        ledger.connection, 3, 1.25, provider_model_version="gemma-exact-v2",
    )
    assert ledger.connection.execute(
        "SELECT count(*) FROM news_ai_token_calibration_v1"
    ).fetchone()[0] == 4
    admitted, active_model, safe_ratio = calibrated_input_tokens(
        ledger.connection,
        requested_model="gemma-4-31b-it",
        purpose="news-impact",
        prompt_contract="impact-v1",
        estimator_version="estimator-v1",
        base_estimated_input_tokens=1_000,
    )
    assert active_model == "gemma-exact-v2"
    assert admitted >= 1_312
    assert safe_ratio >= 1.3125
    ledger.close()

    reopened = ForwardLedger(path)
    assert calibrated_input_tokens(
        reopened.connection,
        requested_model="gemma-4-31b-it",
        purpose="news-impact",
        prompt_contract="impact-v1",
        estimator_version="estimator-v1",
        base_estimated_input_tokens=1_000,
    )[1:] == (active_model, safe_ratio)
    reopened.close()


def test_scheduler_accountant_preserves_base_and_calibrated_admission(tmp_path) -> None:
    ledger = ForwardLedger(tmp_path / "forward.sqlite3")
    _initialize_provider_state(ledger.connection)
    _record_calibration_sample(ledger.connection, 0, 1.20)
    ledger.connection.executescript(
        """DELETE FROM news_ai_account_request_usage_v1;
           DELETE FROM news_ai_account_minute_usage_v1;
           DELETE FROM news_ai_account_daily_usage_v1;
           DELETE FROM news_ai_provider_dispatch_state_v1;
           DELETE FROM news_ai_provider_dispatch_task_state_v1;"""
    )
    accountant = SchedulerModelAccountant(
        ledger.connection,
        ApiCredential("account", "PREEMPTIBLE", "secret", "credential"),
        urgent=False,
    )

    assert accountant.reserve(ModelRequestUsage(
        model=DEFAULT_GEMMA_MODEL,
        purpose="news-impact",
        input_tokens=5_600,
        prompt_contract="impact-v1",
        estimator_version="estimator-v1",
    ))
    row = ledger.connection.execute(
        """SELECT requested_model,purpose,prompt_contract,estimator_version,
                  base_estimated_input_tokens,admitted_input_tokens,
                  input_token_count,calibration_provider_model_version,
                  calibration_safe_ratio
           FROM news_ai_account_request_usage_v1"""
    ).fetchone()

    assert row["requested_model"] == DEFAULT_GEMMA_MODEL
    assert row["purpose"] == "news-impact"
    assert row["prompt_contract"] == "impact-v1"
    assert row["estimator_version"] == "estimator-v1"
    assert row["base_estimated_input_tokens"] == 5_600
    assert row["admitted_input_tokens"] >= 7_056
    assert row["input_token_count"] == row["admitted_input_tokens"]
    assert row["calibration_provider_model_version"] == "gemma-exact-v1"
    assert row["calibration_safe_ratio"] >= 1.26
    ledger.close()


def test_scheduler_accountant_exposes_the_calibrated_base_budget(tmp_path) -> None:
    ledger = ForwardLedger(tmp_path / "forward.sqlite3")
    _initialize_provider_state(ledger.connection)
    _record_calibration_sample(ledger.connection, 0, 1.20)
    accountant = SchedulerModelAccountant(
        ledger.connection,
        ApiCredential("account", "PREEMPTIBLE", "secret", "credential"),
        urgent=False,
    )
    usage = ModelRequestUsage(
        model=DEFAULT_GEMMA_MODEL,
        purpose="news-impact",
        input_tokens=14_000,
        prompt_contract="impact-v1",
        estimator_version="estimator-v1",
    )
    _, _, safe_ratio = calibrated_input_tokens(
        ledger.connection,
        requested_model=usage.model,
        purpose=usage.purpose,
        prompt_contract=usage.prompt_contract,
        estimator_version=usage.estimator_version,
        base_estimated_input_tokens=usage.input_tokens,
    )

    budget = accountant.effective_base_input_token_budget(
        usage, input_tokens_per_minute=15_000,
    )

    assert safe_ratio > 1
    assert budget == math.floor(15_000 / safe_ratio)
    assert math.ceil(budget * safe_ratio) <= 15_000
    ledger.close()


def test_malformed_provider_json_closes_attempt_before_route_retry(
    tmp_path, monkeypatch,
) -> None:
    ledger = ForwardLedger(tmp_path / "forward.sqlite3")
    credential = ApiCredential(
        "account", "PREEMPTIBLE", "secret", "credential",
    )

    class Response:
        def __init__(self, content: bytes) -> None:
            self.content = content

        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            return None

        def read(self) -> bytes:
            return self.content

    monkeypatch.setattr(
        urllib.request, "urlopen",
        lambda *_args, **_kwargs: Response(b"{"),
    )
    gateway = GeminiModelGateway(
        ("secret",), requests_per_key=1,
        accountant=SchedulerModelAccountant(
            ledger.connection, credential, urgent=False,
        ),
    )

    with pytest.raises(ModelGatewayResponseInvalid):
        gateway.generate(
            0,
            model=DEFAULT_GEMMA_MODEL,
            purpose="news-impact",
            prompt_contract="impact-v1",
            payload={"contents": []},
            input_tokens=5_600,
            decode=lambda envelope: envelope,
            retryable_http_codes=frozenset(),
            retryable_decode_errors=(json.JSONDecodeError,),
        )

    first = ledger.connection.execute(
        """SELECT provider_outcome,provider_prompt_token_count,
                  admitted_input_tokens
           FROM news_ai_account_request_usage_v1"""
    ).fetchone()
    assert dict(first) == {
        "provider_outcome": "PROVIDER_FAILED",
        "provider_prompt_token_count": None,
        "admitted_input_tokens": 5_600,
    }
    assert ledger.connection.execute(
        "SELECT count(*) FROM news_ai_token_calibration_v1"
    ).fetchone()[0] == 0
    assert rolling_account_usage(
        ledger.connection,
        account_id=credential.account_id,
        model_families=(DEFAULT_GEMMA_MODEL,),
        now=datetime.now(UTC),
    ) == (1, 5_600)

    with ledger.connection:
        ledger.connection.execute(
            """UPDATE news_ai_provider_dispatch_state_v1
               SET next_eligible_at=?,cooldown_until=NULL""",
            ((datetime.now(UTC) - timedelta(seconds=1)).isoformat(),),
        )
    monkeypatch.setattr(
        urllib.request, "urlopen",
        lambda *_args, **_kwargs: Response(json.dumps({
            "value": 7,
            "modelVersion": "gemma-version",
            "usageMetadata": {"promptTokenCount": 4_800},
        }).encode()),
    )
    retry_gateway = GeminiModelGateway(
        ("secret",), requests_per_key=1,
        accountant=SchedulerModelAccountant(
            ledger.connection, credential, urgent=False,
        ),
    )
    result, _ = retry_gateway.generate(
        0,
        model=DEFAULT_GEMMA_MODEL,
        purpose="news-impact",
        prompt_contract="impact-v1",
        payload={"contents": []},
        input_tokens=5_600,
        decode=lambda envelope: int(envelope["value"]),
        retryable_http_codes=frozenset(),
    )
    outcomes = ledger.connection.execute(
        """SELECT provider_outcome,provider_prompt_token_count
           FROM news_ai_account_request_usage_v1 ORDER BY rowid"""
    ).fetchall()

    assert result == 7
    assert [tuple(row) for row in outcomes] == [
        ("PROVIDER_FAILED", None), ("PROVIDER_SUCCEEDED", 4_800),
    ]
    assert ledger.connection.execute(
        "SELECT lifetime_sample_count FROM news_ai_token_calibration_v1"
    ).fetchone()[0] == 1
    ledger.close()
