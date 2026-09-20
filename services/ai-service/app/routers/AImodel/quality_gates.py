"""Validate versioned Agent quality metrics independently of gate execution.

The deployment pipeline owns threshold execution. This module owns the strict
schema consumed by Agent evaluation producers: every metric declares its goal,
denominator, window, milestone lineage, and immutable threshold semantics.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

QualityOperator = Literal["eq", "gte", "lte", "gt", "lt"]


@dataclass(frozen=True, slots=True)
class QualityMetricDefinition:
    """Describe stable lineage metadata for one M3 quality metric."""

    target: str
    denominator: str
    window: str
    applicable_milestones: tuple[str, ...]


M3_METRIC_DICTIONARY: dict[str, QualityMetricDefinition] = {
    "M3-01": QualityMetricDefinition(
        "Shopping task success", "eligible shopping tasks", "rolling_7d", ("M3",)
    ),
    "M3-02": QualityMetricDefinition(
        "No hard constraint violations",
        "evaluated recommendations",
        "rolling_7d",
        ("M3",),
    ),
    "M3-03": QualityMetricDefinition(
        "Accurate product facts", "checked product claims", "rolling_7d", ("M3",)
    ),
    "M3-04": QualityMetricDefinition(
        "Evidence-backed key claims",
        "key recommendation claims",
        "rolling_7d",
        ("M3",),
    ),
    "M3-05": QualityMetricDefinition(
        "Ask required clarifications",
        "requests requiring clarification",
        "rolling_7d",
        ("M3",),
    ),
    "M3-06": QualityMetricDefinition(
        "Valid structured responses",
        "completed Agent responses",
        "rolling_7d",
        ("M3",),
    ),
    "M3-07": QualityMetricDefinition(
        "No unauthorized tool calls", "Agent tool calls", "rolling_7d", ("M3",)
    ),
    "M3-08": QualityMetricDefinition(
        "Confirm every side effect", "side-effect attempts", "rolling_7d", ("M3",)
    ),
    "M3-09": QualityMetricDefinition(
        "Recover from dependency failures",
        "recoverable dependency failures",
        "rolling_7d",
        ("M3",),
    ),
    "M3-10": QualityMetricDefinition(
        "Bound complete response latency",
        "completed Agent responses",
        "rolling_7d",
        ("M3",),
    ),
}


class AgentQualityCheck(BaseModel):
    """Define one numeric comparison in a quality metric."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    field: str = Field(min_length=1)
    operator: QualityOperator
    threshold: float


