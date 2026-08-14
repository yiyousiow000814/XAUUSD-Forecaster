from __future__ import annotations

import urllib.error
from pathlib import Path

import pytest

from xauusd_forecaster.model_gateway import (
    GeminiModelGateway,
    ModelGatewayCapacityExhausted,
    ModelGatewayResponseInvalid,
    ModelRequestUsage,
)
from tests.model_accounting_fakes import CallbackModelAccountant


def test_gateway_requires_accounting_before_it_can_be_constructed() -> None:
    with pytest.raises(ValueError, match="metered request accounting"):
        GeminiModelGateway(
            ("key",), requests_per_key=1, accountant=None,  # type: ignore[arg-type]
        )


def test_gateway_reserves_exact_usage_before_transport(monkeypatch) -> None:
    events: list[object] = []

    def reserve(usage: ModelRequestUsage) -> bool:
        events.append(usage)
        return True

    def post_json(_key, _model, _method, _payload, *, timeout):
        del timeout
        events.append("transport")
        return {"value": 7, "modelVersion": "exact-model"}

    monkeypatch.setattr(GeminiModelGateway, "_post_json", staticmethod(post_json))
    gateway = GeminiModelGateway(
        ("key",), requests_per_key=1,
        accountant=CallbackModelAccountant(reserve),
    )

    result, model = gateway.generate(
        0,
        model="requested-model",
        purpose="news-impact",
        payload={"contents": []},
        input_tokens=4_321,
        decode=lambda envelope: envelope["value"],
        retryable_http_codes=frozenset(),
    )

    assert result == 7
    assert model == "exact-model"
    assert events == [
        ModelRequestUsage(
            model="requested-model",
            purpose="news-impact",
            input_tokens=4_321,
        ),
        "transport",
    ]


def test_gateway_refusal_never_reaches_generation_transport(monkeypatch) -> None:
    monkeypatch.setattr(
        GeminiModelGateway,
        "_post_json",
        staticmethod(lambda *_args, **_kwargs: pytest.fail("transport called")),
    )
    gateway = GeminiModelGateway(
        ("key",), requests_per_key=1,
        accountant=CallbackModelAccountant(lambda _usage: False),
    )

    with pytest.raises(ModelGatewayCapacityExhausted):
        gateway.generate(
            0, model="model", purpose="headline-translation", payload={},
            input_tokens=10, decode=lambda envelope: envelope,
            retryable_http_codes=frozenset(),
        )


def test_gateway_batch_capacity_decreases_after_each_attempt(monkeypatch) -> None:
    monkeypatch.setattr(
        GeminiModelGateway,
        "_post_json",
        staticmethod(lambda *_args, **_kwargs: {"ok": True}),
    )
    gateway = GeminiModelGateway(
        ("key-a", "key-b"), requests_per_key=3, batch_limit=2,
        accountant=CallbackModelAccountant(lambda _usage: True),
    )

    assert gateway.available_batch_capacity() == 2
    gateway.generate(
        0, model="model", purpose="test", payload={}, input_tokens=1,
        decode=lambda envelope: envelope,
        retryable_http_codes=frozenset(),
    )
    assert gateway.available_batch_capacity() == 1


@pytest.mark.parametrize(
    "purpose",
    ("news-annotation", "chinese-repair", "headline-translation", "news-impact"),
)
def test_failed_provider_attempt_remains_typed_and_accounted(
    monkeypatch, purpose,
) -> None:
    usages: list[ModelRequestUsage] = []
    failure = urllib.error.HTTPError(
        "https://example.invalid", 429, "quota", {}, None,
    )
    monkeypatch.setattr(
        GeminiModelGateway,
        "_post_json",
        staticmethod(lambda *_args, **_kwargs: (_ for _ in ()).throw(failure)),
    )
    gateway = GeminiModelGateway(
        ("key",), requests_per_key=1,
        accountant=CallbackModelAccountant(
            lambda usage: usages.append(usage) or True
        ),
    )

    with pytest.raises(urllib.error.HTTPError):
        gateway.generate(
            0, model="model", purpose=purpose, payload={},
            input_tokens=99, decode=lambda envelope: envelope,
            retryable_http_codes=frozenset({429}),
        )

    assert len(usages) == 1
    assert usages[0].input_tokens == 99


