from typing import Any

from pydantic import BaseModel, Field


class AiModelChatRequest(BaseModel):
    user_id: str = Field(min_length=1, max_length=64)
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
