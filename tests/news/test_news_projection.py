import json
import hashlib
import os
import subprocess
from pathlib import Path

import pytest

from xauusd_forecaster.news_projection import (
    NEWS_PROJECTION_CONTRACT_VERSION,
    split_news_rows,
    receipt_digest,
    receipt_payload_hash,
    NewsProjectionSourceCapture,
    NewsProjectionGeneration,
    NewsSourceCapturePage,
    NewsSourceCaptureStorageUnresolved,
    news_source_capture_record,
    compact_json,
    sha256_json,
    build_news_projection_generation,
)


FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "news_projection_receipt_vectors.json"


def test_receipt_vectors_are_cross_runtime_canonical() -> None:
    vectors = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert vectors["contract_version"] == NEWS_PROJECTION_CONTRACT_VERSION
    for vector in vectors["payload_vectors"]:
        assert receipt_payload_hash(vector["value"]) == vector["expected_hash"]
    assert receipt_digest(
        [vectors["payload_vectors"][0]["value"]],
        [vectors["payload_vectors"][1]["value"]],
    ) == vectors["expected_receipt_digest"]


def test_receipt_numbers_follow_json_number_semantics() -> None:
    assert receipt_payload_hash({"value": 0}) == receipt_payload_hash({"value": 0.0})
    assert receipt_payload_hash({"value": 0}) == receipt_payload_hash({"value": -0.0})
    assert receipt_payload_hash({"value": 1}) == receipt_payload_hash({"value": 1.0})
    with pytest.raises(ValueError, match="safe-integer"):
        receipt_payload_hash({"value": 9_007_199_254_740_992})
    with pytest.raises(ValueError, match="finite"):
        receipt_payload_hash({"value": float("nan")})


def test_receipt_object_order_is_not_semantic() -> None:
    assert receipt_payload_hash({"z": 1, "a": 2}) == receipt_payload_hash({"a": 2, "z": 1})


def test_detail_identity_is_content_addressed_within_one_source_revision() -> None:
    base = {
        "source": "example", "source_item_id": "same", "revision_number": 1,
        "headline": "Original", "body": "immutable body",
    }
    changed = {**base, "body": "corrected immutable body"}

    first_index, first_details = split_news_rows([base])
    repeat_index, repeat_details = split_news_rows([dict(base)])
    changed_index, changed_details = split_news_rows([changed])

    assert first_index[0]["detail_key"] == first_details[0]["detail_key"]
    assert repeat_index[0]["detail_key"] == first_index[0]["detail_key"]
    assert repeat_details[0]["detail_hash"] == first_details[0]["detail_hash"]
    assert changed_index[0]["detail_key"] != first_index[0]["detail_key"]
    assert changed_details[0]["detail_hash"] != first_details[0]["detail_hash"]


def _capture(tmp_path):
    return NewsProjectionSourceCapture(
        tmp_path / "candidate-generation.capture",
        binding={"snapshot_identity": "fixture-owned-immutable-input"},
        watermark="2026-09-07T00:00:00+00:00",
        window_start="2026-07-09T00:00:00+00:00", epoch="fixture-epoch",
    )


def _source_row(number, *, withdrawal=False, body="frozen body"):
    key = f"item-{number:06d}"
    return {
        "source": "example", "source_item_id": key, "revision_number": 1,
        "headline": key, "z_extra": "order matters for detail identity",
        "body": body, "a_extra": {"z": 2, "a": 1},
        "xauusd_relevance": "IRRELEVANT" if withdrawal else "DIRECT",
    }


def _source_record(number, *, withdrawal=False, body="frozen body"):
    row = _source_row(number, withdrawal=withdrawal, body=body)
    return news_source_capture_record(row, [
        "2026-09-07T00:00:00+00:00", "example", row["source_item_id"], 1,
    ])


def _page_reader(records, calls):
    def read(state):
        start = state["source_count"]
        calls.append(start)
        return NewsSourceCapturePage(iter(records[start:start + 128]), start + 128 < len(records))
    return read


def test_source_capture_preserves_canonical_bytes_across_restart_and_no_work(tmp_path):
    records = [_source_record(i, withdrawal=i % 13 == 0) for i in range(131)]
    capture = _capture(tmp_path)
    calls = []
    first = capture.advance(_page_reader(records, calls))
    assert first["state"] == "BUILDING"
    assert first["source_count"] == 128
    assert first["cursor"] == records[127]["cursor"]
    second = _capture(tmp_path).advance(_page_reader(records, calls))
    assert second["state"] == "SOURCE_COMPLETE"
    assert second["source_count"] == second["item_count"] + second["withdrawal_count"] == 131
    assert not isinstance(second, NewsProjectionGeneration)
    assert second["admission"] == "CAPACITY_REVIEW_REQUIRED"
    assert calls == [0, 128]
    assert list(capture.records()) == records
    assert capture.advance(lambda _state: pytest.fail("completed capture must not read source")) == second
    retained = next(record for record in capture.records() if "detail" in record)
    payload = retained["detail"]["payload"]
    assert list(payload).index("z_extra") < list(payload).index("a_extra")
    assert retained["detail"]["detail_hash"] == sha256_json(payload)
    assert retained["detail"]["detail_hash"] != sha256_json(payload, sort_keys=True)