def test_invalid_model_output_preserves_the_validator_failure(monkeypatch) -> None:
    monkeypatch.setattr(
        GeminiModelGateway,
        "_post_json",
        staticmethod(lambda *_args, **_kwargs: {"candidate": "invalid"}),
    )
    gateway = GeminiModelGateway(
        ("key",), requests_per_key=1,
        accountant=CallbackModelAccountant(lambda _usage: True),
    )

    with pytest.raises(ModelGatewayResponseInvalid) as raised:
        gateway.generate(
            0, model="model", purpose="news-impact", payload={},
            input_tokens=10,
            decode=lambda _envelope: (_ for _ in ()).throw(
                ValueError("identity relation contradicts material update")
            ),
            retryable_http_codes=frozenset(),
            retryable_decode_errors=(ValueError,),
        )

    assert raised.value.cause_type == "ValueError"
    assert "identity relation contradicts" in raised.value.cause_message


def test_latest_validation_failure_is_not_misreported_as_an_earlier_http_error(
    monkeypatch,
) -> None:
    calls = []

    def post_json(key, *_args, **_kwargs):
        calls.append(key)
        if key == "key-a":
            raise urllib.error.HTTPError(
                "https://example.invalid", 503, "unavailable", {}, None,
            )
        return {"candidate": "invalid"}

    monkeypatch.setattr(GeminiModelGateway, "_post_json", staticmethod(post_json))
    gateway = GeminiModelGateway(
        ("key-a", "key-b"), requests_per_key=1,
        accountant=CallbackModelAccountant(lambda _usage: True),
    )

    with pytest.raises(ModelGatewayResponseInvalid):
        gateway.generate(
            0, model="model", purpose="news-impact", payload={}, input_tokens=10,
            decode=lambda _envelope: (_ for _ in ()).throw(ValueError("bad JSON")),
            retryable_http_codes=frozenset({503}),
            retryable_decode_errors=(ValueError,),
        )

    assert calls == ["key-a", "key-b"]


def test_all_generation_families_share_the_same_usage_contract(monkeypatch) -> None:
    usages: list[ModelRequestUsage] = []
    monkeypatch.setattr(
        GeminiModelGateway,
        "_post_json",
        staticmethod(lambda *_args, **_kwargs: {"ok": True}),
    )
    purposes = (
        "news-annotation",
        "chinese-repair",
        "headline-translation",
        "news-impact",
    )
    for purpose in purposes:
        gateway = GeminiModelGateway(
            ("key",), requests_per_key=1,
            accountant=CallbackModelAccountant(
                lambda usage: usages.append(usage) or True
            ),
        )
        gateway.generate(
            0, model="model", purpose=purpose, payload={}, input_tokens=1,
            decode=lambda envelope: envelope,
            retryable_http_codes=frozenset(),
        )

    assert tuple(usage.purpose for usage in usages) == purposes


def test_google_model_transport_has_one_source_of_truth() -> None:
    root = Path(__file__).resolve().parents[1]
    package = root / "xauusd_forecaster"
    owners = []
    constructors = []
    for base in (package, root / "scripts"):
        for path in base.rglob("*.py"):
            source = path.read_text(encoding="utf-8")
            if "generativelanguage.googleapis.com" in source:
                owners.append(path.relative_to(root).as_posix())
            if "GeminiModelGateway(" in source:
                constructors.append(path.relative_to(root).as_posix())
    annotation_source = (package / "annotation.py").read_text(encoding="utf-8")

    assert owners == ["xauusd_forecaster/model_gateway.py"]
    assert constructors == ["xauusd_forecaster/annotation.py"]
    assert "def _call_gemini" not in annotation_source
    assert "x-goog-api-key" not in annotation_source
