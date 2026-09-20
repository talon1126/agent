"""Validate the A2 shopping-agent Golden Set and generate its coverage report."""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Literal, Sequence

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SCENARIOS_PATH = ROOT / "fixtures" / "evals" / "shopping_agent_scenarios.json"
DEFAULT_ITEMS_PATH = ROOT / "fixtures" / "data" / "items.json"
DEFAULT_CATEGORIES_PATH = ROOT / "fixtures" / "data" / "categories.json"
DEFAULT_REPORT_PATH = ROOT / "docs" / "shopping_agent_scenario_coverage.md"

CORE_CATEGORIES = {
    "vague_need",
    "hard_constraint",
    "comparison",
    "review_summary",
    "constraint_conflict",
    "no_candidates",
    "tool_failure",
}
REQUIRED_TAGS = {
    "vague_need",
    "budget_boundary",
    "brand_preference",
    "brand_exclusion",
    "specification",
    "delivery_deadline",
    "comparison",
    "review_summary",
    "constraint_conflict",
    "no_candidates",
    "tool_timeout",
    "multi_turn",
    "modify_budget",
    "withdraw_preference",
    "add_exclusion",
}
SENSITIVE_PATTERNS = (
    re.compile(r"\b1[3-9]\d{9}\b"),
    re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),
    re.compile(r"(?i)(authorization|api[_-]?key|bearer\s+[a-z0-9])"),
)

ToolName = Literal[
    "search_product_catalog",
    "get_product_detail_from_link",
    "get_product_reviews",
    "get_inventory",
    "get_delivery_options",
]
ResponseType = Literal[
    "answer",
    "clarification",
    "product_list",
    "comparison",
    "recommendation",
    "fallback",
]
FailureMode = Literal["none", "no_candidates", "tool_timeout", "constraint_conflict"]
AssertionOperator = Literal[
    "eq",
    "ne",
    "lte",
    "gte",
    "in",
    "not_in",
    "contains",
    "exists",
]


class ScenarioValidationError(ValueError):
    """Report a cross-scenario or fixture-reference validation failure."""


class ScenarioTurn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=500)


class ScenarioPageContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    page_type: Literal["none", "search", "product", "cart"] = "none"
    route: str | None = Field(default=None, max_length=200)
    search_query: str | None = Field(default=None, max_length=120)
    current_item_id: str | None = None
    candidate_item_ids: list[str] = Field(default_factory=list, max_length=20)


class ExpectedShoppingGoal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category_id: str | None = None
    budget_min: float | None = Field(default=None, ge=0)
    budget_max: float | None = Field(default=None, ge=0)
    include_brands: list[str] = Field(default_factory=list)
    exclude_brands: list[str] = Field(default_factory=list)
    required_specs: list[str] = Field(default_factory=list)
    delivery_deadline: str | None = Field(default=None, max_length=80)
    quantity: int | None = Field(default=None, gt=0)
    candidate_item_ids: list[str] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def validate_budget_range(self) -> "ExpectedShoppingGoal":
        if (
            self.budget_min is not None
            and self.budget_max is not None
            and self.budget_min > self.budget_max
        ):
            raise ValueError("budget_min must not exceed budget_max")
        return self


class HardAssertion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    assertion_id: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    target: str = Field(min_length=1, max_length=160)
    operator: AssertionOperator
    expected: str | int | float | bool | list[str]