def test_source_capture_byte_yield_uses_last_accepted_raw_cursor(tmp_path, monkeypatch):
    import xauusd_forecaster.news_projection as module
    records = [_source_record(0), _source_record(1, withdrawal=True), _source_record(2)]
    accepted_bytes = sum(len((compact_json(row) + "\n").encode()) for row in records[:2])
    monkeypatch.setattr(module, "NEWS_SOURCE_CAPTURE_PART_BYTES", accepted_bytes + 1)
    calls = []
    capture = _capture(tmp_path)
    first = capture.advance(_page_reader(records, calls))
    assert first["source_count"] == 2
    assert first["withdrawal_count"] == 1
    assert first["cursor"] == records[1]["cursor"]
    assert first["state"] == "BUILDING"
    second = capture.advance(_page_reader(records, calls))
    assert second["state"] == "SOURCE_COMPLETE"
    assert calls == [0, 2]
    assert list(capture.records()) == records


@pytest.mark.parametrize("failure_point", ("part", "before_manifest", "after_manifest"))
def test_source_capture_atomic_progress_survives_interruption(tmp_path, monkeypatch, failure_point):
    import xauusd_forecaster.news_projection as module
    capture = _capture(tmp_path)
    original = capture._atomic
    interrupted = False

    def interrupt(name, raw):
        nonlocal interrupted
        if not interrupted and (
            name.startswith("part-") if failure_point == "part" else (
                name == "manifest.json" and json.loads(raw)["capture"]["source_count"] == 2
            )
        ):
            interrupted = True
            if failure_point != "before_manifest":
                original(name, raw)
            raise OSError("injected exact artifact commit interruption")
        return original(name, raw)

    records = [_source_record(0), _source_record(1, withdrawal=True)]
    monkeypatch.setattr(capture, "_atomic", interrupt)
    with pytest.raises(OSError, match="injected"):
        capture.advance(_page_reader(records, []))
    restored = _capture(tmp_path)
    failed = restored.read()
    assert failed["source_count"] == (2 if failure_point == "after_manifest" else 0)
    assert failed["last_failure"] == "NEWS_SOURCE_CAPTURE_STORAGE_WRITE_FAILED"
    assert restored.advance(lambda _state: pytest.fail("write failure must back off")) == failed
    monkeypatch.setattr(module.time, "time", lambda: failed["retry_not_before"] + 1)
    final = restored.advance(_page_reader(records, []))
    assert final["source_count"] == 2
    assert len(final["parts"]) == 1
    assert list(restored.records()) == records


def test_source_capture_failures_preserve_prefix_and_backoff(tmp_path, monkeypatch):
    import xauusd_forecaster.news_projection as module
    records = [_source_record(i) for i in range(129)]
    capture = _capture(tmp_path)
    accepted = capture.advance(_page_reader(records, []))

    def invalid_page(_state):
        def rows():
            yield records[128]
            raise ValueError("NEWS_SOURCE_CAPTURE_INJECTED_SQL_BUDGET")
        return NewsSourceCapturePage(rows(), True)

    with pytest.raises(ValueError, match="INJECTED_SQL_BUDGET"):
        capture.advance(invalid_page)
    failed = capture.read()
    assert failed["parts"] == accepted["parts"]
    assert failed["cursor"] == accepted["cursor"]
    assert capture.advance(lambda _state: pytest.fail("backoff must not read source")) == failed
    monkeypatch.setattr(module.time, "time", lambda: failed["retry_not_before"] + 1)
    complete = capture.advance(_page_reader(records, []))
    assert complete["last_failure"] == "NEWS_SOURCE_CAPTURE_INJECTED_SQL_BUDGET"
    assert complete["source_count"] == 129


def test_complete_source_needs_explicit_materialized_capacity_admission(tmp_path):
    # Raw source work and retained generation have different bounds. Preserve
    # the capture fact; only full planning and explicit admission enable replay.
    records = [_source_record(i, withdrawal=i > 0) for i in range(10_001)]
    capture = _capture(tmp_path)
    state = capture.read()
    while state["state"] != "SOURCE_COMPLETE":
        state = capture.advance(_page_reader(records, []))
    assert state["source_count"] == 10_001
    assert state["item_count"] == 1
    assert state["withdrawal_count"] == 10_000
    assert state["admission"] == "SOURCE_ROW_CAPACITY_EXCEEDED"
    assert not isinstance(state, NewsProjectionGeneration)
    assert not list(tmp_path.glob("*.json.gz"))
    capture.finalize_plan()
    admitted = capture.open_replay_generation(maximum_batches=4)
    assert admitted.manifest["expected_index_count"] == 1
    assert admitted.manifest["withdrawal_count"] == 10_000
    assert capture.read()["source_count"] == 10_001
    assert capture.read()["admission"] == "SOURCE_ROW_CAPACITY_EXCEEDED"


