from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class AiModelChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_id: int = Field(gt=0)
    conversation_id: int | None = None
    message: str = Field(min_length=1)
    links: list[str] = Field(default_factory=list)


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
