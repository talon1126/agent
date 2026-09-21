"""Configuration-driven normalization for comparable product features."""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation, localcontext
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Any, Literal, Mapping, Self

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationError,
    field_validator,
    model_validator,
)

from .product_models import FactStatus, ProductSnapshotItem


DEFAULT_FEATURE_PROFILE_PATH = Path(__file__).with_name("feature_profiles.yaml")
ProfileText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=128),
]
DisplayText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=256),
]
ErrorPath = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=512),
]
_NUMBER_PATTERN = re.compile(
    r"^([+-]?(?:\d+(?:\.\d*)?|\.\d+))\s*(.*?)$",
    re.DOTALL,
)


class FeatureConfigError(ValueError):
    """A feature profile is invalid and cannot be loaded safely."""


class FeatureDataType(StrEnum):
    NUMBER = "number"
    BOOLEAN = "boolean"
    ENUM = "enum"
    TEXT = "text"


class FeatureMissingPolicy(StrEnum):
    IGNORE_SCORE = "ignore_score"
    LOWER_CONFIDENCE = "lower_confidence"
    NOT_COMPARABLE = "not_comparable"


class FeatureNormalizationStatus(StrEnum):
    KNOWN = "known"
    UNKNOWN = "unknown"
    ERROR = "error"


class UnitFamilyConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    canonical_unit: ProfileText
    conversions: dict[ProfileText, Decimal]
    precision: int = Field(default=6, ge=0, le=18)

    @field_validator("conversions")
    @classmethod
    def validate_conversions(cls, value: dict[str, Decimal]) -> dict[str, Decimal]:
        if not value:
            raise ValueError("at least one unit conversion is required")
        if any(factor <= 0 for factor in value.values()):
            raise ValueError("unit conversion factors must be positive")
        return value


class FeatureDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    display_name: DisplayText
    data_type: FeatureDataType
    aliases: tuple[ProfileText, ...] = ()
    unit_family: ProfileText | None = None
    minimum: Decimal | None = None
    maximum: Decimal | None = None
    larger_is_better: bool | None = None
    missing_policy: FeatureMissingPolicy = FeatureMissingPolicy.IGNORE_SCORE
    hard_filter: bool = False
    ranking: bool = False
    display: bool = True
    enum_values: tuple[ProfileText, ...] = ()
    enum_aliases: dict[ProfileText, ProfileText] = Field(default_factory=dict)
    true_values: tuple[ProfileText, ...] = (
        "true",
        "yes",
        "1",
        "是",
        "支持",
        "有",
    )
    false_values: tuple[ProfileText, ...] = (
        "false",
        "no",
        "0",
        "否",
        "不支持",
        "无",
    )

    @model_validator(mode="after")
    def validate_type_options(self) -> Self:
        if self.data_type is not FeatureDataType.NUMBER and any(
            value is not None
            for value in (
                self.unit_family,
                self.minimum,
                self.maximum,
                self.larger_is_better,
            )
        ):
            raise ValueError("unit, range and direction require a number feature")
        if self.data_type is FeatureDataType.ENUM and not self.enum_values:
            raise ValueError("enum feature requires enum_values")
        if self.data_type is not FeatureDataType.ENUM and (
            self.enum_values or self.enum_aliases
        ):
            raise ValueError("enum values and aliases require an enum feature")
        return self


class CategoryProfileConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    category_aliases: tuple[ProfileText, ...] = ()
    features: dict[ProfileText, FeatureDefinition]
    ranking_weights: dict[ProfileText, Decimal] = Field(default_factory=dict)

    @field_validator("ranking_weights")
    @classmethod
    def validate_weights(cls, value: dict[str, Decimal]) -> dict[str, Decimal]:
        if any(weight < 0 for weight in value.values()):
            raise ValueError("ranking weights cannot be negative")
        return value