@pytest.mark.parametrize("corruption", (
    "incomplete", "plan-failed", "pending-plan", "budget", "index-count",
    "withdrawal-count", "receipt", "source", "window", "duplicate-key",
))
def test_retained_generation_admission_rejects_before_replay(tmp_path, corruption):
    capture = _capture(tmp_path)
    capture.advance(_page_reader([_source_record(i) for i in range(3)], []))
    capture.finalize_plan()
    state = capture.read()
    manifest = state["source_plan"]["manifest"]
    maximum_batches = 4
    if corruption == "incomplete":
        state["state"] = "BUILDING"
    elif corruption == "plan-failed":
        state["plan_failure"] = "NEWS_SOURCE_CAPTURE_PLAN_TIME_BOUND"
    elif corruption == "pending-plan":
        state["plan_in_progress"] = True
    elif corruption == "budget":
        maximum_batches = 1
    elif corruption == "index-count":
        manifest["expected_index_count"] += 1
    elif corruption == "withdrawal-count":
        manifest["withdrawal_count"] = 10_001
    elif corruption == "receipt":
        manifest["expected_receipt_digest"] = "0" * 64
    elif corruption == "source":
        manifest["source_digest"] = "0" * 64
    elif corruption == "window":
        manifest["watermark"] = "2026-09-08T00:00:00+00:00"
    else:
        locations = state["source_plan"]["row_locations"]
        locations[1][0] = locations[0][0]
    capture._save(state)
    before = (capture.directory / "manifest.json").read_bytes()
    with pytest.raises(ValueError, match="NEWS_SOURCE_CAPTURE_|NEWS_PROJECTION_GENERATION_"):
        capture.open_replay_generation(maximum_batches=maximum_batches)
    assert (capture.directory / "manifest.json").read_bytes() == before


def test_retained_and_materialized_consumers_share_exact_batch_contract(tmp_path, monkeypatch):
    from xauusd_forecaster.dashboard.news_resources import _news_projection_batch
    from xauusd_forecaster.dashboard.sync.resources import _frozen_news_projection_batch

    rows = [_source_row(i, withdrawal=i % 3 == 0) for i in range(17)]
    records = [news_source_capture_record(row, [
        "2026-09-07T00:00:00+00:00", row["source"], row["source_item_id"], row["revision_number"],
    ]) for row in rows]
    capture = _capture(tmp_path)
    capture.advance(_page_reader(records, []))
    plan = capture.finalize_plan()["source_plan"]
    retained = capture.open_replay_generation(maximum_batches=8)
    materialized = build_news_projection_generation(
        [row for row in rows if row["xauusd_relevance"] != "IRRELEVANT"],
        [row for row in rows if row["xauusd_relevance"] == "IRRELEVANT"],
        window_start=capture.identity["window_start"], watermark=capture.identity["watermark"],
    )
    assert retained.manifest == materialized.manifest
    monkeypatch.setattr(capture, "read", lambda: pytest.fail("reloaded metadata per batch"))
    monkeypatch.setattr(capture, "_record_locations", lambda: pytest.fail("rescanned accepted source"))
    for kind in ("detail", "index"):
        for batch in reversed(plan["batches"][kind]):
            offset = batch["offset"]
            expected = materialized.batch_items(kind, offset)
            assert _frozen_news_projection_batch(retained, kind=kind, offset=offset) == expected
            assert _news_projection_batch(retained, kind, offset)["items"] == expected


@pytest.mark.parametrize("corruption", (
    None, "identity", "digest", "manifest", "budget", "missing", "location", "target",
))
@pytest.mark.parametrize("source_kind", ("original", "derived"))
def test_retained_artifact_restart_is_exact_and_does_not_materialize_bodies(tmp_path, monkeypatch, corruption, source_kind):
    import gzip
    from xauusd_forecaster.dashboard import news_resources as api
    if source_kind == "derived":
        _, capture, records, _, transition = _derived_reader_fixture(tmp_path)
        capture.derive_reader_segment(**transition)
        capture.advance(_page_reader(records, []))
    else:
        capture = _capture(tmp_path)
        capture.advance(_page_reader([_source_record(i) for i in range(9)], []))
    capture.finalize_plan()
    manifest_before = (capture.directory / "manifest.json").read_bytes()
    generation = capture.open_replay_generation(maximum_batches=64)
    path = capture.directory.with_suffix(".json.gz")
    target = {"origin": "https://candidate.example", "contract_version": generation.manifest["contract_version"]}
    monkeypatch.setattr(api, "_news_projection_generation_payload", lambda *_: pytest.fail("materialized retained bodies"))
    api._write_news_projection_generation_artifact(path, generation, target=target)
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        envelope = json.load(handle)
    payload = envelope["generation"]
    if source_kind == "derived":
        assert payload["capture_identity"] == capture.identity
        assert compact_json(payload["capture_identity"]) != compact_json(capture.identity)
    assert set(payload) == {"storage", "manifest", "capture_identity", "input_digest", "maximum_batches"}
    if corruption == "identity":
        payload["capture_identity"]["binding"] = {"snapshot_identity": "wrong"}
    elif corruption == "digest":
        payload["input_digest"] = "0" * 64
    elif corruption == "manifest":
        payload["manifest"]["generation_id"] = "0" * 64
    elif corruption == "budget":
        payload["maximum_batches"] = 1
    elif corruption == "missing":
        (capture.directory / "manifest.json").unlink()
    elif corruption == "location":
        with pytest.raises(ValueError, match="ARTIFACT_LOCATION"):
            api._write_news_projection_generation_artifact(tmp_path / "other.json.gz", generation, target=target)
        return
    elif corruption == "target":
        envelope["target"] = {**target, "origin": "https://wrong.example"}
    envelope["sha256"] = api._news_projection_payload_digest({
        "generation": payload, "target": target,
    })
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        json.dump(envelope, handle)
    if corruption:
        with pytest.raises(ValueError):
            api._read_news_projection_generation_artifact(path, expected_target=target)
    else:
        with pytest.raises(ValueError, match="TARGET_MISMATCH"):
            api._read_news_projection_generation_artifact(path)
        restored = api._read_news_projection_generation_artifact(path, expected_target=target)
        assert restored.manifest == generation.manifest
        assert restored.batch_items("detail", 8) == generation.batch_items("detail", 8)
        assert (capture.directory / "manifest.json").read_bytes() == manifest_before


