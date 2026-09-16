"""Cached learning surfaces for the local dashboard."""

from __future__ import annotations

import sqlite3
import threading
from typing import Callable
from functools import partial

from xauusd_forecaster.dashboard.learning_curves import learning_curve_payload


LEARNING_REVISION_TABLES = (
    "derived_outcomes",
    "model_updates_v2",
    "predictions_v2",
    "prediction_scores_v2",
)


class LearningSurfaceOwner:
    """Rebuild learning resources only after append-only sources advance."""

    def __init__(
        self,
        *,
        learning_builder: Callable[[sqlite3.Connection], dict] = (
            partial(learning_curve_payload, exact_history=True)
        ),
    ) -> None:
        self._learning_builder = learning_builder
        self._lock = threading.Lock()
        self._cache: dict[str, object] = {}

    @staticmethod
    def revision(connection: sqlite3.Connection) -> tuple[object, ...]:
        database_row = connection.execute("PRAGMA database_list").fetchone()
        database_identity = (
            database_row[2]
            if database_row and database_row[2]
            else id(connection)
        )
        counts = tuple(
            int(connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0])
            for table in LEARNING_REVISION_TABLES
        )
        return (database_identity, *counts)

    def surfaces(self, connection: sqlite3.Connection) -> dict:
        revision = self.revision(connection)
        with self._lock:
            if self._cache.get("revision") != revision:
                self._cache.update({
                    "revision": revision,
                    "learning": self._learning_builder(connection),
                })
            return self._cache["learning"]
