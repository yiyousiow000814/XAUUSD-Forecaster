"""Canonical operational taxonomy and normalized event construction."""

from __future__ import annotations

import json
from functools import lru_cache
from importlib.resources import files
from typing import Mapping


_VALID_KINDS = {"ALERT", "FAILURE_REASON", "HEALTH_REASON"}
_VALID_ROLES = {"ROOT", "SYMPTOM", "STATE"}
_VALID_RECOVERY_POLICIES = {"AUTO", "CONDITIONAL", "OPERATOR"}
_VALID_SEVERITIES = {"ERROR", "WARNING", "INFO"}


@lru_cache(maxsize=1)
def operational_code_registry() -> dict[str, object]:
    path = files("xauusd_forecaster").joinpath("operational_codes.json")
    return json.loads(path.read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def operational_code_index() -> dict[str, dict[str, object]]:
    registry = operational_code_registry()
    return {str(item["code"]): item for item in registry["codes"]}


def validate_operational_code_registry() -> list[str]:
    registry = operational_code_registry()
    errors: list[str] = []
    seen: set[str] = set()
    for item in registry.get("codes", []):
        code = str(item.get("code") or "")
        if not code or code in seen:
            errors.append(f"duplicate or empty code: {code!r}")
        seen.add(code)
        if item.get("kind") not in _VALID_KINDS:
            errors.append(f"{code}: invalid kind")
        if item.get("default_role") not in _VALID_ROLES:
            errors.append(f"{code}: invalid default_role")
        if item.get("recovery_policy") not in _VALID_RECOVERY_POLICIES:
            errors.append(f"{code}: invalid recovery_policy")
        for field in ("category", "root_cause_family", "title_zh", "description"):
            if not str(item.get(field) or "").strip():
                errors.append(f"{code}: missing {field}")
        allowed = item.get("allowed_severities")
        if item.get("kind") == "ALERT" and (
            not isinstance(allowed, list) or not set(allowed) <= _VALID_SEVERITIES
        ):
            errors.append(f"{code}: invalid allowed_severities")
    return errors


def normalize_operational_event(
    code: str,
    *,
    severity: str,
    scope: str,
    message_zh: str,
    evidence: Mapping[str, object],
    blocking: bool = False,
    role: str | None = None,
) -> dict[str, object]:
    """Resolve stable metadata while keeping unknown runtime events visible."""
    metadata = operational_code_index().get(code)
    bounded_evidence = dict(evidence)
    if metadata is None:
        bounded_evidence["taxonomy_error"] = f"UNREGISTERED_OPERATIONAL_CODE:{code}"
        metadata = {
            "category": "RUNTIME",
            "root_cause_family": "UNREGISTERED_OPERATIONAL_CODE",
            "default_role": "ROOT",
            "recovery_policy": "OPERATOR",
        }
    return {
        "code": code,
        "severity": severity,
        "scope": scope,
        "message_zh": message_zh,
        "blocking": blocking,
        "evidence": bounded_evidence,
        "category": metadata["category"],
        "root_cause_family": metadata["root_cause_family"],
        "role": role or metadata["default_role"],
        "recovery_policy": metadata["recovery_policy"],
    }