def test_bootstrap_retained_pin_requires_current_budget_target_and_recoverable_source(tmp_path, monkeypatch):
    from scripts.maintenance import bootstrap_news_projection as bootstrap_owner
    pin_frozen_source_capture = bootstrap_owner.pin_frozen_source_capture
    capture = _capture(tmp_path)
    capture.advance(_page_reader([_source_record(i) for i in range(9)], []))
    capture.finalize_plan()
    arguments = {
        "capture": capture, "state_file": tmp_path / "candidate.json", "state_root": tmp_path,
        "origin": "https://candidate-aurum-signal-room.fixture.workers.dev", "max_cycles": 2,
    }
    generation = pin_frozen_source_capture(**arguments)
    artifact = tmp_path / "candidate-generation.json.gz"
    pinned = artifact.read_bytes()
    with pytest.raises(ValueError, match="REPLAY_WORK_BOUND"):
        pin_frozen_source_capture(**{**arguments, "max_cycles": 1})
    with pytest.raises(ValueError, match="TARGET_MISMATCH"):
        pin_frozen_source_capture(**{**arguments, "origin": "https://other-aurum-signal-room.fixture.workers.dev"})
    resumed = pin_frozen_source_capture(**{**arguments, "max_cycles": 3})
    assert resumed.manifest == generation.manifest
    assert artifact.read_bytes() == pinned
    seen = []
    def observed_sync(*args, **kwargs):
        seen.append(kwargs["frozen_generation"].manifest["generation_id"])
        raise bootstrap_owner.PayloadContractError("test boundary reached")
    monkeypatch.setattr(bootstrap_owner, "_sync_news", observed_sync)
    for candidate, origin, expected in (
        (generation, "https://other-aurum-signal-room.fixture.workers.dev", "TARGET_MISMATCH"),
        (capture.open_replay_generation(maximum_batches=8), arguments["origin"], "TARGET_MISMATCH"),
        (resumed, arguments["origin"], "test boundary reached"),
    ):
        with pytest.raises((ValueError, bootstrap_owner.PayloadContractError), match=expected):
            bootstrap_owner.bootstrap(
                base_config={}, origin=origin, token="fixture", state_file=arguments["state_file"],
                max_cycles=2, retry_seconds=0, frozen_generation=candidate, state_root=tmp_path,
            )
    assert seen == [generation.manifest["generation_id"]]
    arguments["state_file"].write_text(json.dumps({
        "projection_state": "REPLAYING", "generation_id": generation.manifest["generation_id"],
    }), encoding="utf-8")
    artifact.unlink()
    with pytest.raises(ValueError, match="explicit recovery"):
        pin_frozen_source_capture(**arguments)
    assert not artifact.exists()


def test_source_capture_rejects_identity_corruption_and_single_flight(tmp_path):
    capture = _capture(tmp_path)
    state = capture.advance(_page_reader([_source_record(0)], []))
    with capture._locked():
        with pytest.raises(OSError):
            _capture(tmp_path).advance(lambda _state: pytest.fail("second owner"))
    changed = NewsProjectionSourceCapture(
        capture.directory, binding={"snapshot_identity": "different"},
        watermark=capture.identity["watermark"], window_start=capture.identity["window_start"],
        epoch=capture.identity["epoch"],
    )
    with pytest.raises(ValueError, match="IDENTITY_MISMATCH"):
        changed.read()
    part = capture.directory / state["parts"][0]["name"]
    original = part.read_bytes()
    part.write_bytes(original.replace(b"frozen body", b"broken body"))
    assert part.stat().st_size == len(original)
    with pytest.raises(ValueError, match="PART_DIGEST_MISMATCH"):
        list(capture.records())
    assert hashlib.sha256(original).hexdigest() == state["parts"][0]["sha256"]


