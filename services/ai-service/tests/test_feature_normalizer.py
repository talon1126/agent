"""Unit coverage for C3 profile validation and normalization edge cases."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.routers.AImodel.feature_normalizer import (
    FeatureConfigError,
    FeatureNormalizationStatus,
    FeatureNormalizer,
    FeatureProfileRegistry,
    load_feature_profiles,
)
from app.routers.AImodel.product_models import (
    DeliveryCapability,
    FactSource,
    FactStatus,
    Freshness,
    ProductFact,
    ProductSnapshotItem,
    ProductSpecifications,
)


NOW = datetime(2026, 9, 21, 12, tzinfo=UTC)
SOURCE = FactSource(
    provider="mock-api",
    endpoint="/products/snapshots",
    source_version="feature-test-v1",
    captured_at=NOW,
)


def fact(value: object) -> ProductFact:
    return ProductFact(
        status=FactStatus.KNOWN,
        value=value,
        source=SOURCE,
        freshness=Freshness.from_observation(
            observed_at=NOW,
            captured_at=NOW,
            max_age_seconds=60,
        ),
    )


def unknown() -> ProductFact:
    return ProductFact(
        status=FactStatus.UNKNOWN,
        value=None,
        source=SOURCE,
        freshness=Freshness.unknown("not_supplied"),
    )


def product(
    specifications: dict[str, str] | None,
    *,
    category: str = "electronics",
) -> ProductSnapshotItem:
    return ProductSnapshotItem(
        item_id="sku-feature",
        name=fact("Feature product"),
        category=fact(category),
        brand=fact("Talon"),
        current_price=fact(Decimal("99")),
        currency=fact("CNY"),
        stock=fact(3),
        specifications=(
            fact(ProductSpecifications.from_mapping(specifications))
            if specifications is not None
            else unknown()
        ),
        rating=fact(Decimal("4.5")),
        review_count=fact(50),
        delivery=fact(
            DeliveryCapability(
                shipping_available=True,
                pickup_available=False,
                delivery_available=True,
            )
        ),
    )


def feature(result, key: str):
    return next(item for item in result.features if item.key == key)


def test_default_profile_file_loads_and_is_deterministic() -> None:
    first = load_feature_profiles()
    second = load_feature_profiles()

    assert first.profile_ids == second.profile_ids
    assert first.profile_ids == (
        "dairy",
        "electronics",
        "fallback",
        "office_supply",
    )
    assert first.resolve("手机").profile_id == "electronics"
    assert first.resolve("unconfigured").profile_id == "fallback"


def test_missing_specification_fact_produces_unknowns_without_errors() -> None:
    result = FeatureNormalizer(load_feature_profiles()).normalize(product(None))

    assert result.errors == ()
    assert result.features
    assert all(
        item.status is FeatureNormalizationStatus.UNKNOWN for item in result.features
    )


@pytest.mark.parametrize(
    ("specifications", "key", "code"),
    [
        ({"wireless_charging": "sometimes"}, "wireless_charging", "invalid_boolean"),
        ({"color": "red"}, "color", "invalid_enum"),
    ],
)
def test_invalid_typed_values_are_returned_as_errors(
    specifications: dict[str, str],
    key: str,
    code: str,
) -> None:
    category = "office_supply" if key == "color" else "electronics"
    result = FeatureNormalizer(load_feature_profiles()).normalize(
        product(specifications, category=category)
    )

    assert feature(result, key).error.code == code
    assert result.errors[0].code == code


def test_two_source_aliases_for_one_feature_are_not_silently_selected() -> None:
    result = FeatureNormalizer(load_feature_profiles()).normalize(
        product({"RAM": "8 GB", "ram": "16 GB"})
    )

    normalized = feature(result, "memory_gb")
    assert normalized.status is FeatureNormalizationStatus.ERROR
    assert normalized.error.code == "conflicting_aliases"
    assert normalized.value is None


def test_normalized_results_are_deeply_read_only() -> None:
    result = FeatureNormalizer(load_feature_profiles()).normalize(
        product({"memory": "8 GB"})
    )

    with pytest.raises(ValidationError):
        feature(result, "memory_gb").value = Decimal("16")


def test_profile_loader_reports_yaml_and_schema_paths(tmp_path: Path) -> None:
    malformed = tmp_path / "malformed.yaml"
    malformed.write_text("profiles: [", encoding="utf-8")
    with pytest.raises(FeatureConfigError, match="cannot load"):
        load_feature_profiles(malformed)

    invalid = tmp_path / "invalid.yaml"
    invalid.write_text(
        "schema_version: v1\nprofiles:\n  fallback:\n    unexpected: true\n"
        "    features: {}\n",
        encoding="utf-8",
    )
    with pytest.raises(FeatureConfigError, match=r"profiles\.fallback\.unexpected"):
        load_feature_profiles(invalid)


def test_duplicate_category_aliases_are_rejected() -> None:
    with pytest.raises(FeatureConfigError, match=r"profiles\.second\.category_aliases"):
        FeatureProfileRegistry.from_mapping(
            {
                "profiles": {
                    "fallback": {"features": {}},
                    "first": {
                        "category_aliases": ["shared"],
                        "features": {},
                    },
                    "second": {
                        "category_aliases": ["SHARED"],
                        "features": {},
                    },
                }
            }
        )


def test_duplicate_aliases_within_one_category_profile_are_rejected() -> None:
    with pytest.raises(
        FeatureConfigError, match=r"profiles\.electronics\.category_aliases"
    ):
        FeatureProfileRegistry.from_mapping(
            {
                "profiles": {
                    "fallback": {"features": {}},
                    "electronics": {
                        "category_aliases": ["mobile", "MOBILE"],
                        "features": {},
                    },
                }
            }
        )


def test_duplicate_aliases_within_one_feature_are_rejected() -> None:
    with pytest.raises(
        FeatureConfigError, match=r"profiles\.electronics\.features\.memory\.aliases"
    ):
        FeatureProfileRegistry.from_mapping(
            {
                "profiles": {
                    "fallback": {"features": {}},
                    "electronics": {
                        "features": {
                            "memory": {
                                "display_name": "Memory",
                                "data_type": "text",
                                "aliases": ["RAM", "ram"],
                            }
                        }
                    },
                }
            }
        )


def test_unitless_number_rejects_an_unconfigured_unit() -> None:
    registry = FeatureProfileRegistry.from_mapping(
        {
            "profiles": {
                "fallback": {"features": {}},
                "simple": {
                    "features": {
                        "score": {
                            "display_name": "Score",
                            "data_type": "number",
                        }
                    }
                },
            }
        }
    )
    result = FeatureNormalizer(registry).normalize(
        product({"score": "10 points"}, category="simple")
    )

    assert feature(result, "score").error.code == "unknown_unit"


def test_extremely_large_number_returns_range_error_instead_of_raising() -> None:
    result = FeatureNormalizer(load_feature_profiles()).normalize(
        product({"memory": f"{'9' * 100} GB"})
    )

    normalized = feature(result, "memory_gb")
    assert normalized.status is FeatureNormalizationStatus.ERROR
    assert normalized.error.code == "out_of_range"


def test_large_unitless_number_is_not_rounded_by_decimal_context() -> None:
    raw_value = "1234567890123456789012345678901234567890"
    registry = FeatureProfileRegistry.from_mapping(
        {
            "profiles": {
                "fallback": {"features": {}},
                "simple": {
                    "features": {
                        "serial": {
                            "display_name": "Serial",
                            "data_type": "number",
                            "maximum": "9" * 50,
                        }
                    }
                },
            }
        }
    )
    result = FeatureNormalizer(registry).normalize(
        product({"serial": raw_value}, category="simple")
    )

    assert feature(result, "serial").value == Decimal(raw_value)


def test_long_invalid_value_still_returns_a_structured_error() -> None:
    raw_value = "x" * 512
    result = FeatureNormalizer(load_feature_profiles()).normalize(
        product({"wireless_charging": raw_value})
    )

    normalized = feature(result, "wireless_charging")
    assert normalized.status is FeatureNormalizationStatus.ERROR
    assert normalized.error.code == "invalid_boolean"
    assert normalized.error.raw_value == raw_value
    assert len(normalized.error.message) <= 256


def test_canonical_unit_conversion_factor_must_equal_one() -> None:
    with pytest.raises(
        FeatureConfigError,
        match=r"unit_families\.storage\.conversions\.GB",
    ):
        FeatureProfileRegistry.from_mapping(
            {
                "unit_families": {
                    "storage": {
                        "canonical_unit": "GB",
                        "conversions": {"GB": "2", "MB": "0.001"},
                    }
                },
                "profiles": {"fallback": {"features": {}}},
            }
        )