class FeatureProfileDocument(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["v1"] = "v1"
    unit_families: dict[ProfileText, UnitFamilyConfig] = Field(default_factory=dict)
    profiles: dict[ProfileText, CategoryProfileConfig]


class CategoryFeatureProfile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    profile_id: ProfileText
    category_aliases: tuple[ProfileText, ...]
    features: dict[ProfileText, FeatureDefinition]
    ranking_weights: dict[ProfileText, Decimal]


class FeatureNormalizationError(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    code: ProfileText
    path: ErrorPath
    message: DisplayText
    raw_value: str | None = None


NormalizedValue = Decimal | bool | str


class NormalizedFeature(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    key: ProfileText
    display_name: DisplayText
    data_type: FeatureDataType
    status: FeatureNormalizationStatus
    raw_key: str | None = None
    raw_value: str | None = None
    value: NormalizedValue | None = None
    unit: str | None = None
    larger_is_better: bool | None = None
    missing_policy: FeatureMissingPolicy
    hard_filter: bool
    ranking: bool
    display: bool
    error: FeatureNormalizationError | None = None

    @model_validator(mode="after")
    def validate_status_shape(self) -> Self:
        if self.status is FeatureNormalizationStatus.KNOWN:
            if self.value is None or self.error is not None:
                raise ValueError("known normalized feature requires only a value")
        elif self.status is FeatureNormalizationStatus.UNKNOWN:
            if self.value is not None or self.error is not None:
                raise ValueError(
                    "unknown normalized feature cannot carry value or error"
                )
        elif self.value is not None or self.error is None:
            raise ValueError("normalization error requires only an error")
        return self


class NormalizedProductFeatures(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["v1"] = "v1"
    item_id: ProfileText
    category: str | None
    profile_id: ProfileText
    features: tuple[NormalizedFeature, ...]
    errors: tuple[FeatureNormalizationError, ...]


class _NormalizationFailure(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _identity(value: str) -> str:
    return " ".join(value.strip().split()).casefold()


def _unit_identity(value: str) -> str:
    return "".join(value.strip().split()).casefold().replace("²", "2")


def _validation_path(error: Mapping[str, Any]) -> str:
    return ".".join(str(part) for part in error.get("loc", ())) or "config"


def _config_error(path: str, message: str) -> FeatureConfigError:
    return FeatureConfigError(f"{path}: {message}")


class FeatureProfileRegistry:
    """Validated profiles plus normalized category, feature, and unit indexes."""

    def __init__(self, document: FeatureProfileDocument) -> None:
        self._document = document
        self._profiles = {
            profile_id: CategoryFeatureProfile(
                profile_id=profile_id,
                category_aliases=profile.category_aliases,
                features=profile.features,
                ranking_weights=profile.ranking_weights,
            )
            for profile_id, profile in document.profiles.items()
        }
        self._category_index: dict[str, str] = {}
        self._feature_indexes: dict[str, dict[str, str]] = {}
        self._unit_indexes: dict[str, dict[str, Decimal]] = {}
        self._validate_and_index()

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> FeatureProfileRegistry:
        try:
            document = FeatureProfileDocument.model_validate(value)
        except ValidationError as exc:
            error = exc.errors(include_url=False)[0]
            raise _config_error(
                _validation_path(error), str(error.get("msg", "invalid value"))
            ) from exc
        return cls(document)

    @property
    def profile_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._profiles))

    def resolve(self, category: str | None) -> CategoryFeatureProfile:
        profile_id = self._category_index.get(_identity(category or ""), "fallback")
        return self._profiles[profile_id]

    def feature_key(self, profile_id: str, raw_key: str) -> str | None:
        return self._feature_indexes[profile_id].get(_identity(raw_key))

    def convert_number(
        self,
        definition: FeatureDefinition,
        value: Decimal,
        raw_unit: str,
    ) -> tuple[Decimal, str | None]:
        if definition.unit_family is None:
            if raw_unit:
                raise _NormalizationFailure(
                    "unknown_unit", f"unit is not allowed: {raw_unit}"
                )
            with localcontext() as context:
                context.prec = max(28, len(value.as_tuple().digits))
                return value.normalize(), None

        family = self._document.unit_families[definition.unit_family]
        units = self._unit_indexes[definition.unit_family]
        unit_key = _unit_identity(raw_unit or family.canonical_unit)
        factor = units.get(unit_key)
        if factor is None:
            tokens = [_unit_identity(token) for token in raw_unit.split()]
            if len(tokens) > 1 and sum(token in units for token in tokens) > 1:
                raise _NormalizationFailure(
                    "conflicting_units", f"multiple units supplied: {raw_unit}"
                )
            raise _NormalizationFailure("unknown_unit", f"unknown unit: {raw_unit}")
        with localcontext() as context:
            context.prec = max(
                28,
                len(value.as_tuple().digits)
                + len(factor.as_tuple().digits)
                + family.precision
                + 4,
            )
            converted = value * factor
            quantum = Decimal(1).scaleb(-family.precision)
            converted = converted.quantize(quantum).normalize()
        return converted, family.canonical_unit

    def _validate_and_index(self) -> None:
        if "fallback" not in self._profiles:
            raise _config_error("profiles.fallback", "fallback profile is required")

        for family_id, family in self._document.unit_families.items():
            units: dict[str, Decimal] = {}
            for unit, factor in family.conversions.items():
                normalized = _unit_identity(unit)
                if normalized in units:
                    raise _config_error(
                        f"unit_families.{family_id}.conversions.{unit}",
                        "duplicate normalized unit",
                    )
                units[normalized] = factor
            canonical_unit = _unit_identity(family.canonical_unit)
            if canonical_unit not in units:
                raise _config_error(
                    f"unit_families.{family_id}.canonical_unit",
                    "canonical unit must appear in conversions",
                )
            if units[canonical_unit] != Decimal(1):
                raise _config_error(
                    f"unit_families.{family_id}.conversions.{family.canonical_unit}",
                    "canonical unit conversion factor must equal 1",
                )
            self._unit_indexes[family_id] = units

        for profile_id, profile in self._profiles.items():
            profile_categories: set[str] = set()
            normalized_profile_id = _identity(profile_id)
            previous_profile = self._category_index.get(normalized_profile_id)
            if previous_profile is not None and previous_profile != profile_id:
                raise _config_error(
                    f"profiles.{profile_id}",
                    f"profile identity already belongs to {previous_profile}",
                )
            self._category_index[normalized_profile_id] = profile_id
            for category_alias in profile.category_aliases:
                normalized_category = _identity(category_alias)
                if normalized_category in profile_categories:
                    raise _config_error(
                        f"profiles.{profile_id}.category_aliases",
                        "duplicate normalized category alias",
                    )
                profile_categories.add(normalized_category)
                previous = self._category_index.get(normalized_category)
                if previous is not None and previous != profile_id:
                    raise _config_error(
                        f"profiles.{profile_id}.category_aliases",
                        f"category alias already belongs to {previous}",
                    )
                self._category_index[normalized_category] = profile_id

            aliases: dict[str, str] = {}
            for feature_key, definition in profile.features.items():
                path = f"profiles.{profile_id}.features.{feature_key}"
                if (
                    definition.minimum is not None
                    and definition.maximum is not None
                    and definition.maximum < definition.minimum
                ):
                    raise _config_error(
                        f"{path}.maximum", "maximum must be greater than minimum"
                    )
                if (
                    definition.unit_family is not None
                    and definition.unit_family not in self._unit_indexes
                ):
                    raise _config_error(f"{path}.unit_family", "unknown unit family")
                feature_aliases: set[str] = set()
                for alias in (feature_key, *definition.aliases):
                    normalized_alias = _identity(alias)
                    if normalized_alias in feature_aliases:
                        raise _config_error(
                            f"{path}.aliases", "duplicate normalized alias"
                        )
                    feature_aliases.add(normalized_alias)
                    previous = aliases.get(normalized_alias)
                    if previous is not None and previous != feature_key:
                        raise _config_error(
                            f"{path}.aliases",
                            f"feature alias already belongs to {previous}",
                        )
                    aliases[normalized_alias] = feature_key
                self._validate_value_aliases(profile_id, feature_key, definition)

            for weighted_key in profile.ranking_weights:
                if weighted_key not in profile.features:
                    raise _config_error(
                        f"profiles.{profile_id}.ranking_weights.{weighted_key}",
                        "weight references an unknown feature",
                    )
            self._feature_indexes[profile_id] = aliases

    @staticmethod
    def _validate_value_aliases(
        profile_id: str,
        feature_key: str,
        definition: FeatureDefinition,
    ) -> None:
        path = f"profiles.{profile_id}.features.{feature_key}"
        if definition.data_type is FeatureDataType.ENUM:
            canonical = {_identity(value): value for value in definition.enum_values}
            if len(canonical) != len(definition.enum_values):
                raise _config_error(f"{path}.enum_values", "duplicate enum value")
            enum_aliases = set(canonical)
            for alias, target in definition.enum_aliases.items():
                normalized_alias = _identity(alias)
                if normalized_alias in enum_aliases:
                    raise _config_error(
                        f"{path}.enum_aliases.{alias}",
                        "duplicate normalized enum alias",
                    )
                enum_aliases.add(normalized_alias)
                if _identity(target) not in canonical:
                    raise _config_error(
                        f"{path}.enum_aliases.{alias}",
                        "enum alias targets an unknown value",
                    )
        if definition.data_type is FeatureDataType.BOOLEAN:
            true_values = [_identity(value) for value in definition.true_values]
            false_values = [_identity(value) for value in definition.false_values]
            if len(true_values) != len(set(true_values)):
                raise _config_error(
                    f"{path}.true_values", "duplicate normalized boolean alias"
                )
            if len(false_values) != len(set(false_values)):
                raise _config_error(
                    f"{path}.false_values", "duplicate normalized boolean alias"
                )
            if set(true_values) & set(false_values):
                raise _config_error(
                    f"{path}.false_values", "boolean aliases cannot overlap"
                )


class FeatureNormalizer:
    def __init__(self, registry: FeatureProfileRegistry) -> None:
        self._registry = registry

    def normalize(self, item: ProductSnapshotItem) -> NormalizedProductFeatures:
        category = (
            str(item.category.value)
            if item.category.status is FactStatus.KNOWN
            else None
        )
        profile = self._registry.resolve(category)
        raw_specifications = (
            tuple(
                (specification.key, specification.value)
                for specification in item.specifications.value.values
            )
            if item.specifications.status is FactStatus.KNOWN
            else ()
        )

        features: list[NormalizedFeature] = []
        errors: list[FeatureNormalizationError] = []
        for key in sorted(profile.features):
            definition = profile.features[key]
            try:
                raw = self._find_raw_feature(
                    profile.profile_id, key, raw_specifications
                )
            except _NormalizationFailure as exc:
                feature = self._error_feature(
                    profile.profile_id, key, definition, None, exc
                )
            else:
                feature = self._normalize_feature(
                    profile.profile_id, key, definition, raw
                )
            features.append(feature)
            if feature.error is not None:
                errors.append(feature.error)
        return NormalizedProductFeatures(
            item_id=item.item_id,
            category=category,
            profile_id=profile.profile_id,
            features=tuple(features),
            errors=tuple(errors),
        )

    def _find_raw_feature(
        self,
        profile_id: str,
        feature_key: str,
        raw_specifications: tuple[tuple[str, str], ...],
    ) -> tuple[str, str] | None:
        matches = [
            (raw_key, raw_value)
            for raw_key, raw_value in raw_specifications
            if self._registry.feature_key(profile_id, raw_key) == feature_key
        ]
        if not matches:
            return None
        if len(matches) > 1:
            joined = ", ".join(raw_key for raw_key, _ in matches)
            raise _NormalizationFailure(
                "conflicting_aliases", f"multiple source fields supplied: {joined}"
            )
        return matches[0]

    def _normalize_feature(
        self,
        profile_id: str,
        key: str,
        definition: FeatureDefinition,
        raw: tuple[str, str] | None,
    ) -> NormalizedFeature:
        common = {
            "key": key,
            "display_name": definition.display_name,
            "data_type": definition.data_type,
            "unit": (
                self._registry._document.unit_families[
                    definition.unit_family
                ].canonical_unit
                if definition.unit_family
                else None
            ),
            "larger_is_better": definition.larger_is_better,
            "missing_policy": definition.missing_policy,
            "hard_filter": definition.hard_filter,
            "ranking": definition.ranking,
            "display": definition.display,
        }
        if raw is None:
            return NormalizedFeature(
                **common,
                status=FeatureNormalizationStatus.UNKNOWN,
            )

        raw_key, raw_value = raw
        try:
            value, unit = self._normalize_value(definition, raw_value)
        except _NormalizationFailure as exc:
            return self._error_feature(profile_id, key, definition, raw, exc)
        return NormalizedFeature(
            **{**common, "unit": unit},
            status=FeatureNormalizationStatus.KNOWN,
            raw_key=raw_key,
            raw_value=raw_value,
            value=value,
        )

    def _error_feature(
        self,
        profile_id: str,
        key: str,
        definition: FeatureDefinition,
        raw: tuple[str, str] | None,
        failure: _NormalizationFailure,
    ) -> NormalizedFeature:
        raw_key, raw_value = raw if raw is not None else (None, None)
        unit = (
            self._registry._document.unit_families[
                definition.unit_family
            ].canonical_unit
            if definition.unit_family
            else None
        )
        error = FeatureNormalizationError(
            code=failure.code,
            path=f"{profile_id}.features.{key}",
            message=_bounded_error_message(failure),
            raw_value=raw_value,
        )
        return NormalizedFeature(
            key=key,
            display_name=definition.display_name,
            data_type=definition.data_type,
            status=FeatureNormalizationStatus.ERROR,
            raw_key=raw_key,
            raw_value=raw_value,
            unit=unit,
            larger_is_better=definition.larger_is_better,
            missing_policy=definition.missing_policy,
            hard_filter=definition.hard_filter,
            ranking=definition.ranking,
            display=definition.display,
            error=error,
        )

    def _normalize_value(
        self, definition: FeatureDefinition, raw_value: str
    ) -> tuple[NormalizedValue, str | None]:
        if definition.data_type is FeatureDataType.NUMBER:
            value, raw_unit = self._parse_number(raw_value)
            value, unit = self._registry.convert_number(definition, value, raw_unit)
            if definition.minimum is not None and value < definition.minimum:
                raise _NormalizationFailure(
                    "out_of_range", f"value is below {definition.minimum}"
                )
            if definition.maximum is not None and value > definition.maximum:
                raise _NormalizationFailure(
                    "out_of_range", f"value is above {definition.maximum}"
                )
            return value, unit
        if definition.data_type is FeatureDataType.BOOLEAN:
            normalized = _identity(raw_value)
            if normalized in {_identity(value) for value in definition.true_values}:
                return True, None
            if normalized in {_identity(value) for value in definition.false_values}:
                return False, None
            raise _NormalizationFailure(
                "invalid_boolean", f"unrecognized boolean value: {raw_value}"
            )
        if definition.data_type is FeatureDataType.ENUM:
            normalized = _identity(raw_value)
            canonical = {_identity(value): value for value in definition.enum_values}
            aliases = {
                _identity(alias): target
                for alias, target in definition.enum_aliases.items()
            }
            if normalized in canonical:
                return canonical[normalized], None
            target = aliases.get(normalized)
            if target is not None:
                return canonical[_identity(target)], None
            raise _NormalizationFailure(
                "invalid_enum", f"unrecognized enum value: {raw_value}"
            )
        normalized_text = " ".join(raw_value.strip().split())
        if not normalized_text:
            raise _NormalizationFailure("invalid_text", "text value is empty")
        return normalized_text, None

    @staticmethod
    def _parse_number(raw_value: str) -> tuple[Decimal, str]:
        match = _NUMBER_PATTERN.fullmatch(raw_value.strip())
        if match is None:
            raise _NormalizationFailure(
                "invalid_number", f"cannot parse number: {raw_value}"
            )
        try:
            value = Decimal(match.group(1))
        except InvalidOperation as exc:
            raise _NormalizationFailure(
                "invalid_number", f"cannot parse number: {raw_value}"
            ) from exc
        return value, match.group(2).strip()


def load_feature_profiles(
    path: Path | str = DEFAULT_FEATURE_PROFILE_PATH,
) -> FeatureProfileRegistry:
    config_path = Path(path)
    try:
        payload = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise FeatureConfigError(
            f"{config_path}: cannot load feature profiles"
        ) from exc
    if not isinstance(payload, dict):
        raise FeatureConfigError(f"{config_path}: root must be a mapping")
    return FeatureProfileRegistry.from_mapping(payload)


def _bounded_error_message(failure: _NormalizationFailure) -> str:
    message = " ".join(str(failure).split())
    if len(message) <= 256:
        return message
    return message[:253] + "..."
