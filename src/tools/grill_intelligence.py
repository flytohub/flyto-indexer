"""Pure decision-intelligence helpers for the Grill workflow.

This module has no repository or persistence dependencies.  It keeps confidence,
value-of-information, acceptance criteria, and bounded adversarial review
deterministic so the same decision input always produces the same contract.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any


VALID_REVERSIBILITY = {"reversible", "partially_reversible", "irreversible"}
SEVERITY_VALUE = {"critical": 8.0, "high": 5.0, "medium": 3.0, "low": 1.0}
REVERSIBILITY_MULTIPLIER = {
    "reversible": 1.0,
    "partially_reversible": 1.5,
    "irreversible": 2.0,
}
MAX_ACCEPTANCE_ITEMS = 32
MAX_REVIEW_ITEMS = 12
MAX_ITEM_LENGTH = 1000


class DecisionIntelligenceError(ValueError):
    """Raised when decision-intelligence input is unsafe or malformed."""


def _bounded_list(
    value: Any,
    field: str,
    *,
    limit: int,
    item_limit: int = MAX_ITEM_LENGTH,
) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise DecisionIntelligenceError(f"{field} must be a string array")
    if len(value) > limit:
        raise DecisionIntelligenceError(f"{field} exceeds the limit of {limit}")
    normalized = []
    for item in value:
        item = item.strip()
        if not item:
            raise DecisionIntelligenceError(f"{field} cannot contain empty items")
        if len(item) > item_limit:
            raise DecisionIntelligenceError(
                f"{field} item exceeds {item_limit} characters"
            )
        if item not in normalized:
            normalized.append(item)
    return normalized


def _probability(value: Any, field: str, default: float) -> float:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise DecisionIntelligenceError(f"{field} must be a number between 0 and 1")
    value = float(value)
    if not 0.0 <= value <= 1.0:
        raise DecisionIntelligenceError(f"{field} must be between 0 and 1")
    return round(value, 3)


def _decision_cost(value: Any, severity: str) -> int:
    default = {"critical": 8, "high": 6, "medium": 4, "low": 2}[severity]
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 10:
        raise DecisionIntelligenceError("decision_cost must be an integer between 1 and 10")
    return value


def normalize_acceptance(raw: Any) -> dict:
    """Normalize machine-checkable and narrative acceptance criteria."""
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise DecisionIntelligenceError("acceptance must be an object")
    return {
        "expected_paths": _bounded_list(
            raw.get("expected_paths"), "acceptance.expected_paths", limit=MAX_ACCEPTANCE_ITEMS
        ),
        "forbidden_paths": _bounded_list(
            raw.get("forbidden_paths"), "acceptance.forbidden_paths", limit=MAX_ACCEPTANCE_ITEMS
        ),
        "expected_symbols": _bounded_list(
            raw.get("expected_symbols"),
            "acceptance.expected_symbols",
            limit=MAX_ACCEPTANCE_ITEMS,
        ),
        "forbidden_symbols": _bounded_list(
            raw.get("forbidden_symbols"),
            "acceptance.forbidden_symbols",
            limit=MAX_ACCEPTANCE_ITEMS,
        ),
        "assertions": _bounded_list(
            raw.get("assertions"), "acceptance.assertions", limit=MAX_ACCEPTANCE_ITEMS
        ),
        "proof_commands": _bounded_list(
            raw.get("proof_commands"),
            "acceptance.proof_commands",
            limit=MAX_ACCEPTANCE_ITEMS,
        ),
    }


def _adversarial_review(raw: dict, normalized: dict) -> dict:
    provided = raw.get("adversarial_review")
    if provided is None:
        provided = {}
    if not isinstance(provided, dict):
        raise DecisionIntelligenceError("adversarial_review must be an object")
    max_rounds = provided.get("max_rounds", 2)
    if (
        isinstance(max_rounds, bool)
        or not isinstance(max_rounds, int)
        or not 1 <= max_rounds <= 5
    ):
        raise DecisionIntelligenceError(
            "adversarial_review.max_rounds must be between 1 and 5"
        )
    alternatives = _bounded_list(
        provided.get("alternatives"),
        "adversarial_review.alternatives",
        limit=MAX_REVIEW_ITEMS,
    )
    if not alternatives:
        alternatives = [
            option["label"]
            for option in normalized["options"]
            if option.get("label") != normalized["recommendation"]
        ][:MAX_REVIEW_ITEMS]
    failure_conditions = _bounded_list(
        provided.get("failure_conditions", raw.get("failure_conditions")),
        "adversarial_review.failure_conditions",
        limit=MAX_REVIEW_ITEMS,
    )
    if not failure_conditions:
        failure_conditions = [
            "The selected approach violates a declared acceptance criterion.",
            "New repository evidence invalidates a prerequisite or recommendation.",
        ]
    objection = provided.get("strongest_objection", raw.get("counterargument"))
    if objection is None:
        objection = (
            "The recommendation may optimize the intended path without proving "
            "compatibility and failure behavior under the strongest alternative."
        )
    if not isinstance(objection, str) or not objection.strip():
        raise DecisionIntelligenceError(
            "adversarial_review.strongest_objection must be a non-empty string"
        )
    objection = objection.strip()
    if len(objection) > MAX_ITEM_LENGTH:
        raise DecisionIntelligenceError(
            f"adversarial_review.strongest_objection exceeds {MAX_ITEM_LENGTH} characters"
        )
    return {
        "bounded": True,
        "max_rounds": max_rounds,
        "rounds_completed": 1,
        "status": "generated",
        "strongest_objection": objection,
        "alternatives": alternatives,
        "failure_conditions": failure_conditions,
    }


def enrich_decision(raw: dict, normalized: dict) -> dict:
    """Return additive decision-intelligence fields for one normalized node."""
    confidence = raw.get("confidence")
    if confidence is None:
        confidence = {}
    if isinstance(confidence, (int, float)) and not isinstance(confidence, bool):
        confidence = {"recommendation": confidence}
    if not isinstance(confidence, dict):
        raise DecisionIntelligenceError("confidence must be a number or object")
    kind = normalized["kind"]
    defaults = (
        {"evidence": 0.5, "recommendation": 0.5, "human": 0.5}
        if kind == "decision"
        else {"evidence": 0.4, "recommendation": 0.5, "human": 1.0}
    )
    normalized_confidence = {
        field: _probability(confidence.get(field), f"confidence.{field}", default)
        for field, default in defaults.items()
    }
    reversibility = raw.get("reversibility", "partially_reversible")
    if reversibility not in VALID_REVERSIBILITY:
        raise DecisionIntelligenceError(
            f"reversibility must be one of {sorted(VALID_REVERSIBILITY)}"
        )
    decision_cost = _decision_cost(raw.get("decision_cost"), normalized["severity"])
    relevant_confidence = (
        normalized_confidence["evidence"]
        if kind == "fact"
        else normalized_confidence["recommendation"]
    )
    uncertainty = 1.0 - relevant_confidence
    value_of_information = round(
        SEVERITY_VALUE[normalized["severity"]]
        * max(0.1, uncertainty)
        * (1.0 + decision_cost / 10.0)
        * REVERSIBILITY_MULTIPLIER[reversibility],
        3,
    )
    return {
        "confidence": normalized_confidence,
        "decision_cost": decision_cost,
        "reversibility": reversibility,
        "value_of_information": value_of_information,
        "acceptance": normalize_acceptance(raw.get("acceptance")),
        "adversarial_review": _adversarial_review(raw, normalized),
    }


def finalize_adversarial_review(node: dict) -> dict:
    """Close the bounded static review when a human decision is answered."""
    review = deepcopy(node.get("adversarial_review") or {})
    if not review:
        return review
    review["rounds_completed"] = min(
        int(review.get("rounds_completed", 1)) + 1,
        int(review.get("max_rounds", 2)),
    )
    review["status"] = "closed"
    review["disposition"] = (
        "recommendation_accepted"
        if node.get("answer") == node.get("recommendation")
        else "alternative_selected"
    )
    return review
