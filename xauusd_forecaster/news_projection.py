"""Deterministic, bounded generation materialization for the 60-day news mirror."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import stat
import struct
import sys
import tempfile
import time
from contextlib import contextmanager
from collections import OrderedDict
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Callable, Iterable, Iterator

NEWS_PROJECTION_CONTRACT_VERSION = "news-projection-generation-v4"
NEWS_READER_WINDOW_DAYS = 60
NEWS_MIRROR_CONTRACT_VERSION = NEWS_PROJECTION_CONTRACT_VERSION
NEWS_PROJECTION_MAX_ITEMS = 10_000
NEWS_INDEX_BATCH_ITEMS = 4
NEWS_DETAIL_BATCH_ITEMS = 8
NEWS_DETAIL_BATCH_LIMIT_BYTES = 400_000
NEWS_INDEX_BATCH_LIMIT_BYTES = 100_000
EMPTY_RECEIPT_DIGEST = hashlib.sha256(b"").hexdigest()
NEWS_SOURCE_CAPTURE_VERSION = "news-projection-source-capture-v1"
NEWS_SOURCE_CAPTURE_DERIVED_VERSION = "news-projection-source-capture-v2"
NEWS_SOURCE_CAPTURE_PAGE_ITEMS = 128
NEWS_SOURCE_CAPTURE_PART_BYTES = 4 * 1024 * 1024
NEWS_SOURCE_CAPTURE_TOTAL_BYTES = 256 * 1024 * 1024
NEWS_SOURCE_CAPTURE_METADATA_BYTES = 32 * 1024 * 1024
NEWS_SOURCE_CAPTURE_DISK_BYTES = (
    NEWS_SOURCE_CAPTURE_TOTAL_BYTES + 2 * NEWS_SOURCE_CAPTURE_METADATA_BYTES
    + NEWS_SOURCE_CAPTURE_PART_BYTES
)


def _source_api_digest(identity: dict) -> str | None:
    """Read retained source locators without rewriting their signed identity."""
    inputs = identity.get("inputs", {})
    keys = ("scripts/runtime/run_dashboard_api.py", "scripts/run_dashboard_api.py")
    present = [inputs[key] for key in keys if key in inputs]
    if len(present) > 1:
        raise ValueError("NEWS_SOURCE_CAPTURE_API_IDENTITY_AMBIGUOUS")
    return present[0] if present else None


def news_projection_capture_rss() -> int:
    """Observed process memory; not a claim of OS-enforced memory isolation."""
    if os.name != "nt":
        import resource
        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return int(peak if sys.platform == "darwin" else peak * 1024)
    import ctypes
    from ctypes import wintypes

    class MemoryCounters(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
            *[(name, ctypes.c_size_t) for name in (
                "PeakWorkingSetSize", "WorkingSetSize", "QuotaPeakPagedPoolUsage",
                "QuotaPagedPoolUsage", "QuotaPeakNonPagedPoolUsage",
                "QuotaNonPagedPoolUsage", "PagefileUsage", "PeakPagefileUsage",
            )],
        ]
    counters = MemoryCounters()
    counters.cb = ctypes.sizeof(counters)
    get_memory = ctypes.windll.psapi.GetProcessMemoryInfo
    get_memory.argtypes = [wintypes.HANDLE, ctypes.POINTER(MemoryCounters), wintypes.DWORD]
    if not get_memory(wintypes.HANDLE(-1), ctypes.byref(counters), counters.cb):
        raise ValueError("NEWS_SOURCE_CAPTURE_MEMORY_OBSERVATION_UNAVAILABLE")
    return int(counters.WorkingSetSize)

NEWS_INDEX_FIELDS = (
    "category", "source", "source_item_id", "revision_number", "cluster_id",
    "source_published_time", "collector_first_seen_time", "headline",
    "content_characters", "content_status", "content_fetch_status",
    "content_error_type", "annotation_status", "annotation_reason_code",
    "annotation_reason", "model_visibility", "parsed_at", "emerging_topic_zh",
    "impact_status", "impact_class", "impact_event_state",
    "impact_update_type", "impact_assessed_at", "impact_expires_at",
    "impact_event_at", "impact_clock_source", "impact_reason_zh",
    "mirror_updated_at", "syndicated_source_count", "source_text_incomplete",
)
NEWS_PROJECTION_IMPACT_CLOCK_FIELDS = (
    "impact_event_at", "impact_available_at", "impact_expires_at",
)


def canonicalize_news_projection_impact_clocks(item: dict) -> dict:
    """Normalize derived impact clocks at the News projection boundary."""
    for field in NEWS_PROJECTION_IMPACT_CLOCK_FIELDS:
        value = item.get(field)
        if value is None:
            continue
        observed = (
            value if isinstance(value, datetime) else datetime.fromisoformat(value)
        )
        if observed.tzinfo is None or observed.utcoffset() is None:
            raise ValueError(f"{field} must include an explicit UTC offset")
        item[field] = observed.astimezone(UTC).isoformat(timespec="microseconds")
    return item


def compact_json(value: object, *, sort_keys: bool = False) -> str:
    return json.dumps(
        value, ensure_ascii=False, allow_nan=False, separators=(",", ":"),
        sort_keys=sort_keys,
    )


def sha256_json(value: object, *, sort_keys: bool = False) -> str:
    return hashlib.sha256(
        compact_json(value, sort_keys=sort_keys).encode("utf-8")
    ).hexdigest()


def canonical_receipt_bytes(value: object) -> bytes:
    """Encode one JSON value identically across Python and JavaScript runtimes."""
    if value is None:
        return b"n;"
    if value is True:
        return b"t;"
    if value is False:
        return b"f;"
    if isinstance(value, (int, float)):
        if isinstance(value, int) and abs(value) > 9_007_199_254_740_991:
            raise ValueError("receipt integer exceeds the JSON safe-integer range")
        number = float(value)
        if not math.isfinite(number):
            raise ValueError("receipt number must be finite")
        if number == 0:
            number = 0.0
        return b"d" + struct.pack(">d", number).hex().encode("ascii") + b";"
    if isinstance(value, str):
        encoded = value.encode("utf-8")
        return b"s" + str(len(encoded)).encode("ascii") + b":" + encoded + b";"
    if isinstance(value, (list, tuple)):
        return (
            b"a" + str(len(value)).encode("ascii") + b":"
            + b"".join(canonical_receipt_bytes(item) for item in value)
            + b";"
        )
    if isinstance(value, dict):
        if any(not isinstance(key, str) for key in value):
            raise ValueError("receipt object keys must be strings")
        keys = sorted(value, key=lambda key: key.encode("utf-8"))
        return (
            b"o" + str(len(keys)).encode("ascii") + b":"
            + b"".join(
                canonical_receipt_bytes(key) + canonical_receipt_bytes(value[key])
                for key in keys
            )
            + b";"
        )
    raise ValueError(f"unsupported receipt value type: {type(value).__name__}")


def receipt_payload_hash(value: object) -> str:
    return hashlib.sha256(canonical_receipt_bytes(value)).hexdigest()


def stable_news_key(row: dict) -> str:
    identity = "\0".join((
        str(row.get("source", "")), str(row.get("source_item_id", "")),
        str(row.get("revision_number", "")),
    ))
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def content_addressed_detail_key(row: dict, detail_payload: dict) -> str:
    """Bind derived detail identity to both source revision and exact content."""
    return sha256_json({
        "source_identity": stable_news_key(row),
        "detail_hash": sha256_json(detail_payload),
    }, sort_keys=True)


def split_news_rows(rows: Iterable[dict]) -> tuple[list[dict], list[dict]]:
    """Return deterministically ordered index and detail projections."""
    projected: list[tuple[str, dict, dict]] = []
    for raw in rows:
        row = dict(raw)
        detail_payload = {
            key: value for key, value in row.items() if key not in NEWS_INDEX_FIELDS
        }
        detail_key = content_addressed_detail_key(row, detail_payload)
        index = {key: row.get(key) for key in NEWS_INDEX_FIELDS
                 if key not in {"syndicated_source_count", "source_text_incomplete"}
                 or key in row}
        index["cluster_id"] = str(index.get("cluster_id") or detail_key)
        index.update({
            "detail_key": detail_key,
            "mirror_contract": NEWS_MIRROR_CONTRACT_VERSION,
        })
        detail = {
            "detail_key": detail_key,
            "detail_hash": sha256_json(detail_payload),
            "payload": detail_payload,
        }
        projected.append((detail_key, index, detail))
    projected.sort(key=lambda item: item[0])
    return (
        [item[1] for item in projected],
        [item[2] for item in projected],
    )


def bounded_batches(
    rows: list[dict], limit_bytes: int, *, max_items: int,
) -> list[list[dict]]:
    batches: list[list[dict]] = []
    current: list[dict] = []
    for row in rows:
        candidate = [*current, row]
        size = len(compact_json(candidate).encode("utf-8"))
        if current and (size > limit_bytes or len(candidate) > max_items):
            batches.append(current)
            current = [row]
        else:
            current = candidate
        if len(compact_json(current).encode("utf-8")) > limit_bytes:
            raise ValueError("one news projection row exceeds its transport bound")
    if current:
        batches.append(current)
    return batches


def receipt_digest(
    detail_batches: list[list[dict]], index_batches: list[list[dict]],
) -> str:
    digest = EMPTY_RECEIPT_DIGEST
    for kind, batches in (("detail", detail_batches), ("index", index_batches)):
        offset = 0
        for batch in batches:
            payload_hash = receipt_payload_hash(batch)
            digest = hashlib.sha256(
                f"{digest}\n{kind}|{offset}|{len(batch)}|{payload_hash}".encode("utf-8")
            ).hexdigest()
            offset += len(batch)
    return digest


@dataclass(frozen=True)
class NewsProjectionGeneration:
    manifest: dict
    index_rows: tuple[dict, ...]
    detail_rows: tuple[dict, ...]
    index_batches: tuple[tuple[dict, ...], ...]
    detail_batches: tuple[tuple[dict, ...], ...]

    def batch_items(self, kind: str, offset: int) -> list[dict]:
        batches = self.detail_batches if kind == "detail" else self.index_batches if kind == "index" else None
        if batches is None or type(offset) is not int or offset < 0:
            raise ValueError("invalid news projection batch request")
        cursor = 0
        for batch in batches:
            if cursor == offset:
                return list(batch)
            cursor += len(batch)
        if cursor == offset:
            return []
        raise ValueError("news projection offset is not a batch boundary")


def build_news_projection_generation(
    rows: list[dict], withdrawals: list[dict], *, window_start: str, watermark: str,
) -> NewsProjectionGeneration:
    if len(rows) > NEWS_PROJECTION_MAX_ITEMS:
        raise ValueError("news projection exceeds the 10,000-row generation bound")
    index_rows, detail_rows = split_news_rows(rows)
    withdrawal_keys = sorted({stable_news_key(row) for row in withdrawals})
    if len(withdrawal_keys) > NEWS_PROJECTION_MAX_ITEMS:
        raise ValueError("news projection exceeds the 10,000-withdrawal generation bound")
    source_digest = sha256_json({
        "index": index_rows, "details": detail_rows,
        "withdrawal_keys": withdrawal_keys,
    }, sort_keys=True)
    detail_batches = bounded_batches(
        detail_rows, NEWS_DETAIL_BATCH_LIMIT_BYTES,
        max_items=NEWS_DETAIL_BATCH_ITEMS,
    )
    index_batches = bounded_batches(
        index_rows, NEWS_INDEX_BATCH_LIMIT_BYTES,
        max_items=NEWS_INDEX_BATCH_ITEMS,
    )
    manifest = _news_projection_manifest(
        window_start=window_start, watermark=watermark, source_digest=source_digest,
        item_count=len(index_rows), withdrawal_count=len(withdrawal_keys),
        expected_receipt_digest=receipt_digest(detail_batches, index_batches),
    )
    return NewsProjectionGeneration(
        manifest=manifest,
        index_rows=tuple(index_rows), detail_rows=tuple(detail_rows),
        index_batches=tuple(tuple(batch) for batch in index_batches),
        detail_batches=tuple(tuple(batch) for batch in detail_batches),
    )


def _news_projection_manifest(
    *, window_start: str, watermark: str, source_digest: str,
    item_count: int, withdrawal_count: int, expected_receipt_digest: str,
) -> dict:
    snapshot_id = sha256_json({
        "contract_version": NEWS_PROJECTION_CONTRACT_VERSION,
        "window_start": window_start, "watermark": watermark,
        "source_digest": source_digest,
    }, sort_keys=True)
    generation_id = sha256_json({
        "snapshot_id": snapshot_id,
        "contract_version": NEWS_PROJECTION_CONTRACT_VERSION,
    }, sort_keys=True)
    return {
        "generation_id": generation_id,
        "snapshot_id": snapshot_id,
        "contract_version": NEWS_PROJECTION_CONTRACT_VERSION,
        "window_start": window_start,
        "watermark": watermark,
        "expected_index_count": item_count,
        "expected_detail_count": item_count,
        "withdrawal_count": withdrawal_count,
        "source_digest": source_digest,
        "expected_receipt_digest": expected_receipt_digest,
    }


@dataclass(frozen=True)
class NewsSourceCapturePage:
    """One source-key page; records are stepped from the live SQLite cursor."""

    records: Iterator[dict]
    has_more: bool
    metrics: dict = field(default_factory=dict)


class NewsSourceCaptureStorageUnresolved(RuntimeError):
    """No durable retry decision can be written; the caller must stop retrying."""

    retryable = False


def news_source_capture_record(row: dict, cursor: list) -> dict:
    """Preserve the original detail JSON order, never re-hash sorted payloads."""
    identity = (row["source"], row["source_item_id"], row["revision_number"])
    if tuple(cursor[1:]) != identity:
        raise ValueError("NEWS_SOURCE_CAPTURE_IDENTITY_MISMATCH")
    if row.get("xauusd_relevance") == "IRRELEVANT":
        return {"cursor": cursor, "withdrawal": stable_news_key(row)}
    index, detail = split_news_rows([row])
    return {"cursor": cursor, "index": index[0], "detail": detail[0]}


class NewsProjectionSourceCapture:
    """Restartable local source artifact, deliberately not a replay generation.

    The caller owns the private artifact directory and a completed immutable
    SQLite snapshot. Binding mismatch is not permission to start from live data.
    A schema-1 published generation is never written by this capture owner.
    """

    def __init__(self, directory: Path, *, binding: dict, watermark: str,
                 window_start: str, epoch: str,
                 active_producer_identity: dict | None = None) -> None:
        self.directory = Path(os.path.abspath(directory))
        self._path("manifest.json")
        identity = {
            "contract_version": NEWS_PROJECTION_CONTRACT_VERSION,
            "binding": binding, "watermark": watermark,
            "window_start": window_start, "epoch": epoch,
        }
        if len(compact_json(identity).encode("utf-8")) > 64 * 1024:
            raise ValueError("NEWS_SOURCE_CAPTURE_IDENTITY_TOO_LARGE")
        for value in (watermark, window_start):
            stamp = datetime.fromisoformat(value)
            if stamp.utcoffset() is None:
                raise ValueError("NEWS_SOURCE_CAPTURE_TIME_INVALID")
        if not binding or not epoch:
            raise ValueError("NEWS_SOURCE_CAPTURE_IDENTITY_REQUIRED")
        self.identity = identity
        self.active_producer_identity = active_producer_identity
        self._storage_unresolved = False

    def _path(self, name: str) -> Path:
        if not re.fullmatch(r"manifest\.json|owner\.lock|part-[0-9]{8}-[0-9a-f]{64}\.jsonl", name):
            raise ValueError("NEWS_SOURCE_CAPTURE_PATH_INVALID")
        path = self.directory / name
        # Private-directory ownership remains a precondition. Check every use
        # for accidental link/reparse redirection, not just constructor input.
        for ancestor in (path, *path.parents):
            try:
                metadata = ancestor.lstat()
            except FileNotFoundError:
                continue
            if (stat.S_ISLNK(metadata.st_mode)
                    or getattr(metadata, "st_file_attributes", 0) & 0x400):
                raise ValueError("NEWS_SOURCE_CAPTURE_REPARSE_DENIED")
        return path

    @contextmanager
    def _locked(self):
        self._path("owner.lock")
        self.directory.mkdir(parents=True, exist_ok=True)
        with self._path("owner.lock").open("a+b") as stream:
            stream.seek(0, os.SEEK_END)
            if stream.tell() == 0:
                stream.write(b"0")
                stream.flush()
            stream.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            try:
                yield
            finally:
                stream.seek(0)
                if os.name == "nt":
                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)

    def _atomic(self, name: str, raw: bytes) -> None:
        destination = self._path(name)
        if name.startswith("part-") and destination.exists():
            with destination.open("rb") as handle:
                if handle.read(NEWS_SOURCE_CAPTURE_PART_BYTES + 1) != raw:
                    raise ValueError("NEWS_SOURCE_CAPTURE_IMMUTABLE_PART_CONTRADICTION")
            return
        # Include orphan parts and interrupted temporary writes, not merely the
        # manifest's accepted byte counter. Repeated crashes cannot accumulate
        # unlimited bytes outside that counter. No unknown orphan is deleted.
        used = 0
        started = time.monotonic()
        for number, entry in enumerate(self.directory.iterdir()):
            if number >= NEWS_SOURCE_CAPTURE_METADATA_BYTES // 128 or time.monotonic() - started > 15:
                raise ValueError("NEWS_SOURCE_CAPTURE_DIRECTORY_BOUND")
            if not re.fullmatch(
                r"manifest\.json|owner\.lock|part-[0-9]{8}-[0-9a-f]{64}\.jsonl|\.capture-[A-Za-z0-9_-]{1,64}",
                entry.name,
            ):
                raise ValueError("NEWS_SOURCE_CAPTURE_UNEXPECTED_FILE")
            metadata = entry.lstat()
            if (not stat.S_ISREG(metadata.st_mode)
                    or getattr(metadata, "st_file_attributes", 0) & 0x400):
                raise ValueError("NEWS_SOURCE_CAPTURE_REPARSE_DENIED")
            used += metadata.st_size
            if used + len(raw) > NEWS_SOURCE_CAPTURE_DISK_BYTES:
                raise ValueError("NEWS_SOURCE_CAPTURE_DISK_BOUND")
        temporary: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb", prefix=".capture-", dir=destination.parent,
                delete=False,
            ) as handle:
                temporary = handle.name
                handle.write(raw)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self._path(name))
            temporary = None
            with self._path(name).open("rb") as handle:
                if handle.read(len(raw) + 1) != raw:
                    raise ValueError("NEWS_SOURCE_CAPTURE_WRITE_VERIFICATION_FAILED")
        finally:
            if temporary is not None:
                Path(temporary).unlink(missing_ok=True)

    def _save(self, state: dict) -> None:
        envelope = {"sha256": sha256_json(state), "capture": state}
        raw = compact_json(envelope).encode("utf-8")
        if len(raw) > NEWS_SOURCE_CAPTURE_METADATA_BYTES:
            raise ValueError("NEWS_SOURCE_CAPTURE_METADATA_BOUND")
        self._atomic("manifest.json", raw)

    def _publication_failed(self, error: Exception) -> None:
        # A replace may have committed before read-back or the caller failed.
        # Reconcile actual bytes; never write a remembered pre-commit prefix.
        try:
            committed = self.read()
            failed = {
                **committed, "last_failure": "NEWS_SOURCE_CAPTURE_STORAGE_WRITE_FAILED",
                "retry_not_before": time.time() + 30,
            }
            self._save(failed)
        except Exception as storage_error:
            self._storage_unresolved = True
            raise NewsSourceCaptureStorageUnresolved(
                "NEWS_SOURCE_CAPTURE_STORAGE_UNRESOLVED: automatic retry is prohibited"
            ) from storage_error
        raise error

    def read(self) -> dict:
        path = self._path("manifest.json")
        if not path.exists():
            return {
                "schema_version": NEWS_SOURCE_CAPTURE_VERSION,
                "identity": self.identity, "state": "BUILDING",
                "cursor": None, "parts": [], "source_count": 0,
                "item_count": 0, "withdrawal_count": 0,
                "canonical_bytes": 0, "retry_not_before": 0,
                "last_failure": None,
            }
        with path.open("rb") as handle:
            raw = handle.read(NEWS_SOURCE_CAPTURE_METADATA_BYTES + 1)
        if len(raw) > NEWS_SOURCE_CAPTURE_METADATA_BYTES:
            raise ValueError("NEWS_SOURCE_CAPTURE_METADATA_BOUND")
        envelope = json.loads(raw.decode("utf-8"))
        state = envelope.get("capture")
        if (not isinstance(state, dict) or envelope.get("sha256") != sha256_json(state)
                or state.get("schema_version") not in {
                    NEWS_SOURCE_CAPTURE_VERSION, NEWS_SOURCE_CAPTURE_DERIVED_VERSION,
                }
                or state.get("identity") != self.identity):
            raise ValueError("NEWS_SOURCE_CAPTURE_IDENTITY_MISMATCH")
        if state.get("state") not in {"BUILDING", "SOURCE_COMPLETE"}:
            raise ValueError("NEWS_SOURCE_CAPTURE_STATE_INVALID")
        parts = state.get("parts")
        if not isinstance(parts, list):
            raise ValueError("NEWS_SOURCE_CAPTURE_PARTS_INVALID")
        totals = {key: 0 for key in (
            "source_count", "item_count", "withdrawal_count", "canonical_bytes",
        )}
        previous = None
        for number, part in enumerate(parts):
            if (not isinstance(part, dict)
                    or part.get("name") != f"part-{number:08d}-{part.get('sha256')}.jsonl"
                    or part.get("after") != previous):
                raise ValueError("NEWS_SOURCE_CAPTURE_PARTS_INVALID")
            for key in totals:
                value = part.get(key)
                if type(value) is not int or value < 0:
                    raise ValueError("NEWS_SOURCE_CAPTURE_COUNTS_INVALID")
                totals[key] += value
            if (part["source_count"] != part["item_count"] + part["withdrawal_count"]
                    or not 1 <= part["source_count"] <= NEWS_SOURCE_CAPTURE_PAGE_ITEMS
                    or not 0 < part["canonical_bytes"] <= NEWS_SOURCE_CAPTURE_PART_BYTES):
                raise ValueError("NEWS_SOURCE_CAPTURE_COUNTS_INVALID")
            metadata = self._path(part["name"]).stat()
            if metadata.st_size != part["canonical_bytes"]:
                raise ValueError("NEWS_SOURCE_CAPTURE_PART_SIZE_MISMATCH")
            previous = part.get("cursor")
        if (any(state.get(key) != value for key, value in totals.items())
                or any(type(state.get(key)) is not int for key in totals)
                or state.get("cursor") != previous
                or totals["canonical_bytes"] > NEWS_SOURCE_CAPTURE_TOTAL_BYTES):
            raise ValueError("NEWS_SOURCE_CAPTURE_COUNTS_INVALID")
        self._validate_reader_segment(state)
        return state

    def _validate_reader_segment(self, state: dict) -> None:
        segment = state.get("reader_segment")
        if state["schema_version"] == NEWS_SOURCE_CAPTURE_VERSION:
            if segment is not None or any("reader_segment_sha256" in part for part in state["parts"]):
                raise ValueError("NEWS_SOURCE_CAPTURE_READER_SEGMENT_INVALID")
            return
        if not isinstance(segment, dict):
            raise ValueError("NEWS_SOURCE_CAPTURE_READER_SEGMENT_REQUIRED")
        count = segment.get("prefix_part_count")
        if type(count) is not int or not 1 <= count <= len(state["parts"]):
            raise ValueError("NEWS_SOURCE_CAPTURE_READER_PREFIX_INVALID")
        prefix = state["parts"][:count]
        expected = self._reader_prefix(state, prefix)
        # The proof hashes the identity bytes in the validated capture, not the
        # key order of an equivalent caller binding restored from another JSON envelope.
        if (segment.get("prefix") != expected or segment.get("capture_identity_sha256") != sha256_json(state["identity"])
                or not isinstance(segment.get("producer_identity"), dict)
                or not segment["producer_identity"]
                or not isinstance(segment.get("equivalence"), dict)):
            raise ValueError("NEWS_SOURCE_CAPTURE_READER_PREFIX_INVALID")
        equivalence = segment["equivalence"]
        target = _source_api_digest(segment["producer_identity"])
        review = equivalence.get("review_identity")
        if (not isinstance(review, dict) or review.get("target_api_sha256") != target
                or equivalence.get("accepted_part_sha256") not in {part["sha256"] for part in prefix}
                or len(compact_json(segment).encode("utf-8")) > 64 * 1024):
            raise ValueError("NEWS_SOURCE_CAPTURE_READER_PROOF_INVALID")
        for value in (target, review.get("record_sha256"), *(equivalence.get(key) for key in (
            "proof_report_sha256", "proof_input_sha256", "proof_producer_sha256",
            "base_function_ast_sha256", "target_function_ast_sha256",
        ))):
            if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
                raise ValueError("NEWS_SOURCE_CAPTURE_READER_PROOF_INVALID")
        digest = sha256_json(segment)
        if (any("reader_segment_sha256" in part for part in prefix)
                or any(part.get("reader_segment_sha256") != digest for part in state["parts"][count:])):
            raise ValueError("NEWS_SOURCE_CAPTURE_READER_SUFFIX_INVALID")

    @staticmethod
    def _reader_prefix(state: dict, parts: list) -> dict:
        return {
            "parts_sha256": sha256_json(parts), "cursor": parts[-1]["cursor"],
            **{key: sum(part[key] for part in parts) for key in (
                "source_count", "item_count", "withdrawal_count", "canonical_bytes",
            )},
        }

    def derive_reader_segment(self, *, proof_report: bytes, proof_input: bytes,
                              review_identity: dict) -> dict:
        """Fence old code and bind one proven reader correction without SQL work.

        The exact execution producer validates its source files before calling.
        This later identity supplement does not rewrite the original proof.
        """
        if self._storage_unresolved:
            raise NewsSourceCaptureStorageUnresolved("NEWS_SOURCE_CAPTURE_STORAGE_UNRESOLVED")
        if (not isinstance(self.active_producer_identity, dict) or not self.active_producer_identity
                or len(proof_report) > 64 * 1024 or len(proof_input) > 64 * 1024
                or not isinstance(review_identity, dict) or not review_identity):
            raise ValueError("NEWS_SOURCE_CAPTURE_READER_PROOF_INVALID")
        proof = json.loads(proof_report.decode("utf-8"))
        inputs = json.loads(proof_input.decode("utf-8"))
        binding = self.identity["binding"]
        original_source = binding.get("source_identity")
        target_api = _source_api_digest(self.active_producer_identity)
        if (not isinstance(original_source, dict)
                or inputs.get("source_identity") != original_source
                or inputs.get("input_identity") != binding.get("input_identity")
                or inputs.get("snapshot_stat") != binding.get("snapshot_stat")
                or inputs.get("watermark") != self.identity["watermark"]
                or inputs.get("epoch") != self.identity["epoch"]
                or proof.get("source_identity") != original_source
                or proof.get("input_identity") != binding.get("input_identity")
                or proof.get("state") != "READER_PROOF_PASSED"
                or proof.get("equivalent") is not True or proof.get("input_stat_unchanged") is not True
                or proof.get("mismatched_ordinals") != []
                or proof.get("target_file_sha256") != target_api
                or target_api == _source_api_digest(original_source)
                or review_identity.get("target_api_sha256") != target_api
                or proof.get("replacement_scope") != "_news_reader_rows only; all other API AST nodes equal"):
            raise ValueError("NEWS_SOURCE_CAPTURE_READER_PROOF_INVALID")
        for value in (target_api, proof.get("producer_sha256"), proof.get("base_function_ast_sha256"),
                      proof.get("target_function_ast_sha256"), review_identity.get("record_sha256")):
            if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
                raise ValueError("NEWS_SOURCE_CAPTURE_READER_PROOF_INVALID")
        with self._locked():
            state = self.read()
            previous = state.get("reader_segment")
            count = previous["prefix_part_count"] if previous else len(state["parts"])
            prefix = state["parts"][:count]
            part = proof.get("expected_part")
            if (not prefix or part not in prefix
                    or proof.get("actual_canonical_sha256") != part["sha256"]
                    or proof.get("news_result_reads") != part["source_count"]):
                raise ValueError("NEWS_SOURCE_CAPTURE_READER_PROOF_PREFIX_MISMATCH")
            segment = {
                "capture_identity_sha256": sha256_json(state["identity"]),
                "producer_identity": self.active_producer_identity,
                "prefix_part_count": count, "prefix": self._reader_prefix(state, prefix),
                "equivalence": {
                    "proof_report_sha256": hashlib.sha256(proof_report).hexdigest(),
                    "proof_input_sha256": hashlib.sha256(proof_input).hexdigest(),
                    "proof_producer_sha256": proof["producer_sha256"],
                    "base_function_ast_sha256": proof["base_function_ast_sha256"],
                    "target_function_ast_sha256": proof["target_function_ast_sha256"],
                    "accepted_part_sha256": part["sha256"],
                    "review_identity": review_identity,
                },
            }
            if len(compact_json(segment).encode("utf-8")) > 64 * 1024:
                raise ValueError("NEWS_SOURCE_CAPTURE_READER_PROOF_INVALID")
            if previous is not None:
                if previous != segment:
                    raise ValueError("NEWS_SOURCE_CAPTURE_READER_SEGMENT_CONFLICT")
                return state
            if state["state"] != "BUILDING" or state.get("source_plan") or state.get("plan_in_progress"):
                raise ValueError("NEWS_SOURCE_CAPTURE_READER_TRANSITION_INVALID")
            if time.time() < state["retry_not_before"]:
                return state
            updated = {**state, "schema_version": NEWS_SOURCE_CAPTURE_DERIVED_VERSION,
                       "reader_segment": segment}
            try:
                self._save(updated)
            except Exception as error:
                self._publication_failed(error)
            return updated

    def _require_active_reader(self, state: dict) -> None:
        expected = state.get("reader_segment", {}).get("producer_identity")
        if self.active_producer_identity != expected:
            raise ValueError("NEWS_SOURCE_CAPTURE_ACTIVE_PRODUCER_MISMATCH")

    def advance(self, reader: Callable[[dict], NewsSourceCapturePage]) -> dict:
        """Commit at most one complete source prefix; never retry in a loop."""
        if self._storage_unresolved:
            raise NewsSourceCaptureStorageUnresolved(
                "NEWS_SOURCE_CAPTURE_STORAGE_UNRESOLVED: automatic retry is prohibited"
            )
        with self._locked():
            state = self.read()
            self._require_active_reader(state)
            if (state["state"] == "SOURCE_COMPLETE"
                    or time.time() < state["retry_not_before"]):
                return state
            if not self._path("manifest.json").exists():
                try:
                    self._save(state)
                except Exception as error:
                    self._publication_failed(error)
            started = time.monotonic()
            page = None
            try:
                page = reader(state)
                content = bytearray()
                cursor = state["cursor"]
                counts = {"source_count": 0, "item_count": 0, "withdrawal_count": 0}
                complete = not page.has_more
                for record in page.records:
                    next_cursor = record.get("cursor")
                    if (not isinstance(next_cursor, list) or len(next_cursor) != 4
                            or (cursor is not None and tuple(next_cursor) <= tuple(cursor))):
                        raise ValueError("NEWS_SOURCE_CAPTURE_CURSOR_INVALID")
                    is_withdrawal = "withdrawal" in record
                    if is_withdrawal:
                        if not re.fullmatch(r"[0-9a-f]{64}", str(record["withdrawal"])):
                            raise ValueError("NEWS_SOURCE_CAPTURE_WITHDRAWAL_INVALID")
                    elif not isinstance(record.get("index"), dict) or not isinstance(record.get("detail"), dict):
                        raise ValueError("NEWS_SOURCE_CAPTURE_RECORD_INVALID")
                    raw = (compact_json(record) + "\n").encode("utf-8")
                    if len(raw) > NEWS_SOURCE_CAPTURE_PART_BYTES:
                        raise ValueError("NEWS_SOURCE_CAPTURE_ROW_BOUND")
                    if (len(content) + len(raw) > NEWS_SOURCE_CAPTURE_PART_BYTES
                            or counts["source_count"] >= NEWS_SOURCE_CAPTURE_PAGE_ITEMS):
                        complete = False
                        break
                    if state["canonical_bytes"] + len(content) + len(raw) > NEWS_SOURCE_CAPTURE_TOTAL_BYTES:
                        raise ValueError("NEWS_SOURCE_CAPTURE_TOTAL_BOUND")
                    content.extend(raw)
                    cursor = next_cursor
                    counts["source_count"] += 1
                    counts["withdrawal_count" if is_withdrawal else "item_count"] += 1
                # Closing the cursor/transaction is part of the source step;
                # source-stat verification errors must precede its commit.
                closer = getattr(page.records, "close", None)
                if closer is not None:
                    closer()
            except Exception as error:
                # Only source work failed: a manifest replacement that already
                # committed must never be rolled back by an error handler.
                reason = str(error)
                failed = {
                    **state, "last_failure": (
                        reason if reason.startswith("NEWS_SOURCE_CAPTURE_")
                        else type(error).__name__
                    ), "retry_not_before": time.time() + 30,
                    "last_step_seconds": time.monotonic() - started,
                    "last_source_step": dict(page.metrics) if page is not None else {},
                }
                try:
                    self._save(failed)
                except Exception as storage_error:
                    self._publication_failed(storage_error)
                raise
            finally:
                closer = getattr(page.records, "close", None) if page is not None else None
                if closer is not None:
                    closer()
            updated = {**state, "parts": list(state["parts"])}
            if content:
                digest = hashlib.sha256(content).hexdigest()
                name = f"part-{len(state['parts']):08d}-{digest}.jsonl"
                try:
                    self._atomic(name, bytes(content))
                except Exception as error:
                    self._publication_failed(error)
                part = {
                    "name": name, "sha256": digest, "after": state["cursor"],
                    "cursor": cursor, "canonical_bytes": len(content), **counts,
                }
                if state.get("reader_segment") is not None:
                    part["reader_segment_sha256"] = sha256_json(state["reader_segment"])
                updated["parts"].append(part)
                for key in counts:
                    updated[key] += counts[key]
                updated["canonical_bytes"] += len(content)
                updated["cursor"] = cursor
            if not content and not complete:
                raise ValueError("NEWS_SOURCE_CAPTURE_NO_PROGRESS")
            updated["state"] = "SOURCE_COMPLETE" if complete else "BUILDING"
            updated["last_step_seconds"] = time.monotonic() - started
            updated["last_source_step"] = dict(page.metrics)
            updated["retry_not_before"] = 0
            if complete:
                updated["admission"] = (
                    "SOURCE_ROW_CAPACITY_EXCEEDED"
                    if updated["source_count"] > NEWS_PROJECTION_MAX_ITEMS
                    else "CAPACITY_REVIEW_REQUIRED"
                )
            try:
                self._save(updated)
            except Exception as error:
                self._publication_failed(error)
            return updated

    def records(self) -> Iterator[dict]:
        """Read retained bytes for planning, verifying each part before use."""
        for record, *_location in self._record_locations():
            yield record

    def _record_locations(self):
        state = self.read()
        for part in state["parts"]:
            with self._path(part["name"]).open("rb") as handle:
                raw = handle.read(NEWS_SOURCE_CAPTURE_PART_BYTES + 1)
            if (len(raw) != part["canonical_bytes"]
                    or hashlib.sha256(raw).hexdigest() != part["sha256"]):
                raise ValueError("NEWS_SOURCE_CAPTURE_PART_DIGEST_MISMATCH")
            lines = raw.splitlines(keepends=True)
            if len(lines) != part["source_count"]:
                raise ValueError("NEWS_SOURCE_CAPTURE_PART_COUNT_MISMATCH")
            offset = 0
            for line in lines:
                yield (
                    json.loads(line.decode("utf-8")), part["name"], offset,
                    len(line), hashlib.sha256(line).hexdigest(),
                )
                offset += len(line)

    def finalize_plan(self, *, producer_identity: dict | None = None) -> dict:
        """Derive the original global digests/batches without loading a generation.

        One verified part scan produces bounded key/offset metadata. Each of
        the two globally ordered streams reads only its exact JSONL records,
        never a whole-part cache refill per shuffled detail key. All reads are
        retained local bytes, not new SQLite work. This is still not admission.
        """
        if self._storage_unresolved:
            raise NewsSourceCaptureStorageUnresolved("NEWS_SOURCE_CAPTURE_STORAGE_UNRESOLVED")
        if producer_identity is not None and (
            not isinstance(producer_identity, dict) or not producer_identity
            or len(compact_json(producer_identity).encode()) > 64 * 1024
        ):
            raise ValueError("NEWS_SOURCE_CAPTURE_PLAN_PRODUCER_INVALID")
        with self._locked():
            state = self.read()
            if state["state"] != "SOURCE_COMPLETE":
                raise ValueError("NEWS_SOURCE_CAPTURE_INCOMPLETE")
            if state.get("source_plan") is not None:
                return state
            if state.get("plan_failure") is not None or state.get("plan_in_progress"):
                raise ValueError("NEWS_SOURCE_CAPTURE_PLAN_RECOVERY_REQUIRED")
            if time.time() < state["retry_not_before"]:
                return state
            # Persist the attempt before reading retained content. An interrupted
            # hash pass must not become an automatic restart/busy loop.
            state = {**state, "plan_in_progress": True}
            try:
                self._save(state)
            except Exception as error:
                self._publication_failed(error)
            started = time.monotonic()
            metrics = {"record_read_bytes": 0, "part_scan_bytes": 0,
                       "file_opens": 0, "metadata_bytes": 0,
                       "sampled_rss_max_bytes": 0}
            handles = OrderedDict()

            def check_budget():
                metrics["sampled_rss_max_bytes"] = max(
                    metrics["sampled_rss_max_bytes"], news_projection_capture_rss(),
                )
                if metrics["sampled_rss_max_bytes"] > 512 * 1024 * 1024:
                    raise ValueError("NEWS_SOURCE_CAPTURE_PLAN_MEMORY_BOUND")
                if time.monotonic() - started > 15:
                    raise ValueError("NEWS_SOURCE_CAPTURE_PLAN_TIME_BOUND")

            def read_record(descriptor):
                _key, _ordinal, name, offset, length, expected = descriptor
                if name not in handles:
                    if len(handles) == 8:
                        handles.popitem(last=False)[1].close()
                    handles[name] = self._path(name).open("rb", buffering=0)
                    metrics["file_opens"] += 1
                handle = handles[name]
                handles.move_to_end(name)
                handle.seek(offset)
                raw = handle.read(length)
                metrics["record_read_bytes"] += len(raw)
                if len(raw) != length or hashlib.sha256(raw).hexdigest() != expected:
                    raise ValueError("NEWS_SOURCE_CAPTURE_RECORD_DIGEST_MISMATCH")
                return json.loads(raw.decode("utf-8"))

            try:
                check_budget()
                descriptors = []
                withdrawals = set()
                raw_count = 0
                item_record_bytes = 0
                for record, name, offset, length, digest in self._record_locations():
                    check_budget()
                    raw_count += 1
                    metrics["part_scan_bytes"] += length
                    if "withdrawal" in record:
                        metrics["metadata_bytes"] += len(record["withdrawal"].encode()) + 3
                        if metrics["metadata_bytes"] > NEWS_SOURCE_CAPTURE_METADATA_BYTES:
                            raise ValueError("NEWS_SOURCE_CAPTURE_PLAN_METADATA_BOUND")
                        withdrawals.add(record["withdrawal"])
                    else:
                        detail = record["detail"]
                        if (record["index"]["detail_key"] != detail["detail_key"]
                                or sha256_json(detail["payload"]) != detail["detail_hash"]
                                or content_addressed_detail_key(record["index"], detail["payload"]) != detail["detail_key"]):
                            raise ValueError("NEWS_SOURCE_CAPTURE_DETAIL_IDENTITY_MISMATCH")
                        descriptor = [detail["detail_key"], raw_count, name, offset, length, digest]
                        metrics["metadata_bytes"] += len(compact_json(descriptor).encode()) + 1
                        if metrics["metadata_bytes"] > NEWS_SOURCE_CAPTURE_METADATA_BYTES:
                            raise ValueError("NEWS_SOURCE_CAPTURE_PLAN_METADATA_BOUND")
                        descriptors.append(descriptor)
                        item_record_bytes += length
                    if metrics["metadata_bytes"] > NEWS_SOURCE_CAPTURE_METADATA_BYTES:
                        raise ValueError("NEWS_SOURCE_CAPTURE_PLAN_METADATA_BOUND")
                if (raw_count != state["source_count"] or len(descriptors) != state["item_count"]
                        or metrics["part_scan_bytes"] != state["canonical_bytes"]):
                    raise ValueError("NEWS_SOURCE_CAPTURE_PLAN_COUNT_MISMATCH")
                descriptors.sort(key=lambda row: (row[0], row[1]))
                source_hash = hashlib.sha256()
                chain = EMPTY_RECEIPT_DIGEST
                batches = {"detail": [], "index": []}
                for kind, field_name, maximum, byte_limit in (
                    ("detail", "details", NEWS_DETAIL_BATCH_ITEMS, NEWS_DETAIL_BATCH_LIMIT_BYTES),
                    ("index", "index", NEWS_INDEX_BATCH_ITEMS, NEWS_INDEX_BATCH_LIMIT_BYTES),
                ):
                    source_hash.update((('{' if kind == "detail" else ',') + f'"{field_name}":[').encode())
                    current = []
                    current_size = 2
                    offset = 0

                    def flush():
                        nonlocal chain, offset, current, current_size
                        if not current:
                            return
                        payload_hash = receipt_payload_hash(current)
                        chain = hashlib.sha256(
                            f"{chain}\n{kind}|{offset}|{len(current)}|{payload_hash}".encode()
                        ).hexdigest()
                        batches[kind].append({
                            "offset": offset, "count": len(current), "bytes": current_size,
                            "payload_hash": payload_hash,
                        })
                        offset += len(current)
                        current = []
                        current_size = 2

                    for number, descriptor in enumerate(descriptors):
                        check_budget()
                        row = read_record(descriptor)[kind]
                        if number:
                            source_hash.update(b",")
                        source_hash.update(compact_json(row, sort_keys=True).encode("utf-8"))
                        row_size = len(compact_json(row).encode("utf-8"))
                        if row_size + 2 > byte_limit:
                            raise ValueError("NEWS_SOURCE_CAPTURE_PLAN_TRANSPORT_BOUND")
                        if current and (len(current) == maximum or current_size + 1 + row_size > byte_limit):
                            flush()
                        current_size += row_size + int(bool(current))
                        current.append(row)
                    flush()
                    source_hash.update(b"]")
                source_hash.update(b',"withdrawal_keys":')
                source_hash.update(compact_json(sorted(withdrawals)).encode("utf-8"))
                source_hash.update(b"}")
                check_budget()
                if metrics["record_read_bytes"] != 2 * item_record_bytes:
                    raise ValueError("NEWS_SOURCE_CAPTURE_PLAN_READ_BOUND")
                manifest = _news_projection_manifest(
                    window_start=self.identity["window_start"], watermark=self.identity["watermark"],
                    source_digest=source_hash.hexdigest(), item_count=len(descriptors),
                    withdrawal_count=len(withdrawals), expected_receipt_digest=chain,
                )
                metrics["elapsed_seconds"] = time.monotonic() - started
                plan = {"manifest": manifest, "batches": batches, "metrics": metrics,
                        "input_digest": _capture_plan_input_digest(state),
                        "row_locations": descriptors,
                        "minimum_sync_cycles": max(1, math.ceil((len(batches["detail"]) + len(batches["index"])) / 4))}
                if producer_identity is not None:
                    # Derived planning may use a later, explicitly bound code
                    # revision. Never relabel the original capture identity.
                    plan["producer_identity"] = dict(producer_identity)
                updated = {**state, "source_plan": plan, "plan_in_progress": False}
            except Exception as error:
                failed = {**state, "plan_in_progress": False,
                          "plan_failure": str(error) if str(error).startswith("NEWS_SOURCE_CAPTURE_") else type(error).__name__}
                try:
                    self._save(failed)
                except Exception as storage_error:
                    self._publication_failed(storage_error)
                raise
            finally:
                for handle in handles.values():
                    handle.close()
            try:
                self._save(updated)
            except Exception as error:
                self._publication_failed(error)
            return updated

    def open_plan_reader(self) -> _NewsSourcePlanReader:
        """Open bounded retained batches for inspection, never remote admission."""
        with self._locked():
            return _NewsSourcePlanReader(self, self.read())

    def open_replay_generation(self, *, maximum_batches: int) -> NewsProjectionRetainedGeneration:
        """Admit one bounded local replay view, not a production release.

        Source rows include withdrawals; materialized rows and unique withdrawals
        have separate existing Worker limits. Local capture limits still apply.
        The caller's finite command budget and later D1/release gates remain
        independent. No artifact, source identity or prior failure is rewritten.
        """
        with self._locked():
            state = self.read()
            if (state.get("plan_in_progress") or state.get("plan_failure")
                    or time.time() < state["retry_not_before"]):
                raise ValueError("NEWS_SOURCE_CAPTURE_PLAN_RECOVERY_REQUIRED")
            reader = _NewsSourcePlanReader(self, state)
            plan = state["source_plan"]
            manifest = plan.get("manifest")
            if not isinstance(manifest, dict):
                raise ValueError("NEWS_SOURCE_CAPTURE_PLAN_MANIFEST_INVALID")
            counts = [manifest.get(key) for key in (
                "expected_index_count", "expected_detail_count", "withdrawal_count",
            )]
            if any(type(value) is not int or not 0 <= value <= NEWS_PROJECTION_MAX_ITEMS for value in counts):
                raise ValueError("NEWS_PROJECTION_GENERATION_CAPACITY_EXCEEDED")
            if (counts[0] != counts[1] or counts[0] != state["item_count"]
                    or counts[2] > state["withdrawal_count"]
                    or len({row[0] for row in reader._locations}) != counts[0]):
                raise ValueError("NEWS_SOURCE_CAPTURE_PLAN_MEMBERSHIP_INVALID")
            chain = EMPTY_RECEIPT_DIGEST
            number = 0
            for kind in ("detail", "index"):
                for batch in plan["batches"][kind]:
                    number += 1
                    chain = hashlib.sha256(
                        f"{chain}\n{kind}|{batch['offset']}|{batch['count']}|{batch['payload_hash']}".encode()
                    ).hexdigest()
            if type(maximum_batches) is not int or maximum_batches < 1 or number > maximum_batches:
                raise ValueError("NEWS_SOURCE_CAPTURE_REPLAY_WORK_BOUND")
            source_digest = manifest.get("source_digest")
            if not isinstance(source_digest, str) or not re.fullmatch(r"[0-9a-f]{64}", source_digest):
                raise ValueError("NEWS_SOURCE_CAPTURE_PLAN_MANIFEST_INVALID")
            expected = _news_projection_manifest(
                window_start=self.identity["window_start"], watermark=self.identity["watermark"],
                source_digest=source_digest, item_count=counts[0], withdrawal_count=counts[2],
                expected_receipt_digest=chain,
            )
            if manifest != expected:
                raise ValueError("NEWS_SOURCE_CAPTURE_PLAN_MANIFEST_INVALID")
            return NewsProjectionRetainedGeneration(
                dict(manifest), reader, plan["input_digest"], maximum_batches,
            )


def _capture_plan_input_digest(state: dict) -> str:
    values = {key: state[key] for key in (
        "identity", "parts", "source_count", "item_count", "withdrawal_count", "canonical_bytes",
    )}
    if state.get("reader_segment") is not None:
        values["reader_segment"] = state["reader_segment"]
    return sha256_json(values, sort_keys=True)


class _NewsSourcePlanReader:
    """One validated location index; deliberately has no replay manifest.

    Opening validates bounded metadata once. A batch reads only its selected
    record ranges and never reopens source SQLite or scans earlier parts/batches.
    The private artifact owner supplies immutability; stat is not provenance.
    """

    def __init__(self, capture: NewsProjectionSourceCapture, state: dict) -> None:
        plan = state.get("source_plan")
        if state["state"] != "SOURCE_COMPLETE" or not isinstance(plan, dict):
            raise ValueError("NEWS_SOURCE_CAPTURE_PLAN_REQUIRED")
        locations = plan.get("row_locations")
        if not isinstance(locations, list):
            raise ValueError("NEWS_SOURCE_CAPTURE_PLAN_LOCATOR_MISSING")
        if (plan.get("input_digest") != _capture_plan_input_digest(state)
                or len(locations) != state["item_count"]):
            raise ValueError("NEWS_SOURCE_CAPTURE_PLAN_LOCATOR_BINDING")
        parts = {}
        ordinal = 0
        for part in state["parts"]:
            parts[part["name"]] = (part, ordinal + 1, ordinal + part["source_count"])
            ordinal += part["source_count"]
        previous = None
        ordinals = set()
        self._locations = []
        for location in locations:
            if not isinstance(location, (list, tuple)) or len(location) != 6:
                raise ValueError("NEWS_SOURCE_CAPTURE_PLAN_LOCATOR_INVALID")
            key, ordinal, name, offset, length, digest = location
            if (not isinstance(key, str) or not re.fullmatch(r"[0-9a-f]{64}", key)
                    or type(ordinal) is not int or ordinal in ordinals
                    or not isinstance(name, str) or name not in parts
                    or type(offset) is not int or offset < 0
                    or type(length) is not int or length <= 0
                    or not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest)):
                raise ValueError("NEWS_SOURCE_CAPTURE_PLAN_LOCATOR_INVALID")
            part, first, last = parts[name]
            if (not first <= ordinal <= last
                    or offset + length > part["canonical_bytes"]
                    or previous is not None and (key, ordinal) <= previous):
                raise ValueError("NEWS_SOURCE_CAPTURE_PLAN_LOCATOR_INVALID")
            previous = (key, ordinal)
            ordinals.add(ordinal)
            self._locations.append(tuple(location))
        self._batches = {}
        batches = plan.get("batches")
        if not isinstance(batches, dict) or set(batches) != {"detail", "index"}:
            raise ValueError("NEWS_SOURCE_CAPTURE_PLAN_BATCH_INVALID")
        for kind, maximum, byte_limit in (
            ("detail", NEWS_DETAIL_BATCH_ITEMS, NEWS_DETAIL_BATCH_LIMIT_BYTES),
            ("index", NEWS_INDEX_BATCH_ITEMS, NEWS_INDEX_BATCH_LIMIT_BYTES),
        ):
            offset = 0
            lookup = {}
            if not isinstance(batches[kind], list):
                raise ValueError("NEWS_SOURCE_CAPTURE_PLAN_BATCH_INVALID")
            for batch in batches[kind]:
                if (not isinstance(batch, dict) or type(batch.get("offset")) is not int
                        or batch["offset"] != offset or type(batch.get("count")) is not int
                        or not 1 <= batch["count"] <= maximum
                        or type(batch.get("bytes")) is not int or not 2 <= batch["bytes"] <= byte_limit
                        or not isinstance(batch.get("payload_hash"), str)
                        or not re.fullmatch(r"[0-9a-f]{64}", batch["payload_hash"])):
                    raise ValueError("NEWS_SOURCE_CAPTURE_PLAN_BATCH_INVALID")
                lookup[offset] = dict(batch)
                offset += batch["count"]
            if offset != len(self._locations):
                raise ValueError("NEWS_SOURCE_CAPTURE_PLAN_BATCH_INVALID")
            self._batches[kind] = lookup
        self._capture = capture
        self.metrics = {"record_read_bytes": 0, "file_opens": 0, "batch_reads": 0}

    def batch_items(self, kind: str, offset: int) -> list[dict]:
        if kind not in self._batches or type(offset) is not int or offset < 0:
            raise ValueError("NEWS_SOURCE_CAPTURE_PLAN_BATCH_BOUNDARY")
        if offset == len(self._locations):
            return []
        batch = self._batches[kind].get(offset)
        if batch is None:
            raise ValueError("NEWS_SOURCE_CAPTURE_PLAN_BATCH_BOUNDARY")
        rows = []
        for key, _ordinal, name, start, length, digest in self._locations[offset:offset + batch["count"]]:
            with self._capture._path(name).open("rb", buffering=0) as handle:
                self.metrics["file_opens"] += 1
                handle.seek(start)
                raw = handle.read(length)
            self.metrics["record_read_bytes"] += len(raw)
            if len(raw) != length or hashlib.sha256(raw).hexdigest() != digest:
                raise ValueError("NEWS_SOURCE_CAPTURE_RECORD_DIGEST_MISMATCH")
            record = json.loads(raw.decode("utf-8"))
            detail, index = record.get("detail"), record.get("index")
            if (not isinstance(detail, dict) or not isinstance(index, dict)
                    or detail.get("detail_key") != key or index.get("detail_key") != key
                    or sha256_json(detail.get("payload")) != detail.get("detail_hash")
                    or content_addressed_detail_key(index, detail["payload"]) != key):
                raise ValueError("NEWS_SOURCE_CAPTURE_DETAIL_IDENTITY_MISMATCH")
            rows.append(record[kind])
        if (len(compact_json(rows).encode()) != batch["bytes"]
                or receipt_payload_hash(rows) != batch["payload_hash"]):
            raise ValueError("NEWS_SOURCE_CAPTURE_PLAN_BATCH_DIGEST_MISMATCH")
        self.metrics["batch_reads"] += 1
        return rows


@dataclass(frozen=True)
class NewsProjectionRetainedGeneration:
    """An admitted local container; exact remote ACK/release checks still apply."""

    manifest: dict
    _reader: _NewsSourcePlanReader
    input_digest: str
    maximum_batches: int
    _replay_target: tuple[str, str] | None = None

    def require_target(self, *, index_url: str, detail_url: str, contract_version: str) -> None:
        if (self._replay_target is None
                or self._replay_target[1] != contract_version
                or index_url != self._replay_target[0] + "/api/news-index"
                or detail_url != self._replay_target[0] + "/api/news-content"):
            raise ValueError("NEWS_SOURCE_CAPTURE_ARTIFACT_TARGET_MISMATCH")

    def require_work_budget(self, maximum_batches: int) -> None:
        count = sum(len(batches) for batches in self._reader._batches.values())
        if type(maximum_batches) is not int or maximum_batches < 1 or count > maximum_batches:
            raise ValueError("NEWS_SOURCE_CAPTURE_REPLAY_WORK_BOUND")

    def artifact_payload(self, artifact_path: Path) -> dict:
        """Pin existing retained bytes, without copying bodies into gzip rows."""
        expected = artifact_path.with_name(
            artifact_path.name.removesuffix(".json.gz") + ".capture"
        )
        capture = self._reader._capture
        if capture.directory != Path(os.path.abspath(expected)):
            raise ValueError("NEWS_SOURCE_CAPTURE_ARTIFACT_LOCATION_MISMATCH")
        return {
            "storage": "retained-source-plan-v1",
            "manifest": self.manifest,
            "capture_identity": capture.identity,
            "input_digest": self.input_digest,
            "maximum_batches": self.maximum_batches,
        }

    @classmethod
    def from_artifact_payload(cls, artifact_path: Path, payload: dict, *, target: dict) -> NewsProjectionRetainedGeneration:
        identity = payload.get("capture_identity")
        if (not isinstance(identity, dict) or not isinstance(identity.get("binding"), dict)
                or any(not isinstance(identity.get(key), str) for key in (
                    "watermark", "window_start", "epoch",
                ))):
            raise ValueError("NEWS_SOURCE_CAPTURE_ARTIFACT_IDENTITY_INVALID")
        expected = artifact_path.with_name(
            artifact_path.name.removesuffix(".json.gz") + ".capture"
        )
        capture = NewsProjectionSourceCapture(
            expected, binding=identity.get("binding"), watermark=identity.get("watermark"),
            window_start=identity.get("window_start"), epoch=identity.get("epoch"),
        )
        if capture.identity != identity:
            raise ValueError("NEWS_SOURCE_CAPTURE_ARTIFACT_IDENTITY_INVALID")
        generation = capture.open_replay_generation(maximum_batches=payload.get("maximum_batches"))
        if generation.artifact_payload(artifact_path) != payload:
            raise ValueError("NEWS_SOURCE_CAPTURE_ARTIFACT_BINDING_MISMATCH")
        if (not isinstance(target.get("origin"), str) or not target["origin"]
                or target.get("contract_version") != NEWS_MIRROR_CONTRACT_VERSION):
            raise ValueError("NEWS_SOURCE_CAPTURE_ARTIFACT_TARGET_MISMATCH")
        return replace(generation, _replay_target=(target["origin"], target["contract_version"]))

    def batch_items(self, kind: str, offset: int) -> list[dict]:
        return self._reader.batch_items(kind, offset)
