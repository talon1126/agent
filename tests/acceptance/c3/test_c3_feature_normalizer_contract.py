"""Frozen acceptance contract for C3 category feature normalization."""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
SERVICE_ROOT = ROOT / "services" / "ai-service"
if str(SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICE_ROOT))

from app.routers.AImodel.feature_normalizer import (  # noqa: E402
    FeatureConfigError,
    FeatureDataType,
    FeatureMissingPolicy,
    FeatureNormalizationStatus,
    FeatureNormalizer,
    FeatureProfileRegistry,
    load_feature_profiles,
)
from app.routers.AImodel.product_models import (  # noqa: E402
    DeliveryCapability,
    FactSource,
    FactStatus,
    Freshness,
    ProductFact,
    ProductSnapshotItem,
    ProductSpecifications,
)


CASES = json.loads(
    (Path(__file__).with_name("feature_normalization_cases.json")).read_text(
        encoding="utf-8"
    )
)
NOW = datetime(2026, 9, 21, 12, tzinfo=UTC)
SOURCE = FactSource(
    provider="mock-api",
    endpoint="/products/snapshots",
    source_version="feature-normalization-v1",
    captured_at=NOW,
)


def known(value: object) -> ProductFact:
    return ProductFact(
        status=FactStatus.KNOWN,
        value=value,
        source=SOURCE,
        freshness=Freshness.from_observation(
            observed_at=NOW,
            captured_at=NOW,
            max_age_seconds=300,
        ),
    )


def unknown() -> ProductFact:
    return ProductFact(
        status=FactStatus.UNKNOWN,
        value=None,
        source=SOURCE,
        freshness=Freshness.unknown("fixture_missing"),
    )


def product(
    item_id: str,
    category: str,
    specifications: dict[str, str] | None,
) -> ProductSnapshotItem:
    return ProductSnapshotItem(
        item_id=item_id,
        name=known(item_id),
        category=known(category),
        brand=known("Talon"),
        current_price=known(Decimal("100")),
        currency=known("CNY"),
        stock=known(10),
        specifications=(
            known(ProductSpecifications.from_mapping(specifications))
            if specifications is not None
            else unknown()
        ),
        rating=known(Decimal("4.8")),
        review_count=known(100),
        delivery=known(
            DeliveryCapability(
                shipping_available=True,
                pickup_available=False,
                delivery_available=True,
            )
        ),
    )


def by_key(result) -> dict[str, object]:
    return {feature.key: feature for feature in result.features}


@pytest.fixture(scope="module")
def registry() -> FeatureProfileRegistry:
    return load_feature_profiles()


@pytest.fixture(scope="module")
def normalizer(registry: FeatureProfileRegistry) -> FeatureNormalizer:
    return FeatureNormalizer(registry)


def test_default_profiles_cover_two_products_for_each_major_fixture_category(
    registry: FeatureProfileRegistry,
    normalizer: FeatureNormalizer,
) -> None:
    assert {"electronics", "dairy", "office_supply"}.issubset(registry.profile_ids)

    for category, fixtures in CASES["profiles"].items():
        assert len(fixtures) >= 2
        profile = registry.resolve(category)
        assert profile.profile_id == category
        assert profile.features
        for fixture in fixtures:
            result = normalizer.normalize(
                product(
                    fixture["item_id"],
                    category,
                    fixture["specifications"],
                )
            )
            assert result.profile_id == category
            assert not result.errors
            assert any(
                feature.status is FeatureNormalizationStatus.KNOWN
                for feature in result.features
            )


def test_equivalent_units_have_equal_values_and_preserve_raw_lineage(
    normalizer: FeatureNormalizer,
) -> None:
    first, second = CASES["profiles"]["electronics"]
    normalized_first = by_key(
        normalizer.normalize(
            product(first["item_id"], "electronics", first["specifications"])
        )
    )
    normalized_second = by_key(
        normalizer.normalize(
            product(second["item_id"], "electronics", second["specifications"])
        )
    )

    assert normalized_first["memory_gb"].value == Decimal("8")
    assert normalized_second["memory_gb"].value == Decimal("8")
    assert normalized_first["storage_gb"].value == Decimal("512")
    assert normalized_second["storage_gb"].value == Decimal("512")
    assert normalized_first["screen_size_in"].value == Decimal("6.1")
    assert normalized_second["screen_size_in"].value == Decimal("6.1")
    assert normalized_second["memory_gb"].raw_key == "memory"
    assert normalized_second["memory_gb"].raw_value == "8192 MB"
    assert normalized_second["memory_gb"].unit == "GB"


def test_boolean_enum_and_field_capabilities_are_typed(
    normalizer: FeatureNormalizer,
) -> None:
    electronics = by_key(
        normalizer.normalize(
            product(
                "phone",
                "electronics",
                {"wireless_charging": "支持", "memory": "0 GB"},
            )
        )
    )
    office = by_key(
        normalizer.normalize(product("paper", "office_supply", {"颜色": "白色"}))
    )

    assert electronics["wireless_charging"].data_type is FeatureDataType.BOOLEAN
    assert electronics["wireless_charging"].value is True
    assert electronics["memory_gb"].value == Decimal("0")
    assert electronics["memory_gb"].larger_is_better is True
    assert electronics["memory_gb"].hard_filter is True
    assert electronics["memory_gb"].ranking is True
    assert electronics["memory_gb"].display is True
    assert office["color"].data_type is FeatureDataType.ENUM
    assert office["color"].value == "white"


