"""Small deterministic Ridge artifact used only for Shadow Challengers."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from pathlib import Path

import numpy as np

from .forward_ledger import canonical_hash


MIN_FEATURE_SCALE = 1e-12


@dataclass(frozen=True)
class RidgeArtifact:
    feature_names: tuple[str, ...]
    means: tuple[float, ...]
    scales: tuple[float, ...]
    coefficients: tuple[float, ...]
    intercept: float
    alpha: float
    training_dataset_hash: str
    residual_std: float
    training_rows: int
    weighting_version: str | None = None
    weight_summary: dict[str, Any] | None = None

    def predict(self, rows: np.ndarray) -> np.ndarray:
        matrix = np.asarray(rows, dtype=np.float64)
        scales = np.asarray(self.scales, dtype=np.float64)
        safe_scales = np.where(np.abs(scales) < MIN_FEATURE_SCALE, 1.0, scales)
        standardized = (
            matrix - np.asarray(self.means, dtype=np.float64)
        ) / safe_scales
        return self.intercept + standardized @ np.asarray(
            self.coefficients, dtype=np.float64
        )

    def as_dict(self) -> dict:
        payload = {
            "schema": "xauusd.forward.ridge.v2",
            "feature_names": list(self.feature_names),
            "means": list(self.means),
            "scales": list(self.scales),
            "coefficients": list(self.coefficients),
            "intercept": self.intercept,
            "alpha": self.alpha,
            "training_dataset_hash": self.training_dataset_hash,
            "residual_std": self.residual_std,
            "training_rows": self.training_rows,
        }
        if self.weighting_version is not None:
            payload["weighting_version"] = self.weighting_version
            payload["weight_summary"] = dict(self.weight_summary or {})
        return payload

    @property
    def artifact_hash(self) -> str:
        return canonical_hash(self.as_dict())

    def write(self, path: str | Path) -> None:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=False)
        target.write_text(
            json.dumps(self.as_dict(), indent=2, sort_keys=True), encoding="utf-8"
        )

    @classmethod
    def read(cls, path: str | Path) -> "RidgeArtifact":
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(
            feature_names=tuple(payload["feature_names"]),
            means=tuple(float(value) for value in payload["means"]),
            scales=tuple(float(value) for value in payload["scales"]),
            coefficients=tuple(float(value) for value in payload["coefficients"]),
            intercept=float(payload["intercept"]),
            alpha=float(payload["alpha"]),
            training_dataset_hash=str(payload["training_dataset_hash"]),
            residual_std=float(payload.get("residual_std", 0.0)),
            training_rows=int(payload.get("training_rows", 0)),
            weighting_version=payload.get("weighting_version"),
            weight_summary=payload.get("weight_summary"),
        )


def train_ridge(
    rows: np.ndarray,
    target: np.ndarray,
    feature_names: tuple[str, ...],
    alpha: float,
    training_dataset_hash: str,
    sample_weight: np.ndarray | None = None,
    weighting_version: str | None = None,
    weight_summary: dict[str, Any] | None = None,
) -> RidgeArtifact:
    matrix = np.asarray(rows, dtype=np.float64)
    values = np.asarray(target, dtype=np.float64)
    if matrix.ndim != 2 or values.ndim != 1 or len(matrix) != len(values):
        raise ValueError("Ridge inputs have incompatible shapes")
    if matrix.shape[1] != len(feature_names) or len(values) < 2:
        raise ValueError("Ridge needs named features and at least two rows")
    if not np.isfinite(matrix).all() or not np.isfinite(values).all():
        raise ValueError("Ridge inputs must be finite")
    if alpha <= 0:
        raise ValueError("Ridge alpha must be positive")
    weights = (
        np.ones(len(values), dtype=np.float64)
        if sample_weight is None else np.asarray(sample_weight, dtype=np.float64)
    )
    if weights.ndim != 1 or len(weights) != len(values):
        raise ValueError("Ridge sample weights have incompatible shape")
    if not np.isfinite(weights).all() or np.any(weights < 0) or weights.sum() <= 0:
        raise ValueError("Ridge sample weights must be finite, non-negative, and non-zero")
    normalized_weights = weights / weights.sum()
    means = np.sum(matrix * normalized_weights[:, None], axis=0)
    scales = np.sqrt(np.sum(
        np.square(matrix - means) * normalized_weights[:, None], axis=0
    ))
    scales[np.abs(scales) < MIN_FEATURE_SCALE] = 1.0
    standardized = (matrix - means) / scales
    target_mean = float(np.sum(values * normalized_weights))
    centered_target = values - target_mean
    gram = standardized.T @ (weights[:, None] * standardized)
    coefficients = np.linalg.solve(
        gram + alpha * np.eye(matrix.shape[1]),
        standardized.T @ (weights * centered_target),
    )
    fitted = target_mean + standardized @ coefficients
    residual_std = float(np.sqrt(np.sum(
        normalized_weights * np.square(values - fitted)
    )))
    return RidgeArtifact(
        feature_names=feature_names,
        means=tuple(float(value) for value in means),
        scales=tuple(float(value) for value in scales),
        coefficients=tuple(float(value) for value in coefficients),
        intercept=target_mean,
        alpha=float(alpha),
        training_dataset_hash=training_dataset_hash,
        residual_std=residual_std,
        training_rows=len(values),
        weighting_version=weighting_version,
        weight_summary=weight_summary,
    )
