"""Append-only storage for Phase 2F forward evidence."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .news_semantics import (
    CURRENT_NEWS_PROMPT_VERSION,
    NEWS_CATEGORIES,
    RECORD_KINDS,
    news_annotation_schema,
    validate_news_annotation,
)


UTC = timezone.utc
IMMUTABLE_TABLES = (
    "runtime_metadata",
    "market_snapshots",
    "news_revisions",
    "news_annotations",
    "news_title_translations",
    "news_llm_failures",
    "news_content_failures",
    "news_discovery_failures",
    "daily_news_briefs",
    "macro_observations",
    "source_polls",
    "decision_events",
    "predictions",
    "outcomes",
    "prediction_scores",
    "shadow_trade_intents",
    "shadow_trade_results",
    "training_eligibility",
    "model_updates",
    "promotion_approvals",
    "collector_runs",
    "repair_batches",
    "evaluation_epochs",
    "evidence_lane_assignments",
    "source_eligibility_versions",
    "source_eligibility_rules",
    "derived_market_snapshots",
    "derived_news_feature_snapshots",
    "derived_outcomes",
    "training_eligibility_v2",
    "market_crossfit_predictions",
    "model_updates_v2",
    "predictions_v2",
    "prediction_scores_v2",
    "calibration_snapshots_v2",
)

SCHEMA = """
PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;

