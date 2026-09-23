import json
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    StringConstraints,
    TypeAdapter,
    field_validator,
    model_validator,
)


MAX_AIMODEL_MESSAGE_LENGTH = 8_000
MAX_AIMODEL_LINKS = 8
MAX_AIMODEL_LINK_LENGTH = 2_048
MAX_AIMODEL_SEARCH_QUERY_LENGTH = 200
MAX_AIMODEL_CANDIDATES = 20
MAX_AIMODEL_ROUTE_LENGTH = 512
MAX_AIMODEL_SOURCE_EVENT_LENGTH = 64
MAX_AIMODEL_ITEM_ID_LENGTH = 128
MAX_AIMODEL_REQUEST_BYTES = 16 * 1_024

AiModelItemId = (
    Annotated[
        str,
        StringConstraints(
            strip_whitespace=True,
            min_length=1,
            max_length=MAX_AIMODEL_ITEM_ID_LENGTH,
        ),
    ]
    | Annotated[StrictInt, Field(gt=0)]
)


def _reject_non_positive_numeric_string(value: AiModelItemId) -> AiModelItemId:
    if isinstance(value, str):
        try:
            numeric_value = int(value)
        except ValueError:
            return value
        if numeric_value <= 0:
            raise ValueError("numeric item IDs must be positive")
    return value


class AiModelCandidateRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["v1"] = "v1"
    item_id: AiModelItemId

    _validate_item_id = field_validator("item_id")(_reject_non_positive_numeric_string)


class AiModelPageContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["v1"] = "v1"
    page_type: Literal["unknown", "none", "search", "product", "cart", "other"] = (
        "unknown"
    )
    route: (
        Annotated[
            str,
            StringConstraints(
                strip_whitespace=True,
                min_length=1,
                max_length=MAX_AIMODEL_ROUTE_LENGTH,
            ),
        ]
        | None
    ) = None
    search_query: (
        Annotated[
            str,
            StringConstraints(
                strip_whitespace=True,
                min_length=1,
                max_length=MAX_AIMODEL_SEARCH_QUERY_LENGTH,
            ),
        ]
        | None
    ) = None
    current_item_id: AiModelItemId | None = None
    candidate_refs: list[AiModelCandidateRef] = Field(
        default_factory=list,
        max_length=MAX_AIMODEL_CANDIDATES,
    )
    source_event: (
        Annotated[
            str,
            StringConstraints(
                strip_whitespace=True,
                min_length=1,
                max_length=MAX_AIMODEL_SOURCE_EVENT_LENGTH,
            ),
        ]
        | None
    ) = None
    client_time: datetime | None = None

    _validate_current_item_id = field_validator("current_item_id")(
        _reject_non_positive_numeric_string
    )


class AiModelChatRequest(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"x-max-serialized-bytes": MAX_AIMODEL_REQUEST_BYTES},
    )

    user_id: int = Field(gt=0)
    conversation_id: int | None = None
    message: str = Field(min_length=1, max_length=MAX_AIMODEL_MESSAGE_LENGTH)
    links: list[
        Annotated[
            str,
            StringConstraints(
                strip_whitespace=True,
                min_length=1,
                max_length=MAX_AIMODEL_LINK_LENGTH,
            ),
        ]
    ] = Field(default_factory=list, max_length=MAX_AIMODEL_LINKS)
    request_version: Literal["v1", "v2"] = "v1"
    page_context: AiModelPageContext | None = None

    @model_validator(mode="before")
    @classmethod
    def validate_serialized_size(cls, value: Any) -> Any:
        if isinstance(value, dict):
            try:
                serialized = json.dumps(
                    value,
                    ensure_ascii=False,
                    separators=(",", ":"),
                ).encode("utf-8")
            except (TypeError, ValueError):
                return value
            if len(serialized) > MAX_AIMODEL_REQUEST_BYTES:
                raise ValueError(
                    f"serialized request exceeds {MAX_AIMODEL_REQUEST_BYTES} bytes"
                )
        return value

    @model_validator(mode="after")
    def validate_context_version(self) -> "AiModelChatRequest":
        if self.request_version == "v1" and self.page_context is not None:
            raise ValueError("page_context requires request_version v2")
        return self