def _derived_reader_fixture(tmp_path, boundary="normal"):
    original = {"revision": "original-fixture", "inputs": {"scripts/runtime/run_dashboard_api.py": "a" * 64}}
    corrected = {"revision": "corrected-fixture", "inputs": {"scripts/runtime/run_dashboard_api.py": "b" * 64}}
    if boundary in {"historical-path", "relocated-path"}:
        original["inputs"] = {"scripts/run_dashboard_api.py": "a" * 64}
    if boundary == "historical-path":
        corrected["inputs"] = {"scripts/run_dashboard_api.py": "b" * 64}
    if boundary == "ambiguous-path":
        corrected["inputs"]["scripts/run_dashboard_api.py"] = "b" * 64
    values = {
        "binding": {"source_identity": original, "input_identity": {"fixture": "immutable"},
                    "snapshot_stat": {"fixture": "unchanged"}},
        "watermark": "2026-09-07T00:00:00+00:00", "window_start": "2026-07-09T00:00:00+00:00",
        "epoch": "fixture-epoch",
    }
    directory = tmp_path / "derived.capture"
    capture = NewsProjectionSourceCapture(directory, **values)
    records = [_source_record(number, withdrawal=number % 3 == 0) for number in range(131)]
    state = capture.advance(_page_reader(records, []))
    current = NewsProjectionSourceCapture(directory, **values, active_producer_identity=corrected)
    proof = {
        "state": "READER_PROOF_PASSED", "equivalent": True, "input_stat_unchanged": True,
        "mismatched_ordinals": [], "source_identity": original,
        "input_identity": values["binding"]["input_identity"], "target_file_sha256": "b" * 64,
        "expected_part": state["parts"][0], "actual_canonical_sha256": state["parts"][0]["sha256"],
        "news_result_reads": 128, "producer_sha256": "c" * 64,
        "base_function_ast_sha256": "d" * 64, "target_function_ast_sha256": "e" * 64,
        "replacement_scope": "_news_reader_rows only; all other API AST nodes equal",
    }
    inputs = {**values["binding"], "watermark": values["watermark"], "epoch": values["epoch"]}
    transition = {
        "proof_report": compact_json(proof).encode(), "proof_input": compact_json(inputs).encode(),
        "review_identity": {"target_api_sha256": "b" * 64, "record_sha256": "f" * 64},
    }
    return capture, current, records, state, transition


@pytest.mark.parametrize("boundary", ("normal", "wrong-clock", "wrong-source", "wrong-result",
                                     "wrong-review", "wrong-producer", "prefix", "suffix", "v1-marker",
                                     "reordered-binding", "identity-order", "historical-path", "relocated-path", "ambiguous-path"))
def test_source_capture_derived_reader_preserves_prefix_and_fences_other_producers(tmp_path, boundary):
    capture, current, records, original, transition = _derived_reader_fixture(tmp_path, boundary)
    if boundary == "ambiguous-path":
        with pytest.raises(ValueError, match="API_IDENTITY_AMBIGUOUS"):
            current.derive_reader_segment(**transition)
        return
    if boundary == "reordered-binding":
        current.identity = json.loads(json.dumps(current.identity, sort_keys=True))
    prefix_path = capture.directory / original["parts"][0]["name"]
    prefix_bytes = prefix_path.read_bytes()
    manifest_before = (capture.directory / "manifest.json").read_bytes()
    if boundary in {"wrong-clock", "wrong-source", "wrong-result", "wrong-review"}:
        if boundary == "wrong-review":
            transition["review_identity"]["target_api_sha256"] = "0" * 64
        elif boundary == "wrong-clock":
            inputs = json.loads(transition["proof_input"])
            inputs["watermark"] = "2026-09-06T00:00:00+00:00"
            transition["proof_input"] = compact_json(inputs).encode()
        else:
            proof = json.loads(transition["proof_report"])
            proof["source_identity" if boundary == "wrong-source" else "equivalent"] = False
            transition["proof_report"] = compact_json(proof).encode()
        with pytest.raises(ValueError, match="READER_PROOF_INVALID"):
            current.derive_reader_segment(**transition)
        assert (capture.directory / "manifest.json").read_bytes() == manifest_before
        return
    if boundary == "v1-marker":
        original["reader_segment"] = {"unexpected": True}
        capture._save(original)
        with pytest.raises(ValueError, match="READER_SEGMENT_INVALID"):
            capture.advance(lambda _state: pytest.fail("invalid v1 queried"))
        return
    derived = current.derive_reader_segment(**transition)
    assert derived["identity"] == original["identity"]
    assert derived["parts"] == original["parts"]
    assert prefix_path.read_bytes() == prefix_bytes
    with pytest.raises(ValueError, match="ACTIVE_PRODUCER_MISMATCH"):
        capture.advance(lambda _state: pytest.fail("original producer queried"))
    if boundary == "wrong-producer":
        current.active_producer_identity = {"revision": "not-the-admitted-producer"}
        with pytest.raises(ValueError, match="ACTIVE_PRODUCER_MISMATCH"):
            current.advance(lambda _state: pytest.fail("wrong producer queried"))
        return
    calls = []
    completed = current.advance(_page_reader(records, calls))
    assert calls == [128]
    assert completed["source_count"] == 131
    assert current.derive_reader_segment(**transition) == completed
    assert completed["parts"][0] == original["parts"][0]
    assert prefix_path.read_bytes() == prefix_bytes
    if boundary in {"prefix", "suffix", "identity-order"}:
        if boundary == "prefix":
            completed["reader_segment"]["prefix"]["cursor"] = None
        elif boundary == "identity-order":
            completed["identity"] = dict(reversed(list(completed["identity"].items())))
        else:
            completed["parts"][-1]["reader_segment_sha256"] = "0" * 64
        current._save(completed)
        with pytest.raises(ValueError, match="READER_(PREFIX|SUFFIX)_INVALID"):
            current.finalize_plan()
        return
    planned = current.finalize_plan(producer_identity={"planner": "actual-current-fixture"})
    expected = build_news_projection_generation(
        [_source_row(number) for number in range(131) if number % 3],
        [_source_row(number, withdrawal=True) for number in range(131) if not number % 3],
        watermark=current.identity["watermark"], window_start=current.identity["window_start"],
    )
    assert planned["source_plan"]["manifest"] == expected.manifest
    assert planned["source_plan"]["producer_identity"] == {"planner": "actual-current-fixture"}
    assert list(current.records()) == records


