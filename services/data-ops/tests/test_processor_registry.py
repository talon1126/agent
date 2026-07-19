"""Protect generic dataset processor registration and lookup behavior.

These tests keep the registry independent from concrete website processors.
Failures usually indicate routing ambiguity, unsafe replacement of a registered
factory, or leakage of site-specific behavior into the generic layer.
"""

from __future__ import annotations

from collections.abc import Sequence

import pandas as pd
import pytest

from data_ops.core.contracts import DatasetContract, ProcessorResult
from data_ops.core.validation import ValidationIssue
from data_ops.processors.registry import (
    DuplicateProcessorError,
    InvalidProcessorError,
    UnknownDatasetTypeError,
    get_processor,
    register_processor,
)


def _contract(dataset_type: str) -> DatasetContract:
    """Build the smallest generic contract needed by registry test processors."""

    return DatasetContract(
        dataset_type=dataset_type,
        required_columns=("dataset_type", "batch_id"),
        column_types={"dataset_type": "string", "batch_id": "string"},
        unique_by=("batch_id",),
        normalized_filename_template="{dataset_type}_{batch_id}_normalized.csv",
        failed_filename_template="{dataset_type}_{batch_id}_failed.csv",
    )


class ExampleProcessor:
    """Provide a protocol-complete processor without any site-specific rules."""

    dataset_type = "registry_example"
    contract = _contract(dataset_type)

    def validate(self, frame: pd.DataFrame) -> Sequence[ValidationIssue]:
        """Accept every row so the tests isolate registry behavior."""

        return ()

    def normalize(self, frame: pd.DataFrame) -> pd.DataFrame:
        """Return a defensive copy of the input table."""

        return frame.copy()

    def split_results(
        self,
        frame: pd.DataFrame,
        issues: Sequence[ValidationIssue],
    ) -> ProcessorResult[pd.DataFrame]:
        """Return all rows as normalized and no failed rows."""

        return ProcessorResult(
            normalized_rows=frame,
            failed_rows=frame.iloc[0:0].copy(),
            summary={"input_rows": len(frame), "normalized_rows": len(frame), "failed_rows": 0},
        )


def test_register_and_get_processor_uses_factory() -> None:
    """A registered dataset type resolves to a newly created processor."""

    register_processor(ExampleProcessor.dataset_type, ExampleProcessor)

    first = get_processor(ExampleProcessor.dataset_type)
    second = get_processor(ExampleProcessor.dataset_type)

    assert isinstance(first, ExampleProcessor)
    assert isinstance(second, ExampleProcessor)
    assert first is not second


def test_register_processor_rejects_duplicate_dataset_type() -> None:
    """Duplicate registration cannot silently replace production routing."""

    dataset_type = "registry_duplicate"

    class DuplicateExample(ExampleProcessor):
        dataset_type = "registry_duplicate"
        contract = _contract("registry_duplicate")

    register_processor(dataset_type, DuplicateExample)

    with pytest.raises(DuplicateProcessorError, match=dataset_type):
        register_processor(dataset_type, DuplicateExample)


def test_get_processor_rejects_unknown_dataset_type() -> None:
    """Unknown routing keys return a stable, explicit error."""

    with pytest.raises(UnknownDatasetTypeError, match="registry_unknown"):
        get_processor("registry_unknown")


def test_get_processor_rejects_factory_with_mismatched_contract() -> None:
    """A factory cannot claim a routing key that differs from its contract."""

    class MismatchedProcessor(ExampleProcessor):
        dataset_type = "registry_mismatch_actual"
        contract = _contract(dataset_type)

    register_processor("registry_mismatch", MismatchedProcessor)

    with pytest.raises(InvalidProcessorError, match="registry_mismatch"):
        get_processor("registry_mismatch")


def test_second_processor_extension_requires_only_contract_and_registration() -> None:
    """A second dataset type plugs in without changing generic core behavior."""

    class SecondSiteProcessor(ExampleProcessor):
        dataset_type = "registry_second_site"
        contract = _contract(dataset_type)

    register_processor(SecondSiteProcessor.dataset_type, SecondSiteProcessor)

    resolved = get_processor(SecondSiteProcessor.dataset_type)

    assert isinstance(resolved, SecondSiteProcessor)
    assert resolved.contract.dataset_type == "registry_second_site"