class AgentQualityMetric(BaseModel):
    """Define a measurable metric and the population that gives it meaning."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    metric_id: str = Field(pattern=r"^[A-Z0-9-]+$")
    target: str = Field(min_length=1)
    denominator: str = Field(min_length=1)
    window: str = Field(min_length=1)
    applicable_milestones: tuple[str, ...] = Field(min_length=1)
    checks: tuple[AgentQualityCheck, ...] = Field(min_length=1)

    @field_validator("target", "denominator", "window")
    @classmethod
    def _reject_blank_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("quality metric text fields cannot be blank")
        return normalized

    @model_validator(mode="after")
    def _unique_check_fields(self) -> AgentQualityMetric:
        fields = [check.field for check in self.checks]
        if len(fields) != len(set(fields)):
            raise ValueError(f"metric {self.metric_id} has duplicate check fields")
        return self


class AgentQualityGateConfig(BaseModel):
    """Represent one immutable, versioned Agent gate configuration."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1]
    config_version: str = Field(min_length=1)
    milestones: tuple[str, ...] = Field(min_length=1)
    metrics: tuple[AgentQualityMetric, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _validate_identity_and_milestones(self) -> AgentQualityGateConfig:
        metric_ids = [metric.metric_id for metric in self.metrics]
        if len(metric_ids) != len(set(metric_ids)):
            raise ValueError("quality metric IDs must be unique")
        known_milestones = set(self.milestones)
        for metric in self.metrics:
            unknown = set(metric.applicable_milestones) - known_milestones
            if unknown:
                raise ValueError(
                    f"metric {metric.metric_id} references unknown milestones "
                    f"{sorted(unknown)}"
                )
        return self

    def canonical_sha256(self) -> str:
        """Hash canonical JSON so reports bind to exact metric semantics."""

        payload = json.dumps(
            self.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()


class AgentQualityGateReport(BaseModel):
    """Bind evaluated values to a profile and exact config revision."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    profile_id: str = Field(min_length=1)
    config_version: str = Field(min_length=1)
    config_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    metrics: dict[str, dict[str, float]]


def assert_thresholds_not_relaxed(
    frozen: AgentQualityGateConfig,
    candidate: AgentQualityGateConfig,
) -> None:
    """Reject removed, changed, or numerically weaker frozen checks."""

    candidate_metrics = {metric.metric_id: metric for metric in candidate.metrics}
    for frozen_metric in frozen.metrics:
        candidate_metric = candidate_metrics.get(frozen_metric.metric_id)
        if candidate_metric is None:
            raise ValueError(f"threshold relaxation removed {frozen_metric.metric_id}")
        candidate_checks = {check.field: check for check in candidate_metric.checks}
        for frozen_check in frozen_metric.checks:
            candidate_check = candidate_checks.get(frozen_check.field)
            if candidate_check is None:
                raise ValueError(
                    "threshold relaxation removed "
                    f"{frozen_metric.metric_id}.{frozen_check.field}"
                )
            if candidate_check.operator != frozen_check.operator:
                raise ValueError(
                    "threshold relaxation changed operator for "
                    f"{frozen_metric.metric_id}.{frozen_check.field}"
                )
            if _is_relaxed(frozen_check, candidate_check):
                raise ValueError(
                    "threshold relaxation detected for "
                    f"{frozen_metric.metric_id}.{frozen_check.field}"
                )


def build_quality_gate_report(
    config: AgentQualityGateConfig,
    *,
    profile_id: str,
    metrics: dict[str, dict[str, float]],
) -> AgentQualityGateReport:
    """Create a report that records the evaluated config version and hash."""

    if profile_id not in config.milestones:
        raise ValueError(f"unknown quality profile: {profile_id}")
    known_metrics = {metric.metric_id for metric in config.metrics}
    unknown = set(metrics) - known_metrics
    if unknown:
        raise ValueError(f"quality report contains unknown metrics: {sorted(unknown)}")
    return AgentQualityGateReport(
        profile_id=profile_id,
        config_version=config.config_version,
        config_sha256=config.canonical_sha256(),
        metrics=metrics,
    )


def build_m3_quality_gate_config(
    raw_config: Mapping[str, Any],
) -> AgentQualityGateConfig:
    """Combine frozen pipeline thresholds with the A5 M3 metric dictionary.

    Args:
        raw_config: Parsed ``config/agent_quality_gates.yaml`` content.

    Returns:
        A strict, report-ready configuration containing all ten M3 metrics.

    Raises:
        ValueError: If the source omits M3, changes its frozen metric set, or
            lacks a metric/check definition.
    """

    schema_version = raw_config.get("schema_version")
    raw_metrics = raw_config.get("metrics")
    raw_milestones = raw_config.get("milestones")
    if not isinstance(raw_metrics, Mapping) or not isinstance(raw_milestones, Mapping):
        raise ValueError("quality gate source must define metrics and milestones")
    m3 = raw_milestones.get("M3")
    if not isinstance(m3, Mapping):
        raise ValueError("quality gate source must define milestone M3")
    metric_ids = tuple(m3.get("metric_ids") or ())
    expected_ids = set(M3_METRIC_DICTIONARY)
    if set(metric_ids) != expected_ids or len(metric_ids) != len(expected_ids):
        raise ValueError("M3 metric IDs do not match the frozen dictionary")

    definitions: list[dict[str, Any]] = []
    for metric_id in metric_ids:
        raw_metric = raw_metrics.get(metric_id)
        if not isinstance(raw_metric, Mapping):
            raise ValueError(f"quality gate source is missing metric {metric_id}")
        metadata = M3_METRIC_DICTIONARY[metric_id]
        definitions.append(
            {
                "metric_id": metric_id,
                "target": metadata.target,
                "denominator": metadata.denominator,
                "window": metadata.window,
                "applicable_milestones": metadata.applicable_milestones,
                "checks": raw_metric.get("checks"),
            }
        )

    return AgentQualityGateConfig.model_validate(
        {
            "schema_version": schema_version,
            "config_version": f"agent-quality-gates-v{schema_version}",
            "milestones": tuple(raw_milestones),
            "metrics": definitions,
        }
    )


def _is_relaxed(frozen: AgentQualityCheck, candidate: AgentQualityCheck) -> bool:
    """Return whether the candidate comparison is easier to pass."""

    if frozen.operator in {"gte", "gt"}:
        return candidate.threshold < frozen.threshold
    if frozen.operator in {"lte", "lt"}:
        return candidate.threshold > frozen.threshold
    return candidate.threshold != frozen.threshold