class ShoppingScenario(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scenario_id: str = Field(pattern=r"^shop_[a-z0-9_]+$")
    category: Literal[
        "vague_need",
        "hard_constraint",
        "comparison",
        "review_summary",
        "constraint_conflict",
        "no_candidates",
        "tool_failure",
    ]
    turns: list[ScenarioTurn] = Field(min_length=1)
    page_context: ScenarioPageContext
    expected_goal: ExpectedShoppingGoal
    required_tools: list[ToolName]
    forbidden_tools: list[ToolName]
    expected_response_type: ResponseType
    hard_assertions: list[HardAssertion] = Field(min_length=1)
    tags: list[str] = Field(min_length=1)
    failure_mode: FailureMode

    @model_validator(mode="after")
    def validate_scenario_invariants(self) -> "ShoppingScenario":
        for field_name, values in (
            ("required_tools", self.required_tools),
            ("forbidden_tools", self.forbidden_tools),
            ("tags", self.tags),
        ):
            if len(values) != len(set(values)):
                raise ValueError(f"{field_name} values must be unique")
        if set(self.required_tools) & set(self.forbidden_tools):
            raise ValueError("required_tools and forbidden_tools must be disjoint")
        assertion_ids = [item.assertion_id for item in self.hard_assertions]
        if len(assertion_ids) != len(set(assertion_ids)):
            raise ValueError("hard assertion ids must be unique within a scenario")
        if self.turns[0].role != "user" or self.turns[-1].role != "user":
            raise ValueError("scenario turns must start and end with a user turn")
        if any(
            previous.role == current.role
            for previous, current in zip(self.turns, self.turns[1:])
        ):
            raise ValueError("scenario turn roles must alternate")
        is_multi_turn = len(self.turns) > 1
        if is_multi_turn != ("multi_turn" in self.tags):
            raise ValueError("multi-turn scenarios must carry exactly one multi_turn tag")
        expected_failure_mode = {
            "constraint_conflict": "constraint_conflict",
            "no_candidates": "no_candidates",
            "tool_failure": "tool_timeout",
        }.get(self.category, "none")
        if self.failure_mode != expected_failure_mode:
            raise ValueError(
                f"failure_mode for {self.category} must be {expected_failure_mode}"
            )
        return self


class ScenarioMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    version: str
    description: str
    item_fixture: str


class ShoppingScenarioDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1]
    metadata: ScenarioMetadata
    scenarios: list[ShoppingScenario] = Field(min_length=40)

    @model_validator(mode="after")
    def validate_unique_scenario_ids(self) -> "ShoppingScenarioDocument":
        scenario_ids = [scenario.scenario_id for scenario in self.scenarios]
        if len(scenario_ids) != len(set(scenario_ids)):
            raise ValueError("scenario_id values must be globally unique")
        return self


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _collect_item_references(value: Any) -> set[str]:
    references: set[str] = set()
    if isinstance(value, dict):
        for key, child in value.items():
            if key in {"item_id", "current_item_id"} and child is not None:
                references.add(str(child))
            elif key in {"item_ids", "candidate_item_ids"}:
                references.update(str(item) for item in child)
            else:
                references.update(_collect_item_references(child))
    elif isinstance(value, list):
        for child in value:
            references.update(_collect_item_references(child))
    elif isinstance(value, str) and value.startswith("item_"):
        references.add(value)
    return references


def _validate_coverage(document: ShoppingScenarioDocument) -> None:
    category_counts = Counter(scenario.category for scenario in document.scenarios)
    missing_categories = sorted(CORE_CATEGORIES - set(category_counts))
    underfilled = sorted(
        category for category in CORE_CATEGORIES if category_counts[category] < 4
    )
    if missing_categories or underfilled:
        raise ScenarioValidationError(
            "core category coverage is incomplete: "
            f"missing={missing_categories}, underfilled={underfilled}"
        )

    all_tags = {tag for scenario in document.scenarios for tag in scenario.tags}
    missing_tags = sorted(REQUIRED_TAGS - all_tags)
    if missing_tags:
        raise ScenarioValidationError(f"required tags are missing: {missing_tags}")

    multi_turn = [scenario for scenario in document.scenarios if len(scenario.turns) > 1]
    if len(multi_turn) < 10:
        raise ScenarioValidationError("at least 10 multi-turn scenarios are required")
    for tag in ("modify_budget", "withdraw_preference", "add_exclusion"):
        if not any(tag in scenario.tags for scenario in multi_turn):
            raise ScenarioValidationError(f"multi-turn change tag is missing: {tag}")


def _validate_references(
    raw_document: dict[str, Any], items_path: Path, categories_path: Path
) -> None:
    item_ids = {str(item["item_id"]) for item in _load_json(items_path)}
    references = _collect_item_references(raw_document.get("scenarios", []))
    unknown_items = sorted(references - item_ids)
    if unknown_items:
        raise ScenarioValidationError(
            f"unknown fixture item references: {unknown_items}"
        )

    category_ids = {
        str(category["category_id"]) for category in _load_json(categories_path)
    }
    referenced_categories = {
        str(scenario.expected_goal.category_id)
        for scenario in ShoppingScenarioDocument.model_validate(raw_document).scenarios
        if scenario.expected_goal.category_id
    }
    unknown_categories = sorted(referenced_categories - category_ids)
    if unknown_categories:
        raise ScenarioValidationError(
            f"unknown fixture category references: {unknown_categories}"
        )


