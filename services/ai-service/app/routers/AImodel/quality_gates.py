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
from pathlib import Path
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


M3_METRIC_IDS = tuple(f"M3-{index:02d}" for index in range(1, 11))


def _metric_dictionary(
    raw_config: Mapping[str, Any],
) -> dict[str, QualityMetricDefinition]:
    raw_metrics = raw_config.get("metrics")
    if not isinstance(raw_metrics, Mapping):
        raise ValueError("quality gate source must define metrics")
    definitions: dict[str, QualityMetricDefinition] = {}
    for metric_id in M3_METRIC_IDS:
        raw_metric = raw_metrics.get(metric_id)
        if not isinstance(raw_metric, Mapping):
            raise ValueError(f"quality gate source is missing metric {metric_id}")
        try:
            definitions[metric_id] = QualityMetricDefinition(
                target=str(raw_metric["target"]),
                denominator=str(raw_metric["denominator"]),
                window=str(raw_metric["window"]),
                applicable_milestones=tuple(raw_metric["applicable_milestones"]),
            )
        except (KeyError, TypeError) as exc:
            raise ValueError(
                f"quality gate metric {metric_id} has incomplete lineage metadata"
            ) from exc
    return definitions


class _ConfigBackedM3Dictionary(Mapping[str, QualityMetricDefinition]):
    """Compatibility view whose values always come from the gate config."""

    @staticmethod
    def _load() -> dict[str, QualityMetricDefinition]:
        for parent in Path(__file__).resolve().parents:
            path = parent / "config" / "agent_quality_gates.yaml"
            if path.is_file():
                return _metric_dictionary(json.loads(path.read_text(encoding="utf-8")))
        raise RuntimeError("config/agent_quality_gates.yaml is unavailable")

    def __getitem__(self, key: str) -> QualityMetricDefinition:
        return self._load()[key]

    def __iter__(self):
        return iter(self._load())

    def __len__(self) -> int:
        return len(self._load())


M3_METRIC_DICTIONARY: Mapping[str, QualityMetricDefinition] = (
    _ConfigBackedM3Dictionary()
)


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
    source_config_sha256: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
        exclude=True,
    )

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

        if self.source_config_sha256 is not None:
            return self.source_config_sha256

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
    expected_ids = set(M3_METRIC_IDS)
    if set(metric_ids) != expected_ids or len(metric_ids) != len(expected_ids):
        raise ValueError("M3 metric IDs do not match the frozen dictionary")

    definitions: list[dict[str, Any]] = []
    for metric_id in metric_ids:
        raw_metric = raw_metrics.get(metric_id)
        if not isinstance(raw_metric, Mapping):
            raise ValueError(f"quality gate source is missing metric {metric_id}")
        definitions.append(
            {
                "metric_id": metric_id,
                "target": raw_metric.get("target"),
                "denominator": raw_metric.get("denominator"),
                "window": raw_metric.get("window"),
                "applicable_milestones": raw_metric.get("applicable_milestones"),
                "checks": raw_metric.get("checks"),
            }
        )

    return AgentQualityGateConfig.model_validate(
        {
            "schema_version": schema_version,
            "config_version": raw_config.get("config_version"),
            "milestones": tuple(raw_milestones),
            "metrics": definitions,
            "source_config_sha256": hashlib.sha256(
                json.dumps(
                    raw_config,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest(),
        }
    )


def _is_relaxed(frozen: AgentQualityCheck, candidate: AgentQualityCheck) -> bool:
    """Return whether the candidate comparison is easier to pass."""

    if frozen.operator in {"gte", "gt"}:
        return candidate.threshold < frozen.threshold
    if frozen.operator in {"lte", "lt"}:
        return candidate.threshold > frozen.threshold
    return candidate.threshold != frozen.threshold