AiModelResponseType = Literal[
    "answer",
    "clarification",
    "product_list",
    "comparison",
    "recommendation",
    "action_preview",
    "action_result",
    "fallback",
]
AiModelShortText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=512),
]
AiModelAnswerText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=16_000),
]
AiModelUrl = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=2_048),
]


class AiModelRecommendedLink(BaseModel):
    model_config = ConfigDict(extra="forbid")

    item_id: AiModelItemId
    item_name: AiModelShortText
    url: AiModelUrl


class AiModelProductRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    item_id: AiModelItemId
    item_name: AiModelShortText
    url: AiModelUrl | None = None


class AiModelClarificationOption(BaseModel):
    model_config = ConfigDict(extra="forbid")

    option_id: AiModelShortText
    label: AiModelShortText
    value: AiModelShortText


class AiModelEvidenceReference(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evidence_id: AiModelShortText
    source_type: Literal[
        "product_fact",
        "catalog",
        "review",
        "policy",
        "external",
    ]
    source_id: AiModelShortText
    title: AiModelShortText | None = None
    url: AiModelUrl | None = None


class AiModelComparisonColumn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: AiModelShortText
    label: AiModelShortText


class AiModelComparisonRow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    item_id: AiModelItemId
    cells: dict[str, AiModelShortText]


class AiModelRecommendationReason(BaseModel):
    model_config = ConfigDict(extra="forbid")

    item_id: AiModelItemId
    reason: AiModelShortText
    evidence_ids: list[AiModelShortText] = Field(default_factory=list)


class AiModelActionPreview(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action_type: Literal["add_to_cart", "remove_from_cart", "compare", "open_item"]
    action_token: AiModelShortText
    summary: AiModelShortText
    target_item_id: AiModelItemId | None = None


def _item_key(item_id: AiModelItemId) -> str:
    return str(item_id)


def _validate_unique_products(products: list[AiModelProductRef]) -> None:
    item_ids = [_item_key(product.item_id) for product in products]
    if len(item_ids) != len(set(item_ids)):
        raise ValueError("product item IDs must be unique")


def _links_from_products(
    products: list[AiModelProductRef],
) -> list[AiModelRecommendedLink]:
    return [
        AiModelRecommendedLink(
            item_id=product.item_id,
            item_name=product.item_name,
            url=product.url,
        )
        for product in products
        if product.url is not None
    ]


def _without_null_schema_defaults(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _without_null_schema_defaults(child)
            for key, child in value.items()
            if not (key == "default" and child is None)
        }
    if isinstance(value, list):
        return [_without_null_schema_defaults(child) for child in value]
    return value


class AiModelResponsePayloadBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["v1"] = "v1"
    answer: AiModelAnswerText
    evidence: list[AiModelEvidenceReference] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_evidence_ids(self) -> "AiModelResponsePayloadBase":
        evidence_ids = [evidence.evidence_id for evidence in self.evidence]
        if len(evidence_ids) != len(set(evidence_ids)):
            raise ValueError("evidence IDs must be unique")
        return self

    def recommended_products(self) -> list[AiModelProductRef]:
        return []


class AiModelAnswerPayload(AiModelResponsePayloadBase):
    response_type: Literal["answer"] = "answer"
    products: list[AiModelProductRef] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_products(self) -> "AiModelAnswerPayload":
        _validate_unique_products(self.products)
        return self

    def recommended_products(self) -> list[AiModelProductRef]:
        return self.products


class AiModelClarificationPayload(AiModelResponsePayloadBase):
    response_type: Literal["clarification"] = "clarification"
    options: list[AiModelClarificationOption] = Field(min_length=1, max_length=12)

    @model_validator(mode="after")
    def validate_option_ids(self) -> "AiModelClarificationPayload":
        option_ids = [option.option_id for option in self.options]
        if len(option_ids) != len(set(option_ids)):
            raise ValueError("clarification option IDs must be unique")
        return self


class AiModelProductListPayload(AiModelResponsePayloadBase):
    response_type: Literal["product_list"] = "product_list"
    products: list[AiModelProductRef] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def validate_products(self) -> "AiModelProductListPayload":
        _validate_unique_products(self.products)
        return self

    def recommended_products(self) -> list[AiModelProductRef]:
        return self.products


class AiModelComparisonPayload(AiModelResponsePayloadBase):
    response_type: Literal["comparison"] = "comparison"
    products: list[AiModelProductRef] = Field(min_length=2, max_length=12)
    columns: list[AiModelComparisonColumn] = Field(min_length=1, max_length=24)
    rows: list[AiModelComparisonRow] = Field(min_length=2, max_length=12)

    @model_validator(mode="after")
    def validate_matrix(self) -> "AiModelComparisonPayload":
        _validate_unique_products(self.products)
        product_ids = {_item_key(product.item_id) for product in self.products}
        column_keys = [column.key for column in self.columns]
        if len(column_keys) != len(set(column_keys)):
            raise ValueError("comparison column keys must be unique")
        row_ids = [_item_key(row.item_id) for row in self.rows]
        if len(row_ids) != len(set(row_ids)):
            raise ValueError("comparison row item IDs must be unique")
        if set(row_ids) != product_ids:
            raise ValueError("comparison rows must match products")
        for row in self.rows:
            if set(row.cells) != set(column_keys):
                raise ValueError("comparison row cells must match columns")
        return self

    def recommended_products(self) -> list[AiModelProductRef]:
        return self.products


class AiModelRecommendationPayload(AiModelResponsePayloadBase):
    response_type: Literal["recommendation"] = "recommendation"
    candidates: list[AiModelProductRef] = Field(min_length=1, max_length=100)
    recommendations: list[AiModelRecommendationReason] = Field(
        min_length=1,
        max_length=20,
    )

    @model_validator(mode="after")
    def validate_recommendations(self) -> "AiModelRecommendationPayload":
        _validate_unique_products(self.candidates)
        candidates_by_id = {
            _item_key(candidate.item_id): candidate for candidate in self.candidates
        }
        recommendation_ids = [
            _item_key(recommendation.item_id) for recommendation in self.recommendations
        ]
        if len(recommendation_ids) != len(set(recommendation_ids)):
            raise ValueError("recommendation item IDs must be unique")
        if not set(recommendation_ids) <= set(candidates_by_id):
            raise ValueError("recommended products must be candidates")
        evidence_ids = {evidence.evidence_id for evidence in self.evidence}
        for recommendation in self.recommendations:
            if not set(recommendation.evidence_ids) <= evidence_ids:
                raise ValueError("recommendation evidence must resolve")
        return self

    def recommended_products(self) -> list[AiModelProductRef]:
        candidates_by_id = {
            _item_key(candidate.item_id): candidate for candidate in self.candidates
        }
        return [
            candidates_by_id[_item_key(recommendation.item_id)]
            for recommendation in self.recommendations
        ]


class AiModelActionPreviewPayload(AiModelResponsePayloadBase):
    response_type: Literal["action_preview"] = "action_preview"
    products: list[AiModelProductRef] = Field(default_factory=list, max_length=20)
    action: AiModelActionPreview

    @model_validator(mode="after")
    def validate_action_target(self) -> "AiModelActionPreviewPayload":
        _validate_unique_products(self.products)
        if self.action.target_item_id is not None and _item_key(
            self.action.target_item_id
        ) not in {_item_key(product.item_id) for product in self.products}:
            raise ValueError("action target must resolve to a product")
        return self

    def recommended_products(self) -> list[AiModelProductRef]:
        if self.action.target_item_id is None:
            return []
        target_key = _item_key(self.action.target_item_id)
        return [
            product
            for product in self.products
            if _item_key(product.item_id) == target_key
        ]


class AiModelActionResultPayload(AiModelResponsePayloadBase):
    response_type: Literal["action_result"] = "action_result"
    products: list[AiModelProductRef] = Field(default_factory=list, max_length=20)
    action_token: AiModelShortText
    status: Literal["success", "failed"]
    result_summary: AiModelShortText

    @model_validator(mode="after")
    def validate_products(self) -> "AiModelActionResultPayload":
        _validate_unique_products(self.products)
        return self

    def recommended_products(self) -> list[AiModelProductRef]:
        return self.products


class AiModelFallbackPayload(AiModelResponsePayloadBase):
    response_type: Literal["fallback"] = "fallback"
    reason_code: AiModelShortText


AiModelResponsePayload = Annotated[
    AiModelAnswerPayload
    | AiModelClarificationPayload
    | AiModelProductListPayload
    | AiModelComparisonPayload
    | AiModelRecommendationPayload
    | AiModelActionPreviewPayload
    | AiModelActionResultPayload
    | AiModelFallbackPayload,
    Field(discriminator="response_type"),
]
AiModelResponsePayloadAdapter = TypeAdapter(AiModelResponsePayload)


class AiModelToolResult(BaseModel):
    tool: str
    ok: bool
    input: str
    item_id: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None


class AiModelChatResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    response_version: Literal["v1"] = "v1"
    conversation_id: int | None = None
    response_type: AiModelResponseType
    payload: AiModelResponsePayload
    answer: AiModelAnswerText
    recommended_links: list[AiModelRecommendedLink]

    @classmethod
    def model_json_schema(
        cls,
        *args: Any,
        **kwargs: Any,
    ) -> dict[str, Any]:
        return _without_null_schema_defaults(super().model_json_schema(*args, **kwargs))

    @classmethod
    def from_payload(
        cls,
        payload: AiModelResponsePayload,
        *,
        conversation_id: int | None = None,
    ) -> "AiModelChatResponse":
        return cls(
            conversation_id=conversation_id,
            response_type=payload.response_type,
            payload=payload,
            answer=payload.answer,
            recommended_links=_links_from_products(payload.recommended_products()),
        )

    @classmethod
    def from_legacy(
        cls,
        *,
        answer: str,
        recommended_links: list[AiModelRecommendedLink | dict[str, Any]],
        conversation_id: int | None = None,
    ) -> "AiModelChatResponse":
        links = [
            link
            if isinstance(link, AiModelRecommendedLink)
            else AiModelRecommendedLink.model_validate(link)
            for link in recommended_links
        ]
        payload = AiModelAnswerPayload(
            answer=answer,
            products=[
                AiModelProductRef(
                    item_id=link.item_id,
                    item_name=link.item_name,
                    url=link.url,
                )
                for link in links
            ],
        )
        return cls.from_payload(payload, conversation_id=conversation_id)

    @model_validator(mode="after")
    def validate_legacy_projection(self) -> "AiModelChatResponse":
        if self.response_type != self.payload.response_type:
            raise ValueError("response_type must match payload")
        if self.answer != self.payload.answer:
            raise ValueError("answer must be derived from payload")
        expected_links = _links_from_products(self.payload.recommended_products())
        if self.recommended_links != expected_links:
            raise ValueError("recommended_links must be derived from payload")
        return self


class AiModelConversationSummary(BaseModel):
    id: int
    title: str | None = None
    created_at: Any | None = None
    updated_at: Any | None = None


class AiModelStoredMessage(BaseModel):
    id: int
    role: str
    content: str
    links: list[str] = Field(default_factory=list)
    recommended_links: list[dict[str, Any]] = Field(default_factory=list)
    structured_response: AiModelChatResponse | None = None
    created_at: Any | None = None