@pytest.mark.parametrize("failure", ("before", "after"))
def test_source_capture_reader_transition_reconciles_atomic_storage(tmp_path, monkeypatch, failure):
    capture, current, _records, original, transition = _derived_reader_fixture(tmp_path)
    original_atomic = current._atomic
    injected = False

    def interrupt(name, raw):
        nonlocal injected
        if not injected and name == "manifest.json":
            injected = True
            if failure == "after":
                original_atomic(name, raw)
            raise OSError("reader transition publication interruption")
        return original_atomic(name, raw)

    monkeypatch.setattr(current, "_atomic", interrupt)
    with pytest.raises(OSError, match="publication interruption"):
        current.derive_reader_segment(**transition)
    actual = current.read()
    assert actual["parts"] == original["parts"]
    assert actual["last_failure"] == "NEWS_SOURCE_CAPTURE_STORAGE_WRITE_FAILED"
    assert actual["retry_not_before"] > 0
    assert ("reader_segment" in actual) == (failure == "after")
    assert current.derive_reader_segment(**transition) == actual


@pytest.mark.parametrize("bound", ("row", "total", "metadata"))
def test_source_capture_storage_bounds_never_publish_a_partial_generation(tmp_path, monkeypatch, bound):
    import xauusd_forecaster.news_projection as module
    capture = _capture(tmp_path)
    records = [_source_record(0), _source_record(1)]
    if bound == "row":
        monkeypatch.setattr(module, "NEWS_SOURCE_CAPTURE_PART_BYTES", 10)
    elif bound == "total":
        monkeypatch.setattr(module, "NEWS_SOURCE_CAPTURE_TOTAL_BYTES", 10)
    else:
        monkeypatch.setattr(module, "NEWS_SOURCE_CAPTURE_METADATA_BYTES", 10)
    error_type = NewsSourceCaptureStorageUnresolved if bound == "metadata" else ValueError
    with pytest.raises(error_type, match="STORAGE_UNRESOLVED" if bound == "metadata" else "_BOUND"):
        capture.advance(_page_reader(records, []))
    assert not list(tmp_path.glob("*.json.gz"))
    if bound != "metadata":
        state = capture.read()
        assert state["state"] == "BUILDING"
        assert state["source_count"] == 0
        assert state["parts"] == []


def test_source_capture_unwritable_failure_state_is_not_retryable(tmp_path, monkeypatch):
    capture = _capture(tmp_path)
    calls = []

    def unavailable(*_args):
        raise OSError("injected full storage")

    monkeypatch.setattr(capture, "_atomic", unavailable)
    with pytest.raises(NewsSourceCaptureStorageUnresolved, match="automatic retry is prohibited") as caught:
        capture.advance(_page_reader([_source_record(0)], calls))
    assert caught.value.retryable is False
    with pytest.raises(NewsSourceCaptureStorageUnresolved):
        capture.advance(lambda _state: pytest.fail("unresolved storage cannot read source"))
    assert calls == []  # The identity must be durable before source execution.


def test_source_capture_accounts_for_uncommitted_disk_bytes(tmp_path, monkeypatch):
    import xauusd_forecaster.news_projection as module
    capture = _capture(tmp_path)
    capture.advance(_page_reader([_source_record(i) for i in range(129)], []))
    state = capture.read()
    orphan = capture.directory / ".capture-interrupted"
    orphan.write_bytes(b"x" * 1024)
    occupied = sum(path.stat().st_size for path in capture.directory.iterdir())
    monkeypatch.setattr(module, "NEWS_SOURCE_CAPTURE_DISK_BYTES", occupied)
    with pytest.raises(NewsSourceCaptureStorageUnresolved):
        capture.advance(_page_reader([_source_record(i) for i in range(129)], []))
    assert orphan.read_bytes() == b"x" * 1024
    assert sum(path.stat().st_size for path in capture.directory.iterdir()) == occupied
    assert capture.read()["source_count"] == state["source_count"] == 128