def _validate_sanitized_content(document: ShoppingScenarioDocument) -> None:
    for scenario in document.scenarios:
        text = "\n".join(turn.content for turn in scenario.turns)
        if any(pattern.search(text) for pattern in SENSITIVE_PATTERNS):
            raise ScenarioValidationError(
                f"scenario contains sensitive-looking content: {scenario.scenario_id}"
            )


def validate_scenario_document(
    raw_document: dict[str, Any],
    *,
    items_path: Path = DEFAULT_ITEMS_PATH,
    categories_path: Path = DEFAULT_CATEGORIES_PATH,
) -> ShoppingScenarioDocument:
    """Validate schema, coverage, fixture references, and sanitization."""

    document = ShoppingScenarioDocument.model_validate(raw_document)
    _validate_coverage(document)
    _validate_references(raw_document, items_path, categories_path)
    _validate_sanitized_content(document)
    return document


def coverage_summary(document: ShoppingScenarioDocument) -> dict[str, Any]:
    scenarios = document.scenarios
    return {
        "total_scenarios": len(scenarios),
        "categories": dict(sorted(Counter(item.category for item in scenarios).items())),
        "tags": dict(
            sorted(Counter(tag for item in scenarios for tag in item.tags).items())
        ),
        "response_types": dict(
            sorted(Counter(item.expected_response_type for item in scenarios).items())
        ),
        "failure_modes": dict(
            sorted(Counter(item.failure_mode for item in scenarios).items())
        ),
        "multi_turn": sum(len(item.turns) > 1 for item in scenarios),
        "multi_turn_changes": {
            tag: sum(tag in item.tags for item in scenarios if len(item.turns) > 1)
            for tag in ("modify_budget", "withdraw_preference", "add_exclusion")
        },
    }


def _coverage_table(title: str, counts: dict[str, int]) -> list[str]:
    lines = [f"## {title}", "", "| Value | Count |", "| --- | ---: |"]
    lines.extend(f"| `{name}` | {count} |" for name, count in counts.items())
    lines.append("")
    return lines


def render_coverage_report(document: ShoppingScenarioDocument) -> str:
    """Render a deterministic Markdown report from validated scenarios."""

    summary = coverage_summary(document)
    lines = [
        "<!-- generated by scripts/validate_shopping_agent_scenarios.py -->",
        "# Shopping Agent Scenario Coverage",
        "",
        f"- Golden Set: `{document.metadata.name}` `{document.metadata.version}`",
        f"- Total scenarios: **{summary['total_scenarios']}**",
        f"- Multi-turn scenarios: **{summary['multi_turn']}**",
        f"- Item fixture: `{document.metadata.item_fixture}`",
        "",
    ]
    lines.extend(_coverage_table("Category Coverage", summary["categories"]))
    lines.extend(_coverage_table("Tag Coverage", summary["tags"]))
    lines.extend(
        _coverage_table("Response Type Coverage", summary["response_types"])
    )
    lines.extend(_coverage_table("Failure Mode Coverage", summary["failure_modes"]))
    lines.extend(
        [
            "## Multi-turn Coverage",
            "",
            f"- Total: **{summary['multi_turn']}**",
            f"- Budget modifications: **{summary['multi_turn_changes']['modify_budget']}**",
            f"- Preference withdrawals: **{summary['multi_turn_changes']['withdraw_preference']}**",
            f"- Added exclusions: **{summary['multi_turn_changes']['add_exclusion']}**",
            "",
            "All counts are generated from the JSON fixture. Do not edit this report by hand.",
            "",
        ]
    )
    return "\n".join(lines)


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(content)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, default=DEFAULT_SCENARIOS_PATH)
    parser.add_argument("--items", type=Path, default=DEFAULT_ITEMS_PATH)
    parser.add_argument("--categories", type=Path, default=DEFAULT_CATEGORIES_PATH)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT_PATH)
    parser.add_argument(
        "--check-report",
        action="store_true",
        help="fail instead of writing when the generated report differs",
    )
    args = parser.parse_args(argv)

    try:
        raw_document = _load_json(args.input)
        document = validate_scenario_document(
            raw_document,
            items_path=args.items,
            categories_path=args.categories,
        )
        report = render_coverage_report(document)
        if args.check_report:
            if not args.report.is_file() or args.report.read_text(encoding="utf-8") != report:
                raise ScenarioValidationError(
                    f"coverage report is stale; regenerate {args.report}"
                )
        else:
            _write_text(args.report, report)
    except (OSError, json.JSONDecodeError, ValidationError, ScenarioValidationError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    print(json.dumps(coverage_summary(document), ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
