"""Validate evaluation fixtures and behavior contracts.

The Phase G evaluation work starts with a stable golden dataset. Later metric
and runner tasks can rely on this file without redefining the sample schema.
These tests intentionally validate the fixture as data because the first
increment is about establishing the dataset contract rather than executing an
evaluator.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

RAG_ROOT = Path(__file__).resolve().parents[2]
GOLDEN_SET_PATH = RAG_ROOT / "tests" / "fixtures" / "golden_set.json"


@pytest.mark.unit
def test_golden_set_fixture_exists_and_contains_representative_cases() -> None:
    """Require the golden set fixture to exist and cover the shopping guide domain."""

    samples = _load_golden_set()

    assert len(samples) >= 3
    assert {sample["collection"] for sample in samples} == {"shopping_guides"}
    joined_questions = "\n".join(str(sample["question"]) for sample in samples)
    assert "无线耳机" in joined_questions
    assert "人体工学键盘" in joined_questions
    assert "解压玩具" in joined_questions


@pytest.mark.unit
def test_golden_set_samples_follow_required_schema() -> None:
    """Require every sample to expose fields needed by retrieval and generation metrics."""

    samples = _load_golden_set()
    sample_ids: set[str] = set()
    for sample in samples:
        sample_id = _required_non_empty_string(sample, "id")
        assert sample_id not in sample_ids
        sample_ids.add(sample_id)

        _required_non_empty_string(sample, "collection")
        _required_non_empty_string(sample, "question")
        _required_non_empty_string(sample, "golden_answer")
        expected_sources = _required_non_empty_string_list(sample, "expected_sources")
        expected_keywords = _required_non_empty_string_list(sample, "expected_keywords")

        assert all(source.startswith("shopping_guides/") for source in expected_sources)
        assert len(expected_keywords) >= 3
        assert len(str(sample["golden_answer"])) >= 30


def _load_golden_set() -> list[dict[str, Any]]:
    """Load the repository golden set fixture as a JSON array."""

    with GOLDEN_SET_PATH.open(encoding="utf-8") as file:
        payload = json.load(file)
    assert isinstance(payload, list)
    assert payload
    assert all(isinstance(item, dict) for item in payload)
    return payload


def _required_non_empty_string(sample: dict[str, Any], field_name: str) -> str:
    """Return a required non-empty string field from one golden sample."""

    value = sample.get(field_name)
    assert isinstance(value, str), f"{field_name} must be a string"
    assert value.strip(), f"{field_name} must not be blank"
    return value


def _required_non_empty_string_list(
    sample: dict[str, Any],
    field_name: str,
) -> list[str]:
    """Return a required non-empty string-list field from one golden sample."""

    value = sample.get(field_name)
    assert isinstance(value, list), f"{field_name} must be a list"
    assert value, f"{field_name} must not be empty"
    assert all(isinstance(item, str) and item.strip() for item in value)
    return value