def test_source_capture_declared_directory_rejects_symlink(tmp_path):
    actual = tmp_path / "actual"
    actual.mkdir()
    redirected = tmp_path / "candidate-generation.capture"
    try:
        redirected.symlink_to(actual, target_is_directory=True)
    except OSError:
        if os.name != "nt":
            raise
        command = Path(os.environ["SystemRoot"]) / "System32" / "cmd.exe"
        subprocess.run(
            [str(command), "/d", "/c", "mklink", "/J", str(redirected), str(actual)],
            check=True, capture_output=True, timeout=5,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    try:
        with pytest.raises(ValueError, match="REPARSE_DENIED"):
            _capture(tmp_path)
    finally:
        if os.name == "nt":
            os.rmdir(redirected)
        else:
            redirected.unlink()
    assert not list(actual.iterdir())


@pytest.mark.parametrize("case", ("empty", "withdrawals", "unicode-byte-batches", "item-batches"))
def test_capture_global_plan_matches_original_generation_across_parts(tmp_path, monkeypatch, case):
    import xauusd_forecaster.news_projection as module
    rows = [
        _source_row(i, withdrawal=(case == "withdrawals" or i % 11 == 0),
                    body=('ä¸­\\"\n' * 20_000 if case == "unicode-byte-batches" else "frozen"))
        for i in range(0 if case == "empty" else 21)
    ]
    for row in rows:
        row["numbers"] = [None, True, 1, 1.0, -0.0]
    records = [news_source_capture_record(row, [
        "2026-09-07T00:00:00+00:00", row["source"], row["source_item_id"], row["revision_number"],
    ]) for row in rows]
    if records:
        # Force >8 raw-cursor parts; final ordering is deliberately different.
        max_record = max(len((compact_json(record) + "\n").encode()) for record in records)
        monkeypatch.setattr(module, "NEWS_SOURCE_CAPTURE_PART_BYTES", max_record + 1)
    capture = _capture(tmp_path)
    state = capture.read()
    while state["state"] != "SOURCE_COMPLETE":
        state = capture.advance(_page_reader(records, []))
    expected = build_news_projection_generation(
        [row for row in rows if row["xauusd_relevance"] != "IRRELEVANT"],
        [row for row in rows if row["xauusd_relevance"] == "IRRELEVANT"],
        window_start=capture.identity["window_start"], watermark=capture.identity["watermark"],
    )
    producer_identity = {"revision": "derived-planner-test", "capture_revision": "unchanged"}
    result = capture.finalize_plan(producer_identity=producer_identity)
    assert result["source_plan"]["manifest"] == expected.manifest
    plan = result["source_plan"]
    assert plan["producer_identity"] == producer_identity
    assert result["identity"] == state["identity"]
    assert len(plan["row_locations"]) == len(expected.detail_rows)
    reader = capture.open_plan_reader()
    assert not isinstance(reader, NewsProjectionGeneration)
    assert not hasattr(reader, "manifest")  # This is inspection, not admission.
    monkeypatch.setattr(capture, "read", lambda: pytest.fail("batch reloaded complete metadata"))
    monkeypatch.setattr(capture, "_record_locations", lambda: pytest.fail("batch rescanned retained parts"))
    assert plan["minimum_sync_cycles"] == max(1, (len(expected.detail_batches) + len(expected.index_batches) + 3) // 4)
    for kind, batches in (("detail", expected.detail_batches), ("index", expected.index_batches)):
        offset = 0
        assert len(plan["batches"][kind]) == len(batches)
        for actual, batch in zip(plan["batches"][kind], batches, strict=True):
            assert actual == {
                "offset": offset, "count": len(batch),
                "bytes": len(compact_json(batch).encode()),
                "payload_hash": receipt_payload_hash(batch),
            }
            offset += len(batch)
        # Seek the final batch first, then earlier batches. No prefix payloads
        # or entire-part reads may be hidden behind a late batch request.
        for actual, batch in reversed(list(zip(plan["batches"][kind], batches, strict=True))):
            before = dict(reader.metrics)
            assert reader.batch_items(kind, actual["offset"]) == list(batch)
            selected = plan["row_locations"][actual["offset"]:actual["offset"] + actual["count"]]
            assert reader.metrics["record_read_bytes"] - before["record_read_bytes"] == sum(row[4] for row in selected)
            assert reader.metrics["file_opens"] - before["file_opens"] == len(batch)
        assert reader.batch_items(kind, offset) == []
    metrics = plan["metrics"]
    assert metrics["part_scan_bytes"] == state["canonical_bytes"]
    assert metrics["record_read_bytes"] <= 2 * state["canonical_bytes"]
    assert metrics["sampled_rss_max_bytes"] < 512 * 1024 * 1024
    assert metrics["metadata_bytes"] < 32 * 1024 * 1024
    assert _capture(tmp_path).finalize_plan() == result
    assert not isinstance(result, NewsProjectionGeneration)
    assert result["admission"] == "CAPACITY_REVIEW_REQUIRED"
    assert not list(tmp_path.glob("*.json.gz"))


@pytest.mark.parametrize("corruption", (
    "missing-index", "source-binding", "part", "offset", "length", "order",
    "line-digest", "batch-digest", "same-size-content",
))
def test_capture_direct_batch_reader_rejects_corruption_without_source_rebuild(tmp_path, monkeypatch, corruption):
    capture = _capture(tmp_path)
    capture.advance(_page_reader([_source_record(i) for i in range(3)], []))
    capture.finalize_plan()
    state = capture.read()
    plan = state["source_plan"]
    locations = plan["row_locations"]
    if corruption == "missing-index":
        del plan["row_locations"]
    elif corruption == "source-binding":
        plan["input_digest"] = "0" * 64
    elif corruption == "part":
        locations[0][2] = "part-99999999-" + "0" * 64 + ".jsonl"
    elif corruption == "offset":
        locations[0][3] = -1
    elif corruption == "length":
        locations[0][4] = state["parts"][0]["canonical_bytes"] + 1
    elif corruption == "order":
        locations.reverse()
    elif corruption == "line-digest":
        locations[0][5] = "0" * 64
    elif corruption == "batch-digest":
        plan["batches"]["detail"][0]["payload_hash"] = "0" * 64
    capture._save(state)  # Recomputed envelope cannot excuse a bad semantic index.
    monkeypatch.setattr(capture, "_record_locations", lambda: pytest.fail("bad index rebuilt from source"))
    if corruption in {"line-digest", "batch-digest", "same-size-content"}:
        reader = capture.open_plan_reader()
        if corruption == "same-size-content":
            path = capture.directory / locations[0][2]
            raw = path.read_bytes()
            changed = bytearray(raw)
            changed[locations[0][3] + 1] ^= 1
            path.write_bytes(changed)
            assert len(changed) == len(raw)
        monkeypatch.setattr(capture, "read", lambda: pytest.fail("batch reread complete manifest"))
        with pytest.raises(ValueError, match="DIGEST_MISMATCH"):
            reader.batch_items("detail", 0)
    else:
        with pytest.raises(ValueError, match="PLAN_LOCATOR"):
            capture.open_plan_reader()


def test_capture_plan_failure_retains_capture_without_automatic_loop(tmp_path, monkeypatch):
    import xauusd_forecaster.news_projection as module
    capture = _capture(tmp_path)
    complete = capture.advance(_page_reader([_source_record(0)], []))
    monkeypatch.setattr(module, "news_projection_capture_rss", lambda: 513 * 1024 * 1024)
    with pytest.raises(ValueError, match="PLAN_MEMORY_BOUND"):
        capture.finalize_plan()
    failed = capture.read()
    assert failed["cursor"] == complete["cursor"]
    assert failed["parts"] == complete["parts"]
    assert "source_plan" not in failed
    monkeypatch.setattr(capture, "_record_locations", lambda: pytest.fail("failed plan reran"))
    with pytest.raises(ValueError, match="PLAN_RECOVERY_REQUIRED"):
        capture.finalize_plan()


@pytest.mark.parametrize("failure_point", (
    "before_attempt", "before_plan_commit", "after_plan_commit", "interrupted_hash",
))
def test_capture_plan_publication_and_restart_never_repeat_hash_unbounded(tmp_path, monkeypatch, failure_point):
    import xauusd_forecaster.news_projection as module
    capture = _capture(tmp_path)
    complete = capture.advance(_page_reader([_source_record(0)], []))
    original = capture._atomic
    interrupted = False

    def interrupt(name, raw):
        nonlocal interrupted
        state = json.loads(raw)["capture"] if name == "manifest.json" else {}
        target = (state.get("plan_in_progress") and "source_plan" not in state
                  if failure_point == "before_attempt" else "source_plan" in state)
        if not interrupted and target and failure_point != "interrupted_hash":
            interrupted = True
            if failure_point == "after_plan_commit":
                original(name, raw)
            raise OSError("injected plan publication failure")
        return original(name, raw)

    monkeypatch.setattr(capture, "_atomic", interrupt)
    if failure_point == "interrupted_hash":
        def interrupted_read():
            raise KeyboardInterrupt("injected process interruption after durable attempt")
        monkeypatch.setattr(capture, "_record_locations", interrupted_read)
    with pytest.raises(KeyboardInterrupt if failure_point == "interrupted_hash" else OSError):
        capture.finalize_plan()
    restored = _capture(tmp_path)
    state = restored.read()
    assert state["parts"] == complete["parts"]
    assert state["cursor"] == complete["cursor"]
    monkeypatch.setattr(restored, "_record_locations", lambda: pytest.fail("restart repeated hash"))
    if failure_point in {"before_attempt", "after_plan_commit"}:
        assert restored.finalize_plan() == state
        assert ("source_plan" in state) == (failure_point == "after_plan_commit")
    else:
        with pytest.raises(ValueError, match="PLAN_RECOVERY_REQUIRED"):
            restored.finalize_plan()
    monkeypatch.setattr(module.time, "time", lambda: state["retry_not_before"] + 1)
    if failure_point == "before_attempt":
        # The failed attempt was never published and performed no hash work;
        # only this case may recover naturally after its durable backoff.
        assert "source_plan" in _capture(tmp_path).finalize_plan()
    elif failure_point != "after_plan_commit":
        with pytest.raises(ValueError, match="PLAN_RECOVERY_REQUIRED"):
            restored.finalize_plan()
