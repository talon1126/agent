import json
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    StringConstraints,
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


class AiModelRecommendedLink(BaseModel):
    item_id: str
    item_name: str
    url: str


class AiModelToolResult(BaseModel):
    tool: str
    ok: bool
    input: str
    item_id: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None


class AiModelChatResponse(BaseModel):
    conversation_id: int | None = None
    answer: str
    recommended_links: list[AiModelRecommendedLink] = Field(default_factory=list)


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
    created_at: Any | None = None
