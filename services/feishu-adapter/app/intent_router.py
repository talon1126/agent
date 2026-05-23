import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


WAREHOUSE_INTENTS_PATH = Path(__file__).parent / "intents" / "warehouse.json"


@dataclass(frozen=True)
class IntentRoute:
    status: str
    intent: str
    executor: str
    confidence: float
    slots: dict[str, Any]
    signals: list[str]
    candidates: list[dict[str, Any]]
    reason: str
    clarification_question: str | None = None

    @property
    def matched(self) -> bool:
        return self.status == "matched"

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["matched"] = self.matched
        return data


def route_warehouse_intent(message: str) -> IntentRoute:
    config = _load_config(WAREHOUSE_INTENTS_PATH)
    normalized_message = _normalize(message)
    signals = _match_signals(normalized_message, config["lexicon"])
    slots = _extract_slots(normalized_message, config.get("slots", {}))
    _extract_sku(normalized_message, signals, slots)

    candidates = sorted(
        (
            _score_intent(intent_config, signals)
            for intent_config in config["intents"]
        ),
        key=lambda item: (item["confidence"], item["_priority"]),
        reverse=True,
    )
    for candidate in candidates:
        candidate.pop("_priority", None)
    if not candidates:
        return _fallback(signals, slots, [], "no_intents_configured")

    top = candidates[0]
    min_score = float(config.get("min_score", 0.65))
    if top["confidence"] < min_score:
        return _fallback(signals, slots, candidates, "confidence_below_threshold")

    second = candidates[1] if len(candidates) > 1 else None
    ambiguity_margin = float(config.get("ambiguity_margin", 0.25))
    # Close high scores are treated as ambiguity instead of executing a tool.
    if (
        second
        and second["confidence"] >= min_score
        and top["confidence"] - second["confidence"] < ambiguity_margin
    ):
        return IntentRoute(
            status="clarification_required",
            intent="unknown",
            executor="clarification",
            confidence=top["confidence"],
            slots=slots,
            signals=signals,
            candidates=candidates,
            reason="top_candidates_too_close",
            clarification_question=_build_clarification_question(candidates[:2], slots),
        )

    return IntentRoute(
        status="matched",
        intent=str(top["intent"]),
        executor=str(top["executor"]),
        confidence=top["confidence"],
        slots=slots,
        signals=signals,
        candidates=candidates,
        reason="matched_by_business_signals",
    )


def _load_config(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _normalize(message: str) -> str:
    text = message.casefold().strip()
    text = re.sub(r"@\S+", "", text)
    return re.sub(r"\s+", " ", text)


def _match_signals(message: str, lexicon: dict[str, list[str]]) -> list[str]:
    hits: list[str] = []
    for signal, aliases in lexicon.items():
        if any(alias.casefold() in message for alias in aliases):
            hits.append(signal)
    return hits


def _extract_slots(
    message: str,
    slot_config: dict[str, dict[str, list[str]]],
) -> dict[str, Any]:
    slots: dict[str, Any] = {}
    for slot_name, values in slot_config.items():
        for canonical_value, aliases in values.items():
            if any(alias.casefold() in message for alias in aliases):
                slots[slot_name] = canonical_value
                break
    return slots


def _extract_sku(message: str, signals: list[str], slots: dict[str, Any]) -> None:
    match = re.search(r"\bsku_[0-9a-z_]+\b", message, flags=re.IGNORECASE)
    if match:
        slots["sku"] = match.group(0).casefold()
        if "sku" not in signals:
            signals.append("sku")


def _score_intent(intent_config: dict[str, Any], signals: list[str]) -> dict[str, Any]:
    signal_set = set(signals)
    group_score = _required_group_score(
        intent_config.get("required_signal_groups", []),
        signal_set,
    )
    weights = intent_config.get("signal_weights", {})
    total_weight = sum(float(weight) for weight in weights.values()) or 1.0
    hit_weight = sum(
        float(weight)
        for signal, weight in weights.items()
        if signal in signal_set
    )
    weighted_score = hit_weight / total_weight

    return {
        "intent": intent_config["intent"],
        "label": intent_config.get("label", intent_config["intent"]),
        "executor": intent_config["executor"],
        "confidence": round(max(group_score, weighted_score), 2),
        "_priority": int(intent_config.get("priority", 0)),
    }


def _required_group_score(groups: list[list[str]], signal_set: set[str]) -> float:
    if not groups:
        return 0.0
    best = 0.0
    for group in groups:
        required = set(group)
        if not required:
            continue
        hit_ratio = len(required & signal_set) / len(required)
        # Partial required-group matches are useful evidence but should not execute.
        score = 0.72 if hit_ratio == 1.0 else hit_ratio * 0.5
        best = max(best, score)
    return best


def _build_clarification_question(
    candidates: list[dict[str, Any]],
    slots: dict[str, Any],
) -> str:
    labels = [str(candidate["label"]) for candidate in candidates]
    warehouse = _warehouse_label(slots.get("warehouse"))
    scope = f"{warehouse}" if warehouse else ""
    if len(labels) >= 2:
        return f"你是想{scope}{labels[0]}，还是{scope}{labels[1]}？"
    if labels:
        return f"你是想{scope}{labels[0]}吗？"
    return "你的仓储需求不够明确，请补充你想查询、同步还是创建视图。"


def _warehouse_label(warehouse: Any) -> str:
    return {
        "wh_hk_1": "香港仓",
        "wh_sz_1": "深圳仓",
        "wh_sg_1": "新加坡仓",
    }.get(warehouse, "")


def _fallback(
    signals: list[str],
    slots: dict[str, Any],
    candidates: list[dict[str, Any]],
    reason: str,
) -> IntentRoute:
    confidence = candidates[0]["confidence"] if candidates else 0.0
    return IntentRoute(
        status="fallback",
        intent="unknown",
        executor="warehouse_agent",
        confidence=confidence,
        slots=slots,
        signals=signals,
        candidates=candidates,
        reason=reason,
    )