def test_unknown_parse_failure_and_real_zero_are_distinct(
    normalizer: FeatureNormalizer,
) -> None:
    result = by_key(
        normalizer.normalize(
            product(
                "distinctions",
                "electronics",
                {
                    "memory": "0 GB",
                    "storage": "not-a-number",
                },
            )
        )
    )

    assert result["memory_gb"].status is FeatureNormalizationStatus.KNOWN
    assert result["memory_gb"].value == Decimal("0")
    assert result["storage_gb"].status is FeatureNormalizationStatus.ERROR
    assert result["storage_gb"].value is None
    assert result["storage_gb"].error.code == "invalid_number"
    assert result["screen_size_in"].status is FeatureNormalizationStatus.UNKNOWN
    assert result["screen_size_in"].value is None
    assert result["screen_size_in"].error is None
    assert (
        result["screen_size_in"].missing_policy is FeatureMissingPolicy.LOWER_CONFIDENCE
    )


@pytest.mark.parametrize(
    ("raw_value", "expected_code"),
    [
        ("8 parsec", "unknown_unit"),
        ("8 GB MB", "conflicting_units"),
        ("4096 GB", "out_of_range"),
    ],
)
def test_invalid_numeric_values_return_explicit_errors(
    normalizer: FeatureNormalizer,
    raw_value: str,
    expected_code: str,
) -> None:
    result = by_key(
        normalizer.normalize(product("broken", "electronics", {"memory": raw_value}))
    )

    feature = result["memory_gb"]
    assert feature.status is FeatureNormalizationStatus.ERROR
    assert feature.error.code == expected_code
    assert feature.error.path == "electronics.features.memory_gb"


def test_unknown_category_uses_fallback_and_never_promotes_arbitrary_specs(
    normalizer: FeatureNormalizer,
) -> None:
    result = normalizer.normalize(
        product(
            "unknown-category",
            "collectibles",
            {
                "model": "  Alpha   2 ",
                "color": "Blue",
                "seller_claim": "best in the universe",
            },
        )
    )

    assert result.profile_id == "fallback"
    assert {feature.key for feature in result.features} == {
        "color",
        "material",
        "model",
    }
    assert by_key(result)["model"].value == "Alpha 2"
    assert "seller_claim" not in {feature.raw_key for feature in result.features}


@pytest.mark.parametrize(
    ("config", "path"),
    [
        (
            {
                "unit_families": {},
                "profiles": {
                    "fallback": {
                        "features": {
                            "one": {
                                "display_name": "One",
                                "data_type": "text",
                                "aliases": ["same"],
                            },
                            "two": {
                                "display_name": "Two",
                                "data_type": "text",
                                "aliases": ["SAME"],
                            },
                        }
                    }
                },
            },
            "profiles.fallback.features.two.aliases",
        ),
        (
            {
                "unit_families": {},
                "profiles": {
                    "fallback": {
                        "features": {
                            "broken": {
                                "display_name": "Broken",
                                "data_type": "number",
                                "minimum": "10",
                                "maximum": "1",
                            }
                        }
                    }
                },
            },
            "profiles.fallback.features.broken.maximum",
        ),
        (
            {
                "unit_families": {},
                "profiles": {
                    "fallback": {
                        "features": {
                            "broken": {
                                "display_name": "Broken",
                                "data_type": "number",
                                "unit_family": "missing",
                            }
                        }
                    }
                },
            },
            "profiles.fallback.features.broken.unit_family",
        ),
        (
            {
                "unit_families": {},
                "profiles": {
                    "fallback": {
                        "features": {
                            "known": {
                                "display_name": "Known",
                                "data_type": "text",
                            }
                        },
                        "ranking_weights": {"missing": "1"},
                    }
                },
            },
            "profiles.fallback.ranking_weights.missing",
        ),
    ],
)
def test_invalid_profiles_fail_with_a_specific_config_path(
    config: dict[str, object],
    path: str,
) -> None:
    with pytest.raises(FeatureConfigError, match=path.replace(".", r"\.")):
        FeatureProfileRegistry.from_mapping(config)


def test_new_profile_is_data_driven_without_normalizer_changes() -> None:
    registry = FeatureProfileRegistry.from_mapping(
        {
            "unit_families": {
                "mass": {
                    "canonical_unit": "g",
                    "conversions": {"g": "1", "kg": "1000"},
                }
            },
            "profiles": {
                "fallback": {"features": {}},
                "pet_food": {
                    "category_aliases": ["pet_food", "宠物食品"],
                    "features": {
                        "net_weight_g": {
                            "display_name": "净重",
                            "data_type": "number",
                            "aliases": ["weight", "净重"],
                            "unit_family": "mass",
                            "minimum": "0",
                            "maximum": "100000",
                            "missing_policy": "not_comparable",
                            "hard_filter": True,
                            "ranking": True,
                            "display": True,
                        }
                    },
                },
            },
        }
    )
    result = FeatureNormalizer(registry).normalize(
        product("pet-food", "宠物食品", {"净重": "1.5 kg"})
    )

    feature = by_key(result)["net_weight_g"]
    assert result.profile_id == "pet_food"
    assert feature.value == Decimal("1500")
    assert feature.unit == "g"