CREATE TABLE IF NOT EXISTS runtime_metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS market_snapshots (
    snapshot_id TEXT PRIMARY KEY,
    decision_time TEXT NOT NULL UNIQUE,
    collected_at TEXT NOT NULL,
    data_role TEXT NOT NULL CHECK(data_role IN ('FORWARD', 'WARMUP_ONLY')),
    source TEXT NOT NULL,
    source_event_time TEXT,
    source_received_time TEXT,
    bid REAL,
    ask REAL,
    spread REAL,
    features_json TEXT NOT NULL,
    feature_version TEXT NOT NULL,
    u5 REAL,
    u5_status TEXT NOT NULL,
    data_health TEXT NOT NULL,
    active_signal INTEGER NOT NULL CHECK(active_signal IN (0, 1)),
    reason_codes_json TEXT NOT NULL,
    snapshot_hash TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS news_revisions (
    source TEXT NOT NULL,
    source_item_id TEXT NOT NULL,
    revision_number INTEGER NOT NULL,
    source_published_time TEXT,
    collector_first_seen_time TEXT NOT NULL,
    item_first_seen_time TEXT NOT NULL,
    fetched_time TEXT NOT NULL,
    headline TEXT NOT NULL,
    body TEXT,
    link TEXT,
    content_hash TEXT NOT NULL,
    cluster_id TEXT NOT NULL,
    collector_latency_seconds REAL,
    PRIMARY KEY(source, source_item_id, revision_number),
    UNIQUE(source, source_item_id, content_hash)
);

CREATE TABLE IF NOT EXISTS news_annotations (
    annotation_id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    source_item_id TEXT NOT NULL,
    revision_number INTEGER NOT NULL,
    raw_content_hash TEXT NOT NULL,
    event_type TEXT NOT NULL,
    entities_json TEXT NOT NULL,
    hawkishness REAL NOT NULL,
    inflation_impulse REAL NOT NULL,
    growth_impulse REAL NOT NULL,
    geopolitical_risk REAL NOT NULL,
    usd_impulse REAL NOT NULL,
    novelty REAL NOT NULL,
    confidence REAL NOT NULL,
    llm_model_version TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    parse_started_at TEXT NOT NULL,
    parsed_at TEXT NOT NULL,
    annotation_json TEXT NOT NULL,
    FOREIGN KEY(source, source_item_id, revision_number)
        REFERENCES news_revisions(source, source_item_id, revision_number)
);

CREATE INDEX IF NOT EXISTS news_annotations_revision_contract
ON news_annotations(source, source_item_id, revision_number,
                    llm_model_version, prompt_version);

CREATE INDEX IF NOT EXISTS news_revisions_cluster_latest
ON news_revisions(cluster_id, source, source_item_id, revision_number);

CREATE TABLE IF NOT EXISTS daily_news_briefs (
    brief_date TEXT NOT NULL,
    revision_number INTEGER NOT NULL,
    source_hash TEXT NOT NULL,
    cutoff_at TEXT NOT NULL,
    generated_at TEXT NOT NULL,
    model_version TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    brief_json TEXT NOT NULL,
    PRIMARY KEY(brief_date, revision_number),
    UNIQUE(brief_date, source_hash)
);

CREATE TABLE IF NOT EXISTS news_title_translations (
    translation_id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    source_item_id TEXT NOT NULL,
    revision_number INTEGER NOT NULL,
    raw_content_hash TEXT NOT NULL,
    headline_zh TEXT NOT NULL,
    llm_model_version TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    parse_started_at TEXT NOT NULL,
    parsed_at TEXT NOT NULL,
    FOREIGN KEY(source, source_item_id, revision_number)
        REFERENCES news_revisions(source, source_item_id, revision_number)
);

CREATE TABLE IF NOT EXISTS news_llm_failures (
    failure_id TEXT PRIMARY KEY,
    task_type TEXT NOT NULL CHECK(task_type IN ('ANNOTATION', 'TITLE_TRANSLATION')),
    source TEXT NOT NULL,
    source_item_id TEXT NOT NULL,
    revision_number INTEGER NOT NULL,
    raw_content_hash TEXT NOT NULL,
    llm_model_version TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    attempt_number INTEGER NOT NULL CHECK(attempt_number >= 1),
    error_type TEXT NOT NULL,
    error_signature TEXT NOT NULL,
    error TEXT NOT NULL,
    failed_at TEXT NOT NULL,
    next_retry_at TEXT,
    is_terminal INTEGER NOT NULL CHECK(is_terminal IN (0, 1)),
    FOREIGN KEY(source, source_item_id, revision_number)
        REFERENCES news_revisions(source, source_item_id, revision_number),
    UNIQUE(task_type, source, source_item_id, revision_number,
           llm_model_version, prompt_version, attempt_number)
);

CREATE INDEX IF NOT EXISTS news_llm_failures_lookup
ON news_llm_failures(task_type, source, source_item_id, revision_number,
                     llm_model_version, prompt_version, attempt_number);

CREATE TABLE IF NOT EXISTS news_content_failures (
    failure_id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    source_item_id TEXT NOT NULL,
    revision_number INTEGER NOT NULL,
    raw_content_hash TEXT NOT NULL,
    attempt_number INTEGER NOT NULL CHECK(attempt_number >= 1),
    error_type TEXT NOT NULL,
    error_signature TEXT NOT NULL,
    error TEXT NOT NULL,
    failed_at TEXT NOT NULL,
    next_retry_at TEXT,
    is_terminal INTEGER NOT NULL CHECK(is_terminal IN (0, 1)),
    FOREIGN KEY(source, source_item_id, revision_number)
        REFERENCES news_revisions(source, source_item_id, revision_number),
    UNIQUE(source, source_item_id, revision_number, attempt_number)
);

CREATE INDEX IF NOT EXISTS news_content_failures_lookup
ON news_content_failures(source, source_item_id, revision_number, attempt_number);

CREATE TABLE IF NOT EXISTS news_discovery_failures (
    failure_id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    source_item_id TEXT NOT NULL,
    attempt_number INTEGER NOT NULL CHECK(attempt_number >= 1),
    error_type TEXT NOT NULL,
    error TEXT NOT NULL,
    failed_at TEXT NOT NULL,
    next_retry_at TEXT NOT NULL,
    UNIQUE(source, source_item_id, attempt_number)
);

CREATE INDEX IF NOT EXISTS news_discovery_failures_lookup
ON news_discovery_failures(source, source_item_id, attempt_number);

CREATE TABLE IF NOT EXISTS macro_observations (
    source TEXT NOT NULL,
    series_id TEXT NOT NULL,
    observation_period TEXT NOT NULL,
    revision_number INTEGER NOT NULL,
    collector_first_seen_time TEXT NOT NULL,
    fetched_time TEXT NOT NULL,
    value REAL NOT NULL,
    unit TEXT NOT NULL,
    footnotes_json TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    PRIMARY KEY(source, series_id, observation_period, revision_number),
    UNIQUE(source, series_id, observation_period, content_hash)
);

CREATE TABLE IF NOT EXISTS source_polls (
    poll_id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    fetched_time TEXT NOT NULL,
    status TEXT NOT NULL,
    payload_hash TEXT,
    error_type TEXT,
    error TEXT
);
CREATE INDEX IF NOT EXISTS idx_source_polls_source_time
ON source_polls(source, fetched_time DESC);

CREATE TABLE IF NOT EXISTS decision_events (
    decision_id TEXT PRIMARY KEY,
    decision_time TEXT NOT NULL UNIQUE,
    snapshot_id TEXT NOT NULL REFERENCES market_snapshots(snapshot_id),
    created_at TEXT NOT NULL,
    visible_news_hash TEXT NOT NULL,
    data_health TEXT NOT NULL,
    effective_action TEXT NOT NULL CHECK(effective_action = 'WAIT'),
    reason_codes_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS predictions (
    decision_id TEXT NOT NULL REFERENCES decision_events(decision_id),
    model_version TEXT NOT NULL,
    model_identity TEXT NOT NULL,
    feature_snapshot_hash TEXT NOT NULL,
    predicted_direction_u5 REAL,
    predicted_news_residual_u5 REAL,
    ev_long_u5 REAL,
    ev_short_u5 REAL,
    uncertainty_u5 REAL,
    recommended_action TEXT NOT NULL,
    effective_action TEXT NOT NULL CHECK(effective_action = 'WAIT'),
    prediction_status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY(decision_id, model_version)
);

CREATE TABLE IF NOT EXISTS outcomes (
    decision_id TEXT PRIMARY KEY REFERENCES decision_events(decision_id),
    entry_time TEXT,
    exit_time TEXT,
    appended_at TEXT NOT NULL,
    label_version TEXT NOT NULL,
    outcome_status TEXT NOT NULL CHECK(outcome_status IN ('VALID', 'INVALID')),
    reason_codes_json TEXT NOT NULL,
    long_return REAL,
    short_return REAL,
    direction_move REAL,
    spread_quote_cost REAL,
    long_mfe REAL,
    long_mae REAL,
    short_mfe REAL,
    short_mae REAL,
    maximum_spread REAL,
    quote_coverage REAL,
    source_hash TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS prediction_scores (
    decision_id TEXT NOT NULL,
    model_version TEXT NOT NULL,
    scored_at TEXT NOT NULL,
    score_json TEXT NOT NULL,
    PRIMARY KEY(decision_id, model_version),
    FOREIGN KEY(decision_id, model_version)
        REFERENCES predictions(decision_id, model_version),
    FOREIGN KEY(decision_id) REFERENCES outcomes(decision_id)
);

CREATE TABLE IF NOT EXISTS shadow_trade_intents (
    decision_id TEXT NOT NULL,
    model_version TEXT NOT NULL,
    model_identity TEXT NOT NULL,
    decision_time TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    recommended_action TEXT NOT NULL CHECK(recommended_action IN ('LONG', 'SHORT', 'WAIT')),
    simulated_action TEXT NOT NULL CHECK(simulated_action IN ('LONG', 'SHORT', 'WAIT')),
    admission_status TEXT NOT NULL CHECK(admission_status IN (
        'ADMITTED', 'MODEL_WAIT', 'PREDICTION_NOT_READY',
        'OVERLAP_BLOCK', 'NOT_ACTION_MODEL')),
    planned_exit_time TEXT,
    simulation_version TEXT NOT NULL,
    PRIMARY KEY(decision_id, model_version),
    FOREIGN KEY(decision_id, model_version)
        REFERENCES predictions(decision_id, model_version)
);

CREATE TABLE IF NOT EXISTS shadow_trade_results (
    decision_id TEXT NOT NULL,
    model_version TEXT NOT NULL,
    settled_at TEXT NOT NULL,
    result_status TEXT NOT NULL CHECK(result_status IN ('VALID', 'INVALID', 'NOT_TRADED')),
    simulated_action TEXT NOT NULL CHECK(simulated_action IN ('LONG', 'SHORT', 'WAIT')),
    pnl_log_return REAL,
    pnl_u5 REAL,
    equity_log_return REAL,
    mfe_u5 REAL,
    mae_u5 REAL,
    cost_model TEXT NOT NULL,
    outcome_source_hash TEXT NOT NULL,
    simulation_version TEXT NOT NULL,
    PRIMARY KEY(decision_id, model_version),
    FOREIGN KEY(decision_id, model_version)
        REFERENCES shadow_trade_intents(decision_id, model_version),
    FOREIGN KEY(decision_id) REFERENCES outcomes(decision_id)
);

CREATE TABLE IF NOT EXISTS training_eligibility (
    decision_id TEXT PRIMARY KEY REFERENCES outcomes(decision_id),
    eligible_at TEXT NOT NULL,
    data_role TEXT NOT NULL CHECK(data_role = 'FORWARD'),
    eligibility_version TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS model_updates (
    model_version TEXT PRIMARY KEY,
    model_identity TEXT NOT NULL,
    created_at TEXT NOT NULL,
    training_cutoff TEXT NOT NULL,
    training_dataset_hash TEXT NOT NULL,
    feature_version TEXT NOT NULL,
    news_prompt_version TEXT,
    hyperparameters_json TEXT NOT NULL,
    artifact_path TEXT NOT NULL,
    artifact_hash TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status = 'CHALLENGER')
);

CREATE TABLE IF NOT EXISTS promotion_approvals (
    approval_id TEXT PRIMARY KEY,
    model_version TEXT NOT NULL REFERENCES model_updates(model_version),
    approved_by TEXT NOT NULL,
    approved_at TEXT NOT NULL,
    evidence_hash TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS collector_runs (
    run_id TEXT PRIMARY KEY,
    started_at TEXT NOT NULL,
    completed_at TEXT NOT NULL,
    decision_time TEXT NOT NULL,
    market_status TEXT NOT NULL,
    news_status_json TEXT NOT NULL,
    snapshot_id TEXT NOT NULL,
    decision_id TEXT NOT NULL
);
"""


def _iso(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return value.astimezone(UTC).isoformat(timespec="microseconds")


def canonical_hash(value: Any) -> str:
    payload = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


class ForwardLedger:
    """Own one immutable Forward evidence database."""

    def __init__(self, path: str | Path, now: datetime | None = None) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Collector and annotator are independent long-running writers.  A
        # brief WAL writer collision must wait for the current transaction,
        # not terminate either service during a code reload or normal polling.
        self.connection = sqlite3.connect(self.path, timeout=60.0)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA busy_timeout=60000")
        self.connection.executescript(SCHEMA)
        from .evidence_v2 import install_v2_schema
        from .news_scheduler import install_scheduler_schema

        install_v2_schema(self.connection)
        install_scheduler_schema(self.connection)
        self._install_append_only_triggers()
        created = now or datetime.now(UTC)
        with self.connection:
            self.connection.execute(
                "INSERT OR IGNORE INTO runtime_metadata VALUES (?, ?, ?)",
                ("FORWARD_EPOCH", _iso(created), _iso(created)),
            )

    def close(self) -> None:
        self.connection.close()

    @property
    def forward_epoch(self) -> datetime:
        row = self.connection.execute(
            "SELECT value FROM runtime_metadata WHERE key='FORWARD_EPOCH'"
        ).fetchone()
        return datetime.fromisoformat(str(row["value"]))

    def _install_append_only_triggers(self) -> None:
        statements = []
        for table in IMMUTABLE_TABLES:
            statements.extend(
                [
                    f"CREATE TRIGGER IF NOT EXISTS {table}_no_update "
                    f"BEFORE UPDATE ON {table} BEGIN SELECT RAISE(ABORT, "
                    f"'{table} is append-only'); END;",
                    f"CREATE TRIGGER IF NOT EXISTS {table}_no_delete "
                    f"BEFORE DELETE ON {table} BEGIN SELECT RAISE(ABORT, "
                    f"'{table} is append-only'); END;",
                ]
            )
        self.connection.executescript("\n".join(statements))

    def append_snapshot(self, record: dict[str, Any]) -> None:
        decision_time = _iso(record["decision_time"])
        role = record["data_role"]
        if role == "FORWARD" and record["decision_time"] < self.forward_epoch:
            raise ValueError("FORWARD snapshot predates FORWARD_EPOCH")
        received = record.get("source_received_time")
        if received is not None and received > record["decision_time"]:
            raise ValueError("snapshot uses market data received after decision")
        features = record.get("features", {})
        reasons = tuple(record.get("reason_codes", ()))
        stored = {
            **features,
            "bid": record.get("bid"),
            "ask": record.get("ask"),
            "decision_time": decision_time,
            "feature_version": record["feature_version"],
        }
        with self.connection:
            self.connection.execute(
                """INSERT INTO market_snapshots VALUES
                (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    record["snapshot_id"],
                    decision_time,
                    _iso(record["collected_at"]),
                    role,
                    record["source"],
                    _iso(record["source_event_time"])
                    if record.get("source_event_time") else None,
                    _iso(record["source_received_time"])
                    if record.get("source_received_time") else None,
                    record.get("bid"),
                    record.get("ask"),
                    record.get("spread"),
                    json.dumps(features, sort_keys=True, separators=(",", ":")),
                    record["feature_version"],
                    record.get("u5"),
                    record["u5_status"],
                    record["data_health"],
                    int(bool(record.get("active_signal"))),
                    json.dumps(reasons, separators=(",", ":")),
                    canonical_hash(stored),
                ),
            )

    def append_news_revision(self, record: dict[str, Any]) -> tuple[int, bool]:
        if record["collector_first_seen_time"] > record["fetched_time"]:
            raise ValueError("news first-seen time cannot follow fetched time")
        existing = self.connection.execute(
            """SELECT revision_number FROM news_revisions
            WHERE source=? AND source_item_id=? AND content_hash=?""",
            (record["source"], record["source_item_id"], record["content_hash"]),
        ).fetchone()
        if existing is not None:
            return int(existing["revision_number"]), False
        latest = self.connection.execute(
            """SELECT * FROM news_revisions WHERE source=? AND source_item_id=?
            ORDER BY revision_number DESC LIMIT 1""",
            (record["source"], record["source_item_id"]),
        ).fetchone()
        content_hash = record["content_hash"]
        revision = 1 if latest is None else int(latest["revision_number"]) + 1
        first_seen = record["collector_first_seen_time"]
        item_first_seen = (
            first_seen if latest is None else datetime.fromisoformat(latest["item_first_seen_time"])
        )
        published = record.get("source_published_time")
        latency = None
        if published is not None:
            latency = (first_seen - published).total_seconds()
        with self.connection:
            self.connection.execute(
                """INSERT INTO news_revisions VALUES
                (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    record["source"], record["source_item_id"], revision,
                    _iso(published) if published else None, _iso(first_seen),
                    _iso(item_first_seen), _iso(record["fetched_time"]),
                    record["headline"], record.get("body"), record.get("link"),
                    content_hash, record["cluster_id"], latency,
                ),
            )
        return revision, True

    def append_macro_observation(self, record: dict[str, Any]) -> tuple[int, bool]:
        """Append one first-seen BLS value or a later published revision."""
        latest = self.connection.execute(
            """SELECT * FROM macro_observations
            WHERE source=? AND series_id=? AND observation_period=?
            ORDER BY revision_number DESC LIMIT 1""",
            (record["source"], record["series_id"], record["observation_period"]),
        ).fetchone()
        content_hash = record["content_hash"]
        if latest is not None and latest["content_hash"] == content_hash:
            return int(latest["revision_number"]), False
        revision = 1 if latest is None else int(latest["revision_number"]) + 1
        payload = record["payload"]
        with self.connection:
            self.connection.execute(
                """INSERT INTO macro_observations VALUES
                (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    record["source"], record["series_id"],
                    record["observation_period"], revision,
                    _iso(record["collector_first_seen_time"]),
                    _iso(record["fetched_time"]), float(record["value"]),
                    record["unit"],
                    json.dumps(record.get("footnotes", []), separators=(",", ":")),
                    json.dumps(payload, sort_keys=True, separators=(",", ":")),
                    content_hash,
                ),
            )
        return revision, True

    def append_source_poll(self, record: dict[str, Any]) -> None:
        with self.connection:
            self.connection.execute(
                "INSERT INTO source_polls VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    record["poll_id"], record["source"],
                    _iso(record["fetched_time"]), record["status"],
                    record.get("payload_hash"), record.get("error_type"),
                    record.get("error"),
                ),
            )

    def latest_source_poll_time(self, source: str) -> datetime | None:
        row = self.connection.execute(
            "SELECT max(fetched_time) AS fetched FROM source_polls WHERE source=?",
            (source,),
        ).fetchone()
        return datetime.fromisoformat(row["fetched"]) if row and row["fetched"] else None

    def append_annotation(self, record: dict[str, Any]) -> None:
        source_key = (
            record["source"], record["source_item_id"], record["revision_number"]
        )
        news = self.connection.execute(
            """SELECT content_hash,headline,body FROM news_revisions
            WHERE source=? AND source_item_id=? AND revision_number=?""",
            source_key,
        ).fetchone()
        if news is None or news["content_hash"] != record["raw_content_hash"]:
            raise ValueError("annotation does not match an immutable news revision")
        if record["parsed_at"] < record["parse_started_at"]:
            raise ValueError("annotation parse completion precedes parse start")
        vector = record["annotation"]
        legacy_fields = {
            "event_type", "entities", "hawkishness", "inflation_impulse",
            "growth_impulse", "geopolitical_risk", "usd_impulse", "novelty",
            "confidence",
        }
        summary_fields = legacy_fields | {"summary_zh"}
        translated_fields = summary_fields | {"headline_zh"}
        classified_fields = translated_fields | {
            "primary_category", "secondary_categories", "emerging_topic_zh"
        }
        storyline_fields = classified_fields | {"story_subjects_zh"}
        event_claim_fields = classified_fields | {
            "record_kind", "actor", "action", "object", "location", "event_time",
            "claim_status", "materiality", "canonical_actor_id", "action_family",
            "canonical_object_id", "canonical_location_id", "episode_key",
            "primary_story_title_zh", "secondary_contexts_zh", "relation_to_prior",
        }
        material_event_fields = event_claim_fields | {
            "document_kind", "material_event_key", "source_organization_id",
            "evidence_role",
        }
        target_semantic_fields = set(
            news_annotation_schema(CURRENT_NEWS_PROMPT_VERSION)["required"]
        )
        if set(vector) not in (
            legacy_fields, summary_fields, translated_fields, classified_fields,
            storyline_fields, event_claim_fields, material_event_fields,
            target_semantic_fields,
        ):
            raise ValueError("annotation does not match frozen JSON schema fields")
        if set(vector) in (material_event_fields, target_semantic_fields):
            source_text = None
            if set(vector) == target_semantic_fields:
                source_text = f"{news['headline']}\n{news['body'] or ''}"
            validate_news_annotation(
                vector,
                prompt_version=record["prompt_version"],
                source_text=source_text,
            )
        if "summary_zh" in vector and not str(vector["summary_zh"]).strip():
            raise ValueError("annotation summary_zh is empty")
        if "headline_zh" in vector and not str(vector["headline_zh"]).strip():
            raise ValueError("annotation headline_zh is empty")
        if classified_fields <= set(vector):
            allowed_categories = NEWS_CATEGORIES
            if vector["primary_category"] not in allowed_categories:
                raise ValueError("annotation primary_category is not controlled")
            secondary = list(vector["secondary_categories"])
            if len(secondary) > 2 or len(set(secondary)) != len(secondary):
                raise ValueError("annotation secondary_categories exceeds limit")
            if any(value not in allowed_categories for value in secondary):
                raise ValueError("annotation secondary category is not controlled")
            if vector["primary_category"] in secondary:
                raise ValueError("annotation category cannot be both primary and secondary")
            if len(str(vector["emerging_topic_zh"])) > 16:
                raise ValueError("annotation emerging_topic_zh is too long")
        if event_claim_fields <= set(vector):
            if vector["record_kind"] not in RECORD_KINDS:
                raise ValueError("annotation record_kind is not controlled")
            if not 0.0 <= float(vector["materiality"]) <= 1.0:
                raise ValueError("annotation materiality is outside [0, 1]")
            contexts = list(vector["secondary_contexts_zh"])
            if len(contexts) > 2 or len(set(contexts)) != len(contexts):
                raise ValueError("annotation secondary contexts exceeds limit")
            core = vector["record_kind"] in {"FACT_EVENT", "OFFICIAL_CLAIM"}
            if vector["episode_key"] and (
                not core and vector["record_kind"] not in {
                    "MARKET_REACTION", "COMMENTARY_FORECAST", "BACKGROUND"
                }
            ):
                raise ValueError("annotation episode membership is invalid")
            if core and vector["episode_key"] and not (
                str(vector["canonical_actor_id"]).strip()
                and str(vector["action_family"]).strip()
                and (
                    str(vector["canonical_object_id"]).strip()
                    or str(vector["canonical_location_id"]).strip()
                )
            ):
                raise ValueError("core episode lacks actor/action/object-location identity")
        for name in (
            "hawkishness", "inflation_impulse", "growth_impulse",
            "geopolitical_risk", "usd_impulse",
        ):
            if not -1.0 <= float(vector[name]) <= 1.0:
                raise ValueError(f"annotation {name} is outside [-1, 1]")
        for name in ("novelty", "confidence"):
            if not 0.0 <= float(vector[name]) <= 1.0:
                raise ValueError(f"annotation {name} is outside [0, 1]")
        with self.connection:
            self.connection.execute(
                """INSERT INTO news_annotations VALUES
                (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    record["annotation_id"], *source_key,
                    record["raw_content_hash"], vector["event_type"],
                    json.dumps(vector["entities"], separators=(",", ":")),
                    vector["hawkishness"], vector["inflation_impulse"],
                    vector["growth_impulse"], vector["geopolitical_risk"],
                    vector["usd_impulse"], vector["novelty"], vector["confidence"],
                    record["llm_model_version"], record["prompt_version"],
                    _iso(record["parse_started_at"]), _iso(record["parsed_at"]),
                    json.dumps(vector, sort_keys=True, separators=(",", ":")),
                ),
            )

    def append_news_impact_assessment(self, record: dict[str, Any]) -> None:
        from .news_impact import (
            EVENT_STATES, IDENTITY_RELATIONS, IMPACT_CLASSES, UPDATE_TYPES,
        )

        source_key = (
            record["source"], record["source_item_id"], record["revision_number"]
        )
        news = self.connection.execute(
            """SELECT content_hash,length(body) AS body_character_count
               FROM news_revisions
            WHERE source=? AND source_item_id=? AND revision_number=?""",
            source_key,
        ).fetchone()
        if news is None or news["content_hash"] != record["raw_content_hash"]:
            raise ValueError("impact assessment does not match immutable news")
        annotation = self.connection.execute(
            "SELECT annotation_id FROM news_annotations WHERE annotation_id=?",
            (record["annotation_id"],),
        ).fetchone()
        if annotation is None:
            raise ValueError("impact assessment annotation is missing")
        if record["assessed_at"] < record["parse_started_at"]:
            raise ValueError("impact assessment completion precedes start")
        if record["impact_class"] not in IMPACT_CLASSES:
            raise ValueError("impact assessment class is not controlled")
        if record["event_state"] not in EVENT_STATES:
            raise ValueError("impact event state is not controlled")
        if record["update_type"] not in UPDATE_TYPES:
            raise ValueError("impact update type is not controlled")
        if not 0.0 <= float(record["confidence"]) <= 1.0:
            raise ValueError("impact confidence is outside [0, 1]")
        if not str(record["reason_zh"]).strip():
            raise ValueError("impact reason is empty")
        has_resolution = record.get("resolution_id") is not None
        from .news_impact import IMPACT_PROMPT_VERSION
        if record["prompt_version"] == IMPACT_PROMPT_VERSION and not has_resolution:
            raise ValueError("current impact assessment requires identity resolution")
        if has_resolution:
            if record.get("identity_relation") not in IDENTITY_RELATIONS:
                raise ValueError("impact identity relation is not controlled")
            if not str(record.get("canonical_episode_id") or "").strip():
                raise ValueError("canonical episode identity is empty")
            if not str(record.get("canonical_event_id") or "").strip():
                raise ValueError("canonical event identity is empty")
            source_context_mode = str(
                record.get("source_context_mode") or "COMPLETE_BODY"
            )
            if source_context_mode not in {"COMPLETE_BODY", "EVIDENCE_WINDOWS"}:
                raise ValueError("impact source context mode is not controlled")
            source_body_character_count = int(
                record.get("source_body_character_count")
                or news["body_character_count"]
            )
            if source_body_character_count <= 0:
                raise ValueError("impact source body character count is empty")
            comparison = {
                "identity_anchor_zh": record.get("identity_anchor_zh"),
                "core_fact_changes_zh": record.get("core_fact_changes_zh"),
                "identity_differences_zh": record.get("identity_differences_zh"),
                "context_differences_zh": record.get("context_differences_zh"),
                "source_context_mode": source_context_mode,
                "source_body_character_count": source_body_character_count,
            }
            if any(
                comparison[field] is None
                for field in (
                    "identity_anchor_zh", "core_fact_changes_zh",
                    "identity_differences_zh", "context_differences_zh",
                )
            ):
                raise ValueError("impact identity comparison is incomplete")
        with self.connection:
            self.connection.execute(
                """INSERT INTO news_impact_assessments_v1 VALUES
                (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    record["assessment_id"], *source_key,
                    record["raw_content_hash"], record["annotation_id"],
                    record["llm_model_version"], record["prompt_version"],
                    _iso(record["parse_started_at"]), _iso(record["assessed_at"]),
                    record["impact_class"], record["event_state"],
                    record["update_type"], float(record["confidence"]),
                    str(record["reason_zh"]).strip(),
                ),
            )
            if has_resolution:
                self.connection.execute(
                    """INSERT INTO news_event_identity_resolutions_v1 (
                    resolution_id,annotation_id,assessment_id,llm_model_version,
                    prompt_version,resolved_at,identity_relation,
                    matched_annotation_id,identity_comparison_json,
                    canonical_episode_id,canonical_event_id
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        record["resolution_id"], record["annotation_id"],
                        record["assessment_id"], record["llm_model_version"],
                        record["prompt_version"], _iso(record["assessed_at"]),
                        record["identity_relation"],
                        record.get("matched_annotation_id"),
                        json.dumps(
                            comparison, ensure_ascii=False, sort_keys=True,
                            separators=(",", ":"),
                        ),
                        record["canonical_episode_id"],
                        record["canonical_event_id"],
                    ),
                )

    def append_news_impact_failure(self, record: dict[str, Any]) -> None:
        with self.connection:
            self.connection.execute(
                """INSERT INTO news_impact_failures_v1 VALUES
                (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    record["failure_id"], record["source"],
                    record["source_item_id"], record["revision_number"],
                    record["raw_content_hash"], record["annotation_id"],
                    record["llm_model_version"], record["prompt_version"],
                    record["attempt_number"], record["error_type"],
                    record["error_signature"], record["error"],
                    _iso(record["failed_at"]),
                    _iso(record["next_retry_at"])
                    if record.get("next_retry_at") else None,
                    int(bool(record["is_terminal"])),
                ),
            )

    def append_title_translation(self, record: dict[str, Any]) -> None:
        source_key = (
            record["source"], record["source_item_id"], record["revision_number"]
        )
        news = self.connection.execute(
            """SELECT content_hash FROM news_revisions
            WHERE source=? AND source_item_id=? AND revision_number=?""",
            source_key,
        ).fetchone()
        if news is None or news["content_hash"] != record["raw_content_hash"]:
            raise ValueError("title translation does not match an immutable news revision")
        if record["parsed_at"] < record["parse_started_at"]:
            raise ValueError("title translation completion precedes start")
        headline_zh = str(record["headline_zh"]).strip()
        if not headline_zh:
            raise ValueError("translated headline is empty")
        with self.connection:
            self.connection.execute(
                """INSERT INTO news_title_translations VALUES
                (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    record["translation_id"], *source_key,
                    record["raw_content_hash"], headline_zh,
                    record["llm_model_version"], record["prompt_version"],
                    _iso(record["parse_started_at"]), _iso(record["parsed_at"]),
                ),
            )

    def append_llm_failure(self, record: dict[str, Any]) -> None:
        source_key = (
            record["source"], record["source_item_id"], record["revision_number"]
        )
        news = self.connection.execute(
            """SELECT content_hash FROM news_revisions
            WHERE source=? AND source_item_id=? AND revision_number=?""",
            source_key,
        ).fetchone()
        if news is None or news["content_hash"] != record["raw_content_hash"]:
            raise ValueError("LLM failure does not match an immutable news revision")
        next_retry = record.get("next_retry_at")
        if next_retry is not None and next_retry < record["failed_at"]:
            raise ValueError("LLM retry time precedes failure time")
        if record.get("is_terminal") and next_retry is not None:
            raise ValueError("terminal LLM failure cannot have a retry time")
        with self.connection:
            self.connection.execute(
                """INSERT INTO news_llm_failures VALUES
                (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    record["failure_id"], record["task_type"], *source_key,
                    record["raw_content_hash"], record["llm_model_version"],
                    record["prompt_version"], record["attempt_number"],
                    record["error_type"], record["error_signature"],
                    record["error"], _iso(record["failed_at"]),
                    _iso(next_retry) if next_retry else None,
                    int(bool(record.get("is_terminal"))),
                ),
            )

    def append_content_failure(self, record: dict[str, Any]) -> None:
        source_key = (
            record["source"], record["source_item_id"], record["revision_number"]
        )
        news = self.connection.execute(
            """SELECT content_hash FROM news_revisions
            WHERE source=? AND source_item_id=? AND revision_number=?""",
            source_key,
        ).fetchone()
        if news is None or news["content_hash"] != record["raw_content_hash"]:
            raise ValueError("content failure does not match an immutable news revision")
        next_retry = record.get("next_retry_at")
        if next_retry is not None and next_retry < record["failed_at"]:
            raise ValueError("content retry time precedes failure time")
        if record.get("is_terminal") and next_retry is not None:
            raise ValueError("terminal content failure cannot have a retry time")
        with self.connection:
            self.connection.execute(
                """INSERT INTO news_content_failures VALUES
                (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    record["failure_id"], *source_key,
                    record["raw_content_hash"], record["attempt_number"],
                    record["error_type"], record["error_signature"],
                    record["error"], _iso(record["failed_at"]),
                    _iso(next_retry) if next_retry else None,
                    int(bool(record.get("is_terminal"))),
                ),
            )

    def append_discovery_failure(self, record: dict[str, Any]) -> None:
        next_retry = record["next_retry_at"]
        if next_retry <= record["failed_at"]:
            raise ValueError("discovery retry time must follow failure time")
        with self.connection:
            self.connection.execute(
                """INSERT INTO news_discovery_failures VALUES
                (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    record["failure_id"], record["source"],
                    record["source_item_id"], record["attempt_number"],
                    record["error_type"], record["error"],
                    _iso(record["failed_at"]), _iso(next_retry),
                ),
            )

    def visible_news(self, decision_time: datetime) -> list[sqlite3.Row]:
        cutoff = _iso(decision_time)
        return self.connection.execute(
            """SELECT n.* FROM news_revisions n
            WHERE n.collector_first_seen_time <= ?
              AND NOT EXISTS (
                SELECT 1 FROM news_revisions newer
                WHERE newer.source=n.source
                  AND newer.source_item_id=n.source_item_id
                  AND newer.revision_number>n.revision_number
                  AND newer.collector_first_seen_time <= ?)
            ORDER BY n.source, n.source_item_id""",
            (cutoff, cutoff),
        ).fetchall()

    def visible_annotations(self, decision_time: datetime) -> list[sqlite3.Row]:
        cutoff = _iso(decision_time)
        return self.connection.execute(
            """SELECT a.* FROM news_annotations a
            JOIN news_revisions n USING(source, source_item_id, revision_number)
            WHERE a.parsed_at <= ? AND n.collector_first_seen_time <= ?
              AND a.raw_content_hash=n.content_hash
            ORDER BY a.source, a.source_item_id, a.revision_number""",
            (cutoff, cutoff),
        ).fetchall()

    def append_decision(self, record: dict[str, Any]) -> None:
        decision_time = record["decision_time"]
        snapshot = self.connection.execute(
            "SELECT * FROM market_snapshots WHERE snapshot_id=?",
            (record["snapshot_id"],),
        ).fetchone()
        if snapshot is None or snapshot["data_role"] != "FORWARD":
            raise ValueError("decision requires a FORWARD snapshot")
        if decision_time < self.forward_epoch:
            raise ValueError("decision predates FORWARD_EPOCH")
        visible = self.visible_news(decision_time)
        visible_hash = canonical_hash(
            [(row["source"], row["source_item_id"], row["content_hash"]) for row in visible]
        )
        with self.connection:
            self.connection.execute(
                "INSERT INTO decision_events VALUES (?, ?, ?, ?, ?, ?, 'WAIT', ?)",
                (
                    record["decision_id"], _iso(decision_time), record["snapshot_id"],
                    _iso(record["created_at"]), visible_hash, record["data_health"],
                    json.dumps(record.get("reason_codes", ()), separators=(",", ":")),
                ),
            )
            for prediction in record["predictions"]:
                self.connection.execute(
                    """INSERT INTO predictions VALUES
                    (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'WAIT', ?, ?)""",
                    (
                        record["decision_id"], prediction["model_version"],
                        prediction["model_identity"], snapshot["snapshot_hash"],
                        prediction.get("predicted_direction_u5"),
                        prediction.get("predicted_news_residual_u5"),
                        prediction.get("ev_long_u5"), prediction.get("ev_short_u5"),
                        prediction.get("uncertainty_u5"),
                        prediction.get("recommended_action", "WAIT"),
                        prediction["prediction_status"], _iso(record["created_at"]),
                    ),
                )
            from .shadow_simulation import append_shadow_intents

            append_shadow_intents(
                self.connection,
                record["decision_id"],
                decision_time,
                record["created_at"],
                record["predictions"],
            )

    def append_outcome(self, record: dict[str, Any]) -> None:
        decision = self.connection.execute(
            """SELECT d.decision_time, s.u5
            FROM decision_events d JOIN market_snapshots s USING(snapshot_id)
            WHERE d.decision_id=?""",
            (record["decision_id"],),
        ).fetchone()
        if decision is None:
            raise ValueError("outcome requires an existing decision")
        valid = record["outcome_status"] == "VALID"
        if valid:
            if record["exit_time"] < record["entry_time"]:
                raise ValueError("outcome exit precedes entry")
            if record["exit_time"] < record["entry_time"] + record["horizon"]:
                raise ValueError("outcome is earlier than the fixed horizon")
        with self.connection:
            self.connection.execute(
                """INSERT INTO outcomes VALUES
                (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    record["decision_id"],
                    _iso(record["entry_time"]) if record.get("entry_time") else None,
                    _iso(record["exit_time"]) if record.get("exit_time") else None,
                    _iso(record["appended_at"]), record["label_version"],
                    record["outcome_status"],
                    json.dumps(record.get("reason_codes", ()), separators=(",", ":")),
                    record.get("long_return"),
                    record.get("short_return"), record.get("direction_move"),
                    record.get("spread_quote_cost"), record.get("long_mfe"),
                    record.get("long_mae"), record.get("short_mfe"),
                    record.get("short_mae"), record.get("maximum_spread"),
                    record.get("quote_coverage"),
                    record["source_hash"],
                ),
            )
            from .shadow_simulation import append_shadow_results

            append_shadow_results(
                self.connection,
                record,
                decision["u5"],
            )

    def append_score(self, record: dict[str, Any]) -> None:
        with self.connection:
            self.connection.execute(
                "INSERT INTO prediction_scores VALUES (?, ?, ?, ?)",
                (
                    record["decision_id"], record["model_version"],
                    _iso(record["scored_at"]),
                    json.dumps(record["score"], sort_keys=True, separators=(",", ":")),
                ),
            )

    def mark_training_eligible(
        self, decision_id: str, eligible_at: datetime, version: str = "v1"
    ) -> None:
        row = self.connection.execute(
            """SELECT o.appended_at, o.outcome_status, d.decision_time,
                      s.data_role, s.u5, s.data_health
            FROM outcomes o JOIN decision_events d USING(decision_id)
            JOIN market_snapshots s USING(snapshot_id)
            WHERE o.decision_id=?""",
            (decision_id,),
        ).fetchone()
        if row is None:
            raise ValueError("training eligibility requires an appended outcome")
        if row["outcome_status"] != "VALID":
            raise ValueError("invalid outcomes cannot enter training")
        if row["data_role"] != "FORWARD":
            raise ValueError("WARMUP_ONLY data cannot enter training")
        if row["u5"] is None or row["data_health"] != "OK":
            raise ValueError("unhealthy or U5-warmup snapshots cannot enter training")
        if datetime.fromisoformat(row["decision_time"]) < self.forward_epoch:
            raise ValueError("pre-epoch data cannot enter training")
        if eligible_at < datetime.fromisoformat(row["appended_at"]):
            raise ValueError("training eligibility cannot precede outcome")
        score_count = self.connection.execute(
            "SELECT count(*) AS n FROM prediction_scores WHERE decision_id=?",
            (decision_id,),
        ).fetchone()["n"]
        prediction_count = self.connection.execute(
            "SELECT count(*) AS n FROM predictions WHERE decision_id=?",
            (decision_id,),
        ).fetchone()["n"]
        if score_count != prediction_count:
            raise ValueError("all old predictions must be scored before training")
        with self.connection:
            self.connection.execute(
                "INSERT INTO training_eligibility VALUES (?, ?, 'FORWARD', ?)",
                (decision_id, _iso(eligible_at), version),
            )

    def training_dataset_hash(self, cutoff: datetime) -> tuple[str, int]:
        rows = self.connection.execute(
            """SELECT e.decision_id, d.decision_time, s.snapshot_hash,
                      o.direction_move, o.spread_quote_cost, o.source_hash
            FROM training_eligibility e
            JOIN decision_events d USING(decision_id)
            JOIN market_snapshots s USING(snapshot_id)
            JOIN outcomes o USING(decision_id)
            WHERE e.eligible_at <= ? AND d.decision_time >= ?
            ORDER BY d.decision_time, e.decision_id""",
            (_iso(cutoff), _iso(self.forward_epoch)),
        ).fetchall()
        serializable = [tuple(row) for row in rows]
        return canonical_hash(serializable), len(serializable)

    def append_model_update(self, record: dict[str, Any]) -> None:
        expected_hash, rows = self.training_dataset_hash(record["training_cutoff"])
        if rows <= 0:
            raise ValueError("model update requires matured Forward training rows")
        if expected_hash != record["training_dataset_hash"]:
            raise ValueError("training dataset hash does not match cutoff evidence")
        with self.connection:
            self.connection.execute(
                """INSERT INTO model_updates VALUES
                (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'CHALLENGER')""",
                (
                    record["model_version"], record["model_identity"],
                    _iso(record["created_at"]), _iso(record["training_cutoff"]),
                    record["training_dataset_hash"], record["feature_version"],
                    record.get("news_prompt_version"),
                    json.dumps(record["hyperparameters"], sort_keys=True, separators=(",", ":")),
                    record["artifact_path"], record["artifact_hash"],
                ),
            )

    def count(self, table: str) -> int:
        if table not in IMMUTABLE_TABLES:
            raise ValueError("unknown evidence table")
        return int(self.connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0])
